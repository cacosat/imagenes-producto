import io
import json
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from imagenes_producto import generar
from imagenes_producto.cli import main
from imagenes_producto.config import Config, Generar
from imagenes_producto.generar import (
    ErrorFatal,
    ErrorGeneracion,
    armar_prompt,
    costo,
    generar_lote,
    preparar_original,
    producto_desde_original,
)
from test_estandarizar import telefono

RAIZ = Path(__file__).parents[1]
# Rutas absolutas a la receta del repo, para que las pruebas no dependan de la carpeta actual.
CFG = replace(Config(), generar=Generar(
    prompts=str(RAIZ / "estilo/prompts"),
    referencias=str(RAIZ / "estilo/referencias"),
    rasgos=str(RAIZ / "estilo/rasgos.toml"),
))
USO = {"input_tokens": 3000, "input_tokens_details": {"image_tokens": 2500, "text_tokens": 500}, "output_tokens": 1400}


def png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


class ClienteFalso:
    """Devuelve siempre el mismo teléfono transparente, sin llamar a ninguna API."""

    def __init__(self, fallar_en: set[str] = frozenset(), fatal: bool = False):
        self.pedidos = []
        self.fallar_en, self.fatal = fallar_en, fatal

    def generar(self, prompt, imagenes, cfg):
        self.pedidos.append((prompt, [nombre for nombre, _ in imagenes]))
        vista = next(v for v in cfg.generar.vistas if f"VISTA {v.upper()}" in prompt)
        if self.fatal:
            raise ErrorFatal("API key inválida")
        if vista in self.fallar_en:
            raise ErrorGeneracion("pedido rechazado")
        return [png(telefono((1024, 1536), (262, 150, 762, 1350)))] * cfg.generar.candidatos, USO


def original(carpeta: Path, nombre="iPhone 12 Azul.png", lado=1200):
    """Foto original de mentira: el par trasera + frontal sobre fondo blanco, con márgenes."""
    im = Image.new("RGB", (lado, lado), (255, 255, 255))
    im.paste(telefono((lado, lado), (lado // 5, lado // 6, lado // 2, lado * 5 // 6)), (0, 0),
             telefono((lado, lado), (lado // 5, lado // 6, lado // 2, lado * 5 // 6)))
    ruta = carpeta / nombre
    im.save(ruta)
    return ruta


def test_producto_desde_el_nombre_del_original():
    assert producto_desde_original(Path("iPhone 12 Pro Plata.webp")) == ("iPhone-12-Pro-Plata", "iPhone 12 Pro", "Plata")
    assert producto_desde_original(Path("iPhone-13-Azul.jpg")) == ("iPhone-13-Azul", "iPhone 13", "Azul")
    assert producto_desde_original(Path("iPhone SE (3rd generation) Rojo.webp"))[1:] == ("iPhone SE (3rd generation)", "Rojo")
    with pytest.raises(ErrorGeneracion):
        producto_desde_original(Path("Azul.png"))


def test_prompt_por_vista_con_modelo_color_y_rasgos():
    prompt = armar_prompt("Lateral", "iPhone 15 Pro", "Azul", CFG, ["Botón de Acción en el lado izquierdo"])
    assert "vista lateral" in prompt and "iPhone 15 Pro, color Azul" in prompt
    assert "VISTA LATERAL" in prompt and "VISTA FRONTAL" not in prompt
    assert "- Botón de Acción en el lado izquierdo" in prompt
    assert "Fondo transparente" in prompt
    assert "{" not in prompt and "}" not in prompt  # no quedaron variables sin reemplazar

    sin_rasgos = armar_prompt("Frontal", "iPhone 12", "Blanco", replace(CFG, generar=replace(CFG.generar, fondo="blanco")))
    assert "RASGOS CLAVE" not in sin_rasgos and "Pantalla apagada" in sin_rasgos
    assert "blanco liso" in sin_rasgos


def test_original_sin_margenes_y_alerta_si_es_chico(tmp_path):
    datos, alertas = preparar_original(original(tmp_path, lado=1200), CFG)
    im = Image.open(io.BytesIO(datos))
    assert im.width < 600 and im.height < 1100  # se recortó el fondo blanco sobrante
    assert [a.codigo for a in alertas] == []

    _, alertas = preparar_original(original(tmp_path, "iPhone 12 Rojo.png", lado=500), CFG)
    assert [a.codigo for a in alertas] == ["ORIGINAL_CHICO"]


def test_costo_segun_tokens():
    # 500 × 5 + 2500 × 8 + 1400 × 30, por millón de tokens
    assert costo(USO, CFG) == pytest.approx(0.0645)
    assert costo({"input_tokens": 1000, "output_tokens": 0}, CFG) == pytest.approx(0.008)  # sin desglose: como imagen


def test_lote_guarda_imagenes_y_metadatos_y_sigue_ante_un_error(tmp_path):
    cliente = ClienteFalso(fallar_en={"Frontal"})
    resultados = generar_lote([original(tmp_path)], tmp_path / "generadas", CFG, cliente)

    assert [(r.vista, r.error) for r in resultados] == [("Lateral", None), ("Frontal", "pedido rechazado"), ("Trasera", None)]
    assert all(nombres == ["original.png", "referencia.png"] for _, nombres in cliente.pedidos)
    carpeta = tmp_path / "generadas" / "iPhone-12-Azul"
    assert sorted(p.name for p in carpeta.iterdir()) == ["Lateral.json", "Lateral.png", "Trasera.json", "Trasera.png"]
    meta = json.loads((carpeta / "Trasera.json").read_text(encoding="utf-8"))
    assert meta["modelo"] == CFG.generar.modelo and meta["costo_usd"] == pytest.approx(0.0645)
    assert meta["referencia"].endswith("-Trasera.webp") and "VISTA TRASERA" in meta["prompt"]


def test_error_fatal_detiene_el_lote(tmp_path):
    with pytest.raises(ErrorFatal):
        generar_lote([original(tmp_path)], tmp_path / "generadas", CFG, ClienteFalso(fatal=True))


def test_cli_generar_sin_api_key_no_gasta(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert main(["generar", str(original(tmp_path))]) == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().err


def test_cli_generar_de_punta_a_punta(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "clave-de-prueba")
    monkeypatch.setattr(generar, "crear_cliente", lambda cfg: ClienteFalso())
    (tmp_path / "config.toml").write_text(
        f'[generar]\nprompts = "{(RAIZ / "estilo/prompts").as_posix()}"\n'
        f'referencias = "{(RAIZ / "estilo/referencias").as_posix()}"\n', encoding="utf-8")

    assert main(["generar", str(original(tmp_path)), "--si"]) == 0

    finales = sorted(p.name for p in (tmp_path / "salida" / "iPhone-12-Azul").glob("*.webp"))
    assert finales == [f"iPhone-12-Azul-Version-Final-{v}.webp" for v in ("Frontal", "Lateral", "Portada", "Trasera")]


def test_cli_generar_pide_confirmacion(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "clave-de-prueba")
    cliente = ClienteFalso()
    monkeypatch.setattr(generar, "crear_cliente", lambda cfg: cliente)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert main(["generar", str(original(tmp_path))]) == 1
    assert cliente.pedidos == []  # no se llamó a la API
