"""Configuración del pipeline.

Los defaults viven acá; `config.toml` solo necesita lo que se quiera cambiar.
Las secciones y claves del TOML se llaman igual que las clases y campos de este módulo.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from PIL import ImageColor

TRANSPARENTE = "transparente"
FORMATOS = ("png", "jpg", "webp")
METODOS_FONDO = ("auto", "alfa", "rembg")
# Vistas de la galería, en el orden en que se muestran. La Portada se compone con la Trasera y la Frontal.
VISTAS = ("portada", "lateral", "frontal", "trasera")


class ErrorConfig(ValueError):
    pass


@dataclass(frozen=True)
class Lienzo:
    ancho: int = 1000
    alto: int = 1000
    fondo: str = "#FFFFFF"

    @property
    def rgba_fondo(self) -> tuple[int, int, int, int]:
        if self.fondo == TRANSPARENTE:
            return (0, 0, 0, 0)
        r, g, b = ImageColor.getrgb(self.fondo)[:3]
        return (r, g, b, 255)


@dataclass(frozen=True)
class Producto:
    altura: float = 0.90
    ancho_max: float = 0.90


@dataclass(frozen=True)
class Portada:
    visible_trasera: float = 5 / 6


@dataclass(frozen=True)
class Sombra:
    """Sombra de contacto bajo el producto. Las medidas son fracciones del alto del producto.

    Los defaults se ajustaron por mínimos cuadrados a las 4 referencias de estilo (estilo/referencias/).
    """

    activa: bool = True
    opacidad: float = 0.24
    base: float = 0.024
    desplazamiento: float = 0.004
    desenfoque_x: float = 0.029
    desenfoque_y: float = 0.018


@dataclass(frozen=True)
class Recorte:
    umbral_alfa: int = 128
    objeto_min: float = 0.005
    rellenar_interior: bool = True


@dataclass(frozen=True)
class QuitarFondo:
    metodo: str = "auto"
    modelo: str = "birefnet-general"
    limpiar_bordes: bool = True


@dataclass(frozen=True)
class Salida:
    formato: str = "webp"
    calidad: int = 90
    calidad_min: int = 60
    peso_max_kb: int = 50
    nombre: str = "{producto}-Version-Final-{vista}"


@dataclass(frozen=True)
class Alertas:
    ampliacion_max: float = 1.0
    margen_borde: int = 2
    diferencia_proporcion: float = 0.03


@dataclass(frozen=True)
class Config:
    lienzo: Lienzo = field(default_factory=Lienzo)
    producto: Producto = field(default_factory=Producto)
    portada: Portada = field(default_factory=Portada)
    sombra: Sombra = field(default_factory=Sombra)
    recorte: Recorte = field(default_factory=Recorte)
    quitar_fondo: QuitarFondo = field(default_factory=QuitarFondo)
    salida: Salida = field(default_factory=Salida)
    alertas: Alertas = field(default_factory=Alertas)

    def __post_init__(self) -> None:
        _validar(self)


def cargar_config(ruta: Path | None) -> Config:
    """Lee un TOML sobre los defaults. Sin ruta, devuelve los defaults."""
    if ruta is None:
        return Config()
    with open(ruta, "rb") as f:
        datos = tomllib.load(f)

    secciones = {s.name: s.default_factory for s in fields(Config)}
    desconocidas = set(datos) - set(secciones)
    if desconocidas:
        raise ErrorConfig(f"{ruta}: sección desconocida [{', '.join(sorted(desconocidas))}]")

    valores = {}
    for nombre, clase in secciones.items():
        opciones = datos.get(nombre, {})
        validas = {f.name for f in fields(clase)}
        extra = set(opciones) - validas
        if extra:
            raise ErrorConfig(
                f"{ruta}: [{nombre}] no tiene la opción {', '.join(sorted(extra))} "
                f"(opciones: {', '.join(sorted(validas))})"
            )
        valores[nombre] = clase(**opciones)
    return Config(**valores)


def _validar(c: Config) -> None:
    def exigir(condicion: bool, mensaje: str) -> None:
        if not condicion:
            raise ErrorConfig(mensaje)

    def numero(valor) -> bool:
        return isinstance(valor, (int, float)) and not isinstance(valor, bool)

    def entero(valor, minimo: int, maximo: int | None = None) -> bool:
        return (isinstance(valor, int) and not isinstance(valor, bool)
                and valor >= minimo and (maximo is None or valor <= maximo))

    def fraccion(valor) -> bool:
        return numero(valor) and 0 < valor <= 1

    exigir(entero(c.lienzo.ancho, 1) and entero(c.lienzo.alto, 1),
           "lienzo.ancho y lienzo.alto deben ser enteros positivos")
    exigir(c.lienzo.fondo == TRANSPARENTE or _es_color(c.lienzo.fondo),
           'lienzo.fondo debe ser un color hex (p. ej. "#FFFFFF") o "transparente"')
    exigir(fraccion(c.producto.altura), "producto.altura debe ser un número entre 0 y 1")
    exigir(fraccion(c.producto.ancho_max), "producto.ancho_max debe ser un número entre 0 y 1")
    exigir(fraccion(c.portada.visible_trasera), "portada.visible_trasera debe ser un número entre 0 y 1")
    exigir(isinstance(c.sombra.activa, bool), "sombra.activa debe ser true o false")
    exigir(numero(c.sombra.opacidad) and 0 <= c.sombra.opacidad <= 1, "sombra.opacidad debe ser un número entre 0 y 1")
    exigir(fraccion(c.sombra.base), "sombra.base debe ser un número entre 0 y 1")
    exigir(numero(c.sombra.desplazamiento) and -1 <= c.sombra.desplazamiento <= 1,
           "sombra.desplazamiento debe ser un número entre -1 y 1")
    exigir(numero(c.sombra.desenfoque_x) and numero(c.sombra.desenfoque_y)
           and c.sombra.desenfoque_x >= 0 and c.sombra.desenfoque_y >= 0,
           "sombra.desenfoque_x y sombra.desenfoque_y deben ser números mayores o iguales a 0")
    exigir(entero(c.recorte.umbral_alfa, 1, 255), "recorte.umbral_alfa debe ser un entero entre 1 y 255")
    exigir(numero(c.recorte.objeto_min) and 0 <= c.recorte.objeto_min < 1,
           "recorte.objeto_min debe ser un número entre 0 y 1")
    exigir(isinstance(c.recorte.rellenar_interior, bool), "recorte.rellenar_interior debe ser true o false")
    exigir(c.quitar_fondo.metodo in METODOS_FONDO,
           f"quitar_fondo.metodo debe ser uno de: {', '.join(METODOS_FONDO)}")
    exigir(isinstance(c.quitar_fondo.modelo, str) and c.quitar_fondo.modelo != "",
           "quitar_fondo.modelo debe ser el nombre de un modelo de rembg")
    exigir(isinstance(c.quitar_fondo.limpiar_bordes, bool), "quitar_fondo.limpiar_bordes debe ser true o false")
    exigir(c.salida.formato in FORMATOS, f"salida.formato debe ser uno de: {', '.join(FORMATOS)}")
    exigir(entero(c.salida.calidad, 1, 100), "salida.calidad debe ser un entero entre 1 y 100")
    exigir(entero(c.salida.calidad_min, 1, 100) and c.salida.calidad_min <= c.salida.calidad,
           "salida.calidad_min debe ser un entero entre 1 y salida.calidad")
    exigir(entero(c.salida.peso_max_kb, 0), "salida.peso_max_kb debe ser un entero mayor o igual a 0 (0 = sin límite)")
    exigir(_plantilla_valida(c.salida.nombre),
           'salida.nombre debe incluir {producto} y {vista}, p. ej. "{producto}-Version-Final-{vista}"')
    exigir(not (c.salida.formato == "jpg" and c.lienzo.fondo == TRANSPARENTE),
           "jpg no admite transparencia: usa png o webp, o un color en lienzo.fondo")
    exigir(numero(c.alertas.ampliacion_max) and c.alertas.ampliacion_max > 0,
           "alertas.ampliacion_max debe ser un número mayor que 0")
    exigir(entero(c.alertas.margen_borde, 0), "alertas.margen_borde debe ser un entero mayor o igual a 0")
    exigir(numero(c.alertas.diferencia_proporcion) and c.alertas.diferencia_proporcion >= 0,
           "alertas.diferencia_proporcion debe ser un número mayor o igual a 0")


def _es_color(valor) -> bool:
    if not isinstance(valor, str):
        return False
    try:
        ImageColor.getrgb(valor)
    except ValueError:
        return False
    return True


def _plantilla_valida(plantilla) -> bool:
    if not isinstance(plantilla, str) or "{producto}" not in plantilla or "{vista}" not in plantilla:
        return False
    try:
        plantilla.format(producto="p", vista="v")
    except (KeyError, IndexError, ValueError):
        return False
    return True
