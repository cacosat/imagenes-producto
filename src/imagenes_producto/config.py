"""Configuración del pipeline.

Los defaults viven acá; `config.toml` solo necesita lo que se quiera cambiar.
Las secciones y claves del TOML se llaman igual que las clases y campos de este módulo.
"""

from __future__ import annotations

import colorsys
import re
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from PIL import ImageColor

TRANSPARENTE = "transparente"
FORMATOS = ("png", "jpg", "webp")
METODOS_FONDO = ("auto", "alfa", "rembg")
# Vistas de la galería, en el orden en que se muestran. La Portada se compone con la Trasera y la Frontal;
# la Frontal-Wallpaper sale de la Frontal cuando `pantalla.variantes` la pide.
VISTAS = ("Portada", "Lateral", "Frontal", "Frontal-Wallpaper", "Trasera")
# Vistas que genera la IA: la Portada nunca, porque se compone.
VISTAS_GENERADAS = ("Lateral", "Frontal", "Trasera")
# Variantes de pantalla de la Frontal y la vista de galería de cada una.
VISTA_DE_VARIANTE = {"apagada": "Frontal", "wallpaper": "Frontal-Wallpaper"}
ANCLAS_WALLPAPER = ("centro", "arriba")
LADOS = ("izquierda", "derecha")
ADELANTE = ("frontal", "trasera")
CALIDADES_IA = ("low", "medium", "high", "xhigh", "max", "auto")
FONDOS_IA = ("transparente", "blanco")
PROVEEDORES = ("openai",)


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
    """Composición de la Portada con la Trasera y la Frontal, del mismo alto.

    `solape` es la fracción del ancho del teléfono de adelante que tapa al de atrás: 0 = se tocan; 1/6 deja
    ver 5/6 de la trasera, como la referencia de estilo; negativo = separados.
    """

    solape: float = 1 / 6
    desfase_vertical: float = 0.0  # fracción del alto; positivo = la frontal más abajo que la trasera
    adelante: str = "frontal"
    lado_trasera: str = "izquierda"
    frontal: str = "apagada"  # variante de la Frontal que va en la Portada


@dataclass(frozen=True)
class Pantalla:
    """Pantalla de la Frontal.

    Con "wallpaper" en `variantes`, la IA genera la Frontal con la pantalla en `color_croma` y de esa única
    imagen salen las variantes: "apagada" (vista Frontal) y "wallpaper" (vista Frontal-Wallpaper), con la
    misma geometría. Sin "wallpaper", la IA la genera apagada y se usa tal cual.
    """

    variantes: tuple[str, ...] = ("apagada", "wallpaper")
    color_croma: str = "#00FF00"
    tolerancia_tono: tuple[float, float] = (18, 40)  # grados: hasta el primero es croma; desde el segundo, no
    wallpapers: str = "estilo/wallpapers"  # carpeta (<producto>, <modelo> o _default) o un archivo para todos
    ancla_wallpaper: str = "centro"
    color_apagada: str = "#0B0B0F"
    reflejo_apagada: float = 0.06


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
    ampliacion_max: float = 1.10
    margen_borde: int = 2
    diferencia_proporcion: float = 0.03


@dataclass(frozen=True)
class Generar:
    """Etapa 1. Precios en US$ por millón de tokens (OpenAI, gpt-image-2.5, al 2026-09-24)."""

    proveedor: str = "openai"
    modelo: str = "gpt-image-2.5-sunburst"
    calidad: str = "high"
    medidas: str = "1024x1536"
    candidatos: int = 1
    fondo: str = "transparente"
    vistas: tuple[str, ...] = VISTAS_GENERADAS
    prompts: str = "estilo/prompts"
    referencias: str = "estilo/referencias"
    rasgos: str = "estilo/rasgos.toml"
    original_min: int = 800
    precio_texto_entrada: float = 5.0
    precio_imagen_entrada: float = 8.0
    precio_imagen_salida: float = 30.0


@dataclass(frozen=True)
class Config:
    lienzo: Lienzo = field(default_factory=Lienzo)
    producto: Producto = field(default_factory=Producto)
    portada: Portada = field(default_factory=Portada)
    pantalla: Pantalla = field(default_factory=Pantalla)
    sombra: Sombra = field(default_factory=Sombra)
    recorte: Recorte = field(default_factory=Recorte)
    quitar_fondo: QuitarFondo = field(default_factory=QuitarFondo)
    salida: Salida = field(default_factory=Salida)
    alertas: Alertas = field(default_factory=Alertas)
    generar: Generar = field(default_factory=Generar)

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

    if "visible_trasera" in datos.get("portada", {}):
        raise ErrorConfig(
            f"{ruta}: portada.visible_trasera se reemplazó por portada.solape, la fracción del ancho de la "
            "frontal que tapa a la trasera. Con las dos del mismo ancho, solape = 1 − visible_trasera "
            "(5/6 → solape = 0.1667)"
        )

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
        # Las listas del TOML pasan a tuplas: la configuración es inmutable.
        valores[nombre] = clase(**{k: tuple(v) if isinstance(v, list) else v for k, v in opciones.items()})
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
    p = c.portada
    exigir(numero(p.solape) and -0.5 <= p.solape <= 0.95,
           "portada.solape debe ser un número entre -0.5 (separados) y 0.95")
    exigir(numero(p.desfase_vertical) and -0.5 <= p.desfase_vertical <= 0.5,
           "portada.desfase_vertical debe ser un número entre -0.5 y 0.5")
    exigir(p.adelante in ADELANTE, f"portada.adelante debe ser uno de: {', '.join(ADELANTE)}")
    exigir(p.lado_trasera in LADOS, f"portada.lado_trasera debe ser uno de: {', '.join(LADOS)}")
    exigir(p.frontal in c.pantalla.variantes,
           f"portada.frontal debe ser una de las variantes de pantalla.variantes ({', '.join(c.pantalla.variantes)})")

    s = c.pantalla
    exigir(isinstance(s.variantes, tuple) and len(s.variantes) > 0 and len(set(s.variantes)) == len(s.variantes)
           and all(v in VISTA_DE_VARIANTE for v in s.variantes),
           f"pantalla.variantes debe ser una lista sin repetir con algunas de: {', '.join(VISTA_DE_VARIANTE)}")
    exigir(_es_color(s.color_croma) and _saturado(s.color_croma),
           'pantalla.color_croma debe ser un color hex bien saturado, p. ej. "#00FF00"')
    exigir(isinstance(s.tolerancia_tono, tuple) and len(s.tolerancia_tono) == 2 and all(map(numero, s.tolerancia_tono))
           and 0 <= s.tolerancia_tono[0] < s.tolerancia_tono[1] <= 90,
           "pantalla.tolerancia_tono debe ser [mínimo, máximo] en grados, con 0 ≤ mínimo < máximo ≤ 90")
    exigir(isinstance(s.wallpapers, str) and s.wallpapers != "", "pantalla.wallpapers debe ser una ruta")
    exigir(s.ancla_wallpaper in ANCLAS_WALLPAPER,
           f"pantalla.ancla_wallpaper debe ser uno de: {', '.join(ANCLAS_WALLPAPER)}")
    exigir(_es_color(s.color_apagada), 'pantalla.color_apagada debe ser un color hex, p. ej. "#0B0B0F"')
    exigir(numero(s.reflejo_apagada) and 0 <= s.reflejo_apagada <= 1,
           "pantalla.reflejo_apagada debe ser un número entre 0 y 1")
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

    g = c.generar
    exigir(g.proveedor in PROVEEDORES, f"generar.proveedor debe ser uno de: {', '.join(PROVEEDORES)}")
    exigir(isinstance(g.modelo, str) and g.modelo != "", "generar.modelo debe ser el nombre de un modelo")
    exigir(g.calidad in CALIDADES_IA, f"generar.calidad debe ser una de: {', '.join(CALIDADES_IA)}")
    exigir(isinstance(g.medidas, str) and (g.medidas == "auto" or re.fullmatch(r"\d+x\d+", g.medidas) is not None),
           'generar.medidas debe ser "auto" o ANCHOxALTO, p. ej. "1024x1536"')
    exigir(entero(g.candidatos, 1, 10), "generar.candidatos debe ser un entero entre 1 y 10")
    exigir(g.fondo in FONDOS_IA, f"generar.fondo debe ser uno de: {', '.join(FONDOS_IA)}")
    exigir(isinstance(g.vistas, tuple) and len(g.vistas) > 0 and all(v in VISTAS_GENERADAS for v in g.vistas),
           f"generar.vistas debe ser una lista con algunas de: {', '.join(VISTAS_GENERADAS)} "
           "(la Portada no se genera: se compone)")
    exigir(all(isinstance(r, str) and r != "" for r in (g.prompts, g.referencias, g.rasgos)),
           "generar.prompts, generar.referencias y generar.rasgos deben ser rutas")
    exigir(entero(g.original_min, 0), "generar.original_min debe ser un entero mayor o igual a 0")
    exigir(all(numero(p) and p >= 0 for p in (g.precio_texto_entrada, g.precio_imagen_entrada, g.precio_imagen_salida)),
           "los precios de generar deben ser números mayores o iguales a 0")


def _es_color(valor) -> bool:
    if not isinstance(valor, str):
        return False
    try:
        ImageColor.getrgb(valor)
    except ValueError:
        return False
    return True


def _saturado(color: str) -> bool:
    """Un color croma tiene que distinguirse del marco, del fondo y de la pantalla negra."""
    r, g, b = (v / 255 for v in ImageColor.getrgb(color)[:3])
    _, saturacion, brillo = colorsys.rgb_to_hsv(r, g, b)
    return saturacion >= 0.6 and brillo >= 0.5


def _plantilla_valida(plantilla) -> bool:
    if not isinstance(plantilla, str) or "{producto}" not in plantilla or "{vista}" not in plantilla:
        return False
    try:
        plantilla.format(producto="p", vista="v")
    except (KeyError, IndexError, ValueError):
        return False
    return True
