from dataclasses import replace

import numpy as np
import pytest
from PIL import Image

from imagenes_producto import pantalla
from imagenes_producto.config import ErrorConfig, cargar_config
from imagenes_producto.estandarizar import estandarizar, variantes_frontal
from test_estandarizar import TRANSPARENTE, frontal_croma, telefono, wallpaper

PRODUCTO = "iPhone-15-Pro-Azul"


def con_wallpapers(ruta, **pantalla_):
    return replace(TRANSPARENTE, pantalla=replace(TRANSPARENTE.pantalla, wallpapers=str(ruta), **pantalla_))


def solo_apagada():
    return replace(TRANSPARENTE, pantalla=replace(TRANSPARENTE.pantalla, variantes=("apagada",)))


def verdes(im):
    """Píxeles del teléfono con el tono del croma (verde saturado)."""
    px = np.asarray(im.convert("RGBA")).astype(np.float32) / 255
    tono, saturacion, brillo = pantalla._hsv(px[..., :3])
    return int(((np.abs(tono - 120) < 40) & (saturacion > 0.35) & (brillo > 0.2) & (px[..., 3] > 0.5)).sum())


def codigos(alertas):
    return [a.codigo for a in alertas]


def test_detecta_la_pantalla_croma_y_la_dynamic_island():
    d = pantalla.detectar(estandarizar(frontal_croma(), TRANSPARENTE).maestro, TRANSPARENTE)
    assert d is not None
    assert d.elementos == 1  # la Dynamic Island
    assert 0.75 < d.cobertura < 0.95


def test_sin_pantalla_croma_no_detecta_nada():
    negra = estandarizar(telefono((1000, 1500), (200, 100, 700, 1300)), TRANSPARENTE).maestro
    assert pantalla.detectar(negra, TRANSPARENTE) is None


def test_las_variantes_cambian_solo_la_pantalla(tmp_path):
    wallpaper(tmp_path / "_default.png")
    cfg = con_wallpapers(tmp_path)
    e = estandarizar(frontal_croma(), cfg)
    assert verdes(e.maestro) > 100_000
    variantes = {v.vista: v for v in variantes_frontal(e, PRODUCTO, cfg)}
    assert list(variantes) == ["Frontal", "Frontal-Wallpaper"]
    assert variantes["Frontal-Wallpaper"].wallpaper == str(tmp_path / "_default.png")

    d = pantalla.detectar(e.maestro, cfg)
    x0, y0, x1, y1 = d.caja
    original = np.asarray(e.maestro).astype(int)
    # La Dynamic Island: lo negro dentro del rectángulo de la pantalla (más las esquinas del marco, que
    # también deben seguir negras). Su centro queda 37 px bajo el borde superior de la pantalla.
    isla = np.zeros(original.shape[:2], bool)
    isla[y0 + 6:y1 - 6, x0 + 6:x1 - 6] = original[y0 + 6:y1 - 6, x0 + 6:x1 - 6, :3].max(axis=2) < 10
    centro_isla = (y0 + 37, (x0 + x1) // 2)
    assert isla.sum() > 3000 and isla[centro_isla]

    for v in variantes.values():
        px = np.asarray(v.estandarizada.maestro).astype(int)
        assert verdes(v.estandarizada.maestro) == 0  # ni un píxel croma, tampoco en el borde de la pantalla
        assert (px[..., 3] == original[..., 3]).all()  # la silueta no cambia…
        assert v.estandarizada.escala == e.escala and v.estandarizada.final.size == e.final.size  # …ni la geometría
        assert px[isla, :3].max() < 15  # la Dynamic Island sigue negra
        assert (px[5, (x0 + x1) // 2, :3] == original[5, (x0 + x1) // 2, :3]).all()  # el marco, igual

    apagada = np.asarray(variantes["Frontal"].estandarizada.maestro).astype(int)
    assert apagada[(y0 + y1) // 2, (x0 + x1) // 2, :3].max() < 40
    con_wp = np.asarray(variantes["Frontal-Wallpaper"].estandarizada.maestro).astype(int)
    arriba, abajo = con_wp[y0 + (y1 - y0) // 8, (x0 + x1) // 2], con_wp[y1 - (y1 - y0) // 8, (x0 + x1) // 2]
    assert arriba[0] > arriba[2] + 60 and abajo[2] > abajo[0] + 60  # el wallpaper, rojo arriba y azul abajo


def test_sin_pantalla_croma_la_apagada_pasa_tal_cual_y_la_de_wallpaper_falla(tmp_path):
    wallpaper(tmp_path / "_default.png")
    cfg = con_wallpapers(tmp_path)
    e = estandarizar(telefono((1000, 1500), (200, 100, 700, 1300)), cfg)
    frontal, con_wp = variantes_frontal(e, PRODUCTO, cfg)
    assert frontal.vista == "Frontal" and frontal.estandarizada.maestro is e.maestro
    assert codigos(frontal.estandarizada.alertas) == ["SIN_PANTALLA_CROMA"]
    assert con_wp.vista == "Frontal-Wallpaper" and con_wp.estandarizada is None and "croma" in con_wp.error

    # Si no se pide wallpaper, no se espera croma: la Frontal pasa sin alertas.
    (unica,) = variantes_frontal(e, PRODUCTO, solo_apagada())
    assert unica.vista == "Frontal" and unica.estandarizada.maestro is e.maestro and not unica.estandarizada.alertas


def test_alerta_si_la_ia_dibujo_hora_o_iconos_en_la_pantalla():
    e = estandarizar(frontal_croma(hora=True), TRANSPARENTE)
    (frontal,) = variantes_frontal(e, PRODUCTO, solo_apagada())
    assert codigos(frontal.estandarizada.alertas) == ["PANTALLA_CON_ELEMENTOS"]


def test_sin_wallpaper_la_variante_da_error(tmp_path):
    cfg = con_wallpapers(tmp_path)  # carpeta vacía
    frontal, con_wp = variantes_frontal(estandarizar(frontal_croma(), cfg), PRODUCTO, cfg)
    assert frontal.estandarizada is not None and not frontal.error
    assert con_wp.estandarizada is None and "no hay wallpaper" in con_wp.error


def test_wallpaper_por_producto_por_modelo_o_por_defecto(tmp_path):
    cfg = con_wallpapers(tmp_path)
    assert pantalla.buscar_wallpaper(PRODUCTO, cfg) is None
    for nombre in ("_default.jpg", "iPhone-15-Pro.png", f"{PRODUCTO}.webp"):  # cada uno más específico
        Image.new("RGB", (10, 20)).save(tmp_path / nombre)
        assert pantalla.buscar_wallpaper(PRODUCTO, cfg) == tmp_path / nombre
    assert pantalla.buscar_wallpaper("iPhone-15-Pro-Negro", cfg) == tmp_path / "iPhone-15-Pro.png"
    assert pantalla.buscar_wallpaper("iPhone-13-Rojo", cfg) == tmp_path / "_default.jpg"

    unico = tmp_path / "unico.jpg"
    Image.new("RGB", (10, 20)).save(unico)
    assert pantalla.buscar_wallpaper("Cualquier-Cosa", con_wallpapers(unico)) == unico


def test_wallpaper_anclado_arriba_conserva_la_parte_de_arriba(tmp_path):
    # Wallpaper mucho más alto que la pantalla: centrado se ve la mitad (morado); arriba, el rojo.
    wallpaper(tmp_path / "_default.png", medidas=(600, 4000))
    e = estandarizar(frontal_croma(), TRANSPARENTE)
    d = pantalla.detectar(e.maestro, TRANSPARENTE)
    x0, y0, x1, y1 = d.caja
    punto = (y0 + (y1 - y0) // 8, (x0 + x1) // 2)
    for ancla, rojo_mayor in (("centro", False), ("arriba", True)):
        cfg = con_wallpapers(tmp_path / "_default.png", ancla_wallpaper=ancla)
        px = np.asarray(pantalla.con_wallpaper(e.maestro, d, tmp_path / "_default.png", cfg)).astype(int)[punto]
        assert (px[0] > px[2] + 60) == rojo_mayor


@pytest.mark.parametrize("toml, clave", [
    ('[pantalla]\nvariantes = ["brillante"]\n', "pantalla.variantes"),
    ('[pantalla]\nvariantes = ["apagada", "apagada"]\n', "pantalla.variantes"),
    ('[pantalla]\ncolor_croma = "#808080"\n', "pantalla.color_croma"),
    ('[pantalla]\ntolerancia_tono = [40, 18]\n', "pantalla.tolerancia_tono"),
    ('[pantalla]\nvariantes = ["wallpaper"]\n', "portada.frontal"),  # la Portada pide la apagada
    ('[portada]\nsolape = 1.2\n', "portada.solape"),
    ('[portada]\nlado_trasera = "arriba"\n', "portada.lado_trasera"),
])
def test_config_de_pantalla_y_portada_valida_los_valores(tmp_path, toml, clave):
    ruta = tmp_path / "config.toml"
    ruta.write_text(toml, encoding="utf-8")
    with pytest.raises(ErrorConfig, match=clave):
        cargar_config(ruta)
