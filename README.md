# Imágenes de producto

Pipeline para generar las imágenes de producto de Reuse (reuse.cl, reuse.mx, reuse.pe) a partir de las fotos originales de cada equipo.

Regla de diseño: **la IA solo crea las vistas; la geometría la hace un script.** Pedirle a un modelo generativo que centre o escale obliga a redibujar la imagen completa, y en ese redibujo puede cambiar el teléfono (cámaras, color, botones). Un error de ficha termina en cambios y devoluciones, así que la fidelidad al equipo real pesa más que la estética.

## Etapas

| # | Etapa | Qué hace | Estado |
|---|---|---|---|
| 1 | Generar | IA vía API (OpenAI o Gemini, por decidir tras compararlos): una llamada por vista (Lateral, Frontal, Trasera), con prompt fijo y siempre la misma referencia de estilo. | Pendiente |
| 2 | Estandarizar | Script determinista: quita el fondo, guarda un recorte maestro transparente, escala el teléfono a una altura fija, lo centra en un lienzo fijo y compone la Portada. Alerta ante problemas de recorte, tamaño o peso. | Implementada; validada solo con imágenes sintéticas |
| 3 | Revisar | Control de fidelidad contra el original antes de publicar. | Por definir |
| 4 | Publicar | Subida a las tiendas Shopify. | Por definir |

Cada producto lleva 4 imágenes, en este orden: **Portada, Lateral, Frontal y Trasera**. La Portada no se genera con IA: se compone con los recortes de la Trasera (a la izquierda y detrás) y la Frontal (a la derecha y delante), del mismo alto; de la trasera se ve 5/6 del ancho.

Decisiones vigentes:

- **Pantallas apagadas** (negras) en todas las vistas, incluida la Portada. Más adelante se puede pasar a wallpaper si hace falta.
- **Altura de 900 px** en todas las vistas y modelos. El prompt pide ~903 px en la Portada; las referencias van de 873 a 900.

## Instalación

Requiere Python 3.11 o superior. Funciona en macOS, Windows y Linux; solo cambia cómo se crea y se activa el entorno.

macOS / Linux:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Windows (PowerShell), con Python instalado desde python.org o con `winget install Python.Python.3.12`:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Si PowerShell no deja ejecutar el script de activación, corre antes `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. Con el entorno activo, el comando `imgprod` y `pytest` funcionan igual en los tres sistemas.

### Claves de API

La etapa 1 (generar) usa APIs de pago. Copia `.env.example` como `.env` y completa las claves. `.env` no se sube a GitHub.

- `OPENAI_API_KEY`: clave de la **plataforma de API** de OpenAI (platform.openai.com), no de ChatGPT. La suscripción a ChatGPT no incluye la API: se paga aparte, con crédito prepagado. Conviene crear un proyecto propio para este pipeline, con un límite de gasto mensual.
- `GEMINI_API_KEY`: opcional, para comparar con Gemini (Google AI Studio).

## Uso

### Estandarizar

```bash
imgprod estandarizar carpeta/
```

Acepta imágenes con el nombre del catálogo (`iPhone-12-Azul-Version-Final-Frontal.webp`) o con la estructura `<producto>/<vista>.<ext>` (png, jpg o webp):

```
generadas/
└── iPhone-12-Azul/
    ├── lateral.png
    ├── frontal.png
    └── trasera.png
```

La salida queda en `salida/` (se cambia con `-s`; no se versiona):

```
salida/iPhone-12-Azul/
├── iPhone-12-Azul-Version-Final-Portada.webp   compuesta con la Trasera y la Frontal
├── iPhone-12-Azul-Version-Final-Lateral.webp
├── iPhone-12-Azul-Version-Final-Frontal.webp
├── iPhone-12-Azul-Version-Final-Trasera.webp
├── maestros/                                   recortes transparentes sin escalar (PNG): permiten recomponer sin volver a generar
└── reporte.json                                medidas, escala, calidad, peso y alertas de cada vista
```

Cada imagen final mide 1000×1000, con el teléfono (o el par, en la Portada) a 900 px de alto y centrado, y con la misma sombra de contacto suave. Pesa como máximo 50 KB: se usa la mayor calidad WEBP que cumple ese peso. La altura y la sombra salen de medir las referencias de `estilo/referencias/`.

#### Por qué se quita el fondo

Para medir y mover el teléfono, el script necesita saber qué píxeles son teléfono y cuáles fondo. Medir "todo lo que no es blanco" no sirve por tres motivos:

- la sombra que dibuja la IA se contaría como parte del teléfono;
- un equipo blanco o plata sobre fondo blanco casi no se distingue del fondo;
- para componer la Portada hay que superponer los recortes, no imágenes con fondo.

Por eso cada imagen pasa primero a un recorte con fondo transparente:

- Si la imagen ya trae transparencia (la API de OpenAI puede entregarla), se usa tal cual.
- Si no (Gemini, o las imágenes que hoy salen de ChatGPT), la librería [rembg](https://github.com/danielgatis/rembg) lo hace con BiRefNet, un modelo de segmentación con licencia MIT. El modelo que rembg trae por defecto no permite uso comercial.
  - La primera vez descarga el modelo: 973 MB, en `~/.rembg/`.
  - En CPU tarda ~30–50 s por imagen.

La sombra de la IA se descarta con el fondo, y el script dibuja la suya, igual en todas las vistas.

Alertas (no detienen el proceso: quedan en pantalla y en `reporte.json`):

| Código | Significa |
|---|---|
| `AMPLIACION` | El producto es más chico que la altura final: hay que ampliarlo y puede verse borroso. |
| `TOCA_BORDE` | El producto toca el borde de la imagen de entrada: puede estar cortado. |
| `VARIOS_OBJETOS` | Hay más de un objeto en la imagen. |
| `HUECOS_RELLENADOS` | El recorte tenía huecos dentro del producto y se rellenaron con la imagen original: revisar el recorte. |
| `PROPORCIONES_DISTINTAS` | La Frontal y la Trasera no tienen la misma proporción ancho/alto: una de las dos puede estar deformada. |
| `LIMITADO_POR_ANCHO` | El producto es tan ancho que no cabe a la altura estándar y quedó más bajo. |
| `DESBORDA_LIENZO` | Parte del recorte (halo o sombra) quedó fuera del lienzo. |
| `PESO_EXCEDIDO` | Ni con la calidad mínima se llega al peso máximo. |

## Configuración

Todo se ajusta en [`config.toml`](config.toml): lienzo, altura del producto, composición de la Portada, sombra, formato, peso, nombre de archivo y umbrales de las alertas. Los defaults están en `src/imagenes_producto/config.py`.

## Estructura

```
config.toml               parámetros del pipeline
estilo/                   receta fija de generación: prompt y referencias de estilo (se versiona)
muestras/                 material de ejemplo del proceso actual (no se versiona)
src/imagenes_producto/    código: config, imagen (lectura y escritura), fondo, estandarizar, cli
tests/                    pruebas
```

## Pruebas

```bash
pytest
```

En GitHub, las pruebas corren solas en Linux y Windows con cada cambio (`.github/workflows/pruebas.yml`).
