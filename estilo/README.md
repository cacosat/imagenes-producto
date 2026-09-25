# Estilo

Receta fija de generación: lo que se le envía siempre igual a la IA.

- `prompt-actual.md`: el prompt que se usa hoy en ChatGPT. Es el punto de partida de los prompts por vista.
- `prompts/`: lo que la etapa 1 envía a la IA. Se arma con `comun.md` (jerarquía de referencias, fidelidad y presentación) más el archivo de la vista (`lateral.md`, `frontal.md`, `trasera.md`). Admite `{modelo}`, `{color}`, `{vista}`, `{fondo}` y `{rasgos}`; no uses otras llaves. El tamaño, el formato, el peso, los nombres y la Portada no van en el prompt, porque los resuelve el script.
  - `frontal.md` lleva además `{pantalla}`, que se reemplaza por `pantalla-croma.md` si `pantalla.variantes` incluye `"wallpaper"`, o por `pantalla-apagada.md` si no. `pantalla-croma.md` admite `{color_croma}`. Sin `{pantalla}`, `imgprod generar` se detiene antes de gastar.
- `wallpapers/`: los wallpapers de la Frontal-Wallpaper (ver su README).
- `referencias/`: una imagen de estilo por vista, con nombre terminado en `-Lateral`, `-Frontal` o `-Trasera`. Cada llamada envía la de su vista.
- `rasgos.toml`: rasgos clave por modelo, confirmados con la ficha oficial, que se agregan al prompt. Está vacío por ahora.

Esta carpeta se versiona, porque cambiar una referencia o el prompt cambia el resultado de todo el catálogo.
