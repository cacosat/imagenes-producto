"""Línea de comandos: `imgprod <comando>` (o `python -m imagenes_producto <comando>`)."""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

from . import __version__
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
    return _estandarizar(args.entradas, args.salida, cfg)


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


def _imagenes(n: int) -> str:
    return f"{n} imagen" if n == 1 else f"{n} imágenes"
