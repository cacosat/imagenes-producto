"""Etapa 2 — estandarizar: recortar el producto, escalarlo a una altura fija y centrarlo en un lienzo fijo.

Es determinista: la misma imagen con la misma configuración da siempre el mismo resultado.
Toda la geometría se mide sobre el canal alfa (el recorte del producto), nunca sobre el fondo.
La Portada no se genera: se compone con los recortes maestros de la Trasera y la Frontal.
"""

from __future__ import annotations

import functools
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from . import imagen
from .config import VISTAS, Config
from .fondo import ErrorFondo, Removedor

Caja = tuple[int, int, int, int]  # x0, y0, x1, y1 (x1 e y1 exclusivos)

ENTRADA_PORTADA = "compuesta: Trasera + Frontal"

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
    """Recorte del par de la Portada: la trasera a la izquierda y detrás, la frontal a la derecha y delante.

    Las dos quedan del mismo alto y alineadas, y de la trasera se ve `portada.visible_trasera` del ancho
    de la frontal. El par se lleva después al lienzo con `componer`, como cualquier otra vista.
    """
    cajas = [_contorno(m, cfg) for m in (trasera, frontal)]
    alto = min(y1 - y0 for _, y0, _, y1 in cajas)  # se reduce la más alta: nunca se amplía
    capas = []
    for maestro, (x0, y0, x1, y1) in zip((trasera, frontal), cajas):
        escalado, sx, sy = _escalar(maestro, alto / (y1 - y0))
        capas.append((escalado, x0 * sx, y0 * sy, (x1 - x0) * sx))
    (t, tx0, ty0, _), (f, fx0, fy0, ancho_frontal) = capas

    # Contorno de la trasera en x = 0; el de la frontal empieza donde la trasera deja de verse.
    posiciones = [(round(-tx0), round(-ty0)), (round(cfg.portada.visible_trasera * ancho_frontal - fx0), round(-fy0))]
    x_min, y_min = min(x for x, _ in posiciones), min(y for _, y in posiciones)
    x_max = max(x + im.width for (x, _), im in zip(posiciones, (t, f)))
    y_max = max(y + im.height for (_, y), im in zip(posiciones, (t, f)))
    par = Image.new("RGBA", (x_max - x_min, y_max - y_min), (0, 0, 0, 0))
    for (x, y), im in zip(posiciones, (t, f)):  # la trasera primero, para que quede detrás
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


def identificar(archivo: Path, cfg: Config) -> tuple[str, str]:
    """Producto y vista de una imagen de entrada.

    Acepta el mismo formato de nombre de la salida (p. ej. `iPhone-12-Azul-Version-Final-Frontal.webp`)
    o la estructura `<producto>/<vista>.<ext>` (p. ej. `iPhone-12-Azul/frontal.png`).
    """
    coincidencia = _patron_nombre(cfg.salida.nombre).fullmatch(archivo.stem)
    if coincidencia:
        return coincidencia["producto"], _vista_canonica(coincidencia["vista"])
    return archivo.resolve().parent.name, _vista_canonica(archivo.stem)


def _vista_canonica(nombre: str) -> str:
    """'frontal' → 'Frontal'. Un nombre que no es una vista conocida queda igual."""
    return nombre.capitalize() if nombre.lower() in VISTAS else nombre


@functools.cache
def _patron_nombre(plantilla: str) -> re.Pattern:
    partes = re.split(r"(\{producto\}|\{vista\})", plantilla)
    grupos = {"{producto}": "(?P<producto>.+)", "{vista}": f"(?P<vista>{'|'.join(VISTAS)})"}
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
    recorte maestro (`maestros/<Vista>.png`), y actualiza `reporte.json`. La Portada se compone con los
    maestros guardados, así que también se arma si la Trasera y la Frontal se procesaron en corridas
    distintas; si viene como entrada, se estandariza como las demás vistas en vez de componerse.
    Un error en una imagen no detiene el lote: queda registrado en su resultado.
    """
    quitar_fondo = Removedor(cfg.quitar_fondo)
    resultados: list[Resultado] = []

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
            r.caja_producto, r.escala, r.alertas = e.caja_producto, round(e.escala, 4), e.alertas
            _guardar(e.final, e.maestro, r, salida, cfg)
        except (OSError, ErrorFondo, ErrorEstandarizacion) as ex:
            r.error = str(ex)
        registrar(r)

    con_portada = {r.producto for r in resultados if r.vista == "Portada"}
    for producto in dict.fromkeys(r.producto for r in resultados):
        maestros = [salida / producto / "maestros" / f"{v}.png" for v in ("Trasera", "Frontal")]
        if producto in con_portada or not all(m.exists() for m in maestros):
            continue
        r = Resultado(producto=producto, vista="Portada", entrada=ENTRADA_PORTADA)
        try:
            par, r.alertas = componer_portada(*(imagen.abrir(m) for m in maestros), cfg)
            final, escala, alertas = componer(par, cfg)
            r.escala = round(escala, 4)
            r.alertas += alertas
            _guardar(final, par, r, salida, cfg)
        except (OSError, ErrorEstandarizacion) as ex:
            r.error = str(ex)
        registrar(r)
    return resultados


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
    orden = {v.capitalize(): i for i, v in enumerate(VISTAS)}
    reporte = dict(sorted(reporte.items(), key=lambda kv: orden.get(kv[0], len(orden))))
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(reporte, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
