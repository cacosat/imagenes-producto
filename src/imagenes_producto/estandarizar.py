"""Etapa 2 — estandarizar: recortar el producto, escalarlo a una altura fija y centrarlo en un lienzo fijo.

Es determinista: la misma imagen con la misma configuración da siempre el mismo resultado.
Toda la geometría se mide sobre el canal alfa (el recorte del producto), nunca sobre el fondo.
La Portada no se genera: se compone con los recortes maestros de la Trasera y la Frontal. La Frontal da una
salida por cada variante de pantalla pedida (apagada y con wallpaper), todas con la misma geometría.
"""

from __future__ import annotations

import functools
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

from . import imagen, pantalla
from .config import VISTA_DE_VARIANTE, VISTAS, Config
from .fondo import ErrorFondo, Removedor

Caja = tuple[int, int, int, int]  # x0, y0, x1, y1 (x1 e y1 exclusivos)

ENTRADA_PORTADA = "compuesta: Trasera + Frontal"
MUESTRAS_PORTADA = "muestras-portada.jpg"

# Píxeles con opacidad hasta este valor que no estén pegados al producto se tratan como ruido del recorte.
_ALFA_RUIDO = 16
# Radio (px) alrededor del producto en el que se conserva el borde suave, aunque sea más tenue que _ALFA_RUIDO.
_RADIO_BORDE = 2
# Ancho (px) de la franja del contorno que conserva la transparencia suave al rellenar el interior.
_FRANJA_BORDE = 3
# Si los huecos rellenados superan esta fracción del producto, se alerta: la segmentación dudó.
_HUECOS_MAX = 0.001
# Ampliar menos de un 1 % no se nota: no se alerta.
_TOLERANCIA_AMPLIACION = 0.01


class ErrorEstandarizacion(ValueError):
    pass


@dataclass
class Alerta:
    codigo: str
    mensaje: str


@dataclass
class Estandarizada:
    final: Image.Image  # producto centrado en el lienzo
    maestro: Image.Image  # recorte transparente, sin escalar
    caja_producto: Caja  # contorno del producto en la imagen de entrada
    escala: float
    alertas: list[Alerta]


def caja(mascara: np.ndarray) -> Caja | None:
    """Rectángulo mínimo que contiene los píxeles verdaderos de la máscara."""
    filas = np.flatnonzero(mascara.any(axis=1))
    if filas.size == 0:
        return None
    columnas = np.flatnonzero(mascara.any(axis=0))
    return int(columnas[0]), int(filas[0]), int(columnas[-1]) + 1, int(filas[-1]) + 1


def _contorno(maestro: Image.Image, cfg: Config) -> Caja:
    contorno = caja(np.asarray(maestro.getchannel("A")) >= cfg.recorte.umbral_alfa)
    if contorno is None:
        raise ErrorEstandarizacion("el recorte maestro está vacío")
    return contorno


def limpiar_alfa(alfa: np.ndarray, umbral: int, objeto_min: float) -> tuple[np.ndarray, int]:
    """Borra las manchas sueltas del recorte. Devuelve el alfa limpio y cuántos objetos quedan.

    Un objeto se conserva si su área opaca (alfa >= umbral) es al menos `objeto_min` veces
    la del objeto más grande.
    """
    etiquetas, n = ndimage.label(alfa > _ALFA_RUIDO, structure=np.ones((3, 3), dtype=bool))
    if n == 0:
        return np.zeros_like(alfa), 0
    area = ndimage.sum_labels(alfa >= umbral, etiquetas, index=np.arange(1, n + 1))
    if area.max() == 0:
        return np.zeros_like(alfa), 0
    conservar = area >= objeto_min * area.max()
    mascara = np.concatenate(([False], conservar))[etiquetas]
    mascara = ndimage.binary_dilation(mascara, iterations=_RADIO_BORDE)
    return np.where(mascara, alfa, 0).astype(np.uint8), int(conservar.sum())


def rellenar_interior(rgba: np.ndarray, umbral: int, original: np.ndarray | None) -> float:
    """Deja el cuerpo del producto 100 % opaco; solo la franja del contorno conserva la transparencia suave.

    La segmentación a veces deja el interior levemente transparente (el color se aclara sobre fondo blanco) o
    con huecos (típico de un teléfono blanco sobre fondo blanco). Con la imagen `original` (RGB, antes de
    quitar el fondo) se rellenan también los huecos, con su color real. Modifica `rgba` y devuelve la
    fracción del producto que eran huecos.
    """
    nucleo = rgba[..., 3] >= umbral
    relleno = ndimage.binary_fill_holes(nucleo) if original is not None else nucleo
    interior = ndimage.binary_erosion(relleno, iterations=_FRANJA_BORDE)
    if original is not None:
        rgba[interior, :3] = original[interior]
    rgba[interior, 3] = 255
    return float((relleno & ~nucleo).sum() / max(1, nucleo.sum()))


def extraer_maestro(
    im: Image.Image, cfg: Config, original: Image.Image | None = None
) -> tuple[Image.Image, Caja, int, float]:
    """Recorte transparente del producto, su contorno en la imagen de entrada, cuántos objetos hay y qué
    fracción del producto eran huecos rellenados (ver `rellenar_interior`)."""
    rgba = np.array(im.convert("RGBA"))
    rgba[..., 3], objetos = limpiar_alfa(rgba[..., 3], cfg.recorte.umbral_alfa, cfg.recorte.objeto_min)
    contorno = caja(rgba[..., 3] >= cfg.recorte.umbral_alfa)
    if contorno is None:
        raise ErrorEstandarizacion("no se encontró el producto: el recorte quedó vacío")
    huecos = 0.0
    if cfg.recorte.rellenar_interior:
        rgb_original = np.asarray(original.convert("RGB")) if original is not None else None
        huecos = rellenar_interior(rgba, cfg.recorte.umbral_alfa, rgb_original)
    rgba[rgba[..., 3] == 0, :3] = 0
    return Image.fromarray(rgba).crop(caja(rgba[..., 3] > 0)), contorno, objetos, huecos


def _escalar(maestro: Image.Image, escala: float) -> tuple[Image.Image, float, float]:
    """Recorte escalado y los factores reales por eje (difieren levemente de `escala` por el redondeo)."""
    medidas = (max(1, round(maestro.width * escala)), max(1, round(maestro.height * escala)))
    escalado = maestro.resize(medidas, Image.Resampling.LANCZOS)  # Pillow premultiplica el alfa: sin halos
    return escalado, medidas[0] / maestro.width, medidas[1] / maestro.height


def componer(maestro: Image.Image, cfg: Config) -> tuple[Image.Image, float, list[Alerta]]:
    """Escala el recorte para que el producto mida `producto.altura` del lienzo y lo centra."""
    ancho_lienzo, alto_lienzo = cfg.lienzo.ancho, cfg.lienzo.alto
    x0, y0, x1, y1 = _contorno(maestro, cfg)
    ancho, alto = x1 - x0, y1 - y0
    alertas = []

    escala = cfg.producto.altura * alto_lienzo / alto
    if ancho * escala > cfg.producto.ancho_max * ancho_lienzo:
        escala = cfg.producto.ancho_max * ancho_lienzo / ancho
        alertas.append(Alerta(
            "LIMITADO_POR_ANCHO",
            f"el producto es muy ancho para el lienzo: queda al {alto * escala / alto_lienzo:.0%} "
            f"del alto en vez del {cfg.producto.altura:.0%}",
        ))
    if escala > cfg.alertas.ampliacion_max * (1 + _TOLERANCIA_AMPLIACION):
        alertas.append(Alerta(
            "AMPLIACION",
            f"el producto mide {alto} px de alto y hay que ampliarlo ×{escala:.2f} "
            f"(a {round(alto * escala)} px): puede verse borroso",
        ))

    escalado, sx, sy = _escalar(maestro, escala)
    # Centro del contorno del producto, en coordenadas del recorte ya escalado.
    pos = (round(ancho_lienzo / 2 - (x0 + x1) / 2 * sx), round(alto_lienzo / 2 - (y0 + y1) / 2 * sy))
    if min(pos) < 0 or pos[0] + escalado.width > ancho_lienzo or pos[1] + escalado.height > alto_lienzo:
        alertas.append(Alerta("DESBORDA_LIENZO", "parte del recorte (halo o sombra) queda fuera del lienzo"))

    capa = Image.new("RGBA", (ancho_lienzo, alto_lienzo), (0, 0, 0, 0))
    capa.paste(escalado, pos)
    final = Image.new("RGBA", capa.size, cfg.lienzo.rgba_fondo)
    if cfg.sombra.activa:
        contorno = (pos[0] + x0 * sx, pos[1] + y0 * sy, pos[0] + x1 * sx, pos[1] + y1 * sy)
        sombra = np.zeros((alto_lienzo, ancho_lienzo, 4), np.uint8)
        sombra[..., 3] = np.round(_sombra(np.asarray(capa.getchannel("A")), contorno, cfg) * 255)
        final = Image.alpha_composite(final, Image.fromarray(sombra))
    return Image.alpha_composite(final, capa), escala, alertas


def _sombra(alfa: np.ndarray, contorno: tuple[float, float, float, float], cfg: Config) -> np.ndarray:
    """Opacidad (0-1) de la sombra de contacto: la silueta del producto aplastada contra su borde inferior
    y desenfocada.

    Como sale de la silueta, es la misma en todas las vistas y se adapta al ancho de cada una: bajo la
    Lateral, que es angosta, queda más tenue, como en las referencias.
    """
    s = cfg.sombra
    alto_lienzo, ancho_lienzo = alfa.shape
    x0, y0, x1, y1 = (int(round(v)) for v in contorno)
    alto_producto = y1 - y0
    # La silueta, limitada a lo que cae dentro del lienzo.
    x0, x1, y0, y1 = max(0, x0), min(ancho_lienzo, x1), max(0, y0), min(alto_lienzo, y1)
    fuente = np.zeros(alfa.shape, np.float32)
    if x1 <= x0 or y1 <= y0:
        return fuente
    base = max(1, round(s.base * alto_producto))
    barra = np.asarray(Image.fromarray(alfa[y0:y1, x0:x1]).resize((x1 - x0, base), Image.Resampling.BOX))

    arriba = y1 + round(s.desplazamiento * alto_producto) - base
    filas = slice(max(0, arriba), min(alto_lienzo, arriba + base))
    fuente[filas, x0:x1] = barra[filas.start - arriba: filas.stop - arriba] / 255
    difusa = ndimage.gaussian_filter(fuente, (s.desenfoque_y * alto_producto, s.desenfoque_x * alto_producto))
    return np.clip(difusa * s.opacidad, 0, 1)


def estandarizar(im: Image.Image, cfg: Config, original: Image.Image | None = None) -> Estandarizada:
    """Imagen RGBA ya sin fondo → producto centrado en el lienzo, recorte maestro y alertas.

    `original` es la imagen antes de quitarle el fondo (solo si se segmentó): da el color real del interior.
    """
    maestro, contorno, objetos, huecos = extraer_maestro(im, cfg, original)
    alertas = []
    if objetos > 1:
        alertas.append(Alerta("VARIOS_OBJETOS", f"hay {objetos} objetos separados en la imagen; se esperaba 1"))
    if huecos > _HUECOS_MAX:
        alertas.append(Alerta(
            "HUECOS_RELLENADOS",
            f"el recorte tenía huecos dentro del producto ({huecos:.1%} de su área) y se rellenaron con la "
            "imagen original: revisar que el recorte sea correcto",
        ))
    m = cfg.alertas.margen_borde
    x0, y0, x1, y1 = contorno
    if x0 <= m or y0 <= m or x1 >= im.width - m or y1 >= im.height - m:
        alertas.append(Alerta("TOCA_BORDE", "el producto toca el borde de la imagen: puede estar cortado"))
    final, escala, alertas_lienzo = componer(maestro, cfg)
    return Estandarizada(final, maestro, contorno, escala, alertas + alertas_lienzo)


def componer_portada(trasera: Image.Image, frontal: Image.Image, cfg: Config) -> tuple[Image.Image, list[Alerta]]:
    """Recorte del par de la Portada, con la trasera y la frontal del mismo alto.

    `[portada]` decide la ubicación: de qué lado va la trasera (`lado_trasera`), cuál queda encima
    (`adelante`), qué fracción del ancho del teléfono de adelante tapa al de atrás (`solape`) y cuánto más
    abajo queda la frontal (`desfase_vertical`, fracción del alto). Por defecto: la trasera a la izquierda y
    detrás, y la frontal le tapa 1/6 de su propio ancho (con las dos del mismo ancho, de la trasera se ve 5/6,
    como en la referencia). El par se lleva después al lienzo con `componer`, como cualquier otra vista.
    """
    p = cfg.portada
    cajas = [_contorno(m, cfg) for m in (trasera, frontal)]
    alto = min(y1 - y0 for _, y0, _, y1 in cajas)  # se reduce la más alta: nunca se amplía
    capas = []
    for maestro, (x0, y0, x1, y1) in zip((trasera, frontal), cajas):
        escalado, sx, sy = _escalar(maestro, alto / (y1 - y0))
        capas.append((escalado, x0 * sx, y0 * sy, (x1 - x0) * sx))
    (t, tx0, ty0, ancho_trasera), (f, fx0, fy0, ancho_frontal) = capas

    # Posición de cada contorno: el de un lado en x = 0; el del otro, donde el primero deja de verse.
    solape = p.solape * (ancho_frontal if p.adelante == "frontal" else ancho_trasera)
    if p.lado_trasera == "izquierda":
        xt, xf = 0.0, ancho_trasera - solape
    else:
        xt, xf = ancho_frontal - solape, 0.0
    yt, yf = 0.0, p.desfase_vertical * alto
    posiciones = [(round(xt - tx0), round(yt - ty0)), (round(xf - fx0), round(yf - fy0))]
    x_min, y_min = min(x for x, _ in posiciones), min(y for _, y in posiciones)
    x_max = max(x + im.width for (x, _), im in zip(posiciones, (t, f)))
    y_max = max(y + im.height for (_, y), im in zip(posiciones, (t, f)))
    par = Image.new("RGBA", (x_max - x_min, y_max - y_min), (0, 0, 0, 0))
    orden = (0, 1) if p.adelante == "frontal" else (1, 0)  # primero el de atrás
    for i in orden:
        (x, y), im = posiciones[i], (t, f)[i]
        par.alpha_composite(im, (x - x_min, y - y_min))

    alertas = []
    proporcion_t, proporcion_f = ((x1 - x0) / (y1 - y0) for x0, y0, x1, y1 in cajas)
    diferencia = abs(proporcion_t - proporcion_f) / ((proporcion_t + proporcion_f) / 2)
    if diferencia > cfg.alertas.diferencia_proporcion:
        alertas.append(Alerta(
            "PROPORCIONES_DISTINTAS",
            f"la frontal y la trasera no tienen la misma proporción ancho/alto ({proporcion_f:.3f} y "
            f"{proporcion_t:.3f}, {diferencia:.1%} de diferencia): una de las dos puede estar deformada",
        ))
    return par, alertas


@dataclass
class Variante:
    """Una variante de la Frontal: su vista, la imagen (None si no se pudo armar), el wallpaper usado y el error."""

    vista: str
    estandarizada: Estandarizada | None
    wallpaper: str | None = None
    error: str | None = None


def variantes_frontal(e: Estandarizada, producto: str, cfg: Config) -> list[Variante]:
    """Las variantes de la Frontal que pide `pantalla.variantes`, en ese orden.

    Si la Frontal trae la pantalla en color croma, cada variante reemplaza solo los píxeles de la pantalla del
    recorte maestro: todas quedan con la misma geometría y las mismas alertas de recorte. Si no la trae (p. ej.
    se generó apagada), la variante apagada es la Frontal tal cual y la de wallpaper no se puede armar.
    """
    pedidas = cfg.pantalla.variantes
    deteccion = pantalla.detectar(e.maestro, cfg)
    if deteccion is None:
        variantes = []
        for v in pedidas:
            if v == "apagada":
                alertas = list(e.alertas)
                if "wallpaper" in pedidas:
                    alertas.append(Alerta(
                        "SIN_PANTALLA_CROMA",
                        f"la frontal no trae la pantalla en color croma ({cfg.pantalla.color_croma}): se usa tal "
                        "cual como Frontal (revisar que esté apagada) y no se puede armar la Frontal-Wallpaper",
                    ))
                variantes.append(Variante("Frontal", replace(e, alertas=alertas)))
            else:
                variantes.append(Variante("Frontal-Wallpaper", None, error=(
                    f"la frontal no trae la pantalla en color croma ({cfg.pantalla.color_croma}): regenerarla; "
                    'el prompt la pide así cuando pantalla.variantes incluye "wallpaper"'
                )))
        return variantes

    alertas = list(e.alertas)
    if deteccion.elementos > 1:
        alertas.append(Alerta(
            "PANTALLA_CON_ELEMENTOS",
            f"la pantalla croma tiene {deteccion.elementos} elementos (la Dynamic Island cuenta 1): la IA "
            "dibujó hora, íconos o texto; regenerar la frontal",
        ))
    variantes = []
    for v in pedidas:
        wallpaper = None
        if v == "apagada":
            maestro = pantalla.apagada(e.maestro, deteccion, cfg)
        else:
            wallpaper = pantalla.buscar_wallpaper(producto, cfg)
            if wallpaper is None:
                variantes.append(Variante(VISTA_DE_VARIANTE[v], None, error=(
                    f"no hay wallpaper para {producto} en {cfg.pantalla.wallpapers}/ "
                    f"(se busca {producto}.*, {producto.rpartition('-')[0]}.* y _default.*)"
                )))
                continue
            maestro = pantalla.con_wallpaper(e.maestro, deteccion, wallpaper, cfg)
        final, _, _ = componer(maestro, cfg)  # misma geometría que la Frontal: mismas alertas de lienzo
        variantes.append(Variante(
            VISTA_DE_VARIANTE[v],
            Estandarizada(final, maestro, e.caja_producto, e.escala, list(alertas)),
            wallpaper=str(wallpaper) if wallpaper else None,
        ))
    return variantes


def identificar(archivo: Path, cfg: Config) -> tuple[str, str]:
    """Producto y vista de una imagen de entrada.

    Acepta el mismo formato de nombre de la salida (p. ej. `iPhone-12-Azul-Version-Final-Frontal.webp`)
    o la estructura `<producto>/<vista>.<ext>` (p. ej. `iPhone-12-Azul/frontal.png`).
    """
    coincidencia = _patron_nombre(cfg.salida.nombre).fullmatch(archivo.stem)
    if coincidencia:
        return coincidencia["producto"], _vista_canonica(coincidencia["vista"])
    return archivo.resolve().parent.name, _vista_canonica(archivo.stem)


_VISTA_CANONICA = {v.lower(): v for v in VISTAS}


def _vista_canonica(nombre: str) -> str:
    """'frontal' → 'Frontal', 'frontal-wallpaper' → 'Frontal-Wallpaper'. Un nombre que no es una vista
    conocida queda igual."""
    return _VISTA_CANONICA.get(nombre.lower(), nombre)


@functools.cache
def _patron_nombre(plantilla: str) -> re.Pattern:
    partes = re.split(r"(\{producto\}|\{vista\})", plantilla)
    vistas = "|".join(re.escape(v) for v in sorted(VISTAS, key=len, reverse=True))  # la más larga primero
    grupos = {"{producto}": "(?P<producto>.+)", "{vista}": f"(?P<vista>{vistas})"}
    return re.compile("".join(grupos.get(p, re.escape(p)) for p in partes), re.IGNORECASE)


@dataclass
class Resultado:
    """Lo que queda registrado en el reporte por cada imagen de salida."""

    producto: str
    vista: str
    entrada: str
    final: str | None = None
    maestro: str | None = None
    dimensiones_entrada: tuple[int, int] | None = None
    caja_producto: Caja | None = None
    escala: float | None = None
    metodo_fondo: str | None = None
    wallpaper: str | None = None
    calidad: int | None = None
    peso_kb: float | None = None
    alertas: list[Alerta] = field(default_factory=list)
    error: str | None = None


def estandarizar_lote(
    archivos: list[Path],
    salida: Path,
    cfg: Config,
    avance: Callable[[Resultado], None] | None = None,
) -> list[Resultado]:
    """Estandariza un conjunto de imágenes y compone la Portada de cada producto que tenga Trasera y Frontal.

    Por cada vista escribe en `salida/<producto>/` la imagen final (nombre según `salida.nombre`) y el
    recorte maestro (`maestros/<Vista>.png`), y actualiza `reporte.json`. La Frontal da una salida por cada
    variante de `pantalla.variantes` (Frontal, Frontal-Wallpaper). La Portada se compone con los maestros
    guardados, así que también se arma si la Trasera y la Frontal se procesaron en corridas distintas; si
    viene como entrada, se estandariza como las demás vistas en vez de componerse.
    Un error en una imagen no detiene el lote: queda registrado en su resultado.
    """
    quitar_fondo = Removedor(cfg.quitar_fondo)
    resultados: list[Resultado] = []
    frontales: dict[str, set[str]] = {}  # por producto, las variantes de la Frontal guardadas en esta corrida

    def registrar(r: Resultado) -> None:
        _actualizar_reporte(salida / r.producto / "reporte.json", r)
        resultados.append(r)
        if avance:
            avance(r)

    for archivo in archivos:
        producto, vista = identificar(archivo, cfg)
        r = Resultado(producto=producto, vista=vista, entrada=str(archivo))
        try:
            im = imagen.abrir(archivo)
            r.dimensiones_entrada = im.size
            sin_fondo, r.metodo_fondo = quitar_fondo(im)
            # Si se segmentó, la imagen original tiene el color real del interior del producto.
            e = estandarizar(sin_fondo, cfg, original=im if sin_fondo is not im else None)
        except (OSError, ErrorFondo, ErrorEstandarizacion) as ex:
            r.error = str(ex)
            registrar(r)
            continue
        if vista != "Frontal":
            registrar(_guardar_resultado(e, r, salida, cfg))
            continue
        frontales[producto] = set()
        for v in variantes_frontal(e, producto, cfg):
            rv = replace(r, vista=v.vista, wallpaper=v.wallpaper, error=v.error, alertas=[])
            if v.estandarizada is not None:
                _guardar_resultado(v.estandarizada, rv, salida, cfg)
                if not rv.error:
                    frontales[producto].add(v.vista)
            registrar(rv)

    con_portada = {r.producto for r in resultados if r.vista == "Portada"}
    for producto in dict.fromkeys(r.producto for r in resultados):
        if producto in con_portada:
            continue
        r = componer_portada_de(salida / producto, cfg, frontales.get(producto))
        if r is not None:
            registrar(r)
    return resultados


def _guardar_resultado(e: Estandarizada, r: Resultado, salida: Path, cfg: Config) -> Resultado:
    r.caja_producto, r.escala, r.alertas = e.caja_producto, round(e.escala, 4), list(e.alertas)
    try:
        _guardar(e.final, e.maestro, r, salida, cfg)
    except OSError as ex:
        r.error = str(ex)
    return r


def _maestros_portada(carpeta: Path, cfg: Config, frontales: set[str] | None = None
                      ) -> tuple[Path, Path, list[Alerta]] | None:
    """Maestros de la Trasera y de la Frontal para la Portada, con la variante de `portada.frontal` o, si no
    está, la otra (con alerta). `frontales` limita las Frontales a las guardadas en la corrida actual, para
    no mezclar una Frontal recién procesada con un maestro viejo de otra variante. None si falta alguno."""
    maestros = carpeta / "maestros"
    preferida = VISTA_DE_VARIANTE[cfg.portada.frontal]
    for vista in (preferida, *(v for v in VISTA_DE_VARIANTE.values() if v != preferida)):
        ruta = maestros / f"{vista}.png"
        if (frontales is None or vista in frontales) and ruta.exists() and (maestros / "Trasera.png").exists():
            alertas = [] if vista == preferida else [Alerta(
                "PORTADA_CON_OTRA_FRONTAL", f"no está la {preferida}: la Portada se armó con la {vista}",
            )]
            return maestros / "Trasera.png", ruta, alertas
    return None


def componer_portada_de(carpeta: Path, cfg: Config, frontales: set[str] | None = None) -> Resultado | None:
    """Compone la Portada del producto de `carpeta` con sus recortes maestros y la guarda. None si faltan
    los maestros de la Trasera o de la Frontal."""
    maestros = _maestros_portada(carpeta, cfg, frontales)
    if maestros is None:
        return None
    trasera, frontal, alertas_frontal = maestros
    r = Resultado(producto=carpeta.name, vista="Portada", entrada=f"compuesta: Trasera + {frontal.stem}")
    try:
        par, r.alertas = componer_portada(imagen.abrir(trasera), imagen.abrir(frontal), cfg)
        final, escala, alertas = componer(par, cfg)
        r.escala = round(escala, 4)
        r.alertas = alertas_frontal + r.alertas + alertas
        _guardar(final, par, r, carpeta.parent, cfg)
    except (OSError, ErrorEstandarizacion) as ex:
        r.error = str(ex)
    return r


def carpetas_de_producto(rutas: list[Path]) -> list[Path]:
    """Carpetas de producto (las que tienen `maestros/`): las rutas dadas o las que están dentro de ellas."""
    carpetas: list[Path] = []
    for ruta in rutas:
        if (ruta / "maestros").is_dir():
            carpetas.append(ruta)
        elif ruta.is_dir():
            carpetas += sorted(p for p in ruta.iterdir() if (p / "maestros").is_dir())
    return list(dict.fromkeys(carpetas))


def recomponer_portadas(
    carpetas: list[Path], cfg: Config, avance: Callable[[Resultado], None] | None = None
) -> list[Resultado]:
    """Vuelve a componer la Portada de cada producto con sus maestros, sin volver a quitar el fondo. Sirve
    para aplicar un cambio de `[portada]` o de `portada.frontal` en segundos."""
    resultados = []
    for carpeta in carpetas:
        r = componer_portada_de(carpeta, cfg)
        if r is None:
            r = Resultado(producto=carpeta.name, vista="Portada", entrada=ENTRADA_PORTADA,
                          error="faltan los recortes maestros de la Trasera o de la Frontal")
        else:
            _actualizar_reporte(carpeta / "reporte.json", r)
        resultados.append(r)
        if avance:
            avance(r)
    return resultados


def muestras_portada(carpetas: list[Path], solapes: list[float], cfg: Config, destino: Path) -> int:
    """Hoja para comparar valores de `portada.solape`: una fila por producto y una columna por valor, cada una
    con la Portada tal como quedaría. No modifica ninguna imagen. Devuelve cuántos productos entraron."""
    variantes = [replace(cfg, portada=replace(cfg.portada, solape=s)) for s in solapes]  # valida los valores
    filas = []
    for carpeta in carpetas:
        maestros = _maestros_portada(carpeta, cfg)
        if maestros is None:
            continue
        trasera, frontal = imagen.abrir(maestros[0]), imagen.abrir(maestros[1])
        filas.append((carpeta.name, [componer(componer_portada(trasera, frontal, c)[0], c)[0] for c in variantes]))
    if not filas:
        return 0

    lado, margen, texto = 320, 16, 26
    hoja = Image.new("RGB", (margen + len(solapes) * (lado + margen),
                             margen + len(filas) * (lado + texto + margen)), (233, 236, 242))
    dibujo, fuente = ImageDraw.Draw(hoja), ImageFont.load_default(size=14)
    for i, (producto, finales) in enumerate(filas):
        for j, (solape, final) in enumerate(zip(solapes, finales)):
            x, y = margen + j * (lado + margen), margen + i * (lado + texto + margen)
            fondo = Image.new("RGBA", final.size, (255, 255, 255, 255))
            miniatura = Image.alpha_composite(fondo, final).convert("RGB")
            miniatura.thumbnail((lado, lado), Image.Resampling.LANCZOS)
            hoja.paste(miniatura, (x, y))
            dibujo.text((x, y + lado + 6), f"{producto} · solape {solape:g}", fill=(21, 25, 48), font=fuente)
    destino.parent.mkdir(parents=True, exist_ok=True)
    hoja.save(destino, "JPEG", quality=90)
    return len(filas)


def _guardar(final: Image.Image, maestro: Image.Image, r: Resultado, salida: Path, cfg: Config) -> None:
    carpeta = salida / r.producto
    ruta_maestro = carpeta / "maestros" / f"{r.vista}.png"
    ruta_final = carpeta / f"{cfg.salida.nombre.format(producto=r.producto, vista=r.vista)}.{cfg.salida.formato}"
    imagen.guardar(maestro, ruta_maestro)
    peso_max = cfg.salida.peso_max_kb * 1000
    peso, r.calidad = imagen.guardar(
        final, ruta_final, cfg.salida.formato,
        calidad=cfg.salida.calidad, calidad_min=cfg.salida.calidad_min, peso_max=peso_max,
    )
    r.final, r.maestro, r.peso_kb = str(ruta_final), str(ruta_maestro), round(peso / 1000, 1)
    if peso_max and peso > peso_max:
        detalle = f"incluso con calidad {r.calidad}" if r.calidad else "y png no se comprime más: usa webp o jpg"
        r.alertas.append(Alerta(
            "PESO_EXCEDIDO", f"pesa {peso / 1000:.1f} KB, más que el máximo de {cfg.salida.peso_max_kb} KB, {detalle}",
        ))


def _actualizar_reporte(ruta: Path, r: Resultado) -> None:
    reporte = json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {}
    reporte[r.vista] = asdict(r)
    orden = {v: i for i, v in enumerate(VISTAS)}
    reporte = dict(sorted(reporte.items(), key=lambda kv: orden.get(kv[0], len(orden))))
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(reporte, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
