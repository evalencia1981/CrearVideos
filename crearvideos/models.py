"""Estructuras de datos que viajan por el pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Word:
    """Una palabra narrada, con su tiempo absoluto dentro del video final."""

    text: str
    start: float
    duration: float

    @property
    def end(self) -> float:
        return self.start + self.duration

    def shifted(self, offset: float) -> Word:
        return Word(self.text, self.start + offset, self.duration)


@dataclass
class ScriptMeta:
    """La cabecera del guion: el contexto que no se narra pero manda en la imagen.

    `estilo` es el look. El resto es lo que evita que un video de prehistoria
    salga con carritos de supermercado y un gato: la época y el lugar acotan
    qué puede aparecer en cuadro, `protagonistas` fija a quién seguimos en
    todas las escenas, `paleta` unifica el color, y `evitar` lista los
    anacronismos que el modelo mete solo si no se le prohíben.

    Todos los campos son opcionales: sin cabecera el director los deduce del
    propio guion.
    """

    estilo: str = ""
    epoca: str = ""
    protagonistas: str = ""
    paleta: str = ""
    evitar: str = ""

    def as_brief(self) -> str:
        """Resumen en texto para dárselo al director. Vacío si no hay nada."""
        partes = [
            ("Era/period and place", self.epoca),
            ("Recurring cast we follow", self.protagonistas),
            ("Color palette", self.paleta),
            ("Must never appear", self.evitar),
        ]
        return "\n".join(f"{k}: {v}" for k, v in partes if v.strip())


@dataclass
class Scene:
    """Un segmento del guion: una imagen + su fragmento de narración."""

    index: int
    text: str
    image_prompt: str
    #: El sujeto de la imagen sin el estilo pegado detrás. Los generadores de
    #: IA quieren `image_prompt` entero; un buscador de stock quiere solo esto,
    #: porque el estilo es idéntico en todas las escenas y, como término de
    #: búsqueda, las haría devolver todas la misma foto.
    image_subject: str = ""
    audio_path: Path | None = None
    image_path: Path | None = None
    words: list[Word] = field(default_factory=list)
    start: float = 0.0
    duration: float = 0.0

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclass
class Project:
    """Un video en construcción y todo lo que se generó para él."""

    name: str
    root: Path
    scenes: list[Scene] = field(default_factory=list)

    @property
    def audio_dir(self) -> Path:
        return self.root / "audio"

    @property
    def image_dir(self) -> Path:
        return self.root / "images"

    @property
    def clip_dir(self) -> Path:
        return self.root / "clips"

    @property
    def subtitle_path(self) -> Path:
        return self.root / "subtitulos.ass"

    @property
    def narration_path(self) -> Path:
        # WAV, no MP3: la unión de las escenas debe ser exacta al sample.
        return self.root / "narracion.wav"

    @property
    def output_path(self) -> Path:
        return self.root / f"{self.name}.mp4"

    @property
    def total_duration(self) -> float:
        return sum(s.duration for s in self.scenes)

    def ensure_dirs(self) -> None:
        for d in (self.audio_dir, self.image_dir, self.clip_dir):
            d.mkdir(parents=True, exist_ok=True)

    def all_words(self) -> list[Word]:
        return [w for s in self.scenes for w in s.words]
