# Imágenes de producto (iPhone) — documento de referencia

> Brief autocontenido para seguir el proyecto `imagenes-producto` con otro asistente de IA.
> Estado al **2026-09-25**: las etapas 1 (Generar) y 2 (Estandarizar) están implementadas, con las variantes
> de pantalla de la Frontal y la Portada con solape configurable. Las etapas 3 (Revisar) y 4 (Publicar)
> están por definir; aquí va su diseño propuesto. Las specs de proveedores de IA y marketplaces se
> verificaron el 2026-09-24 contra documentación oficial (§12).

---

## 0. Instrucciones para la IA que lea esto

**Tu rol:** ingeniero/a de software en un pipeline que produce las imágenes de ficha de los iPhone de Reuse,
un ecommerce de tecnología reacondicionada (reuse.cl, reuse.mx, reuse.pe).

**El repo:**
- Python ≥3.11 (se usa 3.12 en `.venv`), paquete `src/imagenes_producto/` y comando `imgprod`.
- Pruebas con `pytest`. En GitHub corren en Linux y Windows con cada cambio.
- Dependencias: Pillow, numpy, scipy, rembg (BiRefNet), openai y python-dotenv.

**Reglas no negociables**

1. **La IA solo crea las vistas; la geometría la hace un script determinista.** No le pidas a un modelo
   generativo que centre, escale ni cambie una pantalla: redibuja la imagen y puede alterar el teléfono.
2. `pytest` pasa antes y después de tu cambio, y todo lo nuevo lleva pruebas. Corre también con
   `-W error::DeprecationWarning`: Pillow 13 (2026-10-15) quita el parámetro `mode` de `Image.fromarray`.
3. Todo parámetro nuevo va en `config.py` (dataclass `frozen`, con validación en `_validar`) y en
   `config.toml`, con un comentario. Las claves desconocidas son error de configuración a propósito.
4. Secretos solo en `.env` (`OPENAI_API_KEY`, `GEMINI_API_KEY`): nunca en código, logs ni commits.
5. **No gastes sin confirmación.** `imgprod generar` muestra el costo estimado y pide confirmación; no uses
   `--si` sin permiso explícito. Toda escritura en Shopify (etapa 4) se simula primero y se confirma.
6. No se versionan `generadas/`, `salida/` ni `muestras/` (ver `.gitignore`).
7. Código, mensajes y documentación en español. Mantén al día `README.md`, `config.toml` y este documento.

---

## 1. El problema

Antes, cada set de fotos se hacía a mano en ChatGPT: se enviaban los originales, referencias y un prompt; se
generaban 4 vistas; se pedía estandarizar centrado y tamaño; y se subía a Shopify ficha por ficha.

**Lo que se midió** (Shopify Reuse Chile, fichas de iPhone activas, 2026-09-24):

| Métrica | Valor |
|---|---|
| Fichas de iPhone activas | 673 |
| Combinaciones únicas modelo + color | **183** (45 modelos): las fotos se comparten entre capacidades y condiciones (3,7 fichas por combinación en promedio) |
| Tamaños de imagen distintos | **25**; el 36 % de las fichas mezcla tamaños en su propia galería |
| Fichas con el estándar `FichaFinalReuse` | 64 %: Portada/Trasera/Lateral/Frontal en 1000×1000 WebP, más un QR de Subtel |
| Imágenes subidas una vez por ficha | El QR de Subtel está subido 401 veces |

El trabajo real se hace por combinación, no por ficha. El catálogo completo son 183 × 3 = 549 imágenes de
IA; a ~US$0,062 cada una (costo real, §6), unos US$34.

---

## 2. Cómo funciona hoy

```
muestras/originales/<Modelo> <Color>.<ext>
   │
   └─ imgprod generar ──► generadas/<producto>/<Vista>.png + <Vista>.json        (Lateral, Frontal, Trasera)
         │                  la Frontal con la pantalla en verde croma si se pide wallpaper
         │
         └─ imgprod estandarizar ──► salida/<producto>/<producto>-Version-Final-<Vista>.webp
                                       Portada · Lateral · Frontal · Frontal-Wallpaper · Trasera
                                     + salida/<producto>/maestros/<Vista>.png   (recortes sin fondo)
                                     + salida/<producto>/reporte.json
               │
               └─ imgprod portada ──► recompone la Portada desde maestros/ (segundos, sin rehacer nada)
                  imgprod portada --solape 0.2,0.3 ──► salida/muestras-portada.jpg (no cambia imágenes)

[3 Revisar] y [4 Publicar en Shopify]: por definir (§8 y §9)
```

| # | Etapa | Estado |
|---|---|---|
| 1 | Generar: IA vía API, una llamada por vista | Implementada con OpenAI; probada con la API real en 2 productos |
| 2 | Estandarizar: recorte, escala, centrado, variantes de pantalla y Portada | Implementada; probada con imágenes sintéticas y reales |
| 3 | Revisar: control de fidelidad antes de publicar | Por definir |
| 4 | Publicar: subida a Shopify CL/MX/PE | Por definir |

### 2.1 Qué pasa por defecto

Con el `config.toml` del repo:

1. `generar` pide la Frontal con la pantalla en **verde croma `#00FF00`**, porque `pantalla.variantes` incluye
   `"wallpaper"`.
2. `estandarizar` deja **5 imágenes** por producto: Portada, Lateral, Frontal (apagada), Frontal-Wallpaper y
   Trasera. Son 1000×1000 WebP de máx. 50 KB, con el teléfono a 900 px de alto.
3. La **Portada** usa la Frontal **apagada**, con la trasera a la izquierda y detrás. El **solape es 0,1667**:
   la frontal tapa 1/6 de su ancho y de la trasera se ve 5/6. No hay desfase.
4. Los wallpapers se buscan en `estilo/wallpapers/`, que **hoy no tiene imágenes**. Sin wallpaper, la
   Frontal-Wallpaper no se genera (error) y el comando termina con código 1. Las otras 4 salen igual.
5. Las Frontales generadas antes del cambio tienen la pantalla negra, sin croma. Salen como Frontal con la
   alerta `SIN_PANTALLA_CROMA` y sin Frontal-Wallpaper, hasta regenerarlas.

---

## 3. Estructura del repo

```
config.toml               parámetros (los defaults están en src/imagenes_producto/config.py)
docs/                     pipeline.html (explicación) y este documento
estilo/                   receta fija de generación; se versiona porque cambiarla cambia todo el catálogo
├── prompts/              comun.md + lateral.md / frontal.md / trasera.md + pantalla-apagada.md / pantalla-croma.md
├── referencias/          una imagen de estilo por vista: *-Lateral, *-Frontal, *-Trasera (y *-Portada)
├── rasgos.toml           rasgos clave por modelo que se agregan al prompt (vacío por ahora)
└── wallpapers/           <producto>.jpg, <modelo>.jpg, _default.jpg (vacía por ahora)
generadas/                salida de la etapa 1 (no se versiona)
salida/                   salida de la etapa 2 (no se versiona)
muestras/                 originales de ejemplo y un wallpaper de ejemplo (no se versiona)
src/imagenes_producto/    código (tabla abajo)
tests/                    test_estandarizar.py, test_pantalla.py, test_generar.py
```

| Módulo | Responsabilidad | Piezas clave |
|---|---|---|
| `config.py` | Configuración inmutable y validada | `Lienzo`, `Producto`, `Portada`, `Pantalla`, `Sombra`, `Recorte`, `QuitarFondo`, `Salida`, `Alertas`, `Generar`, `Config`, `cargar_config`, `_validar`; `VISTAS`, `VISTAS_GENERADAS`, `VISTA_DE_VARIANTE` |
| `imagen.py` | Leer y escribir imágenes | `abrir` (sRGB + EXIF), `codificar`/`guardar` (busca la mayor calidad que cumple el peso), `buscar_imagenes` |
| `fondo.py` | Quitar el fondo | `Removedor`: usa el alfa si la imagen trae transparencia; si no, rembg con `birefnet-general` |
| `pantalla.py` | Pantalla croma de la Frontal | `detectar`, `apagada`, `con_wallpaper`, `buscar_wallpaper` |
| `estandarizar.py` | Etapa 2 | `extraer_maestro`, `componer`, `estandarizar`, `variantes_frontal`, `componer_portada`, `componer_portada_de`, `recomponer_portadas`, `muestras_portada`, `estandarizar_lote`, `identificar`, `Resultado` |
| `generar.py` | Etapa 1 | `ClienteOpenAI`, `armar_prompt`, `pantalla_croma`, `preparar_original`, `costo`, `estimar`, `generar_lote` |
| `cli.py` | Comando `imgprod` | `generar`, `estandarizar`, `portada` |

---

## 4. Convenciones

- **Original:** `<Modelo> <Color>.<ext>`, con el color como última palabra. `iPhone 12 Pro Plata.webp` da el
  producto `iPhone-12-Pro-Plata`, el modelo `iPhone 12 Pro` y el color `Plata`.
- **Modelo de un producto** (para buscar wallpaper): el producto sin la última palabra, p. ej. `iPhone-12-Pro`.
- **Vistas de la galería, en orden:** `Portada`, `Lateral`, `Frontal`, `Frontal-Wallpaper`, `Trasera`
  (`config.VISTAS`). La IA genera `Lateral`, `Frontal` y `Trasera`.
- **Variantes de pantalla:** `apagada` da la vista `Frontal`; `wallpaper` da `Frontal-Wallpaper`.
- **Nombre de salida:** `salida.nombre = "{producto}-Version-Final-{vista}"`.
- **Entrada de `estandarizar`:** ese mismo nombre, o la estructura `<producto>/<vista>.<ext>`.
- **`maestros/<Vista>.png`:** recorte transparente sin escalar. La Portada se compone desde aquí.
- **`reporte.json`:** una entrada por vista, con los campos de `Resultado`: `producto`, `vista`, `entrada`,
  `final`, `maestro`, `dimensiones_entrada`, `caja_producto`, `escala`, `metodo_fondo`, `wallpaper`,
  `calidad`, `peso_kb`, `alertas` y `error`. La Portada tiene `entrada = "compuesta: Trasera + <Frontal usada>"`.

---

## 5. Configuración (`config.toml`)

| Sección | Claves (default) |
|---|---|
| `[lienzo]` | `ancho` 1000, `alto` 1000, `fondo` "#FFFFFF" o "transparente" |
| `[producto]` | `altura` 0,90 (fracción del alto del lienzo), `ancho_max` 0,90 |
| `[portada]` | `solape` 0,1667, `desfase_vertical` 0, `adelante` "frontal", `lado_trasera` "izquierda", `frontal` "apagada" |
| `[pantalla]` | `variantes` ["apagada", "wallpaper"], `color_croma` "#00FF00", `tolerancia_tono` [18, 40], `wallpapers` "estilo/wallpapers", `ancla_wallpaper` "centro", `color_apagada` "#0B0B0F", `reflejo_apagada` 0,06 |
| `[sombra]` | `activa` true, `opacidad` 0,24, `base` 0,024, `desplazamiento` 0,004, `desenfoque_x` 0,029, `desenfoque_y` 0,018 (ajustadas a las referencias) |
| `[recorte]` | `umbral_alfa` 128, `objeto_min` 0,005, `rellenar_interior` true |
| `[quitar_fondo]` | `metodo` "auto" / "alfa" / "rembg", `modelo` "birefnet-general", `limpiar_bordes` true |
| `[salida]` | `formato` "webp", `calidad` 90, `calidad_min` 60, `peso_max_kb` 50, `nombre` "{producto}-Version-Final-{vista}" |
| `[generar]` | `proveedor` "openai", `modelo` "gpt-image-2.5-sunburst", `calidad` "high", `medidas` "1024x1536", `candidatos` 1, `fondo` "transparente", `vistas` ["Lateral", "Frontal", "Trasera"], rutas a prompts, referencias y rasgos, `original_min` 800, precios por millón de tokens |
| `[alertas]` | `ampliacion_max` 1,10, `margen_borde` 2, `diferencia_proporcion` 0,03 |

Validaciones relevantes:
- `portada.solape` va de −0,5 a 0,95 y `portada.desfase_vertical` de −0,5 a 0,5.
- `portada.frontal` tiene que estar en `pantalla.variantes`.
- `pantalla.color_croma` tiene que ser un color saturado (s ≥ 0,6 y v ≥ 0,5).
- `portada.visible_trasera` (clave vieja) da un error que explica cómo pasar a `solape`.

---

## 6. Etapa 1 — Generar · implementada (`generar.py`)

**Entrada:** originales (archivos o carpetas), `estilo/`, `OPENAI_API_KEY` en `.env`.
**Salida:** `generadas/<producto>/<Vista>.png` (las candidatas extra van como `<Vista>-2.png`, …) y
`<Vista>.json`, con producto, modelo, color, vista, original, referencia, proveedor, modelo de IA, calidad,
medidas, fondo, archivos, uso de tokens, `costo_usd`, segundos, fecha y el prompt completo.

**Qué se envía en cada llamada** (una por vista, `images.edit`):
1. El original, **recortado a su contenido**, en sRGB, en PNG y con un lado mayor de máx. 2048 px. Si el
   equipo ocupa menos de 800 px, alerta `ORIGINAL_CHICO`.
2. La referencia de estilo de la vista (`estilo/referencias/*-<Vista>.*`, exactamente una).
3. El prompt: `comun.md` + `<vista>.md`, con `{modelo}`, `{color}`, `{vista}`, `{fondo}` y `{rasgos}` (de
   `rasgos.toml`). `frontal.md` lleva además `{pantalla}`, que se reemplaza así:
   - `pantalla-croma.md` (con `{color_croma}`) si `pantalla.variantes` incluye `"wallpaper"`;
   - si no, `pantalla-apagada.md`.

   Si falta `{pantalla}` y se pide wallpaper, `generar` se detiene antes de gastar.

**Parámetros:** `model`, `size` = `medidas`, `quality`, `background` "transparent" u "opaque",
`output_format` "png" y `n` = `candidatos`.

**Costos:**
- El estimado previo es de ~US$0,21 por producto (3 vistas en calidad high).
- **El real medido es US$0,061–0,062 por vista**, con 24–35 s por llamada (`gpt-image-2.5-sunburst`, high,
  1024×1536). Queda en cada `.json`.

**Errores:** `ErrorGeneracion` (el lote sigue con la siguiente vista) y `ErrorFatal` (API key inválida,
acceso denegado, falta una referencia o falta `{pantalla}`: el lote se detiene).

Después de generar, `imgprod generar` estandariza lo generado, salvo con `--solo-generar`.

---

## 7. Etapa 2 — Estandarizar · implementada (`estandarizar.py`, `pantalla.py`)

### 7.1 Cada imagen

1. `imagen.abrir`: sRGB (convierte Display P3 y otros perfiles) y orientación EXIF.
2. `fondo.Removedor`: si la imagen trae transparencia, usa el alfa; si no, rembg con BiRefNet (MIT). La
   primera vez descarga 973 MB y tarda ~30–50 s por imagen en CPU. El default de rembg no es de uso comercial.
3. `extraer_maestro`:
   - borra manchas menores al 0,5 % del objeto principal (`VARIOS_OBJETOS` si queda más de uno);
   - rellena el interior (opaco al 100 %) y, si se segmentó, los huecos con el color original
     (`HUECOS_RELLENADOS` si superan el 0,1 %);
   - devuelve el contorno medido con alfa ≥ 128 (`TOCA_BORDE` si queda a ≤2 px del borde de la entrada).
4. `componer`:
   - escala para que el contorno mida `producto.altura × lienzo.alto` = 900 px, o menos si no cabe a lo ancho
     (`LIMITADO_POR_ANCHO`);
   - `AMPLIACION` si hay que ampliar más de 1,10×;
   - centra el contorno en el lienzo y agrega la sombra de contacto: la silueta aplastada contra el borde
     inferior y desenfocada, ajustada a las referencias;
   - `DESBORDA_LIENZO` si algo queda fuera.
5. `imagen.guardar`: WebP con la mayor calidad entre 60 y 90 que cumple 50 KB (búsqueda binaria);
   `PESO_EXCEDIDO` si ni con 60 alcanza. También guarda `maestros/<Vista>.png` y actualiza `reporte.json`.

### 7.2 La Frontal y sus variantes de pantalla

`variantes_frontal` trabaja sobre el recorte maestro de la Frontal:

1. **Detección** (`pantalla.detectar`), por tono y no por distancia de color, para tolerar el degradado que
   agregue la IA:
   - tono a menos de 18° del croma (ninguno sobre 40°, con rampa entremedio), saturación 0,25–0,45 y
     brillo 0,12–0,25 (rampas);
   - solo dentro del teléfono erosionado 1 px;
   - de ahí, el componente más grande.
2. **Huecos:** los menores al 0,1 % de la pantalla son ruido y se rellenan; los mayores son elementos (la
   Dynamic Island cuenta 1). Más de 1 da `PANTALLA_CON_ELEMENTOS`. El notch no es hueco, porque toca el marco.
3. **Cobertura:** si la pantalla cubre menos del 40 % del teléfono, no hay pantalla croma.
4. **Reemplazo:**
   - borde suave de 2 px y el interior al 100 %;
   - en la banda del borde se quita el tinte croma (desaturación proporcional);
   - solo cambian los píxeles de la pantalla: el alfa y el marco quedan iguales.
   - *apagada*: `color_apagada` con un reflejo diagonal (`reflejo_apagada`).
   - *wallpaper*: `buscar_wallpaper` (`<producto>` → `<modelo>` → `_default`; o un archivo único), escalado
     para cubrir la caja de la pantalla, anclado al centro o arriba.
5. **Composición:** cada variante pasa por `componer`, con la misma escala y el mismo centro que la Frontal.

| Caso | Frontal | Frontal-Wallpaper |
|---|---|---|
| Frontal con croma y wallpaper disponible | Se arma apagada | Se arma, y registra `wallpaper` en el reporte |
| Frontal con croma, sin wallpaper | Se arma apagada | Error: "no hay wallpaper para …" |
| Frontal sin croma, con `wallpaper` pedido | Tal cual, con alerta `SIN_PANTALLA_CROMA` | Error: "no trae la pantalla en color croma…" |
| Frontal sin croma, solo `apagada` pedida | Tal cual, sin alerta | No se pide |

### 7.3 La Portada

`componer_portada(trasera, frontal, cfg)`:
- Lleva los dos maestros al mismo alto (se reduce el más alto).
- Ubica los contornos: con la trasera a la izquierda, `x_trasera = 0` y
  `x_frontal = ancho_trasera − solape × ancho_adelante` (espejado a la derecha).
- `y_frontal − y_trasera = desfase_vertical × alto`.
- Pega primero el de atrás.
- `PROPORCIONES_DISTINTAS` si las proporciones difieren más de 3 %.
- Después el par pasa por `componer` como una vista más: 900 px de alto el par entero, así que con más
  desfase cada teléfono queda más chico.

`componer_portada_de` elige los maestros:
- la Trasera, y la Frontal de `portada.frontal`;
- si no está, la otra variante, con `PORTADA_CON_OTRA_FRONTAL`;
- si la corrida de `estandarizar` procesó la Frontal, solo considera las variantes guardadas en esa corrida,
  para no mezclarla con un maestro viejo de la otra variante.

`imgprod portada [CARPETA…]` recompone con `recomponer_portadas`. Con `--solape v1,v2,…`,
`muestras_portada` arma `muestras-portada.jpg` (una fila por producto y una columna por valor), sin tocar
nada más. Los valores se validan igual que en `config.toml`.

**Nota de migración:**
- Antes, `visible_trasera` fijaba la parte visible de la trasera; ahora `solape` fija cuánto la tapa la
  frontal.
- Con los dos teléfonos del mismo ancho es lo mismo: `solape = 1 − visible_trasera`.
- En las generadas reales, la IA dibujó trasera y frontal con ~2 % de diferencia de ancho, así que la frontal
  se corre 8–9 px de 1000.

### 7.4 Resultados de las pruebas

| Prueba | Resultado |
|---|---|
| Suite | 54 pruebas en verde (antes 28), también con `-W error::DeprecationWarning` |
| Regresión con datos reales (iPhone 13 Azul, 17 Pro Naranjo) | Lateral, Frontal y Trasera idénticas byte a byte a la versión anterior |
| Variantes sobre la Frontal real del 17 Pro con croma simulado | 5 imágenes sin alertas; Dynamic Island intacta; 0 píxeles croma restantes |
| Solape (pruebas unitarias) | La parte visible de la trasera es 1 − solape (±1 %) para 0, 1/6, 0,4 y −0,1; espejado y desfase correctos |
| Hoja de solapes 0,2–0,5 en 4 productos reales (12, 12 Pro, 13 y 17 Pro) | Hasta 0,3 se ven completos las cámaras y el logo en los cuatro; desde 0,4 la frontal tapa parte del logo en los cuatro |

---

## 8. Etapa 3 — Revisar · por definir (diseño propuesto)

**Objetivo:** que una persona apruebe cada vista antes de publicar. En reacondicionados, una foto con cámaras
o color equivocados termina en un reclamo de "no es lo que compré".

- **`imgprod revisar [salida/]`** (propuesto): una hoja de contacto con una fila por producto, las vistas en el
  orden de galería y las alertas de `reporte.json` en rojo. Opcional: el original al lado, para comparar.
- **Registro:** en `salida/<producto>/revision.json`, por vista: `estado` (aprobada | rechazada), `motivo` y
  `nota`. Motivos: modelo, cámaras, color, logo, botones, pantalla, texto o artefactos, recorte, otro.
- **Checklist:**
  - cantidad y disposición de cámaras, flash y LiDAR;
  - color;
  - logo;
  - botones: lado, cantidad, botón de acción y Control de Cámara en 16/17;
  - en las frontales, la isla o el notch intactos y sin restos de verde;
  - en la portada, el solape y el orden;
  - nada cortado, sin texto ni accesorios.
- **Regenerar solo lo rechazado:**
  - una vista de IA se regenera con `[generar] vistas = [...]` o con un filtro nuevo `--vista`;
  - una Frontal o Frontal-Wallpaper rechazada regenera la Frontal croma;
  - una Portada mal compuesta se corrige en `[portada]` + `imgprod portada`, sin la IA.

---

## 9. Etapa 4 — Publicar en Shopify · por definir (diseño propuesto)

**Objetivo:** subir cada imagen aprobada **una vez por tienda** y asociarla a todas las fichas de su
combinación modelo+color. Hoy la misma imagen se sube una vez por ficha.

**Fichas de una combinación.** Se consultan las fichas activas y se parsea cada título con esta expresión,
que reconoce 673/673 títulos:

```
^Apple iPhone (?P<modelo>.+?) (?:5G )?(?P<cap>\d+ ?(?:GB|TB)) (?P<color>.+?)\s*(?P<cond>Reacondicionado|Con Detalles|Open ?Box|Nuevo|Sellado)?$
```

**Operaciones GraphQL** (Admin API; validadas contra el schema de la tienda el 2026-09-24). Scopes:
`write_files`, `write_images`, `write_products`, `read_products` y `read_files`.

```graphql
mutation SubidaTemporal($input: [StagedUploadInput!]!) {
  stagedUploadsCreate(input: $input) {
    stagedTargets { url resourceUrl parameters { name value } }
    userErrors { field message }
  }
}
# $input = [{ resource: IMAGE, filename: "iPhone-16-Pro-Blanco-Version-Final-Trasera.webp", mimeType: "image/webp", httpMethod: POST }]
# luego: POST multipart a `url` con cada `parameters` + el archivo

mutation CrearArchivo($files: [FileCreateInput!]!) {
  fileCreate(files: $files) {
    files { id fileStatus alt }
    userErrors { field message code }
  }
}
# $files = [{ originalSource: <resourceUrl>, contentType: IMAGE, filename: "…", alt: "iPhone 16 Pro Blanco reacondicionado – vista trasera", duplicateResolutionMode: RAISE_ERROR }]

query EstadoArchivo($id: ID!) {
  node(id: $id) { ... on MediaImage { fileStatus image { width height url } } }
}
# repetir hasta READY (FAILED = error)

mutation AsociarAFichas($files: [FileUpdateInput!]!) {
  fileUpdate(files: $files) {
    files { id fileStatus }
    userErrors { field message code }
  }
}
# nueva:   [{ id: "gid://shopify/MediaImage/…", referencesToAdd: [<productos de la combinación>] }]
# antigua: [{ id: <foto antigua>, referencesToRemove: [<producto>] }]  (nunca las imágenes fijas)

mutation OrdenarGaleria($id: ID!, $moves: [MoveInput!]!) {
  productReorderMedia(id: $id, moves: $moves) {
    job { id done }
    mediaUserErrors { field message code }
  }
}
# $moves = [{ id: <media>, newPosition: "0" }, …]  (base 0; se aplican en secuencia)
```

**Reglas:**
- El orden de la galería es `config.VISTAS` y, al final, las imágenes fijas (QR de Subtel, sellos), que no se
  tocan.
- Nombres estables, sin UUID. No asumas que `REPLACE` conserva las asociaciones: verifícalo antes de usarlo.
- Cada tienda (CL, MX, PE) va con su propio token.
- Siempre primero `--simular` (qué entra, qué sale y el orden, por ficha); después, una combinación con
  confirmación; recién entonces, lotes.

---

## 10. Canales de venta (verificado el 2026-09-24)

Hoy el pipeline produce **un solo formato**: el de la ficha de Shopify (1000×1000 WebP ≤50 KB, producto al
90 % del alto). Para los marketplaces habría que exportar perfiles propios desde los maestros (pendiente).

| Canal | Tamaño | Producto ocupa | Fondo | Formato / peso | Imagen principal |
|---|---|---|---|---|---|
| Shopify | Hasta 5000 px; 2048² "usually displays best" | — | — | <20 MB | Misma relación de aspecto en todas |
| Mercado Libre | Mín. 500 px; 1200² recomendado | 95 %, sin márgenes | Blanco puro | JPG/PNG ≤10 MB | — |
| Amazon | Mín. 500 px; 1000+ para zoom | 85 % | RGB 255,255,255 | JPEG recomendado | **Producto una sola vez: nunca frente + trasera** |
| Falabella | 1500² óptimo | ~94 % | Blanco | Solo JPG | Spec de Colombia; la de Chile requiere login |
| Walmart México | 1000², máx. 3000² | ~90 % | Blanco | JPG/PNG ≤1 MB | **Debe ser una frontal** |
| Liverpool | Sin spec pública | — | — | — | — |

## 11. Proveedores de IA (verificado el 2026-09-24)

- **OpenAI** (en uso):
  - `gpt-image-2.5-sunburst` (ediciones precisas; el del config) y `gpt-image-2.5-flare`, lanzados el
    2026-09-08;
  - transparencia nativa, hasta 16 imágenes de entrada, tamaños libres en múltiplos de 16 (lado mayor
    ≤3840 px);
  - costo real: ~US$0,062 por vista.
  - `gpt-image-1` se apaga el 2026-10-23; `gpt-image-1.5` y `gpt-image-1-mini`, el 2026-12-01.
- **Google Gemini** (alternativa, sin implementar):
  - `gemini-3.1-flash-image` ("Nano Banana 2"), US$0,101 a 2K, y `gemini-3-pro-image`, US$0,134 a 2K; la
    mitad con Batch;
  - hasta 14 referencias;
  - sin transparencia documentada: habría que pedir blanco liso, y el pipeline lo resolvería con rembg.

---

## 12. Decisiones abiertas

1. **Wallpapers:** cuáles usar (uno por modelo o uno general) y con qué derechos de uso. Hasta que haya uno,
   no sale la Frontal-Wallpaper.
2. **Regenerar las Frontales existentes** con pantalla croma: ~US$0,06 por producto.
3. **Solape definitivo:** el default es 0,1667. En la hoja con 0,2–0,5, hasta 0,3 no se tapa el logo.
4. **Portada con la Frontal apagada o con wallpaper** (`portada.frontal`).
5. **Diseño de Revisar y Publicar** (§8 y §9).
6. **Perfiles para marketplaces**, y si Multivende toma un solo set o uno por canal.
7. **Formato de Shopify:** mantener 1000×1000 o subir a 2048×2048 (zoom). Implica generar a mayor resolución.

## 13. Riesgos

| Riesgo | Mitigación |
|---|---|
| La IA altera el equipo (cámaras, botones, color) | Revisión humana (etapa 3); el original como fuente de verdad en el prompt; `rasgos.toml` |
| La IA no respeta la pantalla croma (hora, íconos, degradado) | Alertas `SIN_PANTALLA_CROMA` y `PANTALLA_CON_ELEMENTOS`; subir `tolerancia_tono`; regenerar solo la Frontal |
| Deprecación del modelo | Modelo fijo en `config.toml`; revisar las deprecaciones de OpenAI |
| Escritura masiva errónea en Shopify | Simular, confirmar, una combinación primero, imágenes fijas protegidas |
| Cambio de Pillow 13 (2026-10-15) | Probar con `-W error::DeprecationWarning` |

## 14. Fuentes

- OpenAI — ChatGPT Images 2.5: https://openai.com/index/introducing-chatgpt-images-2-5/
- OpenAI — Generación de imágenes: https://developers.openai.com/api/docs/guides/image-generation
- OpenAI — Deprecaciones: https://developers.openai.com/api/docs/deprecations
- Google — Imágenes con Gemini: https://ai.google.dev/gemini-api/docs/image-generation
- Google — Precios de Gemini: https://ai.google.dev/gemini-api/docs/pricing
- Amazon México — Imágenes: https://sellercentral.amazon.com.mx/help/hub/reference/external/G1881
- Mercado Libre — Fotos: https://www.mercadolibre.cl/ayuda/805
- Falabella — Imágenes (Colombia): https://ayudaseller.falabella.com.co/s/article/Requisitos-de-las-imagenes
- Walmart México — Imágenes: https://marketplacelearn.walmart.com/mx/guides/Gesti%C3%B3n%20de%20Cat%C3%A1logo/Carga%20de%20art%C3%ADculos/lineamientos-de-las-im-genes
- Shopify — Media de producto: https://help.shopify.com/en/manual/products/product-media/product-media-types
- rembg: https://github.com/danielgatis/rembg
