"""Registro de proveedores.

Todo lo que cuesta dinero está detrás de una de estas dos interfaces. Cambiar
de proveedor es cambiar una línea del .env, no tocar el pipeline.
"""

from __future__ import annotations

from ..config import Settings
from .images.base import ImageProvider
from .tts.base import TTSProvider


def get_image_provider(settings: Settings) -> ImageProvider:
    proveedor = _image_provider(settings)
    # El formato del video viaja al proveedor: así pide la imagen ya en 16:9
    # o 1:1, en vez de generar un 9:16 y recortarle dos tercios.
    proveedor.formato = settings.formato or "vertical"
    return proveedor


def _image_provider(settings: Settings) -> ImageProvider:
    name = settings.image_provider.lower()
    if name == "placeholder":
        from .images.placeholder import PlaceholderImages

        return PlaceholderImages()
    if name == "pollinations":
        from .images.pollinations import PollinationsImages

        return PollinationsImages(
            model=settings.image_model or None,
            references=settings.reference_images,
            free=settings.pollinations_free,
        )
    if name == "pexels":
        from .images.pexels import PexelsImages

        return PexelsImages()
    if name == "gemini":
        from .images.gemini import GeminiImages

        return GeminiImages()
    if name == "openai":
        from .images.openai_images import OpenAIImages

        return OpenAIImages()
    raise ValueError(
        f"Proveedor de imágenes desconocido: '{name}'. "
        f"Opciones: pollinations, pexels, placeholder, gemini, openai"
    )


def get_tts_provider(settings: Settings) -> TTSProvider:
    name = settings.tts_provider.lower()
    if name == "edge":
        from .tts.edge import EdgeTTS

        return EdgeTTS(settings.voice)
    if name == "openai":
        from .tts.openai_tts import OpenAITTS

        return OpenAITTS()
    raise ValueError(f"Proveedor de voz desconocido: '{name}'. Opciones: edge, openai")
