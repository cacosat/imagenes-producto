"""Quitar el fondo: usar la transparencia que ya trae la imagen o segmentar con rembg."""

from __future__ import annotations

from PIL import Image

from .config import QuitarFondo


class ErrorFondo(RuntimeError):
    pass


def tiene_transparencia(im: Image.Image) -> bool:
    return im.mode == "RGBA" and im.getextrema()[3][0] < 255


class Removedor:
    """Quita el fondo según la configuración. El modelo de rembg se carga una vez, al primer uso."""

    def __init__(self, cfg: QuitarFondo):
        self.cfg = cfg
        self._sesion = None

    def __call__(self, im: Image.Image) -> tuple[Image.Image, str]:
        """Devuelve la imagen RGBA sin fondo y el método usado."""
        transparente = tiene_transparencia(im)
        if self.cfg.metodo == "alfa" and not transparente:
            raise ErrorFondo('la imagen no trae transparencia (quitar_fondo.metodo = "alfa")')
        if self.cfg.metodo == "alfa" or (self.cfg.metodo == "auto" and transparente):
            return im, "alfa"
        return self._rembg(im), f"rembg:{self.cfg.modelo}"

    def _rembg(self, im: Image.Image) -> Image.Image:
        try:
            from rembg import new_session, remove
        except ImportError as e:
            raise ErrorFondo('rembg no está instalado (pip install -e ".[dev]")') from e
        if self._sesion is None:
            # Siempre con un modelo explícito: el default de rembg (bria-rmbg) no admite uso comercial.
            try:
                self._sesion = new_session(self.cfg.modelo)
            except ValueError as e:
                raise ErrorFondo(f"modelo de rembg desconocido: {self.cfg.modelo}") from e
        # rembg trabaja en RGB; si la imagen traía alfa, se aplana sobre blanco.
        blanco = Image.new("RGBA", im.size, (255, 255, 255, 255))
        rgb = Image.alpha_composite(blanco, im.convert("RGBA")).convert("RGB")
        # decontaminate recupera el color real de los bordes suaves en vez de dejar el del fondo.
        return remove(rgb, session=self._sesion, decontaminate=self.cfg.limpiar_bordes).convert("RGBA")
