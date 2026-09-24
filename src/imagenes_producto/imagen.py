"""Lectura, escritura y búsqueda de imágenes."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageCms, ImageOps

EXTENSIONES = {".png", ".jpg", ".jpeg", ".webp"}
_SRGB = ImageCms.createProfile("sRGB")


def abrir(ruta: Path) -> Image.Image:
    """Abre una imagen como RGBA en sRGB, con la orientación EXIF ya aplicada."""
    with Image.open(ruta) as im:
        im = ImageOps.exif_transpose(im)
        im = _a_srgb(im).convert("RGBA")
    # El perfil original ya no describe los píxeles: si quedara, se volvería a incrustar al guardar.
    im.info.pop("icc_profile", None)
    return im


def _a_srgb(im: Image.Image) -> Image.Image:
    """Convierte a sRGB si la imagen trae otro perfil de color (p. ej. Display P3 de una foto de iPhone).

    Sin esto, los colores saturados se ven distintos en la web que en el original.
    """
    icc = im.info.get("icc_profile")
    if not icc:
        return im
    try:
        origen = ImageCms.ImageCmsProfile(io.BytesIO(icc))
    except (OSError, ImageCms.PyCMSError):
        return im  # perfil ilegible: se asume sRGB, como hace el navegador
    if im.mode not in ("RGB", "RGBA", "CMYK"):
        im = im.convert("RGBA" if im.has_transparency_data else "RGB")
    return ImageCms.profileToProfile(im, origen, _SRGB, outputMode="RGBA" if im.mode == "RGBA" else "RGB")


def codificar(
    im: Image.Image,
    formato: str = "png",
    calidad: int = 90,
    calidad_min: int | None = None,
    peso_max: int | None = None,
) -> tuple[bytes, int | None]:
    """Codifica la imagen y devuelve los bytes y la calidad usada (None en png).

    En jpg y webp, con `peso_max` (bytes) busca la mayor calidad entre `calidad_min` y `calidad` que no lo
    supere. Si ni con `calidad_min` alcanza, devuelve esa versión: quien llama decide si alertar.
    """
    if im.mode == "RGBA" and im.getextrema()[3][0] == 255:
        im = im.convert("RGB")  # totalmente opaca: sin canal alfa pesa menos
    if formato == "png":
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return buf.getvalue(), None
    if formato not in ("jpg", "webp"):
        raise ValueError(f"formato no soportado: {formato}")
    if not peso_max:
        return _con_calidad(im, formato, calidad), calidad

    minima = calidad_min or 1
    mejor = None
    bajo, alto = minima, calidad
    while bajo <= alto:  # búsqueda binaria: el peso crece con la calidad
        q = (bajo + alto) // 2
        datos = _con_calidad(im, formato, q)
        if len(datos) <= peso_max:
            mejor, bajo = (datos, q), q + 1
        else:
            alto = q - 1
    return mejor or (_con_calidad(im, formato, minima), minima)


def _con_calidad(im: Image.Image, formato: str, calidad: int) -> bytes:
    buf = io.BytesIO()
    if formato == "jpg":
        # subsampling=0 (4:4:4) evita que los bordes de color se vean lavados.
        im.convert("RGB").save(buf, "JPEG", quality=calidad, subsampling=0, optimize=True)
    else:
        im.save(buf, "WEBP", quality=calidad, method=6)
    return buf.getvalue()


def guardar(im: Image.Image, ruta: Path, formato: str = "png", **opciones) -> tuple[int, int | None]:
    """Guarda la imagen (ver `codificar`). Devuelve el peso en bytes y la calidad usada."""
    datos, calidad = codificar(im, formato, **opciones)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(datos)
    return len(datos), calidad


def buscar_imagenes(rutas: list[Path], excluir: Path | None = None) -> list[Path]:
    """Expande carpetas (recursivamente) a las imágenes que contienen, en orden.

    Ignora carpetas ocultas y todo lo que esté dentro de `excluir` (la carpeta de salida).
    """
    excluir = excluir.resolve() if excluir else None
    encontradas: list[Path] = []
    for ruta in rutas:
        candidatas = sorted(ruta.rglob("*")) if ruta.is_dir() else [ruta]
        for c in candidatas:
            if not c.is_file() or c.suffix.lower() not in EXTENSIONES:
                continue
            if ruta.is_dir() and any(p.startswith(".") for p in c.relative_to(ruta).parts):
                continue
            if excluir and c.resolve().is_relative_to(excluir):
                continue
            encontradas.append(c)
    return list(dict.fromkeys(encontradas))
