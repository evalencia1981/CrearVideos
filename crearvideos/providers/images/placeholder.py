"""Imágenes generadas localmente con Pillow. Gratis, sin API key, sin red.

Sirve para dos cosas: probar el pipeline completo sin gastar un centavo, y
seguir trabajando en el montaje cuando no hay saldo. No es arte generativo:
son fondos con degradado y grano que se ven intencionales en movimiento.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter

from .base import ImageProvider

# Paletas (arriba, abajo, acento). Se elige por hash del prompt, así que la
# misma escena siempre da el mismo fondo y el video es reproducible.
# Las bases son oscuras a propósito: encima van subtítulos blancos enormes.
PALETTES = [
    ((6, 9, 20), (34, 18, 64), (255, 176, 84)),
    ((4, 16, 22), (8, 58, 62), (72, 240, 198)),
    ((20, 5, 12), (74, 18, 30), (255, 118, 92)),
    ((4, 7, 16), (18, 30, 78), (104, 168, 255)),
    ((16, 9, 3), (72, 40, 12), (255, 186, 64)),
    ((11, 4, 20), (54, 14, 66), (214, 108, 255)),
]


def _pick(prompt: str, index: int) -> tuple:
    digest = hashlib.sha256(f"{index}:{prompt}".encode()).digest()
    return PALETTES[digest[0] % len(PALETTES)], digest


def _gradient(size: tuple[int, int], top: tuple, bottom: tuple) -> Image.Image:
    w, h = size
    base = Image.new("RGB", (1, h))
    px = base.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        # Curva suave: evita la banda dura del degradado lineal.
        t = t * t * (3 - 2 * t)
        px[0, y] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return base.resize((w, h), Image.BILINEAR)


def _glow(img: Image.Image, digest: bytes, accent: tuple) -> Image.Image:
    """Focos de luz difusa: dan profundidad y algo que mirar durante el zoom.

    Se componen con `screen` (suma de luz), no con `blend`. Fundir contra una
    capa mayormente negra promedia hacia el negro y apaga la imagen entera;
    screen solo aclara donde hay luz y respeta las sombras.
    """
    w, h = img.size
    # El halo se dibuja pequeño y se amplía: el desenfoque grande es lo caro,
    # y a esta escala el resultado es idéntico.
    sw, sh = w // 4, h // 4
    layer = Image.new("RGB", (sw, sh), (0, 0, 0))
    draw = ImageDraw.Draw(layer)

    for i in range(3):
        cx = int(sw * (0.15 + 0.7 * (digest[1 + i] / 255)))
        cy = int(sh * (0.10 + 0.55 * (digest[4 + i] / 255)))
        r = int(min(sw, sh) * (0.18 + 0.20 * (digest[7 + i] / 255)))
        # Cada foco baja de intensidad: uno domina y los otros acompañan.
        k = (0.85, 0.5, 0.3)[i]
        draw.ellipse(
            [cx - r, cy - r, cx + r, cy + r],
            fill=tuple(int(c * k) for c in accent),
        )

    layer = layer.filter(ImageFilter.GaussianBlur(radius=min(sw, sh) // 5))
    layer = layer.resize((w, h), Image.BICUBIC)
    return ImageChops.screen(img, layer)


def _grain(img: Image.Image) -> Image.Image:
    """Grano fino: sin él, el degradado se ve plástico al comprimir a MP4."""
    w, h = img.size
    small = Image.effect_noise((w // 4, h // 4), 26).convert("L")
    noise = small.resize((w, h), Image.BILINEAR).convert("RGB")
    return Image.blend(img, noise, 0.055)


def _vignette(img: Image.Image) -> Image.Image:
    """Oscurece los bordes para empujar la mirada al centro."""
    w, h = img.size
    sw, sh = w // 4, h // 4
    mask = Image.new("L", (sw, sh), 0)
    draw = ImageDraw.Draw(mask)
    inset = int(min(sw, sh) * 0.35)
    draw.ellipse([-inset, -inset, sw + inset, sh + inset], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=min(sw, sh) // 4))
    mask = mask.resize((w, h), Image.BICUBIC)

    dark = Image.new("RGB", (w, h), (0, 0, 0))
    darkened = Image.composite(img, dark, mask)
    # Solo al 65%: una viñeta a tope se nota como efecto y se ve barata.
    return Image.blend(img, darkened, 0.65)


def _punch(img: Image.Image) -> Image.Image:
    """Un poco de contraste y color. El degradado plano se ve muerto en video."""
    img = ImageEnhance.Contrast(img).enhance(1.12)
    return ImageEnhance.Color(img).enhance(1.18)


class PlaceholderImages(ImageProvider):
    name = "placeholder"
    cost_per_image = 0.0

    def generate(
        self, prompt: str, out_path: Path, *, index: int = 0, subject: str = ""
    ) -> Path:
        (top, bottom, accent), digest = _pick(prompt, index)
        size = self.destino

        img = _gradient(size, top, bottom)
        img = _glow(img, digest, accent)
        img = _vignette(img)
        img = _punch(img)
        img = _grain(img)  # el grano va al final, si no lo suaviza la viñeta

        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, "JPEG", quality=92)
        return out_path
