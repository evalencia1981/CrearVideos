"""Unir segmentos de video que ya traen su propia voz.

El caso es distinto al del montaje con guion: aquí los clips ya vienen
narrados, no hace falta generar voz ni subtítulos. Solo hay que ponerlos en
orden, dejarlos todos en el mismo formato y pegarlos.

    carpeta/
      01_intro.mp4      ─┐
      02_desarrollo.mp4  ├─ en orden natural o por el número del nombre
      03_cierre.mp4     ─┘
                        │
                        └─> video.mp4  (con su audio original)

Dos formas de pegar:

- **Corte seco**: un clip detrás de otro. Es lo más rápido y no toca la imagen.
- **Con transición**: cruce de medio segundo entre clips (fundido, disolución,
  barrido…). Cruza también el audio, así que la voz no se corta de golpe.

Ojo con las transiciones: cada una **se come** el tiempo del cruce, porque los
dos clips se solapan. Con 10 clips y medio segundo de transición, el video
final dura 4,5 segundos menos que la suma de las partes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import ffmpeg, medios as medios_lib
from .config import FPS, OUTPUT_DIR, formato as resolver_formato

#: Transiciones de ffmpeg que se ven bien en este tipo de video, con su nombre
#: en español. Hay decenas más, pero la mitad son efectos de los 90.
TRANSICIONES = {
    "ninguna": ("", "Corte seco, sin transición"),
    "fundido": ("fade", "Fundido suave entre clips"),
    "negro": ("fadeblack", "Pasa por negro"),
    "blanco": ("fadewhite", "Pasa por blanco"),
    "disolver": ("dissolve", "Disolución con grano"),
    "izquierda": ("slideleft", "El siguiente entra desde la derecha"),
    "derecha": ("slideright", "El siguiente entra desde la izquierda"),
    "arriba": ("slideup", "El siguiente entra desde abajo"),
    "barrido": ("wipeleft", "Barrido lateral"),
    "circulo": ("circleopen", "Se abre en círculo"),
    "pixel": ("pixelize", "Pixelado entre clips"),
}

#: Duración de la transición. Medio segundo es lo que usa casi todo el mundo:
#: menos no se nota, más se siente lento.
DURACION_TRANSICION = 0.5
#: Cuánto dura en pantalla una imagen suelta, si la carpeta trae alguna.
SEGUNDOS_IMAGEN = 3.0


class EnsamblarError(RuntimeError):
    """No se pudo unir: faltan clips o son incompatibles."""


@dataclass
class Segmento:
    path: Path
    duracion: float
    tiene_audio: bool
    ancho: int
    alto: int
    es_imagen: bool


@dataclass
class EnsambleResult:
    salida: Path
    segmentos: list[Segmento]
    formato: str
    tamaño: tuple[int, int]
    transicion: str
    duracion: float

    @property
    def suma_clips(self) -> float:
        return sum(s.duracion for s in self.segmentos)


def _info(path: Path, es_imagen: bool) -> Segmento:
    """Duración, tamaño y si trae audio. Todo medido, nada supuesto."""
    if es_imagen:
        from PIL import Image

        with Image.open(path) as im:
            ancho, alto = im.size
        return Segmento(path, SEGUNDOS_IMAGEN, False, ancho, alto, True)

    datos = ffmpeg.probe(path)
    video = next((s for s in datos.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise EnsamblarError(f"{path.name} no tiene pista de video.")
    audio = any(s.get("codec_type") == "audio" for s in datos.get("streams", []))
    return Segmento(
        path=path,
        duracion=float(datos.get("format", {}).get("duration") or 0.0),
        tiene_audio=audio,
        ancho=int(video.get("width") or 0),
        alto=int(video.get("height") or 0),
        es_imagen=False,
    )


def revisar(carpeta: Path) -> list[Segmento]:
    """Lee la carpeta y describe cada segmento, sin tocar nada."""
    encontrados = medios_lib.recolectar(Path(carpeta))
    segmentos = [_info(m.path, m.tipo == "imagen") for m in encontrados]
    if not segmentos:
        raise EnsamblarError(f"No hay nada que unir en {carpeta}.")
    return segmentos


def _tamaño_objetivo(segmentos: list[Segmento], formato: str) -> tuple[int, int]:
    """El formato de salida. 'original' = el del primer clip de video."""
    if formato != "original":
        return resolver_formato(formato)
    for s in segmentos:
        if not s.es_imagen and s.ancho and s.alto:
            # Par: libx264 no acepta dimensiones impares.
            return (s.ancho - s.ancho % 2, s.alto - s.alto % 2)
    return resolver_formato("vertical")


def _normalizar(seg: Segmento, destino: Path, tamaño: tuple[int, int]) -> Path:
    """Deja el clip en el formato de salida, con audio siempre.

    Dos clips solo se pueden pegar si coinciden en tamaño, fps, relación de
    píxel y formato de audio. Aquí se igualan todos; a los que no traen sonido
    se les pone silencio, porque si no, el audio del resto se descoloca.
    """
    w, h = tamaño
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},fps={FPS},setsar=1,format=yuv420p"
    )
    args: list[str] = []
    if seg.es_imagen:
        args += ["-loop", "1", "-t", f"{seg.duracion:.3f}", "-i", str(seg.path.resolve())]
        args += ["-f", "lavfi", "-t", f"{seg.duracion:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    else:
        args += ["-i", str(seg.path.resolve())]
        if not seg.tiene_audio:
            args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100", "-shortest"]

    args += ["-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18"]
    # Audio y video TIENEN que acabar en el mismo instante. Muchos clips traen
    # la pista de sonido más corta que la imagen (pasa al exportar desde otras
    # herramientas): sin igualarlos, cada transición arrastra ese hueco y el
    # audio del video final termina antes que la imagen, cortando la última
    # frase. `apad` rellena con silencio y `-t` corta ambos a la misma marca.
    args += ["-af", "apad", "-t", f"{seg.duracion:.3f}"]
    args += ["-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2"]
    if seg.es_imagen or not seg.tiene_audio:
        args += ["-map", "0:v:0", "-map", "1:a:0"]
    args += [str(destino.resolve())]
    ffmpeg.run(args)
    return destino


def _cadena_xfade(duraciones: list[float], efecto: str, t: float) -> tuple[str, str]:
    """Construye el filtro de transiciones encadenadas (video y audio).

    El `offset` de cada cruce es dónde empieza la transición dentro de lo que
    ya se lleva montado. Como cada cruce solapa `t` segundos, hay que restarlo
    en cada paso; si no, las transiciones se van corriendo y el último clip
    aparece antes de tiempo.
    """
    video: list[str] = []
    audio: list[str] = []
    etiqueta_v, etiqueta_a = "[0:v]", "[0:a]"
    acumulado = duraciones[0]

    for i in range(1, len(duraciones)):
        offset = max(acumulado - t, 0.0)
        salida_v = f"[v{i}]" if i < len(duraciones) - 1 else "[vout]"
        salida_a = f"[a{i}]" if i < len(duraciones) - 1 else "[aout]"
        video.append(
            f"{etiqueta_v}[{i}:v]xfade=transition={efecto}:duration={t:.3f}:"
            f"offset={offset:.3f}{salida_v}"
        )
        audio.append(f"{etiqueta_a}[{i}:a]acrossfade=d={t:.3f}:c1=tri:c2=tri{salida_a}")
        etiqueta_v, etiqueta_a = salida_v, salida_a
        acumulado += duraciones[i] - t

    return ";".join(video), ";".join(audio)


def ensamblar(
    carpeta: Path,
    nombre: str,
    *,
    formato: str = "original",
    transicion: str = "ninguna",
    duracion_transicion: float = DURACION_TRANSICION,
    musica: Path | None = None,
    volumen_musica: float = 0.12,
    output_dir: Path | None = None,
    on_progress=None,
) -> EnsambleResult:
    """Une los clips de la carpeta en un solo video, con su audio original."""

    def report(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    if transicion not in TRANSICIONES:
        raise EnsamblarError(
            f"Transición desconocida: '{transicion}'. Opciones: {', '.join(TRANSICIONES)}"
        )

    segmentos = revisar(carpeta)
    tamaño = _tamaño_objetivo(segmentos, formato)
    raiz = (output_dir or OUTPUT_DIR) / nombre
    trabajo = raiz / "clips"
    trabajo.mkdir(parents=True, exist_ok=True)

    efecto = TRANSICIONES[transicion][0]
    t = max(0.1, min(duracion_transicion, 3.0))
    if efecto and len(segmentos) > 1:
        # Una transición no puede durar más que el clip más corto, o ffmpeg
        # devuelve un video con fotogramas congelados al final.
        t = min(t, min(s.duracion for s in segmentos) * 0.5)

    normalizados: list[Path] = []
    for i, seg in enumerate(segmentos):
        report(f"Preparando {i + 1}/{len(segmentos)}: {seg.path.name}")
        normalizados.append(_normalizar(seg, trabajo / f"seg_{i:03d}.mp4", tamaño))

    salida = raiz / f"{nombre}.mp4"
    if not efecto or len(normalizados) == 1:
        report("Uniendo los clips (corte seco)")
        ffmpeg.concat_demuxer(normalizados, raiz / "clips.txt", salida)
    else:
        report(f"Uniendo con transición «{transicion}» de {t:.1f}s")
        entradas: list[str] = []
        for p in normalizados:
            entradas += ["-i", str(p.resolve())]
        duraciones = [ffmpeg.duration(p) for p in normalizados]
        filtro_v, filtro_a = _cadena_xfade(duraciones, efecto, t)
        ffmpeg.run(
            entradas
            + [
                "-filter_complex", f"{filtro_v};{filtro_a}",
                "-map", "[vout]", "-map", "[aout]",
                "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                "-pix_fmt", "yuv420p", "-profile:v", "high",
                "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
                "-movflags", "+faststart",
                str(salida.resolve()),
            ]
        )

    if musica is not None:
        report("Mezclando la música de fondo")
        con_musica = raiz / f"{nombre}_mus.mp4"
        total = ffmpeg.duration(salida)
        # Volumen bajo y fijo: los clips ya traen voz, y un ducking agresivo
        # sobre varias voces distintas suena a bombeo.
        ffmpeg.run(
            [
                "-i", str(salida.resolve()),
                "-stream_loop", "-1", "-i", str(musica.resolve()),
                "-filter_complex",
                f"[1:a]aformat=sample_rates=44100:channel_layouts=stereo,"
                f"volume={volumen_musica},atrim=0:{total:.3f},asetpts=N/SR/TB,"
                f"afade=t=out:st={max(total - 1.5, 0):.3f}:d=1.5[mus];"
                f"[0:a][mus]amix=inputs=2:duration=first:normalize=0[aout]",
                "-map", "0:v", "-map", "[aout]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                str(con_musica.resolve()),
            ]
        )
        con_musica.replace(salida)

    final = ffmpeg.duration(salida)
    (raiz / "ensamblaje.json").write_text(
        json.dumps(
            {
                "carpeta": str(Path(carpeta).resolve()),
                "formato": formato,
                "tamaño": list(tamaño),
                "transicion": transicion,
                "duracion_transicion": t if efecto else 0,
                "duracion": round(final, 2),
                "suma_clips": round(sum(s.duracion for s in segmentos), 2),
                "segmentos": [
                    {
                        "n": i + 1,
                        "archivo": s.path.name,
                        "duracion": round(s.duracion, 2),
                        "audio": s.tiene_audio,
                        "tamaño": f"{s.ancho}x{s.alto}",
                    }
                    for i, s in enumerate(segmentos)
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return EnsambleResult(
        salida=salida,
        segmentos=segmentos,
        formato=formato,
        tamaño=tamaño,
        transicion=transicion,
        duracion=final,
    )
