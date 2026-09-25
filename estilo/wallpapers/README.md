# Wallpapers

Wallpapers para la variante `Frontal-Wallpaper`. Para cada producto se busca, en orden:

1. `<producto>.jpg`: `iPhone-15-Pro-Azul.jpg`, solo para ese color.
2. `<modelo>.jpg`: `iPhone-15-Pro.jpg`, para todos los colores del modelo. El modelo es el producto sin la última palabra (el color).
3. `_default.jpg`: para todo lo demás.

También sirven `.png` y `.webp`. El wallpaper se escala para cubrir la pantalla sin deformarse. Si no calza, se recorta desde el centro o desde arriba, según `pantalla.ancla_wallpaper`. Conviene que sea vertical, al menos de 1290 × 2796 px (la pantalla de un iPhone Pro Max).

Si no hay ninguno, la `Frontal-Wallpaper` de ese producto no se genera y el comando lo informa.

Esta carpeta se versiona como el resto de `estilo/`: cambiar un wallpaper cambia las fichas de todo el catálogo. Usa solo imágenes con derecho de uso comercial.
