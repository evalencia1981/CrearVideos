"""Imágenes vía OpenAI gpt-image-1-mini. ~$0.005-0.02 por imagen según calidad.

La opción de pago más barata. Genera 1024x1536 (2:3), un poco menos alargado
que 9:16, así que el montaje le hace un recorte central al llevarlo a 1080x1920.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from pathlib import Path

from ...config import api_key
from .base import ImageProvider

ENDPOINT = "https://api.openai.com/v1/images/generations"


class OpenAIImages(ImageProvider):
    name = "openai"
    cost_per_image = 0.015

    def __init__(self, model: str = "gpt-image-1-mini", quality: str = "medium"):
        self.model = model
        self.quality = quality

    def _key(self) -> str:
        key = api_key("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "Falta OPENAI_API_KEY en .env para el proveedor de imágenes 'openai'.\n"
                "Nota: la suscripción de ChatGPT Plus no incluye API. El saldo se carga\n"
                "aparte en https://platform.openai.com/settings/organization/billing"
            )
        return key

    def preflight(self) -> None:
        self._key()

    def generate(
        self, prompt: str, out_path: Path, *, index: int = 0, subject: str = ""
    ) -> Path:
        key = self._key()
        payload = {
            "model": self.model,
            "prompt": prompt,
            "n": 1,
            # gpt-image da 2:3, 3:2 o 1:1: el más cercano al formato del video;
            # el montaje recorta el resto.
            "size": {"9:16": "1024x1536", "16:9": "1536x1024"}.get(self.relacion, "1024x1024"),
            "quality": self.quality,
            "output_format": "jpeg",
        }
        req = urllib.request.Request(
            ENDPOINT,
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"OpenAI Images respondió {e.code}: {e.read().decode()}") from e

        data = body.get("data") or []
        if not data:
            raise RuntimeError(f"OpenAI no devolvió imagen para la escena {index + 1}.")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Los modelos gpt-image siempre responden en base64, no con URL.
        out_path.write_bytes(base64.b64decode(data[0]["b64_json"]))
        return out_path
