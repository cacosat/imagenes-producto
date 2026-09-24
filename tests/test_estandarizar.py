import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw
from scipy import ndimage

from imagenes_producto.cli import main
from imagenes_producto.config import Config, ErrorConfig, Lienzo, Salida, Sombra, cargar_config
from imagenes_producto.estandarizar import (
    ErrorEstandarizacion,
    caja,
    componer,
    componer_portada,
    estandarizar,
    identificar,
)
from imagenes_producto.imagen import abrir, codificar

RAIZ = Path(__file__).parents[1]
CFG = Config()
# Con fondo transparente el canal alfa del resultado muestra dónde quedó el producto.
TRANSPARENTE = replace(CFG, lienzo=Lienzo(fondo="transparente"))
ALTO_ESPERADO = CFG.producto.altura * CFG.lienzo.alto
CENTRO = (CFG.lienzo.ancho / 2, CFG.lienzo.alto / 2)
AZUL, ROJO = (40, 60, 160, 255), (160, 40, 40, 255)


def telefono(lienzo, rect, color=(30, 30, 30, 255)):
    """Rectángulo redondeado con bordes suavizados (se dibuja al doble y se reduce) sobre fondo transparente."""
    k = 2
    grande = Image.new("RGBA", (lienzo[0] * k, lienzo[1] * k), (0, 0, 0, 0))
    x0, y0, x1, y1 = (v * k for v in rect)
    radio = min(x1 - x0, y1 - y0) // 8
    ImageDraw.Draw(grande).rounded_rectangle((x0, y0, x1 - 1, y1 - 1), radius=radio, fill=color)
    return grande.resize(lienzo, Image.Resampling.LANCZOS)


def medir(final):
    """Ancho, alto y centro del producto en la imagen final."""
    x0, y0, x1, y1 = caja(np.asarray(final.getchannel("A")) >= 128)
    return x1 - x0, y1 - y0, (x0 + x1) / 2, (y0 + y1) / 2


def centrado(cx, cy):
    return abs(cx - CENTRO[0]) <= 1 and abs(cy - CENTRO[1]) <= 1


def codigos(resultado):
    return [a.codigo for a in resultado.alertas]


def test_misma_altura_y_centro_en_todas_las_vistas():
    # Frente descentrado, lateral angosto y trasera grande, en imágenes de distinto tamaño.
    vistas = [
        telefono((1200, 1600), (100, 300, 700, 1500)),
        telefono((800, 1000), (500, 50, 540, 850)),
        telefono((2000, 2600), (1000, 100, 1900, 2500)),
    ]
    for im in vistas:
        final = estandarizar(im, TRANSPARENTE).final
        assert final.size == (CFG.lienzo.ancho, CFG.lienzo.alto)
        _, alto, cx, cy = medir(final)
        assert abs(alto - ALTO_ESPERADO) <= 1.5
        assert centrado(cx, cy)


def test_alerta_de_ampliacion_solo_si_hay_que_ampliar():
    chico = estandarizar(telefono((1000, 1000), (300, 200, 600, 800)), TRANSPARENTE)
    assert chico.escala == pytest.approx(ALTO_ESPERADO / 600)
    assert "AMPLIACION" in codigos(chico)

    grande = estandarizar(telefono((1500, 2400), (400, 100, 1300, 2300)), TRANSPARENTE)
    assert grande.escala < 1
    assert "AMPLIACION" not in codigos(grande)


def test_alerta_si_el_producto_toca_el_borde():
    assert "TOCA_BORDE" in codigos(estandarizar(telefono((600, 800), (0, 100, 300, 700)), TRANSPARENTE))
    assert "TOCA_BORDE" not in codigos(estandarizar(telefono((600, 800), (100, 100, 400, 700)), TRANSPARENTE))


def test_producto_muy_ancho_se_limita_por_ancho():
    r = estandarizar(telefono((1000, 1000), (50, 400, 950, 600)), TRANSPARENTE)
    ancho, _, cx, cy = medir(r.final)
    assert "LIMITADO_POR_ANCHO" in codigos(r)
    assert abs(ancho - CFG.producto.ancho_max * CFG.lienzo.ancho) <= 1.5
    assert centrado(cx, cy)


def test_manchas_sueltas_se_borran_y_objetos_extra_se_alertan():
    base = telefono((1000, 1400), (200, 100, 800, 1300))

    con_mancha = base.copy()
    con_mancha.paste((30, 30, 30, 255), (950, 20, 953, 23))  # 3×3 px lejos del teléfono
    manchado = estandarizar(con_mancha, TRANSPARENTE)
    assert manchado.caja_producto == estandarizar(base, TRANSPARENTE).caja_producto
    assert "VARIOS_OBJETOS" not in codigos(manchado)

    con_objeto = base.copy()
    con_objeto.paste((30, 30, 30, 255), (850, 20, 950, 120))  # 100×100 px: ~1,4 % del teléfono
    assert "VARIOS_OBJETOS" in codigos(estandarizar(con_objeto, TRANSPARENTE))


def test_imagen_sin_producto_da_error():
    with pytest.raises(ErrorEstandarizacion):
        estandarizar(Image.new("RGBA", (100, 100), (0, 0, 0, 0)), TRANSPARENTE)


def test_bordes_sin_halo_sobre_fondo_blanco():
    # Teléfono blanco; los píxeles transparentes traen color basura, como pasa en recortes reales.
    im = np.array(telefono((500, 900), (100, 100, 400, 800), color=(255, 255, 255, 255)))
    im[im[..., 3] == 0, :3] = (255, 0, 0)
    sin_sombra = replace(CFG, sombra=Sombra(activa=False))
    final = estandarizar(Image.fromarray(im), sin_sombra).final
    assert np.asarray(final.convert("RGB")).min() >= 250  # ni halo oscuro ni rojizo


def test_sombra_bajo_el_producto_e_identica_entre_imagenes():
    # El mismo teléfono en dos imágenes de distinto tamaño y posición da la misma imagen final, sombra incluida.
    a = estandarizar(telefono((1200, 1600), (100, 300, 700, 1500)), CFG).final
    b = estandarizar(telefono((700, 1000), (380, 50, 680, 650)), CFG).final
    diferencia = np.abs(np.asarray(a.convert("L")).astype(int) - np.asarray(b.convert("L")).astype(int))
    assert diferencia.mean() < 0.5

    gris = np.asarray(a.convert("L")).astype(int)
    abajo = round(CENTRO[1] + ALTO_ESPERADO / 2)  # borde inferior del teléfono
    columna = round(CENTRO[0])
    assert 15 <= 255 - gris[abajo + 6, columna] <= 40  # sombra suave justo debajo, como en las referencias
    assert 255 - gris[abajo + 45, columna] <= 2  # y se desvanece en ~40 px
    assert (gris[: round(CENTRO[1] - ALTO_ESPERADO / 2) - 2] == 255).all()  # nada encima del teléfono


def test_interior_opaco_y_huecos_rellenados_con_el_color_original():
    original = telefono((600, 1000), (100, 100, 500, 900), color=AZUL)  # el teléfono tal como se ve
    recorte = np.array(original)
    recorte[recorte[..., 3] == 255, 3] = 230  # la segmentación dejó el cuerpo algo transparente…
    recorte[400:500, 250:350, 3] = 0  # …y con un hueco de 100×100 px (~3 % del teléfono)

    r = estandarizar(Image.fromarray(recorte), TRANSPARENTE, original=original)
    assert "HUECOS_RELLENADOS" in codigos(r)
    px = np.asarray(r.maestro).astype(int)
    interior = ndimage.binary_erosion(px[..., 3] >= 128, iterations=5)
    assert px[interior, 3].min() == 255
    assert np.abs(px[interior, :3] - AZUL[:3]).max() <= 1  # el hueco quedó con el color real

    # Sin la imagen original no hay con qué rellenar el hueco: queda transparente y no se alerta.
    sin_original = estandarizar(Image.fromarray(recorte), TRANSPARENTE)
    assert "HUECOS_RELLENADOS" not in codigos(sin_original)
    alfa = np.asarray(sin_original.maestro.getchannel("A"))
    x0, y0, x1, y1 = caja(alfa >= 128)
    assert (alfa[y0:y1, x0:x1] == 0).sum() > 10_000


def test_portada_trasera_detras_con_cinco_sextos_visibles():
    trasera = estandarizar(telefono((700, 1300), (50, 100, 530, 1100), color=ROJO), CFG).maestro
    frontal = estandarizar(telefono((900, 1500), (300, 200, 780, 1200), color=AZUL), CFG).maestro
    par, alertas = componer_portada(trasera, frontal, CFG)
    final, _, alertas_lienzo = componer(par, TRANSPARENTE)
    assert not alertas + alertas_lienzo

    _, alto, cx, cy = medir(final)
    assert abs(alto - ALTO_ESPERADO) <= 1.5
    assert centrado(cx, cy)

    px = np.asarray(final).astype(int)
    opaco = px[..., 3] >= 128
    columnas_frontal = np.flatnonzero((opaco & (px[..., 2] > px[..., 0] + 50)).any(axis=0))
    izquierda_trasera = np.flatnonzero(opaco.any(axis=0))[0]
    ancho_frontal = columnas_frontal[-1] - columnas_frontal[0] + 1
    visible = columnas_frontal[0] - izquierda_trasera
    assert visible / ancho_frontal == pytest.approx(5 / 6, abs=0.01)


def test_portada_alerta_si_frontal_y_trasera_no_tienen_la_misma_proporcion():
    trasera = estandarizar(telefono((700, 1300), (50, 100, 530, 1100)), CFG).maestro  # 480 × 1000
    frontal = estandarizar(telefono((700, 1300), (50, 100, 590, 1100)), CFG).maestro  # 540 × 1000
    _, alertas = componer_portada(trasera, frontal, CFG)
    assert [a.codigo for a in alertas] == ["PROPORCIONES_DISTINTAS"]


def test_webp_baja_la_calidad_hasta_cumplir_el_peso():
    ruido = Image.fromarray(np.random.default_rng(0).integers(0, 256, (300, 300, 3), dtype=np.uint8))
    peso = {q: len(codificar(ruido, "webp", q)[0]) for q in (60, 90)}

    limite = (peso[60] + peso[90]) // 2
    datos, calidad = codificar(ruido, "webp", 90, 60, limite)
    assert len(datos) <= limite and 60 <= calidad < 90

    datos, calidad = codificar(ruido, "webp", 90, 60, peso[60] - 1)  # ni con la calidad mínima alcanza
    assert calidad == 60 and len(datos) == peso[60]


def test_identificar_producto_y_vista():
    assert identificar(Path("x/iPhone-12-Azul-Version-Final-Frontal.webp"), CFG) == ("iPhone-12-Azul", "Frontal")
    assert identificar(Path("iPhone-12-Azul/trasera.png"), CFG) == ("iPhone-12-Azul", "Trasera")
    assert identificar(Path("carpeta/foto 1.png"), CFG) == ("carpeta", "foto 1")


def test_jpg_con_fondo_transparente_es_error_de_config():
    with pytest.raises(ErrorConfig):
        Config(lienzo=Lienzo(fondo="transparente"), salida=Salida(formato="jpg"))


def test_config_parcial_y_claves_desconocidas(tmp_path):
    ruta = tmp_path / "config.toml"
    ruta.write_text("[lienzo]\nancho = 800\n", encoding="utf-8")
    cfg = cargar_config(ruta)
    assert cfg.lienzo.ancho == 800 and cfg.lienzo.alto == CFG.lienzo.alto

    ruta.write_text("[producto]\naltrua = 0.8\n", encoding="utf-8")
    with pytest.raises(ErrorConfig, match="altrua"):
        cargar_config(ruta)


def test_config_con_tipos_equivocados_da_mensaje_claro(tmp_path):
    ruta = tmp_path / "config.toml"
    ruta.write_text('[producto]\naltura = "0.9"\n', encoding="utf-8")
    with pytest.raises(ErrorConfig, match="producto.altura"):
        cargar_config(ruta)


def test_config_del_repo_es_valida():
    cargar_config(RAIZ / "config.toml")


P3 = Path("/System/Library/ColorSync/Profiles/Display P3.icc")


@pytest.mark.skipif(not P3.exists(), reason="requiere el perfil Display P3 de macOS")
def test_abrir_convierte_display_p3_a_srgb(tmp_path):
    ruta = tmp_path / "p3.png"
    Image.new("RGBA", (10, 10), (200, 100, 50, 128)).save(ruta, icc_profile=P3.read_bytes())
    im = abrir(ruta)
    r, g, b, a = im.getpixel((5, 5))
    assert a == 128  # la transparencia se conserva
    assert r > 200 and b < 50  # el mismo color en sRGB necesita valores más saturados
    assert "icc_profile" not in im.info


def test_cli_compone_la_portada_y_nombra_como_el_catalogo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # sin config.toml: defaults
    carpeta = tmp_path / "entrada" / "iPhone-Prueba-Azul"
    carpeta.mkdir(parents=True)
    telefono((1000, 1500), (200, 100, 700, 1300)).save(carpeta / "frontal.png")
    telefono((1000, 1500), (250, 150, 750, 1350)).save(carpeta / "trasera.png")
    telefono((600, 1500), (280, 100, 320, 1300)).save(carpeta / "lateral.png")

    assert main(["estandarizar", "entrada", "-s", "salida"]) == 0

    producto = tmp_path / "salida" / "iPhone-Prueba-Azul"
    for vista in ("Portada", "Lateral", "Frontal", "Trasera"):
        ruta = producto / f"iPhone-Prueba-Azul-Version-Final-{vista}.webp"
        assert ruta.stat().st_size <= CFG.salida.peso_max_kb * 1000
        with Image.open(ruta) as final:
            assert final.format == "WEBP" and final.size == (CFG.lienzo.ancho, CFG.lienzo.alto)
        assert (producto / "maestros" / f"{vista}.png").exists()
    reporte = json.loads((producto / "reporte.json").read_text(encoding="utf-8"))
    assert list(reporte) == ["Portada", "Lateral", "Frontal", "Trasera"]
    assert reporte["Portada"]["entrada"] == "compuesta: Trasera + Frontal"
    assert all(not r["alertas"] and not r["error"] for r in reporte.values())
