import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw
from scipy import ndimage

from imagenes_producto.cli import main
from imagenes_producto.config import Config, ErrorConfig, Lienzo, Salida, Sombra, cargar_config
from imagenes_producto.estandarizar import (
    ErrorEstandarizacion,
    caja,
    componer,
    componer_portada,
    componer_portada_de,
    estandarizar,
    identificar,
)
from imagenes_producto.imagen import abrir, codificar

RAIZ = Path(__file__).parents[1]
CFG = Config()
# Con fondo transparente el canal alfa del resultado muestra dónde quedó el producto.
TRANSPARENTE = replace(CFG, lienzo=Lienzo(fondo="transparente"))
ALTO_ESPERADO = CFG.producto.altura * CFG.lienzo.alto
CENTRO = (CFG.lienzo.ancho / 2, CFG.lienzo.alto / 2)
AZUL, ROJO = (40, 60, 160, 255), (160, 40, 40, 255)


def telefono(lienzo, rect, color=(30, 30, 30, 255)):
    """Rectángulo redondeado con bordes suavizados (se dibuja al doble y se reduce) sobre fondo transparente."""
    k = 2
    grande = Image.new("RGBA", (lienzo[0] * k, lienzo[1] * k), (0, 0, 0, 0))
    x0, y0, x1, y1 = (v * k for v in rect)
    radio = min(x1 - x0, y1 - y0) // 8
    ImageDraw.Draw(grande).rounded_rectangle((x0, y0, x1 - 1, y1 - 1), radius=radio, fill=color)
    return grande.resize(lienzo, Image.Resampling.LANCZOS)


def frontal_croma(lienzo=(1000, 1500), rect=(200, 100, 700, 1300), color_pantalla=(0, 255, 0), hora=False):
    """Frontal como la entrega la IA con `pantalla.variantes` que incluye wallpaper: marco negro, pantalla en
    color croma (con un leve degradado vertical, como sale de la IA) y Dynamic Island. Con `hora`, la IA dibujó
    además la hora y unos íconos sobre la pantalla."""
    im = telefono(lienzo, rect, color=(20, 20, 24, 255))
    x0, y0, x1, y1 = rect
    m = (x1 - x0) // 20  # marco
    mascara = telefono(lienzo, (x0 + m, y0 + m, x1 - m, y1 - m)).getchannel("A")
    degradado = np.linspace(1.0, 0.85, lienzo[1])[:, None, None] * np.array(color_pantalla, float)
    im.paste(Image.fromarray(np.broadcast_to(degradado, (lienzo[1], lienzo[0], 3)).astype(np.uint8)), (0, 0), mascara)
    dibujo, cx, arriba = ImageDraw.Draw(im), (x0 + x1) // 2, y0 + m
    dibujo.rounded_rectangle((cx - 60, arriba + 20, cx + 60, arriba + 55), radius=17, fill=(0, 0, 0, 255))
    if hora:
        dibujo.rounded_rectangle((x0 + m + 30, arriba + 25, x0 + m + 110, arriba + 50), radius=6, fill=(250, 250, 250, 255))
        for i in range(4):
            izq = x0 + m + 30 + i * 110
            dibujo.rounded_rectangle((izq, arriba + 150, izq + 80, arriba + 230), radius=18, fill=(255, 190, 60, 255))
    return im


def wallpaper(ruta, arriba=(200, 40, 40), abajo=(40, 40, 200), medidas=(600, 1300)):
    """Wallpaper de mentira: degradado vertical de rojo (arriba) a azul (abajo)."""
    t = np.linspace(0, 1, medidas[1])[:, None, None]
    px = np.array(arriba, float) * (1 - t) + np.array(abajo, float) * t
    Image.fromarray(np.broadcast_to(px, (medidas[1], medidas[0], 3)).astype(np.uint8)).save(ruta)
    return ruta


def medir(final):
    """Ancho, alto y centro del producto en la imagen final."""
    x0, y0, x1, y1 = caja(np.asarray(final.getchannel("A")) >= 128)
    return x1 - x0, y1 - y0, (x0 + x1) / 2, (y0 + y1) / 2


def centrado(cx, cy):
    return abs(cx - CENTRO[0]) <= 1 and abs(cy - CENTRO[1]) <= 1


def codigos(resultado):
    return [a.codigo for a in resultado.alertas]


def test_misma_altura_y_centro_en_todas_las_vistas():
    # Frente descentrado, lateral angosto y trasera grande, en imágenes de distinto tamaño.
    vistas = [
        telefono((1200, 1600), (100, 300, 700, 1500)),
        telefono((800, 1000), (500, 50, 540, 850)),
        telefono((2000, 2600), (1000, 100, 1900, 2500)),
    ]
    for im in vistas:
        final = estandarizar(im, TRANSPARENTE).final
        assert final.size == (CFG.lienzo.ancho, CFG.lienzo.alto)
        _, alto, cx, cy = medir(final)
        assert abs(alto - ALTO_ESPERADO) <= 1.5
        assert centrado(cx, cy)


def test_alerta_de_ampliacion_solo_si_hay_que_ampliar():
    chico = estandarizar(telefono((1000, 1000), (300, 200, 600, 800)), TRANSPARENTE)
    assert chico.escala == pytest.approx(ALTO_ESPERADO / 600)
    assert "AMPLIACION" in codigos(chico)

    grande = estandarizar(telefono((1500, 2400), (400, 100, 1300, 2300)), TRANSPARENTE)
    assert grande.escala < 1
    assert "AMPLIACION" not in codigos(grande)


def test_alerta_si_el_producto_toca_el_borde():
    assert "TOCA_BORDE" in codigos(estandarizar(telefono((600, 800), (0, 100, 300, 700)), TRANSPARENTE))
    assert "TOCA_BORDE" not in codigos(estandarizar(telefono((600, 800), (100, 100, 400, 700)), TRANSPARENTE))


def test_producto_muy_ancho_se_limita_por_ancho():
    r = estandarizar(telefono((1000, 1000), (50, 400, 950, 600)), TRANSPARENTE)
    ancho, _, cx, cy = medir(r.final)
    assert "LIMITADO_POR_ANCHO" in codigos(r)
    assert abs(ancho - CFG.producto.ancho_max * CFG.lienzo.ancho) <= 1.5
    assert centrado(cx, cy)


def test_manchas_sueltas_se_borran_y_objetos_extra_se_alertan():
    base = telefono((1000, 1400), (200, 100, 800, 1300))

    con_mancha = base.copy()
    con_mancha.paste((30, 30, 30, 255), (950, 20, 953, 23))  # 3×3 px lejos del teléfono
    manchado = estandarizar(con_mancha, TRANSPARENTE)
    assert manchado.caja_producto == estandarizar(base, TRANSPARENTE).caja_producto
    assert "VARIOS_OBJETOS" not in codigos(manchado)

    con_objeto = base.copy()
    con_objeto.paste((30, 30, 30, 255), (850, 20, 950, 120))  # 100×100 px: ~1,4 % del teléfono
    assert "VARIOS_OBJETOS" in codigos(estandarizar(con_objeto, TRANSPARENTE))


def test_imagen_sin_producto_da_error():
    with pytest.raises(ErrorEstandarizacion):
        estandarizar(Image.new("RGBA", (100, 100), (0, 0, 0, 0)), TRANSPARENTE)


def test_bordes_sin_halo_sobre_fondo_blanco():
    # Teléfono blanco; los píxeles transparentes traen color basura, como pasa en recortes reales.
    im = np.array(telefono((500, 900), (100, 100, 400, 800), color=(255, 255, 255, 255)))
    im[im[..., 3] == 0, :3] = (255, 0, 0)
    sin_sombra = replace(CFG, sombra=Sombra(activa=False))
    final = estandarizar(Image.fromarray(im), sin_sombra).final
    assert np.asarray(final.convert("RGB")).min() >= 250  # ni halo oscuro ni rojizo


def test_sombra_bajo_el_producto_e_identica_entre_imagenes():
    # El mismo teléfono en dos imágenes de distinto tamaño y posición da la misma imagen final, sombra incluida.
    a = estandarizar(telefono((1200, 1600), (100, 300, 700, 1500)), CFG).final
    b = estandarizar(telefono((700, 1000), (380, 50, 680, 650)), CFG).final
    diferencia = np.abs(np.asarray(a.convert("L")).astype(int) - np.asarray(b.convert("L")).astype(int))
    assert diferencia.mean() < 0.5

    gris = np.asarray(a.convert("L")).astype(int)
    abajo = round(CENTRO[1] + ALTO_ESPERADO / 2)  # borde inferior del teléfono
    columna = round(CENTRO[0])
    assert 15 <= 255 - gris[abajo + 6, columna] <= 40  # sombra suave justo debajo, como en las referencias
    assert 255 - gris[abajo + 45, columna] <= 2  # y se desvanece en ~40 px
    assert (gris[: round(CENTRO[1] - ALTO_ESPERADO / 2) - 2] == 255).all()  # nada encima del teléfono


def test_interior_opaco_y_huecos_rellenados_con_el_color_original():
    original = telefono((600, 1000), (100, 100, 500, 900), color=AZUL)  # el teléfono tal como se ve
    recorte = np.array(original)
    recorte[recorte[..., 3] == 255, 3] = 230  # la segmentación dejó el cuerpo algo transparente…
    recorte[400:500, 250:350, 3] = 0  # …y con un hueco de 100×100 px (~3 % del teléfono)

    r = estandarizar(Image.fromarray(recorte), TRANSPARENTE, original=original)
    assert "HUECOS_RELLENADOS" in codigos(r)
    px = np.asarray(r.maestro).astype(int)
    interior = ndimage.binary_erosion(px[..., 3] >= 128, iterations=5)
    assert px[interior, 3].min() == 255
    assert np.abs(px[interior, :3] - AZUL[:3]).max() <= 1  # el hueco quedó con el color real

    # Sin la imagen original no hay con qué rellenar el hueco: queda transparente y no se alerta.
    sin_original = estandarizar(Image.fromarray(recorte), TRANSPARENTE)
    assert "HUECOS_RELLENADOS" not in codigos(sin_original)
    alfa = np.asarray(sin_original.maestro.getchannel("A"))
    x0, y0, x1, y1 = caja(alfa >= 128)
    assert (alfa[y0:y1, x0:x1] == 0).sum() > 10_000


def test_portada_trasera_detras_con_cinco_sextos_visibles():
    trasera = estandarizar(telefono((700, 1300), (50, 100, 530, 1100), color=ROJO), CFG).maestro
    frontal = estandarizar(telefono((900, 1500), (300, 200, 780, 1200), color=AZUL), CFG).maestro
    par, alertas = componer_portada(trasera, frontal, CFG)
    final, _, alertas_lienzo = componer(par, TRANSPARENTE)
    assert not alertas + alertas_lienzo

    _, alto, cx, cy = medir(final)
    assert abs(alto - ALTO_ESPERADO) <= 1.5
    assert centrado(cx, cy)

    px = np.asarray(final).astype(int)
    opaco = px[..., 3] >= 128
    columnas_frontal = np.flatnonzero((opaco & (px[..., 2] > px[..., 0] + 50)).any(axis=0))
    izquierda_trasera = np.flatnonzero(opaco.any(axis=0))[0]
    ancho_frontal = columnas_frontal[-1] - columnas_frontal[0] + 1
    visible = columnas_frontal[0] - izquierda_trasera
    assert visible / ancho_frontal == pytest.approx(5 / 6, abs=0.01)


def par_de_prueba(**portada):
    """Trasera roja y frontal azul del mismo ancho (480 × 1000), y el par de la Portada con esos parámetros."""
    trasera = estandarizar(telefono((700, 1300), (50, 100, 530, 1100), color=ROJO), CFG).maestro
    frontal = estandarizar(telefono((900, 1500), (300, 200, 780, 1200), color=AZUL), CFG).maestro
    cfg = replace(TRANSPARENTE, portada=replace(CFG.portada, **portada))
    par, alertas = componer_portada(trasera, frontal, cfg)
    px = np.asarray(par).astype(int)
    opaco = px[..., 3] >= 128
    rojo, azul = opaco & (px[..., 0] > px[..., 2] + 50), opaco & (px[..., 2] > px[..., 0] + 50)
    return par, alertas, rojo, azul


def extension(mascara, eje):
    """Primera y última fila (eje 1) o columna (eje 0) de la máscara."""
    indices = np.flatnonzero(mascara.any(axis=eje))
    return indices[0], indices[-1] + 1


@pytest.mark.parametrize("solape", [0.0, 1 / 6, 0.4, -0.1])
def test_portada_con_solape_configurable(solape):
    _, alertas, rojo, azul = par_de_prueba(solape=solape)
    assert not alertas
    (r0, r1), (a0, a1) = extension(rojo, 0), extension(azul, 0)
    # La frontal (adelante) se ve entera: de la trasera se ve 1 − solape del ancho de la frontal (con
    # solape negativo, queda un espacio entre las dos).
    assert (a0 - r0) / (a1 - a0) == pytest.approx(1 - solape, abs=0.01)


def test_portada_espejada_con_la_trasera_adelante():
    par, _, rojo, azul = par_de_prueba(solape=0.3, lado_trasera="derecha", adelante="trasera")
    # Se mide a media altura: en las esquinas redondeadas de la de adelante asoma la de atrás.
    medio = slice(par.height // 2 - 50, par.height // 2 + 50)
    (r0, r1), (a0, a1) = extension(rojo[medio], 0), extension(azul[medio], 0)
    assert a0 < r0 and par.width - r1 <= 3  # la frontal a la izquierda y la trasera a la derecha
    # La trasera tapa a la frontal: lo que se ve de la frontal termina donde empieza la trasera (±el borde suave)…
    assert abs(a1 - r0) <= 2
    assert (r1 - r0 - (a1 - a0)) / (r1 - r0) == pytest.approx(0.3, abs=0.01)  # …y le tapa el 30 % del ancho


def test_portada_con_desfase_vertical():
    _, _, rojo, azul = par_de_prueba(desfase_vertical=0.1)
    (r0, _), (a0, a1) = extension(rojo, 1), extension(azul, 1)
    assert (a0 - r0) / (a1 - a0) == pytest.approx(0.1, abs=0.01)  # la frontal baja un 10 % de su alto


def test_portada_usa_la_otra_frontal_si_falta_la_pedida(tmp_path):
    maestros = tmp_path / "iPhone-Prueba-Azul" / "maestros"
    maestros.mkdir(parents=True)
    estandarizar(telefono((700, 1300), (50, 100, 530, 1100)), CFG).maestro.save(maestros / "Trasera.png")
    estandarizar(telefono((700, 1300), (50, 100, 530, 1100)), CFG).maestro.save(maestros / "Frontal.png")
    cfg = replace(CFG, portada=replace(CFG.portada, frontal="wallpaper"))
    r = componer_portada_de(tmp_path / "iPhone-Prueba-Azul", cfg)
    assert r.entrada == "compuesta: Trasera + Frontal" and codigos(r) == ["PORTADA_CON_OTRA_FRONTAL"]
    assert (tmp_path / "iPhone-Prueba-Azul" / "iPhone-Prueba-Azul-Version-Final-Portada.webp").exists()


def test_portada_alerta_si_frontal_y_trasera_no_tienen_la_misma_proporcion():
    trasera = estandarizar(telefono((700, 1300), (50, 100, 530, 1100)), CFG).maestro  # 480 × 1000
    frontal = estandarizar(telefono((700, 1300), (50, 100, 590, 1100)), CFG).maestro  # 540 × 1000
    _, alertas = componer_portada(trasera, frontal, CFG)
    assert [a.codigo for a in alertas] == ["PROPORCIONES_DISTINTAS"]


def test_webp_baja_la_calidad_hasta_cumplir_el_peso():
    ruido = Image.fromarray(np.random.default_rng(0).integers(0, 256, (300, 300, 3), dtype=np.uint8))
    peso = {q: len(codificar(ruido, "webp", q)[0]) for q in (60, 90)}

    limite = (peso[60] + peso[90]) // 2
    datos, calidad = codificar(ruido, "webp", 90, 60, limite)
    assert len(datos) <= limite and 60 <= calidad < 90

    datos, calidad = codificar(ruido, "webp", 90, 60, peso[60] - 1)  # ni con la calidad mínima alcanza
    assert calidad == 60 and len(datos) == peso[60]


def test_identificar_producto_y_vista():
    assert identificar(Path("x/iPhone-12-Azul-Version-Final-Frontal.webp"), CFG) == ("iPhone-12-Azul", "Frontal")
    assert identificar(Path("iPhone-12-Azul/trasera.png"), CFG) == ("iPhone-12-Azul", "Trasera")
    assert identificar(Path("carpeta/foto 1.png"), CFG) == ("carpeta", "foto 1")


def test_jpg_con_fondo_transparente_es_error_de_config():
    with pytest.raises(ErrorConfig):
        Config(lienzo=Lienzo(fondo="transparente"), salida=Salida(formato="jpg"))


def test_config_parcial_y_claves_desconocidas(tmp_path):
    ruta = tmp_path / "config.toml"
    ruta.write_text("[lienzo]\nancho = 800\n", encoding="utf-8")
    cfg = cargar_config(ruta)
    assert cfg.lienzo.ancho == 800 and cfg.lienzo.alto == CFG.lienzo.alto

    ruta.write_text("[producto]\naltrua = 0.8\n", encoding="utf-8")
    with pytest.raises(ErrorConfig, match="altrua"):
        cargar_config(ruta)


def test_config_con_tipos_equivocados_da_mensaje_claro(tmp_path):
    ruta = tmp_path / "config.toml"
    ruta.write_text('[producto]\naltura = "0.9"\n', encoding="utf-8")
    with pytest.raises(ErrorConfig, match="producto.altura"):
        cargar_config(ruta)


def test_config_del_repo_es_valida():
    cargar_config(RAIZ / "config.toml")


def test_config_con_visible_trasera_explica_como_migrar(tmp_path):
    ruta = tmp_path / "config.toml"
    ruta.write_text("[portada]\nvisible_trasera = 0.8333\n", encoding="utf-8")
    with pytest.raises(ErrorConfig, match="portada.solape"):
        cargar_config(ruta)


P3 = Path("/System/Library/ColorSync/Profiles/Display P3.icc")


@pytest.mark.skipif(not P3.exists(), reason="requiere el perfil Display P3 de macOS")
def test_abrir_convierte_display_p3_a_srgb(tmp_path):
    ruta = tmp_path / "p3.png"
    Image.new("RGBA", (10, 10), (200, 100, 50, 128)).save(ruta, icc_profile=P3.read_bytes())
    im = abrir(ruta)
    r, g, b, a = im.getpixel((5, 5))
    assert a == 128  # la transparencia se conserva
    assert r > 200 and b < 50  # el mismo color en sRGB necesita valores más saturados
    assert "icc_profile" not in im.info


def producto_de_prueba(tmp_path, con_wallpaper=True):
    """Entrada de un producto como la deja la etapa 1 (Frontal con pantalla croma), y el wallpaper por defecto."""
    carpeta = tmp_path / "entrada" / "iPhone-Prueba-Azul"
    carpeta.mkdir(parents=True)
    frontal_croma().save(carpeta / "frontal.png")
    telefono((1000, 1500), (250, 150, 750, 1350)).save(carpeta / "trasera.png")
    telefono((600, 1500), (280, 100, 320, 1300)).save(carpeta / "lateral.png")
    if con_wallpaper:
        (tmp_path / "estilo" / "wallpapers").mkdir(parents=True)
        wallpaper(tmp_path / "estilo" / "wallpapers" / "_default.png")
    return tmp_path / "salida" / "iPhone-Prueba-Azul"


def test_cli_compone_la_portada_y_nombra_como_el_catalogo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # sin config.toml: defaults
    producto = producto_de_prueba(tmp_path)

    assert main(["estandarizar", "entrada", "-s", "salida"]) == 0

    vistas = ["Portada", "Lateral", "Frontal", "Frontal-Wallpaper", "Trasera"]
    for vista in vistas:
        ruta = producto / f"iPhone-Prueba-Azul-Version-Final-{vista}.webp"
        assert ruta.stat().st_size <= CFG.salida.peso_max_kb * 1000
        with Image.open(ruta) as final:
            assert final.format == "WEBP" and final.size == (CFG.lienzo.ancho, CFG.lienzo.alto)
        assert (producto / "maestros" / f"{vista}.png").exists()
    reporte = json.loads((producto / "reporte.json").read_text(encoding="utf-8"))
    assert list(reporte) == vistas
    assert reporte["Portada"]["entrada"] == "compuesta: Trasera + Frontal"
    assert Path(reporte["Frontal-Wallpaper"]["wallpaper"]).name == "_default.png"
    assert all(not r["alertas"] and not r["error"] for r in reporte.values())


def test_cli_portada_compara_solapes_sin_tocar_nada_y_aplica_el_de_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    producto = producto_de_prueba(tmp_path)
    assert main(["estandarizar", "entrada", "-s", "salida"]) == 0
    portada = producto / "iPhone-Prueba-Azul-Version-Final-Portada.webp"
    antes = portada.read_bytes()

    assert main(["portada", "salida", "--solape", "0,0.4"]) == 0
    with Image.open(tmp_path / "salida" / "muestras-portada.jpg") as hoja:
        assert hoja.width > hoja.height  # 1 producto × 2 solapes
    assert portada.read_bytes() == antes  # comparar no cambia ninguna imagen

    (tmp_path / "config.toml").write_text("[portada]\nsolape = 0.4\nfrontal = \"wallpaper\"\n", encoding="utf-8")
    assert main(["portada"]) == 0  # default: salida/
    assert portada.read_bytes() != antes
    reporte = json.loads((producto / "reporte.json").read_text(encoding="utf-8"))
    assert reporte["Portada"]["entrada"] == "compuesta: Trasera + Frontal-Wallpaper"

    assert main(["portada", "salida", "--solape", "2"]) == 2  # fuera de rango
    assert main(["portada", "entrada"]) == 2  # sin recortes maestros
