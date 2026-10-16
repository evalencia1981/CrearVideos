"""Solo la voz: de un guion a la narración en audio, sin montar video.

Es la misma narración del pipeline completo (misma voz, mismos efectos, mismos
tiempos por palabra medidos del audio real), pero se detiene antes de ilustrar
y montar. Sirve para llevarse la pista a otro editor —Premiere, CapCut,
Resolve, Google Flow— junto con los SRT que dicen exactamente cuándo entra cada
escena.

Qué deja en la carpeta del proyecto:

    narracion.mp3      la voz completa, para el editor
    narracion.wav      la misma pista sin comprimir
    audio/escena_NNN.wav   cada escena por separado, ya alineada
    escenas.srt        una entrada por escena: cuándo entra cada imagen
    subtitulos.srt     líneas cortas palabra a palabra, para quemar subtítulos
    narracion.txt      el texto plano de lo que se narró
    escenas.json       tiempos, texto, texto en pantalla y prompt de cada escena
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import clonvoz, compose, ffmpeg, importar, subtitles, voicefx
from .config import OUTPUT_DIR, Settings
from .models import Project
from .pipeline import guardar_narracion, narrar_o_reusar, reusar_narracion
from .script import parse


@dataclass
class AudioResult:
    project: Project
    mp3: Path
    wav: Path
    escenas_srt: Path
    subtitulos_srt: Path
    texto: Path
    datos: Path
    duracion: float
    #: True si se aprovechó una narración anterior en vez de sintetizarla.
    reusada: bool = False

    @property
    def escenas(self) -> int:
        return len(self.project.scenes)

    @property
    def palabras(self) -> int:
        return len(self.project.all_words())


def _a_mp3(src: Path, dst: Path, speed: float = 1.0) -> None:
    args = ["-i", str(src.resolve())]
    if abs(speed - 1.0) > 1e-3:
        # atempo cambia la velocidad sin tocar el tono: la voz no sale de ardilla.
        args += ["-filter:a", compose._atempo_chain(speed)]
    args += ["-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", str(dst.resolve())]
    ffmpeg.run(args)


def create_audio(
    script_source: str | Path,
    name: str,
    settings: Settings,
    *,
    style_key: str | None = None,
    speed: float = 1.0,
    reusar: bool = True,
    voz_clon: Path | None = None,
    clon_idioma: str = "es",
    output_dir: Path | None = None,
    on_progress=None,
) -> AudioResult:
    """Narra el guion completo y devuelve las rutas de todo lo generado."""
    raw = (
        script_source.read_text(encoding="utf-8")
        if isinstance(script_source, Path)
        else script_source
    )
    scenes, _meta = parse(raw, settings.scene)

    # Los bloques originales traen el texto en pantalla y el prompt de imagen,
    # que el guion ya traducido no conserva. Van a escenas.json para que el
    # editor tenga en un solo archivo qué se oye y qué debe verse.
    bloques = importar.parse_storyflow(raw) if importar.es_storyflow(raw) else []

    root = (output_dir or OUTPUT_DIR) / name
    project = Project(name=name, root=root, scenes=scenes)
    project.audio_dir.mkdir(parents=True, exist_ok=True)

    if voz_clon is not None:
        # Con voz clonada el reuso importa aún más: generar 47 escenas con el
        # modelo tarda minutos, y oír el audio antes de montar es justo el uso
        # de este módulo.
        firma = clonvoz.marca(voz_clon, clon_idioma, 0.5, 0.5)
        reusada = reusar and reusar_narracion(project, settings, firma)
        if reusada and on_progress:
            on_progress("Reusando tu voz clonada")
        elif not reusada:
            clonvoz.generar(
                project, voz_clon, settings, idioma=clon_idioma, on_progress=on_progress
            )
            guardar_narracion(project, settings, firma)
    else:
        reusada = narrar_o_reusar(
            project,
            settings,
            voice_filter=voicefx.resolve(settings.voice_fx, style_key),
            reuse=reusar,
            on_progress=on_progress,
        )

    if on_progress:
        on_progress("Uniendo la narración")
    ffmpeg.concat_demuxer(
        [s.audio_path for s in project.scenes],
        project.root / "audio.txt",
        project.narration_path,
    )

    mp3 = project.root / "narracion.mp3"
    _a_mp3(project.narration_path, mp3, speed)

    # Con velocidad distinta de 1 el audio dura menos (o más), así que los
    # tiempos de los subtítulos se escalan igual. Sin esto, el SRT serviría
    # solo para la pista a 1×.
    factor = 1.0 / speed if abs(speed - 1.0) > 1e-3 else 1.0

    escenas_srt = subtitles.write_srt(
        [(s.start * factor, s.end * factor, s.text) for s in project.scenes],
        project.root / "escenas.srt",
    )
    subtitulos_srt = subtitles.write_srt(
        [(a * factor, b * factor, t) for a, b, t in subtitles.words_to_srt(project.all_words())],
        project.root / "subtitulos.srt",
    )

    texto = project.root / "narracion.txt"
    texto.write_text("\n\n".join(s.text for s in project.scenes), encoding="utf-8")

    datos = project.root / "escenas.json"
    datos.write_text(
        json.dumps(
            {
                "proyecto": name,
                "voz": settings.voice.name,
                "efecto": settings.voice_fx,
                "velocidad": speed,
                "duracion": round(project.total_duration * factor, 3),
                "escenas": [
                    {
                        "n": s.index + 1,
                        "inicio": round(s.start * factor, 3),
                        "fin": round(s.end * factor, 3),
                        "duracion": round(s.duration * factor, 3),
                        "narracion": s.text,
                        "texto_pantalla": bloques[s.index].pantalla if s.index < len(bloques) else "",
                        "prompt": bloques[s.index].prompt if s.index < len(bloques) else s.image_subject,
                        "audio": s.audio_path.name if s.audio_path else "",
                    }
                    for s in project.scenes
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return AudioResult(
        project=project,
        mp3=mp3,
        wav=project.narration_path,
        escenas_srt=escenas_srt,
        subtitulos_srt=subtitulos_srt,
        texto=texto,
        datos=datos,
        duracion=project.total_duration * factor,
        reusada=reusada,
    )
