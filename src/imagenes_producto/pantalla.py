"""Pantalla de la Frontal: detectar la pantalla en color croma y reemplazarla por la apagada o un wallpaper.

Con "wallpaper" en `pantalla.variantes`, la IA genera la Frontal una sola vez, con la pantalla llena de
`pantalla.color_croma`. De esa imagen salen todas las variantes, así que comparten exactamente la misma
geometría. El notch, la perforación o la Dynamic Island no son color croma: quedan como los dibujó la IA.
Todo se hace sobre el recorte maestro (sin fondo), así que solo cambian los píxeles de la pantalla.
"""

from __future__ import annotations

import colorsys
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageColor
from scipy import ndimage

from . import imagen
from .config import Config

# Si la pantalla croma cubre menos que esta fracción del teléfono, se considera que no hay pantalla croma.
COBERTURA_MIN = 0.40
# Los huecos dentro de la pantalla más chicos que esta fracción de ella son ruido y se rellenan; los demás
# son elementos reales: la Dynamic Island es 1; la hora, los íconos o un texto suman más.
_HUECO_RUIDO = 0.001
# Rampas de saturación y brillo desde las que un píxel puede ser croma: el marco, el fondo y el negro no lo son.
_SATURACION = (0.25, 0.45)
_BRILLO = (0.12, 0.25)


@dataclass(frozen=True)
class Deteccion:
    alfa: np.ndarray  # 0-1: cuánto de cada píxel es pantalla
    tinte: np.ndarray  # 0-1: parecido de cada píxel al tono croma; sirve para limpiar el borde
    caja: tuple[int, int, int, int]  # rectángulo de la pantalla (x1 e y1 exclusivos)
    cobertura: float  # área de la pantalla / área del teléfono
    elementos: int  # huecos grandes dentro de la pantalla


def detectar(maestro: Image.Image, cfg: Config) -> Deteccion | None:
    """Busca la pantalla croma dentro del teléfono. None si la Frontal no la trae.

    Se mide por tono y no por distancia de color: así se tolera el degradado o la viñeta que agregue la IA.
    """
    rgba = np.asarray(maestro.convert("RGBA"))
    telefono = rgba[..., 3] >= cfg.recorte.umbral_alfa
    tono, saturacion, brillo = _hsv(rgba[..., :3].astype(np.float32) / 255)
    p = cfg.pantalla
    tono_croma = colorsys.rgb_to_hsv(*(v / 255 for v in ImageColor.getrgb(p.color_croma)[:3]))[0] * 360
    minimo, maximo = p.tolerancia_tono
    distancia = np.abs((tono - tono_croma + 180) % 360 - 180)
    tinte = np.clip((maximo - distancia) / (maximo - minimo), 0, 1) * _rampa(saturacion, *_SATURACION)
    alfa = tinte * _rampa(brillo, *_BRILLO) * ndimage.binary_erosion(telefono, iterations=1)

    etiquetas, n = ndimage.label(alfa > 0.5)
    if n == 0:
        return None
    pantalla = etiquetas == int(np.bincount(etiquetas.ravel())[1:].argmax()) + 1
    huecos, n_huecos = ndimage.label(ndimage.binary_fill_holes(pantalla) & ~pantalla)
    elementos = 0
    if n_huecos:
        areas = np.bincount(huecos.ravel())[1:]
        ruido = np.flatnonzero(areas < _HUECO_RUIDO * pantalla.sum()) + 1
        pantalla |= np.isin(huecos, ruido)
        elementos = n_huecos - len(ruido)
    cobertura = float(pantalla.sum() / max(1, telefono.sum()))
    if cobertura < COBERTURA_MIN:
        return None

    # Borde suave en el contorno de la pantalla; interior 100 % pantalla.
    alfa = np.where(ndimage.binary_dilation(pantalla, iterations=2), alfa, 0).astype(np.float32)
    alfa[ndimage.binary_erosion(pantalla, iterations=2)] = 1
    return Deteccion(alfa, tinte.astype(np.float32), _caja(pantalla), cobertura, int(elementos))


def apagada(maestro: Image.Image, deteccion: Deteccion, cfg: Config) -> Image.Image:
    """La pantalla en `color_apagada`, con un reflejo diagonal suave: más claro arriba a la izquierda."""
    p = cfg.pantalla
    relleno = np.empty((maestro.height, maestro.width, 3), np.float32)
    relleno[:] = ImageColor.getrgb(p.color_apagada)[:3]
    x0, y0, x1, y1 = deteccion.caja
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    t = (xx - x0) / max(1, x1 - x0) * 0.55 + (yy - y0) / max(1, y1 - y0) * 0.45
    reflejo = (p.reflejo_apagada * np.clip(1 - t / 0.7, 0, 1))[..., None]
    relleno[y0:y1, x0:x1] = relleno[y0:y1, x0:x1] * (1 - reflejo) + 255 * reflejo
    return _reemplazar(maestro, deteccion, relleno)


def con_wallpaper(maestro: Image.Image, deteccion: Deteccion, ruta: Path, cfg: Config) -> Image.Image:
    """La pantalla con el wallpaper, escalado para cubrirla sin deformarse. Los bordes redondeados y la
    Dynamic Island salen de la máscara de la pantalla."""
    x0, y0, x1, y1 = deteccion.caja
    ancho, alto = x1 - x0, y1 - y0
    wallpaper = imagen.abrir(ruta).convert("RGB")
    escala = max(ancho / wallpaper.width, alto / wallpaper.height)
    wallpaper = wallpaper.resize(
        (max(ancho, math.ceil(wallpaper.width * escala)), max(alto, math.ceil(wallpaper.height * escala))),
        Image.Resampling.LANCZOS,
    )
    dx = (wallpaper.width - ancho) // 2
    dy = 0 if cfg.pantalla.ancla_wallpaper == "arriba" else (wallpaper.height - alto) // 2
    relleno = np.zeros((maestro.height, maestro.width, 3), np.float32)
    relleno[y0:y1, x0:x1] = np.asarray(wallpaper.crop((dx, dy, dx + ancho, dy + alto)), np.float32)
    return _reemplazar(maestro, deteccion, relleno)


def buscar_wallpaper(producto: str, cfg: Config) -> Path | None:
    """El wallpaper del producto en `pantalla.wallpapers`: `<producto>.*` (iPhone-15-Pro-Azul), si no el de
    su modelo, que es el producto sin el color (iPhone-15-Pro), y si no `_default.*`. Si `pantalla.wallpapers`
    es un archivo, sirve para todos. None si no hay."""
    ubicacion = Path(cfg.pantalla.wallpapers)
    if ubicacion.is_file():
        return ubicacion
    for nombre in (producto, producto.rpartition("-")[0], "_default"):
        for extension in sorted(imagen.EXTENSIONES) if nombre else ():
            if (ubicacion / f"{nombre}{extension}").is_file():
                return ubicacion / f"{nombre}{extension}"
    return None


def _reemplazar(maestro: Image.Image, deteccion: Deteccion, relleno: np.ndarray) -> Image.Image:
    """Pinta `relleno` (alto × ancho × 3, en 0-255) donde está la pantalla, sin tocar el alfa.

    En el borde de la pantalla (contra el marco y la Dynamic Island) quita el tinte croma que se cuela, para
    que no quede un halo verde.
    """
    rgba = np.asarray(maestro.convert("RGBA")).astype(np.float32)
    rgb = rgba[..., :3]
    borde = (ndimage.binary_dilation(deteccion.alfa > 0, iterations=2) & (deteccion.alfa < 1))[..., None]
    luminancia = (rgb @ np.array([0.299, 0.587, 0.114], np.float32))[..., None]
    rgb = np.where(borde, luminancia + (rgb - luminancia) * (1 - deteccion.tinte[..., None]), rgb)
    a = deteccion.alfa[..., None]
    rgb = a * relleno + (1 - a) * rgb
    return Image.fromarray(np.dstack([np.clip(np.round(rgb), 0, 255), rgba[..., 3]]).astype(np.uint8))


def _hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """RGB en 0-1 (alto × ancho × 3) → tono en grados, saturación y brillo."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    brillo = rgb.max(axis=2)
    croma = brillo - rgb.min(axis=2)
    saturacion = np.where(brillo > 0, croma / np.maximum(brillo, 1e-6), 0.0)
    c = np.maximum(croma, 1e-6)
    tono = np.where(brillo == r, ((g - b) / c) % 6, np.where(brillo == g, (b - r) / c + 2, (r - g) / c + 4)) * 60
    return np.where(croma > 1e-6, tono, 0.0), saturacion, brillo


def _rampa(valor: np.ndarray, desde: float, hasta: float) -> np.ndarray:
    return np.clip((valor - desde) / (hasta - desde), 0, 1)


def _caja(mascara: np.ndarray) -> tuple[int, int, int, int]:
    filas, columnas = np.flatnonzero(mascara.any(axis=1)), np.flatnonzero(mascara.any(axis=0))
    return int(columnas[0]), int(filas[0]), int(columnas[-1]) + 1, int(filas[-1]) + 1
