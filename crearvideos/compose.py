"""Montaje final con ffmpeg: imágenes fijas -> video con voz y subtítulos.

Se hace en tres pasos en vez de un solo filter_complex gigante. Es
ligeramente más lento, pero cada paso deja su archivo en disco, así que
cuando algo sale mal se ve exactamente dónde.

    1. Cada imagen -> un clip mudo con Ken Burns, de la duración exacta
       de su narración.
    2. Los clips se pegan (corte seco) y los audios también.
    3. Se mezcla todo y se queman los subtítulos.

Los cortes son secos, no encadenados. No es por simplicidad: un crossfade
consume tiempo del clip siguiente y desplazaría la narración respecto a la
imagen. El corte seco cae exactamente en la palabra, que además es el
lenguaje visual del formato.
"""

from __future__ import annotations

from pathlib import Path

from . import ffmpeg
from .config import FPS, HEIGHT, SUPERSAMPLE, Settings, WIDTH
from .models import Project, Scene

# Recorridos de cámara. Se alternan por escena para que el video respire.
MOVES = ["zoom_in", "pan_right", "zoom_out", "pan_left", "zoom_in_up", "pan_down"]

ZOOM = 0.14  # cuánto se acerca la cámara a lo largo de la escena

#: Hasta cuánto se ralentiza un clip propio que se queda corto antes de
#: repetirlo: 1.2 = un 20 % más lento (un clip de 8 s cubre hasta 9,6 s).
ESTIRAR_MAX = 1.2


def _kenburns(move: str, frames: int, size: tuple[int, int] | None = None) -> str:
    """Expresión zoompan para un recorrido.

    Se usa `on` (número de frame de salida) en vez del acumulador `zoom`:
    con una sola imagen de entrada el acumulador es impredecible, mientras
    que `on` da una rampa lineal exacta y reproducible.
    """
    w, h = size or (WIDTH, HEIGHT)
    n = max(frames - 1, 1)
    zin = f"1+{ZOOM}*on/{n}"
    zout = f"{1 + ZOOM}-{ZOOM}*on/{n}"
    cx = "iw/2-(iw/zoom/2)"
    cy = "ih/2-(ih/zoom/2)"

    if move == "zoom_in":
        z, x, y = zin, cx, cy
    elif move == "zoom_out":
        z, x, y = zout, cx, cy
    elif move == "pan_right":
        z, x, y = f"{1 + ZOOM}", f"(iw-iw/zoom)*on/{n}", cy
    elif move == "pan_left":
        z, x, y = f"{1 + ZOOM}", f"(iw-iw/zoom)*(1-on/{n})", cy
    elif move == "pan_down":
        z, x, y = f"{1 + ZOOM}", cx, f"(ih-ih/zoom)*on/{n}"
    elif move == "zoom_in_up":
        z, x, y = zin, cx, f"(ih-ih/zoom)*(1-on/{n})"
    else:
        z, x, y = zin, cx, cy

    return f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={FPS}"


def render_clip(
    scene: Scene,
    out_path: Path,
    *,
    move: str | None = None,
    size: tuple[int, int] | None = None,
    source: Path | None = None,
) -> Path:
    """Convierte la imagen de una escena en un clip mudo de duración exacta.

    `source` permite usar una imagen que no salió del pipeline (la que trajo el
    usuario en su carpeta de medios) sin tocar la escena.
    """
    imagen = source or scene.image_path
    if imagen is None:
        raise ValueError(f"La escena {scene.index + 1} no tiene imagen generada.")

    w, h = size or (WIDTH, HEIGHT)
    frames = max(int(round(scene.duration * FPS)), 1)
    move = move or MOVES[scene.index % len(MOVES)]
    sw, sh = w * SUPERSAMPLE, h * SUPERSAMPLE

    vf = ",".join(
        [
            # Llenar el encuadre y recortar el sobrante: la imagen puede venir
            # en 9:16 (Imagen), 2:3 (OpenAI) o 16:9 (lo que trajo el usuario).
            f"scale={sw}:{sh}:force_original_aspect_ratio=increase",
            f"crop={sw}:{sh}",
            _kenburns(move, frames, (w, h)),
            "setsar=1",
            "format=yuv420p",
        ]
    )

    ffmpeg.run(
        [
            "-loop", "1",
            "-i", str(imagen.resolve()),
            "-vf", vf,
            "-frames:v", str(frames),
            "-r", str(FPS),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            str(out_path.resolve()),
        ]
    )
    return out_path


def render_video_clip(
    src: Path,
    out_path: Path,
    duration: float,
    *,
    size: tuple[int, int] | None = None,
) -> Path:
    """Recorta un video propio a la duración exacta de una escena, mudo.

    Si el clip es un poco más corto que la narración (hasta `ESTIRAR_MAX`) se
    ralentiza para cubrirla; si es mucho más corto se repite
    (`-stream_loop -1`); si es más largo se toma el principio. El audio original se
    descarta: la banda sonora es la narración, y mezclar el audio del clip
    taparía la voz sin que nadie lo pidiera.

    El corte se hace por FOTOGRAMAS, no por segundos: es la misma rejilla que
    usa el resto del pipeline, y es lo que evita que el video se despegue de la
    voz un poco en cada escena.
    """
    w, h = size or (WIDTH, HEIGHT)
    frames = max(int(round(duration * FPS)), 1)

    # Un clip al que le falta poco (los de 8 s de Veo bajo una narración de
    # 9 s) se ralentiza en vez de repetirse: un 10 % más lento casi no se nota;
    # verlo volver a empezar, sí. Si le falta mucho, o no se puede medir, se
    # repite como siempre.
    entrada = ["-stream_loop", "-1"]
    estirar = []
    try:
        propio = ffmpeg.duration(src)
    except (ffmpeg.FFmpegError, KeyError, ValueError):
        propio = 0.0
    if 0 < propio < duration and duration / propio <= ESTIRAR_MAX:
        # Un pelo de más para que nunca falte el último fotograma.
        estirar = [f"setpts={duration / propio * 1.002:.5f}*PTS"]
        entrada = []

    vf = ",".join(
        estirar
        + [
            f"scale={w}:{h}:force_original_aspect_ratio=increase",
            f"crop={w}:{h}",
            f"fps={FPS}",
            "setsar=1",
            "format=yuv420p",
        ]
    )
    ffmpeg.run(
        [
            *entrada,
            "-i", str(src.resolve()),
            "-an",
            "-vf", vf,
            "-frames:v", str(frames),
            "-r", str(FPS),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            str(out_path.resolve()),
        ]
    )
    return out_path


def _audio_filter(project: Project, settings: Settings, music: Path | None) -> tuple[list[str], str]:
    """Devuelve (entradas extra, filtro de audio) según haya música o no.

    Con música, la narración se parte en dos con `asplit`: una copia va a la
    mezcla y la otra es la *llave* del compresor, que solo la escucha para
    decidir cuánto bajar la música. La música pasa por `sidechaincompress`
    con la voz de llave — eso es el ducking: baja sola cuando alguien habla y
    vuelve a subir en los silencios.

    El `-stream_loop -1` de la entrada repite la pista hasta cubrir el video
    aunque sea más corta; `atrim` la corta al final exacto.
    """
    if music is None:
        return [], "[1:a]anull[aout]"

    m = settings.music
    inputs = ["-stream_loop", "-1", "-i", str(music.resolve())]
    total = project.total_duration

    # La pista puede venir en cualquier formato: mono, 48k, lo que sea.
    # sidechaincompress exige que sus dos entradas coincidan, así que la
    # música se iguala a la narración (44100 estéreo) antes de nada.
    cadena = [
        f"[2:a]aformat=sample_rates=44100:channel_layouts=stereo,"
        f"volume={m.volume},atrim=0:{total:.3f},asetpts=N/SR/TB,"
        f"afade=t=in:st=0:d={m.fade_in},"
        f"afade=t=out:st={max(total - m.fade_out, 0):.3f}:d={m.fade_out}[music]"
    ]

    if m.duck:
        cadena.append("[1:a]asplit=2[voz][llave]")
        cadena.append(
            f"[music][llave]sidechaincompress="
            f"threshold={m.duck_threshold}:ratio={m.duck_ratio}:"
            f"attack={m.duck_attack}:release={m.duck_release}:"
            f"makeup=1:level_sc=1[fondo]"
        )
        voz = "[voz]"
    else:
        voz = "[1:a]"
        cadena.append("[music]anull[fondo]")

    # normalize=0: por defecto amix divide cada entrada entre el número de
    # entradas, así que la narración saldría a la mitad solo por añadir música.
    # Aquí se suman tal cual; los niveles ya los fijan volume y el ducking.
    cadena.append(
        f"{voz}[fondo]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
    )
    return inputs, ";".join(cadena)


def finalize(
    project: Project,
    video_raw: Path,
    settings: Settings,
    *,
    music: Path | None = None,
    subtitulos: bool = True,
) -> Path:
    """Une video, narración, música y subtítulos en el MP4 final.

    `subtitulos=False` deja el video sin texto quemado: el máster limpio para
    quien prefiera poner los subtítulos en su editor, o subirlos como pista
    aparte a YouTube.
    """
    music_inputs, audio_filter = _audio_filter(project, settings, music)

    # Ruta relativa a propósito: el comando corre con cwd en la carpeta del
    # proyecto. Ver la nota en ffmpeg.run().
    fade_out = max(project.total_duration - 0.5, 0)
    quemar = f"subtitles={project.subtitle_path.name}:fontsdir=.," if subtitulos else ""
    video_filter = (
        f"[0:v]{quemar}"
        f"fade=t=in:st=0:d=0.4,"
        f"fade=t=out:st={fade_out:.3f}:d=0.5[vout]"
    )

    args = [
        "-i", video_raw.name,
        "-i", project.narration_path.name,
        *music_inputs,
        "-filter_complex", f"{video_filter};{audio_filter}",
        "-map", "[vout]",
        "-map", "[aout]",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-profile:v", "high",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "44100",
        "-movflags", "+faststart",
        "-shortest",
        project.output_path.name,
    ]
    ffmpeg.run(args, cwd=project.root)
    return project.output_path


def _wxh(size: tuple[int, int] | None) -> str:
    w, h = size or (WIDTH, HEIGHT)
    return f"{w}:{h}"


def _fit_vertical(scale_to: str) -> str:
    """Filtro para llenar el encuadre (9:16, 16:9 o 1:1) recortando el sobrante.

    Es el mismo encaje que usan las escenas: la imagen puede venir en cualquier
    proporción, se agranda hasta cubrir el tamaño del video y se
    recorta lo que sobre. Nunca deja franjas negras.
    """
    return (
        f"scale={scale_to}:force_original_aspect_ratio=increase,"
        f"crop={scale_to},setsar=1"
    )


def normalize_cover(src: Path, dst: Path, *, size: tuple[int, int] | None = None) -> Path:
    """Deja una imagen cualquiera como portada del tamaño del video, lista para subir.

    Sirve como miniatura de YouTube (se sube aparte) y como base del fotograma
    de portada que se antepone al video.
    """
    ffmpeg.run(
        [
            "-i", str(src.resolve()),
            "-vf", f"{_fit_vertical(_wxh(size))},format=yuv420p",
            "-frames:v", "1",
            "-q:v", "2",
            str(dst.resolve()),
        ]
    )
    return dst


def add_cover(video_in: Path, cover: Path, video_out: Path, *,
              seconds: float = 2.0, fade: float = 0.35,
              size: tuple[int, int] | None = None) -> Path:
    """Antepone una portada al video: unos segundos fijos y luego el contenido.

    La imagen se encaja al tamaño del video y se sostiene `seconds` como fotograma de
    apertura, con una entrada suave. Así queda como primer fotograma del video,
    que es lo que TikTok ofrece para elegir portada, sin descuadrar nada: los
    subtítulos ya están quemados en el contenido, que solo se desplaza `seconds`
    más tarde.

    Se reencoda una vez (como el cambio de velocidad); solo cuando hay portada.
    """
    filtro = (
        f"[1:v]{_fit_vertical(_wxh(size))},fps={FPS},format=yuv420p,"
        f"fade=t=in:st=0:d={fade}[cov];"
        f"anullsrc=channel_layout=stereo:sample_rate=44100,atrim=0:{seconds:.3f}[sil];"
        f"[cov][0:v]concat=n=2:v=1:a=0[v];"
        f"[sil][0:a]concat=n=2:v=0:a=1[a]"
    )
    ffmpeg.run(
        [
            "-i", str(video_in.resolve()),
            "-loop", "1", "-t", f"{seconds:.3f}", "-i", str(cover.resolve()),
            "-filter_complex", filtro,
            "-map", "[v]",
            "-map", "[a]",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-profile:v", "high",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "44100",
            "-movflags", "+faststart",
            str(video_out.resolve()),
        ]
    )
    return video_out


def _atempo_chain(speed: float) -> str:
    """Cadena de atempo para un factor cualquiera.

    atempo solo acepta [0.5, 2.0] por paso; para ir más allá se encadenan
    varios. Con el rango que expone la app (0.5–2) basta uno, pero encadenar
    deja la puerta abierta sin sorpresas.
    """
    factores = []
    s = speed
    while s > 2.0:
        factores.append(2.0)
        s /= 2.0
    while s < 0.5:
        factores.append(0.5)
        s /= 0.5
    factores.append(s)
    return ",".join(f"atempo={f:.6f}" for f in factores)


def retime(src: Path, dst: Path, speed: float) -> Path:
    """Reexporta un MP4 a otra velocidad, uniforme para imagen y audio.

    `speed` > 1 acelera, < 1 ralentiza. Es lo mismo que el control de velocidad
    de un reproductor: `setpts` re-tiempa el video y `atempo` el audio SIN
    cambiar el tono (nada de voz de ardilla). Los subtítulos ya están quemados
    en los fotogramas, así que se escalan con la imagen y no se descuadran.

    Trabaja sobre el MP4 terminado, no sobre el pipeline: así se puede probar
    otra velocidad sin volver a narrar ni a ilustrar.
    """
    if abs(speed - 1.0) < 1e-3:
        raise ValueError("La velocidad 1.0 no cambia nada.")
    if not 0.25 <= speed <= 4.0:
        raise ValueError("La velocidad debe estar entre 0.25 y 4.0.")

    filtro = f"[0:v]setpts=PTS/{speed:.6f}[v];[0:a]{_atempo_chain(speed)}[a]"
    ffmpeg.run(
        [
            "-i", str(src.resolve()),
            "-filter_complex", filtro,
            "-map", "[v]",
            "-map", "[a]",
            "-c:v", "libx264",
            # veryfast, no medium: es un reexport y el usuario espera en vivo a
            # que salga; el tamaño sube un poco, la calidad visible no baja.
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-profile:v", "high",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "44100",
            "-movflags", "+faststart",
            str(dst.resolve()),
        ]
    )
    return dst


def build_video(project: Project, settings: Settings, *, music: Path | None = None,
                size: tuple[int, int] | None = None, on_progress=None) -> Path:
    """Pasos 1 a 3 del montaje. `on_progress(mensaje)` para reportar avance."""

    def report(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    clips: list[Path] = []
    for scene in project.scenes:
        clip = project.clip_dir / f"clip_{scene.index:03d}.mp4"
        report(f"Montando escena {scene.index + 1}/{len(project.scenes)} ({scene.duration:.1f}s)")
        render_clip(scene, clip, size=size)
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

    report("Quemando subtítulos y mezclando audio")
    return finalize(project, video_raw, settings, music=music)
