"""Pausas dentro de la narración: `(pausa)` en el guion = silencio de verdad.

Los motores de voz leen todo lo que reciben, así que un "(pausa)" escrito en
el guion acababa dicho en voz alta. Aquí se reconoce la marca, se parte el
texto en ese punto, se sintetiza cada trozo por separado y se intercala un
silencio exacto. Los tiempos de palabra de cada trozo se desplazan, así que
los subtítulos siguen cuadrados.

Marcas que se aceptan (con paréntesis o corchetes, en mayúsculas o no):

    (pausa)          0,8 s
    (pausa corta)    0,4 s
    (pausa larga)    1,6 s
    (pausa 2s)       los segundos que digas (también "2", "1.5", "1,5 s"), hasta 10
    (silencio)       igual que (pausa larga)

Una marca al final de una escena alarga el silencio antes de la siguiente.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from .models import Word

CORTA = 0.4
NORMAL = 0.8
LARGA = 1.6
MAXIMA = 10.0

#: Quita el silencio del final de un trozo (se invierte, se recorta el
#: principio y se vuelve a invertir). Deja 0,1 s para no cortar la última
#: sílaba en seco.
_RECORTE_COLA = (
    "areverse,silenceremove=start_periods=1:start_threshold=-50dB:"
    "start_silence=0.1,areverse"
)

_MARCA = re.compile(
    r"[\(\[]\s*(?:"
    r"pausa(?:\s+(?P<tipo>corta|breve|larga|larguísima|larguisima))?"
    r"(?:\s+(?P<seg>\d+(?:[.,]\d+)?)\s*(?:s|seg|segundos?)?)?"
    r"|(?P<silencio>silencio)"
    r")\s*[\)\]]",
    re.IGNORECASE,
)


def _segundos(m: re.Match) -> float:
    if m.group("seg"):
        return min(MAXIMA, float(m.group("seg").replace(",", ".")))
    if m.group("silencio"):
        return LARGA
    tipo = (m.group("tipo") or "").lower()
    if tipo in ("corta", "breve"):
        return CORTA
    if tipo.startswith("larg"):
        return LARGA if tipo == "larga" else LARGA * 2
    return NORMAL


def tiene_pausas(texto: str) -> bool:
    return bool(_MARCA.search(texto or ""))


def segundos_totales(texto: str) -> float:
    """Cuánto silencio suman las marcas, para la duración estimada."""
    return sum(_segundos(m) for m in _MARCA.finditer(texto or ""))


def limpiar(texto: str) -> str:
    """El texto sin marcas: para subtítulos repartidos, imágenes y exportes."""
    return " ".join(_MARCA.sub(" ", texto or "").split())


def segmentos(texto: str) -> list[tuple[str, float]]:
    """Parte el texto en [(trozo, silencio_después)].

    Un trozo vacío con silencio es una pausa al principio. Pausas seguidas se
    suman en una sola.
    """
    partes: list[tuple[str, float]] = []
    cursor = 0
    for m in _MARCA.finditer(texto):
        trozo = " ".join(texto[cursor:m.start()].split())
        if not trozo and partes:
            # Dos marcas seguidas: se suma al silencio anterior.
            t, s = partes[-1]
            partes[-1] = (t, s + _segundos(m))
        else:
            partes.append((trozo, _segundos(m)))
        cursor = m.end()
    resto = " ".join(texto[cursor:].split())
    if resto or not partes:
        partes.append((resto, 0.0))
    return partes


def sintetizar(motor, texto: str, salida: Path) -> tuple[float, list[Word]]:
    """Sintetiza respetando las pausas. Sin marcas, es la síntesis de siempre.

    `motor` es cualquier proveedor con `synthesize_raw(texto, ruta)`. El
    resultado se escribe en `salida` (MP3) con los silencios ya dentro, y las
    palabras vuelven con sus tiempos sobre ese audio completo.
    """
    if not tiene_pausas(texto):
        return motor.synthesize_raw(texto, salida)

    from . import ffmpeg

    partes = segmentos(texto)
    palabras: list[Word] = []
    lista: list[Path] = []
    cursor = 0.0
    with tempfile.TemporaryDirectory(prefix="pausas_") as tmp:
        tmp = Path(tmp)
        for i, (trozo, silencio) in enumerate(partes):
            if trozo:
                crudo = tmp / f"t{i:03d}.mp3"
                _, ws = motor.synthesize_raw(trozo, crudo)
                # Todo a WAV con el mismo formato: así se concatenan sin
                # costuras y la duración medida es la que va a sonar.
                wav = tmp / f"t{i:03d}.wav"
                args = ["-i", str(crudo), "-ar", "44100", "-ac", "1"]
                if silencio > 0:
                    # El motor deja ~0,7 s de silencio al final de cada
                    # trozo; sin recortarlo, "(pausa 2s)" sonaba como 2,7 s.
                    # Solo se toca la cola, así que los tiempos de palabra
                    # no cambian.
                    args += ["-af", _RECORTE_COLA]
                ffmpeg.run(args + [str(wav)])
                dur = ffmpeg.duration(wav)
                palabras.extend(w.shifted(cursor) for w in ws)
                lista.append(wav)
                cursor += dur
            if silencio > 0:
                hueco = tmp / f"s{i:03d}.wav"
                ffmpeg.run([
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                    "-t", f"{silencio:.3f}", str(hueco),
                ])
                lista.append(hueco)
                cursor += silencio
        if not lista:
            raise RuntimeError("La escena solo tiene marcas de pausa, sin texto que narrar.")
        ffmpeg.concat_demuxer(lista, tmp / "lista.txt", tmp / "todo.wav")
        ffmpeg.run(["-i", str(tmp / "todo.wav"), "-c:a", "libmp3lame", "-b:a", "192k",
                    str(Path(salida).resolve())])
    return cursor, palabras
