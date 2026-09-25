"""Etapa 1 — generar: pedirle a un modelo de imágenes cada vista del equipo a partir de su foto original.

Una llamada por vista, siempre con el mismo prompt por vista y la misma referencia de estilo. La Portada no se
genera: la compone la etapa 2 con la Trasera y la Frontal. Cada imagen generada queda junto a un .json con el
modelo, el prompt, los tokens usados y el costo, para poder auditar y reproducir el resultado.
"""

from __future__ import annotations

import base64
import io
import json
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import numpy as np
from PIL import Image

from . import imagen
from .config import Config
from .estandarizar import Alerta, caja

# Costo aproximado de una imagen generada de 1024×1536, en US$. OpenAI no publica el precio por imagen de
# gpt-image-2.5 (cobra por tokens): son estimaciones con la fórmula de gpt-image-2, al 2026-09-24. Solo sirven
# para avisar antes de gastar; el costo real se calcula con los tokens que informa cada respuesta.
_ESTIMADO_POR_IMAGEN = {"low": 0.005, "medium": 0.010, "high": 0.041, "xhigh": 0.074, "max": 0.165, "auto": 0.041}
# Entrada de cada llamada (original, referencia y prompt), también aproximada.
_ESTIMADO_ENTRADA = 0.03
# Lado mayor con que se envía el original: más resolución no le da más detalle a la IA y hace lenta la subida.
_LADO_MAX_ENVIO = 2048
# Diferencia de color con el fondo desde la que un píxel del original cuenta como contenido, al recortar márgenes.
_UMBRAL_CONTENIDO = 16


class ErrorGeneracion(RuntimeError):
    """Falló un pedido; el lote sigue con el siguiente."""


class ErrorFatal(ErrorGeneracion):
    """Falla que afecta a todos los pedidos (p. ej. API key inválida): el lote se detiene."""


class Cliente(Protocol):
    def generar(self, prompt: str, imagenes: list[tuple[str, bytes]], cfg: Config) -> tuple[list[bytes], dict]:
        """Devuelve las imágenes generadas (PNG) y el uso de tokens informado por el proveedor."""


def crear_cliente(cfg: Config) -> Cliente:
    return ClienteOpenAI()


class ClienteOpenAI:
    """Images API de OpenAI: `images.edit`, que acepta varias imágenes de entrada."""

    def __init__(self) -> None:
        from openai import OpenAI

        self._openai = OpenAI(max_retries=5)  # reintenta solo los límites de uso y los errores temporales

    def generar(self, prompt: str, imagenes: list[tuple[str, bytes]], cfg: Config) -> tuple[list[bytes], dict]:
        import openai

        g = cfg.generar
        try:
            respuesta = self._openai.images.edit(
                model=g.modelo,
                image=[(nombre, datos, "image/png") for nombre, datos in imagenes],
                prompt=prompt,
                size=g.medidas,
                quality=g.calidad,
                background="transparent" if g.fondo == "transparente" else "opaque",
                output_format="png",
                n=g.candidatos,
            )
        except openai.AuthenticationError as e:
            raise ErrorFatal("OpenAI rechazó la API key: revisa OPENAI_API_KEY en .env") from e
        except openai.PermissionDeniedError as e:
            raise ErrorFatal(
                f"OpenAI negó el acceso: {_mensaje(e)}. Si pide verificar la organización, se hace en "
                "platform.openai.com → Settings → Organization → General"
            ) from e
        except openai.APIStatusError as e:
            raise ErrorGeneracion(f"OpenAI rechazó el pedido: {_mensaje(e)}") from e
        except openai.APIError as e:
            raise ErrorGeneracion(f"error al llamar a OpenAI: {_mensaje(e)}") from e
        salidas = [base64.b64decode(d.b64_json) for d in respuesta.data or [] if d.b64_json]
        return salidas, (respuesta.usage.model_dump() if respuesta.usage else {})


def _mensaje(e: Exception) -> str:
    return getattr(e, "message", None) or str(e)


def producto_desde_original(ruta: Path) -> tuple[str, str, str]:
    """'iPhone 12 Pro Plata.webp' → ('iPhone-12-Pro-Plata', 'iPhone 12 Pro', 'Plata').

    El color es la última palabra del nombre y el resto es el modelo. Acepta espacios o guiones.
    """
    nombre = " ".join(ruta.stem.replace("-", " ").split())
    modelo, _, color = nombre.rpartition(" ")
    if not modelo:
        raise ErrorGeneracion(f'el nombre "{ruta.name}" debe ser "<Modelo> <Color>", p. ej. "iPhone 12 Azul.webp"')
    return nombre.replace(" ", "-"), modelo, color


def referencia_de(vista: str, cfg: Config) -> Path:
    """La imagen de estilo de la vista: el único archivo de `generar.referencias` que termina en -<Vista>."""
    carpeta = Path(cfg.generar.referencias)
    candidatas = sorted(p for p in carpeta.glob(f"*-{vista}.*") if p.suffix.lower() in imagen.EXTENSIONES)
    if len(candidatas) != 1:
        raise ErrorFatal(f"se esperaba una imagen de referencia *-{vista} en {carpeta}/ y hay {len(candidatas)}")
    return candidatas[0]


def cargar_rasgos(cfg: Config) -> dict[str, list[str]]:
    """Rasgos clave por modelo (`generar.rasgos`). Sin archivo, no hay rasgos."""
    ruta = Path(cfg.generar.rasgos)
    if not ruta.exists():
        return {}
    with open(ruta, "rb") as f:
        datos = tomllib.load(f)
    return {modelo: [str(r) for r in valores.get("rasgos", [])] for modelo, valores in datos.items()}


def pantalla_croma(cfg: Config) -> bool:
    """La Frontal se pide con la pantalla en color croma cuando hay que armar la variante con wallpaper."""
    return "wallpaper" in cfg.pantalla.variantes


def armar_prompt(vista: str, modelo: str, color: str, cfg: Config, rasgos: list[str] | None = None) -> str:
    """Prompt de la vista: `comun.md` más el archivo de la vista, con las variables reemplazadas.

    `{pantalla}` (en `frontal.md`) se reemplaza por `pantalla-croma.md` o `pantalla-apagada.md` según
    `pantalla.variantes`.
    """
    carpeta = Path(cfg.generar.prompts)
    plantilla = (
        (carpeta / "comun.md").read_text(encoding="utf-8").rstrip()
        + "\n\n"
        + (carpeta / f"{vista.lower()}.md").read_text(encoding="utf-8").strip()
        + "\n"
    )
    bloque = ""
    if rasgos:
        bloque = "\nRASGOS CLAVE DE ESTE MODELO (confirmados: respétalos aunque no se vean en la imagen 1)\n"
        bloque += "\n".join(f"- {r}" for r in rasgos) + "\n"
    fondo = "transparente" if cfg.generar.fondo == "transparente" else "blanco liso (#FFFFFF), sin degradados"
    texto_pantalla = ""
    if "{pantalla}" in plantilla:
        archivo = "pantalla-croma.md" if pantalla_croma(cfg) else "pantalla-apagada.md"
        texto_pantalla = (carpeta / archivo).read_text(encoding="utf-8").strip().format(
            color_croma=cfg.pantalla.color_croma)
    return plantilla.format(vista=vista.lower(), modelo=modelo, color=color, fondo=fondo, rasgos=bloque,
                            pantalla=texto_pantalla)


def preparar_original(ruta: Path, cfg: Config) -> tuple[bytes, list[Alerta]]:
    """El original listo para enviar: en sRGB, sin márgenes de fondo y en PNG.

    Recortar los márgenes hace que la IA vea el equipo con el mayor detalle posible. Alerta si aun así el
    equipo ocupa pocos píxeles.
    """
    im = imagen.abrir(ruta)
    rgb = Image.alpha_composite(Image.new("RGBA", im.size, (255, 255, 255, 255)), im).convert("RGB")
    px = np.asarray(rgb).astype(np.int16)
    borde = np.concatenate([px[0], px[-1], px[:, 0], px[:, -1]])
    contenido = caja(np.abs(px - np.median(borde, axis=0)).max(axis=2) > _UMBRAL_CONTENIDO)
    if contenido:
        x0, y0, x1, y1 = contenido
        m = round(0.03 * max(x1 - x0, y1 - y0))
        rgb = rgb.crop((max(0, x0 - m), max(0, y0 - m), min(rgb.width, x1 + m), min(rgb.height, y1 + m)))

    alertas = []
    if max(rgb.size) < cfg.generar.original_min:
        alertas.append(Alerta(
            "ORIGINAL_CHICO",
            f"en el original el equipo ocupa {rgb.width}×{rgb.height} px (se recomienda al menos "
            f"{cfg.generar.original_min} px de lado mayor): la IA tiene poco detalle para ser fiel",
        ))
    if max(rgb.size) > _LADO_MAX_ENVIO:
        rgb.thumbnail((_LADO_MAX_ENVIO, _LADO_MAX_ENVIO), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    rgb.save(buf, "PNG")
    return buf.getvalue(), alertas


def _png(ruta: Path) -> bytes:
    buf = io.BytesIO()
    imagen.abrir(ruta).save(buf, "PNG")
    return buf.getvalue()


def costo(uso: dict, cfg: Config) -> float:
    """Costo en US$ según los tokens informados. Sin desglose de la entrada, se cobra toda como imagen (lo más
    caro); la salida se cobra toda al precio de imagen."""
    g = cfg.generar
    detalle = uso.get("input_tokens_details") or {}
    if detalle:
        texto, imagenes = detalle.get("text_tokens") or 0, detalle.get("image_tokens") or 0
    else:
        texto, imagenes = 0, uso.get("input_tokens") or 0
    salida = uso.get("output_tokens") or 0
    return (texto * g.precio_texto_entrada + imagenes * g.precio_imagen_entrada
            + salida * g.precio_imagen_salida) / 1_000_000


def estimar(originales: int, cfg: Config) -> tuple[int, float]:
    """Cantidad de imágenes a generar y costo aproximado en US$ (ver `_ESTIMADO_POR_IMAGEN`)."""
    g = cfg.generar
    llamadas = originales * len(g.vistas)
    imagenes = llamadas * g.candidatos
    return imagenes, imagenes * _ESTIMADO_POR_IMAGEN[g.calidad] + llamadas * _ESTIMADO_ENTRADA


@dataclass
class Generada:
    """Resultado de un pedido (una vista de un original)."""

    producto: str
    vista: str
    original: str
    archivos: list[str] = field(default_factory=list)
    costo_usd: float = 0.0
    segundos: float = 0.0
    alertas: list[Alerta] = field(default_factory=list)
    error: str | None = None


def generar_lote(
    originales: list[Path],
    carpeta: Path,
    cfg: Config,
    cliente: Cliente,
    avance: Callable[[Generada], None] | None = None,
) -> list[Generada]:
    """Genera las vistas de cada original en `carpeta/<producto>/<Vista>.png` (las candidatas extra, como
    `<Vista>-2.png`), cada una con su `<Vista>.json`. Un pedido fallido queda registrado y el lote sigue,
    salvo con `ErrorFatal`."""
    rutas_referencia = {v: referencia_de(v, cfg) for v in cfg.generar.vistas}  # falla antes de gastar nada
    frontal = Path(cfg.generar.prompts) / "frontal.md"
    if "Frontal" in cfg.generar.vistas and pantalla_croma(cfg) and "{pantalla}" not in frontal.read_text(encoding="utf-8"):
        raise ErrorFatal(f'{frontal} no tiene {{pantalla}}: sin eso la IA no recibe el pedido de pantalla croma '
                         'y no se puede armar la Frontal-Wallpaper')
    referencias = {v: _png(ruta) for v, ruta in rutas_referencia.items()}
    rasgos = cargar_rasgos(cfg)
    resultados: list[Generada] = []

    def registrar(r: Generada) -> None:
        resultados.append(r)
        if avance:
            avance(r)

    for original in originales:
        try:
            producto, modelo, color = producto_desde_original(original)
            datos, alertas_original = preparar_original(original, cfg)
        except (OSError, ErrorGeneracion) as e:
            registrar(Generada(producto=original.stem, vista="—", original=str(original), error=str(e)))
            continue

        for i, vista in enumerate(cfg.generar.vistas):
            # Las alertas del original van una sola vez, en su primera vista.
            r = Generada(producto, vista, str(original), alertas=list(alertas_original) if i == 0 else [])
            prompt = armar_prompt(vista, modelo, color, cfg, rasgos.get(modelo))
            inicio = time.monotonic()
            try:
                salidas, uso = cliente.generar(prompt, [("original.png", datos), ("referencia.png", referencias[vista])], cfg)
                if not salidas:
                    raise ErrorGeneracion("la respuesta no trajo imágenes")
            except ErrorFatal:
                raise
            except ErrorGeneracion as e:
                r.error, r.segundos = str(e), round(time.monotonic() - inicio, 1)
                registrar(r)
                continue
            r.segundos = round(time.monotonic() - inicio, 1)
            r.costo_usd = round(costo(uso, cfg), 4)

            destino = carpeta / producto
            destino.mkdir(parents=True, exist_ok=True)
            for n, png in enumerate(salidas, start=1):
                archivo = destino / (f"{vista}.png" if n == 1 else f"{vista}-{n}.png")
                archivo.write_bytes(png)
                r.archivos.append(str(archivo))
            g = cfg.generar
            metadatos = {
                "producto": producto, "modelo_equipo": modelo, "color": color, "vista": vista,
                "original": str(original), "referencia": str(rutas_referencia[vista]),
                "proveedor": g.proveedor, "modelo": g.modelo, "calidad": g.calidad, "medidas": g.medidas,
                "fondo": g.fondo, "candidatos": g.candidatos, "archivos": r.archivos,
                "uso": uso, "costo_usd": r.costo_usd, "segundos": r.segundos,
                "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"), "prompt": prompt,
            }
            (destino / f"{vista}.json").write_text(json.dumps(metadatos, ensure_ascii=False, indent=2) + "\n",
                                                   encoding="utf-8")
            registrar(r)
    return resultados
