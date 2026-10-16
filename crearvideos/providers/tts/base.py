"""Contrato común de los proveedores de voz."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path

from ...models import Word

_WORD_RE = re.compile(r"[\wÁÉÍÓÚÜÑáéíóúüñ'’\-]+", re.UNICODE)


class TTSProvider(ABC):
    """Convierte texto en audio y dice cuándo suena cada palabra.

    Los tiempos que devuelve son relativos al inicio de ese audio;
    el pipeline los desplaza luego a la línea de tiempo del video.
    """

    name: str = "base"
    #: True si el proveedor entrega tiempos reales del motor de voz.
    #: False si hay que estimarlos (ver `estimate_words`).
    exact_timings: bool = False

    def synthesize(self, text: str, out_path: Path) -> tuple[float, list[Word]]:
        """Genera el audio en `out_path` y devuelve (duración, palabras).

        Respeta las marcas `(pausa)` del guion (ver pausas.py): el texto se
        parte ahí y se intercalan silencios. Sin marcas, es `synthesize_raw`.
        """
        from ... import pausas

        return pausas.sintetizar(self, text, out_path)

    @abstractmethod
    def synthesize_raw(self, text: str, out_path: Path) -> tuple[float, list[Word]]:
        """Síntesis directa del motor, sin interpretar marcas."""

    def preflight(self) -> None:
        """Comprueba que el proveedor puede trabajar. Lanza si no."""


def estimate_words(text: str, total: float) -> list[Word]:
    """Reparte el tiempo entre las palabras cuando el motor no da timestamps.

    Se pondera por longitud, que es una aproximación decente al tiempo de
    locución. Sobre escenas de 3-6 segundos el error acumulado es pequeño,
    pero es una estimación: para sincronía exacta con un motor que no
    reporta tiempos hay que alinear el audio con Whisper.
    """
    tokens = _WORD_RE.findall(text)
    if not tokens:
        return []
    weights = [len(t) + 1 for t in tokens]
    span = sum(weights)
    words: list[Word] = []
    cursor = 0.0
    for token, weight in zip(tokens, weights):
        dur = total * (weight / span)
        words.append(Word(token, cursor, dur))
        cursor += dur
    return words
