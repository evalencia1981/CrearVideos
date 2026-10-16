"""Voz vía edge-tts: voces neuronales de Microsoft, gratis y sin API key.

Es el proveedor por defecto porque entrega los tiempos de cada palabra
directamente desde el motor, que es justo lo que necesitan los subtítulos.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import edge_tts

from ...config import VoiceSettings
from ...models import Word
from .base import TTSProvider


class EdgeTTS(TTSProvider):
    name = "edge"
    exact_timings = True

    def __init__(self, voice: VoiceSettings | None = None):
        self.voice = voice or VoiceSettings()

    async def _synthesize(self, text: str, out_path: Path) -> tuple[float, list[Word]]:
        # Sin boundary="WordBoundary" el servicio solo manda marcas por
        # oración, y el resaltado palabra por palabra se cae.
        comm = edge_tts.Communicate(
            text,
            self.voice.name,
            rate=self.voice.rate,
            pitch=self.voice.pitch,
            volume=self.voice.volume,
            boundary="WordBoundary",
        )
        audio = bytearray()
        words: list[Word] = []
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                words.append(
                    Word(
                        text=chunk["text"],
                        start=chunk["offset"] / 1e7,   # unidades de 100 ns
                        duration=chunk["duration"] / 1e7,
                    )
                )
        if not audio:
            raise RuntimeError(
                f"edge-tts no devolvió audio para la voz '{self.voice.name}'. "
                "Revisa tu conexión o prueba otra voz con:  crearvideos voces"
            )
        out_path.write_bytes(bytes(audio))
        total = words[-1].end if words else 0.0
        return total, words

    def synthesize(self, text: str, out_path: Path) -> tuple[float, list[Word]]:
        return asyncio.run(self._synthesize(text, out_path))


def list_voices(prefix: str = "es-") -> list[dict]:
    async def _fetch():
        vm = await edge_tts.VoicesManager.create()
        return [v for v in vm.voices if v["Locale"].startswith(prefix)]

    return asyncio.run(_fetch())
