"""Montaje con medios propios: tus imágenes y clips + la voz generada aquí.

Es el pipeline completo menos la parte de generar imágenes. Tú traes una
carpeta con las imágenes y los videos de cada escena —de Google Flow,
Midjourney, stock, tu cámara— y esto pone la narración, los subtítulos
sincronizados palabra por palabra, la música y el corte.

    guion.txt  +  carpeta de medios
        |
        +-- voz por escena  -> duración exacta de cada escena
        +-- cada medio      -> clip de esa duración exacta
        |                      imagen -> Ken Burns; video -> recorte (o bucle)
        +-- tiempos         -> subtítulos .ass
        |
    video.mp4

Quien manda sobre la duración es la narración, no el archivo: un clip de 3 s en
una escena de 8 s se repite, y uno de 20 s se corta. Así nunca hay que editar
los medios a mano para que cuadren.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import (
    clonvoz,
    compose,
    ffmpeg,
    importar,
    medios as medios_lib,
    pausas,
    subtitles,
    voicefx,
    vozpropia,
)
from .config import OUTPUT_DIR, Settings, formato as resolver_formato
from .models import Project
from .pipeline import guardar_narracion, narrar_o_reusar, reusar_narracion
from .script import parse


@dataclass
class MontajeResult:
    project: Project
    emparejado: medios_lib.Emparejado
    formato: str
    tamaño: tuple[int, int]
    duracion: float
    #: True si se usó la narración ya generada antes (pestaña Audio o un
    #: montaje anterior) en vez de sintetizarla de nuevo.
    voz_reusada: bool = False
    #: "generada" | "reusada" | "tuya (archivo)" | "tuya (carpeta)"
    origen_voz: str = "generada"

    @property
    def video(self) -> Path:
        return self.project.output_path

    @property
    def avisos(self) -> list[str]:
        return self.emparejado.avisos


def _recorte(texto: str, largo: int) -> str:
    return texto if len(texto) <= largo else texto[:largo].rstrip() + "…"


def previsualizar(script_source: str | Path, carpeta: Path, settings: Settings | None = None) -> dict:
    """Qué archivo iría en cada escena, sin generar ni narrar nada.

    Es barato a propósito: deja ver y corregir los nombres de los archivos
    antes de gastar dos minutos narrando.
    """
    settings = settings or Settings()
    raw = (
        script_source.read_text(encoding="utf-8")
        if isinstance(script_source, Path)
        else script_source
    )
    scenes, _ = parse(raw, settings.scene)
    encontrados = medios_lib.recolectar(Path(carpeta))
    emp = medios_lib.emparejar(encontrados, len(scenes))
    palabras = sum(len(pausas.limpiar(s.text).split()) for s in scenes)
    silencio = sum(pausas.segundos_totales(s.text) for s in scenes)
    return {
        "escenas": len(scenes),
        "palabras": palabras,
        # Estimación antes de narrar: ~2.85 palabras por segundo es el ritmo
        # medido de edge-tts al +8% que usa la app, más las pausas marcadas.
        # La duración real la fija el audio, no esta cuenta.
        "duracion_estimada": round(palabras / 2.85 + silencio),
        "archivos": len(encontrados),
        "metodo": emp.metodo,
        "avisos": emp.avisos,
        "filas": [
            # Solo para la tabla de vista previa; con "…" para que se vea que
            # sigue (la narración usa siempre el texto completo).
            {**fila, "texto": _recorte(pausas.limpiar(s.text), 90)}
            for fila, s in zip(medios_lib.resumen(emp, len(scenes)), scenes)
        ],
    }


def montar(
    script_source: str | Path,
    name: str,
    settings: Settings,
    carpeta: Path,
    *,
    formato: str = "vertical",
    style_key: str | None = None,
    music: Path | None = None,
    subtitulos: bool = True,
    reusar_voz: bool = True,
    voz_propia: Path | None = None,
    voz_clon: Path | None = None,
    clon_idioma: str = "es",
    output_dir: Path | None = None,
    on_progress=None,
) -> MontajeResult:
    """Monta el video usando los medios de `carpeta`."""

    def report(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    tamaño = resolver_formato(formato)

    raw = (
        script_source.read_text(encoding="utf-8")
        if isinstance(script_source, Path)
        else script_source
    )
    scenes, _meta = parse(raw, settings.scene)
    bloques = importar.parse_storyflow(raw) if importar.es_storyflow(raw) else []

    # Los medios se resuelven ANTES de narrar: si la carpeta está vacía o mal
    # escrita, debe fallar en el primer segundo y no después de dos minutos de
    # voz.
    encontrados = medios_lib.recolectar(Path(carpeta))
    emparejado = medios_lib.emparejar(encontrados, len(scenes))

    root = (output_dir or OUTPUT_DIR) / name
    project = Project(name=name, root=root, scenes=scenes)
    project.ensure_dirs()

    # Tu propia voz manda sobre todo lo demás: si traes audio, no se sintetiza
    # nada. Si no, se aprovecha la voz ya generada (mismo nombre de proyecto,
    # mismo guion, misma voz) o se narra.
    voz_reusada = False
    origen_voz = "generada"
    if voz_propia is not None:
        origen_voz = "tuya (" + vozpropia.aplicar(project, voz_propia, settings, on_progress) + ")"
    elif voz_clon is not None:
        # Clonar tarda minutos, así que se reusa igual que la voz generada: la
        # marca incluye la muestra y su fecha, y cambiar cualquiera la invalida.
        firma = clonvoz.marca(voz_clon, clon_idioma, 0.5, 0.5)
        if reusar_voz and reusar_narracion(project, settings, firma):
            voz_reusada, origen_voz = True, "clonada (reusada)"
            if on_progress:
                on_progress("Reusando tu voz clonada")
        else:
            modelo = clonvoz.generar(
                project, voz_clon, settings, idioma=clon_idioma, on_progress=on_progress
            )
            guardar_narracion(project, settings, firma)
            origen_voz = f"clonada · {modelo}"
    else:
        voz_reusada = narrar_o_reusar(
            project,
            settings,
            voice_filter=voicefx.resolve(settings.voice_fx, style_key),
            reuse=reusar_voz,
            on_progress=on_progress,
        )
        if voz_reusada:
            origen_voz = "reusada"

    clips: list[Path] = []
    for scene in project.scenes:
        medio = emparejado.medios[scene.index]
        clip = project.clip_dir / f"clip_{scene.index:03d}.mp4"
        etiqueta = medio.path.name if medio else "sin medio"
        report(
            f"Montando escena {scene.index + 1}/{len(project.scenes)} "
            f"({scene.duration:.1f}s · {etiqueta})"
        )
        if medio is None:
            raise ValueError(
                f"La escena {scene.index + 1} se quedó sin medio y no hay ninguno anterior."
            )
        if medio.es_video:
            compose.render_video_clip(medio.path, clip, scene.duration, size=tamaño)
        else:
            compose.render_clip(scene, clip, size=tamaño, source=medio.path)
        clips.append(clip)

    report("Uniendo clips")
    video_raw = project.root / "video_mudo.mp4"
    ffmpeg.concat_demuxer(clips, project.root / "clips.txt", video_raw)

    report("Uniendo narración")
    ffmpeg.concat_demuxer(
        [s.audio_path for s in project.scenes],
        project.root / "audio.txt",
        project.narration_path,
    )

    report("Generando subtítulos")
    estilo = subtitles.escalar_estilo(settings.subtitle, tamaño)
    subtitles.write_ass(project.all_words(), project.subtitle_path, estilo, tamaño)
    subtitles.write_srt(
        subtitles.words_to_srt(project.all_words()), project.root / "subtitulos.srt"
    )

    # El parte de montaje: qué archivo quedó en cada escena y en qué segundo.
    # Es lo que se mira cuando algo se ve fuera de sitio.
    (project.root / "montaje.json").write_text(
        json.dumps(
            {
                "formato": formato,
                "tamaño": list(tamaño),
                "voz_reusada": voz_reusada,
                "origen_voz": origen_voz,
                "carpeta_medios": str(Path(carpeta).resolve()),
                "metodo_emparejado": emparejado.metodo,
                "avisos": emparejado.avisos,
                "escenas": [
                    {
                        "n": s.index + 1,
                        "inicio": round(s.start, 3),
                        "duracion": round(s.duration, 3),
                        "medio": emparejado.medios[s.index].path.name,
                        "tipo": emparejado.medios[s.index].tipo,
                        "narracion": pausas.limpiar(s.text),
                        "texto_pantalla": bloques[s.index].pantalla if s.index < len(bloques) else "",
                    }
                    for s in project.scenes
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    report("Quemando subtítulos y mezclando audio" if subtitulos else "Mezclando audio")
    compose.finalize(project, video_raw, settings, music=music, subtitulos=subtitulos)

    return MontajeResult(
        project=project,
        emparejado=emparejado,
        formato=formato,
        tamaño=tamaño,
        duracion=project.total_duration,
        voz_reusada=voz_reusada,
        origen_voz=origen_voz,
    )
