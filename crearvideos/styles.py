"""Catálogo de estilos visuales.

Cada estilo es un texto que se concatena al prompt de TODAS las imágenes del
video. Es lo que le da al modelo de IA el contexto de "así deben verse" y lo que
mantiene la coherencia entre escenas — el problema número uno de este tipo de
video. Es la misma idea que la cabecera `estilo:` del guion, pero como presets
elegibles en vez de texto a mano.

Los sufijos van en inglés a propósito: los modelos de imagen (Flux, Imagen,
gpt-image) entienden mucho mejor los términos de estilo en inglés, aunque el
sujeto venga en español. Mezclar los dos idiomas en el prompt es normal y
funciona bien.
"""

from __future__ import annotations

# clave -> (etiqueta visible, sufijo de prompt). El sufijo None significa
# "no imponer estilo": se respeta el del guion, o el de por defecto.
STYLES: dict[str, tuple[str, str | None]] = {
    "auto": ("Automático", None),
    "cinematografico": (
        "Cinematográfico",
        "cinematic film still, dramatic volumetric lighting, shallow depth of "
        "field, film grain, epic composition, color graded, 35mm photography",
    ),
    "documental": (
        "Documental / histórico",
        # Sin 'shallow depth of field' ni 'film grain' a propósito: son justo
        # los términos que ablandan la imagen, y en divulgación histórica lo que
        # se quiere es nitidez y luz creíble, no estética de tráiler.
        "photoreal documentary reconstruction, natural directional daylight, "
        "sharp focus edge to edge, authentic period-accurate materials and "
        "textures, 50mm lens, neutral color grading, National Geographic style",
    ),
    "fotorrealista": (
        "Fotorrealista",
        "hyperrealistic photograph, natural lighting, ultra detailed, sharp "
        "focus, professional photography, high dynamic range, 8k",
    ),
    "anime": (
        "Anime",
        "anime style illustration, vibrant saturated colors, clean line art, "
        "cel shading, detailed background, studio quality",
    ),
    "cartoon": (
        "Cartoon 3D",
        "cute 3d cartoon render, pixar style, soft global illumination, "
        "colorful, expressive, polished, family friendly",
    ),
    "comic": (
        "Cómic",
        "comic book illustration, bold ink outlines, halftone shading, dynamic "
        "composition, vivid colors, action panel",
    ),
    "fantasia": (
        "Fantasía",
        "epic fantasy concept art, magical ethereal atmosphere, glowing light, "
        "highly detailed, painterly, majestic",
    ),
    "terror": (
        "Terror",
        "horror scene, dark eerie atmosphere, low-key lighting, fog, "
        "desaturated cold tones, unsettling, cinematic dread",
    ),
    "suspenso": (
        "Suspenso",
        "suspenseful thriller mood, moody shadows, tense atmosphere, high "
        "contrast lighting, cinematic, mysterious",
    ),
    "scifi": (
        "Ciencia ficción",
        "science fiction concept art, futuristic, neon accents, cyberpunk "
        "atmosphere, intricate detail, cinematic lighting",
    ),
    "pixel": (
        "Arte pixelado",
        "pixel art, retro 16-bit video game style, limited color palette, "
        "crisp pixels, nostalgic",
    ),
    "acuarela": (
        "Acuarela",
        "delicate watercolor painting, soft color washes, textured paper, "
        "artistic, gentle, hand painted",
    ),
    "oleo": (
        "Óleo",
        "classical oil painting, rich textured brushstrokes, dramatic "
        "chiaroscuro, warm tones, museum quality",
    ),
    "minimal": (
        "Minimalista",
        "minimalist flat illustration, clean geometric shapes, limited palette, "
        "modern vector style, elegant",
    ),
}

DEFAULT_KEY = "cinematografico"


def resolve(key: str | None) -> str | None:
    """Devuelve el sufijo de prompt de un estilo. None si no aplica ninguno."""
    if not key:
        return None
    entry = STYLES.get(key.lower())
    return entry[1] if entry else None


def is_known(key: str | None) -> bool:
    return bool(key) and key.lower() in STYLES


def options() -> list[dict[str, str]]:
    """Lista [{key, label}] para pintar el selector en la web."""
    return [{"key": k, "label": label} for k, (label, _) in STYLES.items()]
