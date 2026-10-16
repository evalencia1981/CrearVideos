"""Envoltura delgada sobre ffmpeg/ffprobe."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


def _binary(name: str) -> str:
    """Ruta a ffmpeg/ffprobe: primero el que viene con el programa.

    En la versión instalable, ffmpeg viaja dentro del .exe: así el usuario no
    tiene que instalar nada aparte, que es donde se caen nueve de cada diez
    instalaciones. Desde el código fuente se usa el del PATH, como siempre.
    """
    from .config import RESOURCE_ROOT

    propio = RESOURCE_ROOT / "bin" / f"{name}.exe"
    if propio.is_file():
        return str(propio)

    path = shutil.which(name)
    if not path:
        raise FFmpegError(
            f"No se encontró '{name}' en el PATH. Instálalo con:  winget install Gyan.FFmpeg"
        )
    return path


def run(args: list[str], *, quiet: bool = True, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Ejecuta ffmpeg. `cwd` permite pasar rutas relativas a los filtros.

    Eso importa en el filtro `subtitles`: una ruta absoluta de Windows trae
    `C:` y barras invertidas, que el parser de filtros interpreta como
    separador de opciones y como escape. Trabajar desde el directorio del
    proyecto y usar el nombre pelado del archivo evita todo ese problema.
    """
    cmd = [_binary("ffmpeg"), "-hide_banner", "-y"]
    if quiet:
        cmd += ["-loglevel", "error"]
    cmd += args
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd) if cwd else None,
    )
    if proc.returncode != 0:
        raise FFmpegError(
            f"ffmpeg falló (código {proc.returncode})\n"
            f"Comando: {' '.join(cmd)}\n"
            f"Salida:\n{proc.stderr}"
        )
    return proc


def probe(path: Path) -> dict:
    """Todo lo que ffprobe sabe del archivo: pistas, tamaño y duración.

    Hace falta para unir clips ajenos: saber si traen audio y en qué tamaño
    vienen, en vez de suponerlo y descubrirlo cuando el montaje sale torcido.
    """
    proc = subprocess.run(
        [
            _binary("ffprobe"),
            "-v", "error",
            "-show_streams",
            "-show_format",
            "-of", "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe falló con {path}:\n{proc.stderr}")
    return json.loads(proc.stdout)


def duration(path: Path) -> float:
    """Duración real de un archivo de audio o video, en segundos."""
    proc = subprocess.run(
        [
            _binary("ffprobe"),
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe falló con {path}:\n{proc.stderr}")
    return float(json.loads(proc.stdout)["format"]["duration"])


def concat_demuxer(
    paths: list[Path],
    list_file: Path,
    output: Path,
    *,
    codec_args: list[str] | None = None,
) -> None:
    """Une archivos con el demuxer concat.

    Las rutas van con barra normal y entre comillas simples: el parser de
    ffmpeg trata la barra invertida de Windows como escape.
    """
    lines = [f"file '{p.resolve().as_posix()}'" for p in paths]
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    args = ["-f", "concat", "-safe", "0", "-i", str(list_file)]
    args += codec_args or ["-c", "copy"]
    args += [str(output)]
    run(args)


def apply_filter(src: Path, dst: Path, afilter: str, *, sample_rate: int = 44100) -> None:
    """Aplica una cadena de filtros de audio y deja el resultado como WAV.

    Se usa para los efectos de voz. Sale a PCM porque después este archivo se
    mide y se rellena al frame exacto, igual que la voz sin efecto; un efecto
    con cola (reverb, eco) alarga el audio, y esa duración real es la que el
    pipeline usa para ajustar la escena, así que la cola nunca se corta.
    """
    run(
        [
            "-i", str(src.resolve()),
            "-af", afilter,
            "-ar", str(sample_rate),
            "-ac", "2",
            "-c:a", "pcm_s16le",
            str(dst.resolve()),
        ]
    )


def pad_to_exact(src: Path, dst: Path, seconds: float, *, sample_rate: int = 44100) -> None:
    """Reescribe el audio como WAV de duración exacta, rellenando con silencio.

    Se pasa a PCM a propósito. Concatenar MP3 arrastra el relleno de cada
    frame (26 ms) y ese error se suma escena a escena hasta que la voz se
    despega de la imagen. En PCM la unión es exacta al sample.
    """
    run(
        [
            "-i", str(src.resolve()),
            "-af", "apad",
            "-t", f"{seconds:.6f}",
            "-ar", str(sample_rate),
            "-ac", "2",
            "-c:a", "pcm_s16le",
            str(dst.resolve()),
        ]
    )
