"""Línea de comandos: `imgprod <comando>` (o `python -m imagenes_producto <comando>`)."""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from pathlib import Path

from . import __version__, generar
from .config import Config, ErrorConfig, cargar_config
from .estandarizar import ENTRADA_PORTADA, Resultado, estandarizar_lote
from .imagen import EXTENSIONES, buscar_imagenes

CONFIG_DEFAULT = Path("config.toml")


def main(argv: list[str] | None = None) -> int:
    # En Windows, con la salida redirigida a un archivo, la codificación de la consola no tiene ✓ ⚠ ✗:
    # sin esto, imprimirlos botaría el programa.
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(errors="replace")

    comun = argparse.ArgumentParser(add_help=False)
    comun.add_argument("-c", "--config", type=Path, help="archivo de configuración (default: config.toml, si existe)")

    parser = argparse.ArgumentParser(prog="imgprod", description="Pipeline de imágenes de producto de Reuse.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    comandos = parser.add_subparsers(dest="comando", required=True, metavar="COMANDO")

    gen = comandos.add_parser(
        "generar",
        parents=[comun],
        help="genera con IA las vistas de cada equipo a partir de su foto original, y las estandariza",
        description="Genera con IA las vistas Lateral, Frontal y Trasera de cada equipo a partir de su foto "
        "original (nombrada '<Modelo> <Color>.<ext>', p. ej. 'iPhone 12 Azul.webp') y luego las estandariza "
        "como el comando estandarizar. Antes de gastar muestra el costo estimado y pide confirmación.",
    )
    gen.add_argument("originales", nargs="+", type=Path, metavar="ORIGINAL", help="fotos originales o carpetas")
    gen.add_argument("-g", "--generadas", type=Path, default=Path("generadas"),
                     help="carpeta para lo que devuelve la IA, sin estandarizar (default: generadas/)")
    gen.add_argument("-s", "--salida", type=Path, default=Path("salida"),
                     help="carpeta de las imágenes finales (default: salida/)")
    gen.add_argument("--solo-generar", action="store_true", help="no estandarizar lo generado")
    gen.add_argument("-y", "--si", action="store_true", help="no pedir confirmación antes de gastar")

    est = comandos.add_parser(
        "estandarizar",
        parents=[comun],
        help="recorta, escala y centra el producto en un lienzo fijo, y compone la Portada",
        description="Quita el fondo, recorta el producto, lo escala a una altura fija y lo centra en un "
        "lienzo fijo. Compone la Portada de cada producto con su Trasera y su Frontal. Acepta imágenes "
        "nombradas como la salida (<producto>-Version-Final-<Vista>) o con la estructura <producto>/<vista>.",
    )
    est.add_argument("entradas", nargs="+", type=Path, metavar="ENTRADA", help="imágenes o carpetas de imágenes")
    est.add_argument("-s", "--salida", type=Path, default=Path("salida"), help="carpeta de salida (default: salida/)")

    args = parser.parse_args(argv)
    try:
        cfg = cargar_config(args.config or (CONFIG_DEFAULT if CONFIG_DEFAULT.exists() else None))
    except (ErrorConfig, OSError, tomllib.TOMLDecodeError) as e:
        print(f"Error en la configuración: {e}", file=sys.stderr)
        return 2
    if args.comando == "generar":
        return _generar(args, cfg)
    return _estandarizar(args.entradas, args.salida, cfg)


def _generar(args: argparse.Namespace, cfg: Config) -> int:
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True))  # el .env de la carpeta actual (o de una superior); no pisa variables ya definidas
    if not os.environ.get("OPENAI_API_KEY"):
        print("Falta OPENAI_API_KEY: pega tu clave en el archivo .env (ver .env.example).", file=sys.stderr)
        return 2
    faltantes = [str(o) for o in args.originales if not o.exists()]
    if faltantes:
        print(f"No existe: {', '.join(faltantes)}", file=sys.stderr)
        return 2
    originales = buscar_imagenes(args.originales)
    if not originales:
        print(f"No se encontraron imágenes ({', '.join(sorted(EXTENSIONES))}).", file=sys.stderr)
        return 2

    g = cfg.generar
    n, estimado = generar.estimar(len(originales), cfg)
    print(
        f"Se van a generar {_imagenes(n)} ({len(originales)} original{'es' if len(originales) != 1 else ''} × "
        f"{len(g.vistas)} vistas × {g.candidatos} candidata{'s' if g.candidatos != 1 else ''}) con {g.modelo}, "
        f"calidad {g.calidad}, {g.medidas}.\nCosto estimado: ~US${estimado:.2f} (aproximado: el costo real "
        "queda en el .json de cada vista)."
    )
    if not args.si and not _confirmar("¿Continuar? [s/N] "):
        print("Cancelado: no se generó nada.")
        return 1

    try:
        resultados = generar.generar_lote(originales, args.generadas, cfg, generar.crear_cliente(cfg),
                                          avance=_imprimir_generada)
    except generar.ErrorFatal as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2
    errores = sum(1 for r in resultados if r.error)
    print(f"\nGeneración: {len(resultados) - errores} vistas ok, {errores} con error. "
          f"Costo real: US${sum(r.costo_usd for r in resultados):.3f}. Detalle en {args.generadas}/<producto>/")

    archivos = [Path(a) for r in resultados for a in r.archivos]
    if args.solo_generar or not archivos:
        return 1 if errores else 0
    print()
    codigo = _estandarizar(archivos, args.salida, cfg)
    return 1 if errores else codigo


def _confirmar(pregunta: str) -> bool:
    try:
        return input(pregunta).strip().lower() in ("s", "si", "sí", "y", "yes")
    except EOFError:  # sin terminal interactiva: no se gasta sin confirmación explícita (usar --si)
        print()
        return False


def _estandarizar(entradas: list[Path], salida: Path, cfg: Config) -> int:
    faltantes = [str(e) for e in entradas if not e.exists()]
    if faltantes:
        print(f"No existe: {', '.join(faltantes)}", file=sys.stderr)
        return 2
    archivos = buscar_imagenes(entradas, excluir=salida)
    if not archivos:
        print(f"No se encontraron imágenes ({', '.join(sorted(EXTENSIONES))}).", file=sys.stderr)
        return 2

    print(f"Estandarizando {_imagenes(len(archivos))} → {salida}/")
    resultados = estandarizar_lote(archivos, salida, cfg, avance=_imprimir)
    errores = sum(1 for r in resultados if r.error)
    con_alertas = sum(1 for r in resultados if r.alertas and not r.error)
    print(
        f"\n{_imagenes(len(resultados))} de salida: {len(resultados) - errores - con_alertas} sin alertas, "
        f"{con_alertas} con alertas, {errores} con error. Detalle en {salida}/<producto>/reporte.json"
    )
    return 1 if errores else 0


def _imprimir(r: Resultado) -> None:
    nombre = f"{r.producto} · {r.vista}" + (" (compuesta)" if r.entrada == ENTRADA_PORTADA else "")
    if r.error:
        print(f"  ✗ {nombre}: {r.error}")
        return
    print(f"  {'⚠' if r.alertas else '✓'} {nombre}  ×{r.escala:.2f} · {r.peso_kb:g} KB")
    for a in r.alertas:
        print(f"      {a.codigo}: {a.mensaje}")


def _imprimir_generada(r: generar.Generada) -> None:
    nombre = f"{r.producto} · {r.vista}"
    if r.error:
        print(f"  ✗ {nombre}: {r.error}")
    else:
        print(f"  {'⚠' if r.alertas else '✓'} {nombre}  US${r.costo_usd:.3f} · {r.segundos:g} s")
    for a in r.alertas:
        print(f"      {a.codigo}: {a.mensaje}")


def _imagenes(n: int) -> str:
    return f"{n} imagen" if n == 1 else f"{n} imágenes"
