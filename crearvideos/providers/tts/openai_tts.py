"""Voz vía OpenAI (gpt-4o-mini-tts). De pago, ~$0.015 por minuto de audio.

Camino de mejora cuando la voz de edge-tts se quede corta. Ojo: la API no
reporta tiempos por palabra, así que aquí se estiman (ver `estimate_words`).
Para sincronía perfecta con este proveedor habría que alinear con Whisper.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from ...config import api_key
from ...models import Word
from .base import TTSProvider, estimate_words

ENDPOINT = "https://api.openai.com/v1/audio/speech"


class OpenAITTS(TTSProvider):
    name = "openai"
    exact_timings = False

    def __init__(self, voice: str = "onyx", model: str = "gpt-4o-mini-tts",
                 instructions: str | None = None):
        self.voice = voice
        self.model = model
        self.instructions = instructions or (
            "Narra con energía y ritmo ágil, como un documental para redes sociales. "
            "Tono cercano y seguro."
        )

    def _key(self) -> str:
        key = api_key("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "Falta OPENAI_API_KEY en el archivo .env para usar el proveedor de voz 'openai'."
            )
        return key

    def preflight(self) -> None:
        self._key()

    def synthesize_raw(self, text: str, out_path: Path) -> tuple[float, list[Word]]:
        key = self._key()
        payload = {
            "model": self.model,
            "voice": self.voice,
            "input": text,
            "response_format": "mp3",
        }
        if self.model.startswith("gpt-4o"):
            payload["instructions"] = self.instructions

        req = urllib.request.Request(
            ENDPOINT,
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                out_path.write_bytes(resp.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"OpenAI TTS respondió {e.code}: {e.read().decode()}") from e

        from ...ffmpeg import duration

        total = duration(out_path)
        return total, estimate_words(text, total)
