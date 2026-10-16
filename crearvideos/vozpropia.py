"""Usar TU propia voz (un MP3/WAV grabado o hecho en otra herramienta).

El pipeline normal sintetiza la voz y, de paso, el motor le dice cuándo empieza
cada palabra. Con un audio traído de fuera esa información no existe, así que
hay que deducir dos cosas: dónde termina cada escena y dónde cae cada palabra.

Dos formas de traer la voz, de menos a más exacta:

1. **Un archivo por escena** (una carpeta, como la de los medios). Cada escena
   dura exactamente lo que dura su archivo. Es lo más fiable y lo recomendable
   si tu herramienta puede exportar por bloques.
2. **Un solo archivo con todo**. Se reparte entre las escenas en proporción a
   la longitud del texto de cada una, ajustado a la duración real del audio.

En ambos casos los tiempos por palabra se reparten dentro de su escena según el
largo de cada palabra. Los subtítulos entran y salen en el sitio, pero el
resaltado palabra por palabra es una aproximación, no la medición exacta que da
edge-tts. Con un archivo por escena el error nunca se arrastra de una escena a
la siguiente.
"""

from __future__ import annotations

import math
from pathlib import Path

from . import ffmpeg
from .config import FPS, Settings
from .models import Project, Word

AUDIOS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".wma"}

#: Peso mínimo de una palabra al repartir el tiempo. Sin esto, un "y" suelto se
#: quedaría con una duración de casi cero y el subtítulo parpadearía.
_MIN_PESO = 2.0


class VozPropiaError(RuntimeError):
    """El audio que trajiste no se puede usar tal cual."""


def _quantize(seconds: float) -> float:
    """Al frame exacto, igual que el resto del pipeline."""
    return math.ceil(seconds * FPS) / FPS


def _palabras(texto: str, inicio: float, duracion: float) -> list[Word]:
    """Reparte las palabras de una escena dentro de su tramo de audio.

    El reparto es proporcional al largo de cada palabra: "extraordinarias"
    ocupa más que "de". No es la medición real, pero se acerca mucho más que
    repartir a partes iguales.
    """
    palabras = texto.split()
    if not palabras or duracion <= 0:
        return []
    pesos = [max(len(p), _MIN_PESO) for p in palabras]
    total = sum(pesos)
    salida: list[Word] = []
    cursor = inicio
    for palabra, peso in zip(palabras, pesos):
        dur = duracion * peso / total
        salida.append(Word(palabra, cursor, dur))
        cursor += dur
    return salida


def recolectar_audios(carpeta: Path) -> list[Path]:
    """Los audios de una carpeta, en orden natural (igual que los medios)."""
    from .medios import _clave_natural

    if not carpeta.is_dir():
        raise VozPropiaError(f"No existe la carpeta de audio: {carpeta}")
    archivos = [p for p in carpeta.iterdir() if p.is_file() and p.suffix.lower() in AUDIOS]
    if not archivos:
        raise VozPropiaError(
            f"En {carpeta} no hay audios. Acepta: {', '.join(sorted(AUDIOS))}"
        )
    archivos.sort(key=_clave_natural)
    return archivos


def _preparar(src: Path, destino: Path, settings: Settings) -> float:
    """Normaliza un audio a WAV con duración de frame exacto. Devuelve su duración."""
    real = ffmpeg.duration(src)
    objetivo = _quantize(real + settings.scene_gap)
    ffmpeg.pad_to_exact(src, destino, objetivo)
    return objetivo


def usar_carpeta(project: Project, carpeta: Path, settings: Settings, on_progress=None) -> None:
    """Un audio por escena: cada escena dura lo que dura su archivo."""
    audios = recolectar_audios(Path(carpeta))
    if len(audios) < len(project.scenes):
        raise VozPropiaError(
            f"Hay {len(audios)} audios para {len(project.scenes)} escenas. "
            "Hace falta uno por escena (o usa un único archivo con toda la narración)."
        )

    project.audio_dir.mkdir(parents=True, exist_ok=True)
    cursor = 0.0
    for scene, src in zip(project.scenes, audios):
        if on_progress:
            on_progress(f"Preparando tu voz {scene.index + 1}/{len(project.scenes)} ({src.name})")
        destino = project.audio_dir / f"escena_{scene.index:03d}.wav"
        duracion = _preparar(src, destino, settings)
        scene.audio_path = destino
        scene.duration = duracion
        scene.start = cursor
        scene.words = _palabras(scene.text, cursor, duracion - settings.scene_gap)
        cursor += duracion


def usar_archivo(project: Project, audio: Path, settings: Settings, on_progress=None) -> None:
    """Un solo archivo con toda la narración, repartido entre las escenas.

    El reparto es proporcional al texto de cada escena. Si tu audio sigue el
    guion de corrido eso cuadra bien; si hiciste pausas largas en un sitio
    concreto, esa escena se quedará corta y la siguiente entrará algo tarde.
    Para control exacto, un archivo por escena.
    """
    audio = Path(audio)
    if not audio.is_file():
        raise VozPropiaError(f"No existe el archivo de voz: {audio}")

    total_real = ffmpeg.duration(audio)
    if total_real <= 0:
        raise VozPropiaError(f"El archivo de voz no tiene audio: {audio}")

    pesos = [max(len(s.text), 1) for s in project.scenes]
    suma = sum(pesos)

    project.audio_dir.mkdir(parents=True, exist_ok=True)
    cursor = 0.0        # dónde empieza cada trozo DENTRO del archivo original
    linea = 0.0         # dónde empieza cada escena en el video final
    for scene, peso in zip(project.scenes, pesos):
        if on_progress:
            on_progress(f"Repartiendo tu voz {scene.index + 1}/{len(project.scenes)}")
        # La última escena se lleva exactamente lo que queda: así el video dura
        # lo mismo que el audio y no se pierde (ni se inventa) ni una décima.
        es_ultima = scene.index == len(project.scenes) - 1
        trozo = (total_real - cursor) if es_ultima else total_real * peso / suma
        destino = project.audio_dir / f"escena_{scene.index:03d}.wav"
        crudo = project.audio_dir / f"crudo_{scene.index:03d}.wav"
        ffmpeg.run(
            [
                "-ss", f"{cursor:.6f}",
                "-t", f"{trozo:.6f}",
                "-i", str(audio.resolve()),
                "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le",
                str(crudo.resolve()),
            ]
        )
        duracion = _quantize(ffmpeg.duration(crudo))
        ffmpeg.pad_to_exact(crudo, destino, duracion)
        crudo.unlink(missing_ok=True)

        scene.audio_path = destino
        scene.duration = duracion
        scene.start = linea
        scene.words = _palabras(scene.text, linea, duracion)
        cursor += trozo
        linea += duracion


def aplicar(project: Project, origen: Path, settings: Settings, on_progress=None) -> str:
    """Usa tu voz, venga como carpeta de escenas o como archivo único.

    Devuelve "carpeta" o "archivo", para poder decir en el informe cuál se usó.
    """
    origen = Path(origen)
    if origen.is_dir():
        usar_carpeta(project, origen, settings, on_progress)
        return "carpeta"
    usar_archivo(project, origen, settings, on_progress)
    return "archivo"
