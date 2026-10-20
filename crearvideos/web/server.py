"""Servidor web local para CrearVideos.

Es la misma tubería del CLI con una página delante: se pega el guion, se elige
voz e imágenes, y el video aparece en el navegador. Pensado para uno mismo en
su PC — se ata a 127.0.0.1 y no se expone a la red.

Sin dependencias nuevas: todo sale de la librería estándar. Un trabajo corre en
su propio hilo (la tubería tira de ffmpeg y de la red, así que el hilo no
bloquea al servidor) y la página pregunta el estado cada segundo.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from .. import compose, music, styles, voicefx
from ..config import FORMATOS, OUTPUT_DIR, load_settings
from ..ffmpeg import FFmpegError
from ..pipeline import create

WEB_DIR = Path(__file__).resolve().parent
INDEX = WEB_DIR / "index.html"

# Registro de trabajos en curso. Un usuario, pocos trabajos: un dict en memoria
# basta y evita arrastrar una base de datos a un MVP de escritorio.
_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_counter = 0


def _slug(text: str) -> str:
    import unicodedata

    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[\s_-]+", "-", text) or "video"


def _default_name(guion: str) -> str:
    """Nombre a partir de las primeras palabras del guion, si no dieron uno."""
    words = re.sub(r"[^\w\s]", " ", guion).split()[:5]
    return _slug(" ".join(words)) if words else "video"


def _decode_portada(data: str, destino: Path) -> Path | None:
    """Decodifica la portada que subió el navegador (dataURL base64) a un archivo.

    Devuelve la ruta, o None si no vino ninguna o no se pudo leer. Un dato
    ilegible no debe tumbar el trabajo: simplemente se sigue sin portada.
    """
    if not data:
        return None
    try:
        base64_str = data.split(",", 1)[-1]  # quita el "data:image/…;base64,"
        destino.write_bytes(base64.b64decode(base64_str))
        return destino
    except (ValueError, OSError):
        return None


def _run_job(
    job_id: str,
    guion: str,
    name: str,
    voz: str,
    imagenes: str,
    modelo: str,
    estilo: str,
    director: bool,
    musica: str,
    volumen: float | None,
    voz_fx: str,
    portada_data: str,
    referencias_data: list,
    usar_referencia: bool,
    gratis: bool,
    voz_propia: str = "",
    voz_clon: str = "",
    formato: str = "vertical",
    por_parrafo: bool = False,
) -> None:
    from ..clonvoz import ClonError
    from ..vozpropia import VozPropiaError

    settings = load_settings()
    settings.pollinations_free = gratis
    settings.scene.por_parrafo = por_parrafo
    if voz:
        settings.voice.name = voz
    if imagenes:
        settings.image_provider = imagenes
    if modelo:
        settings.image_model = modelo
    if volumen is not None:
        # El deslizador manda: se acota a [0, 1] por si llega algo raro.
        settings.music.volume = max(0.0, min(1.0, volumen))
    if voicefx.is_known(voz_fx):
        settings.voice_fx = voz_fx

    def progress(msg: str) -> None:
        with _lock:
            _jobs[job_id]["mensaje"] = msg

    # La portada se escribe en la carpeta del proyecto antes de crearlo; create()
    # la lee al final. ensure_dirs() dentro de create() no la borra.
    portada = None
    if portada_data:
        (OUTPUT_DIR / name).mkdir(parents=True, exist_ok=True)
        portada = _decode_portada(portada_data, OUTPUT_DIR / name / "_portada_subida")

    # Personajes de referencia: las imágenes que el usuario subió en su propia
    # sección, más la portada si marcó la casilla. En modo gratis no aplica: la
    # edición con referencia va por el gateway, que necesita token.
    referencias: list[Path] = []
    if not gratis:
        destino = OUTPUT_DIR / name
        destino.mkdir(parents=True, exist_ok=True)
        for i, dato in enumerate(referencias_data or []):
            ruta = _decode_portada(dato, destino / f"_ref_{i:02d}")
            if ruta is not None:
                referencias.append(ruta)
        if usar_referencia and portada is not None:
            referencias.append(portada)

    try:
        project, cost = create(
            guion,
            name,
            settings,
            style=styles.resolve(estilo),
            style_key=estilo,
            direct=director,
            music=music.elegir(musica, style=estilo, seed=name),
            portada=portada,
            references=referencias,
            voz_propia=Path(voz_propia) if voz_propia.strip() else None,
            voz_clon=Path(voz_clon) if voz_clon.strip() else None,
            formato=formato,
            on_progress=progress,
        )
        with _lock:
            _jobs[job_id].update(
                estado="listo",
                mensaje="Listo",
                video=f"/video/{project.name}/{project.output_path.name}",
                duracion=round(project.total_duration, 1),
                escenas=len(project.scenes),
                palabras=len(project.all_words()),
                costo=round(cost, 3),
            )
    except (FFmpegError, VozPropiaError, ClonError, RuntimeError, ValueError) as e:
        # Errores esperados (falta la clave, ffmpeg falló, guion vacío): el
        # mensaje ya está redactado para leerse, se muestra tal cual.
        with _lock:
            _jobs[job_id].update(estado="error", mensaje=str(e))
    except Exception as e:  # noqa: BLE001 - la web no debe tumbarse por un bug
        traceback.print_exc()
        with _lock:
            _jobs[job_id].update(estado="error", mensaje=f"Error inesperado: {e}")
    finally:
        # La portada subida ya se normalizó a portada.jpg; el original no se guarda.
        if portada is not None:
            portada.unlink(missing_ok=True)


def _run_retime(job_id: str, proyecto: str, factor: float) -> None:
    """Reexporta el MP4 de un proyecto a otra velocidad, en su propio hilo."""
    src = OUTPUT_DIR / proyecto / f"{proyecto}.mp4"
    if not src.is_file():
        with _lock:
            _jobs[job_id].update(estado="error", mensaje="No se encontró el video original.")
        return
    try:
        etiqueta = f"{factor}".replace(".", "_").rstrip("0").rstrip("_")
        dst = src.with_name(f"{src.stem}_x{etiqueta}.mp4")
        compose.retime(src, dst, factor)
        with _lock:
            _jobs[job_id].update(
                estado="listo",
                mensaje="Listo",
                video=f"/video/{proyecto}/{dst.name}",
            )
    except (FFmpegError, ValueError) as e:
        with _lock:
            _jobs[job_id].update(estado="error", mensaje=str(e))
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        with _lock:
            _jobs[job_id].update(estado="error", mensaje=f"Error inesperado: {e}")


def _start_retime(proyecto: str, factor: float) -> str:
    global _counter
    with _lock:
        _counter += 1
        job_id = str(_counter)
        _jobs[job_id] = {"estado": "trabajando", "mensaje": f"Ajustando velocidad a {factor}×"}
    threading.Thread(
        target=_run_retime, args=(job_id, proyecto, factor), daemon=True
    ).start()
    return job_id


def _proyecto_voces(datos: dict):
    """Prepara el proyecto de voces a partir del guion que llegó."""
    from .. import voces as voces_lib

    guion = (datos.get("guion") or "").strip()
    if not guion:
        raise voces_lib.VocesError("El guion está vacío.")
    nombre = (datos.get("nombre") or "").strip()
    name = _slug(nombre) if nombre else _default_name(guion)
    settings = load_settings()
    if datos.get("voz"):
        settings.voice.name = datos["voz"]
    project, _ = voces_lib.preparar(guion, name, settings)
    return project, settings, name


def _respuesta_voces(project, settings, name: str) -> dict:
    from .. import voces as voces_lib

    escenas = voces_lib.estado(project, settings)
    return {
        "proyecto": name,
        "escenas": [
            {
                **e.to_dict(),
                "url": f"/archivo/{name}/audio/{e.audio}" if e.audio else "",
            }
            for e in escenas
        ],
        "resumen": voces_lib.resumen(escenas),
        "carpeta": str(project.root),
    }


def _run_instalar_voz(job_id: str) -> None:
    """Instala el entorno de voz clonada. Son varios gigas: va en su hilo."""
    from ..clonvoz import ClonError, instalar

    def progress(msg: str) -> None:
        with _lock:
            _jobs[job_id]["mensaje"] = msg

    try:
        mensaje = instalar(on_progress=progress)
        with _lock:
            _jobs[job_id].update(estado="listo", mensaje=mensaje)
    except (ClonError, OSError) as e:
        with _lock:
            _jobs[job_id].update(estado="error", mensaje=str(e))
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        with _lock:
            _jobs[job_id].update(estado="error", mensaje=f"Error inesperado: {e}")


def _run_voces(job_id: str, datos: dict) -> None:
    """Genera las escenas pendientes (o las pedidas) con su voz asignada."""
    from .. import voces as voces_lib

    def progress(msg: str) -> None:
        with _lock:
            _jobs[job_id]["mensaje"] = msg

    try:
        project, settings, name = _proyecto_voces(datos)
        solo = datos.get("solo")
        solo = [int(i) for i in solo] if solo else None
        voces_lib.generar(
            project, settings, solo=solo, rehacer=bool(solo), on_progress=progress
        )
        with _lock:
            _jobs[job_id].update(
                estado="listo", mensaje="Listo", **_respuesta_voces(project, settings, name)
            )
    except (voces_lib.VocesError, FFmpegError, RuntimeError, ValueError, OSError) as e:
        with _lock:
            _jobs[job_id].update(estado="error", mensaje=str(e))
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        with _lock:
            _jobs[job_id].update(estado="error", mensaje=f"Error inesperado: {e}")


def _start_voces(datos: dict) -> str:
    global _counter
    with _lock:
        _counter += 1
        job_id = str(_counter)
        _jobs[job_id] = {"estado": "trabajando", "mensaje": "Preparando"}
    threading.Thread(target=_run_voces, args=(job_id, datos), daemon=True).start()
    return job_id


def _run_audio(
    job_id: str,
    guion: str,
    name: str,
    voz: str,
    voz_fx: str,
    velocidad: float,
    reusar: bool = True,
) -> None:
    """Módulo audio: narra el guion entero sin montar video."""
    from .. import narracion

    def progress(msg: str) -> None:
        with _lock:
            _jobs[job_id]["mensaje"] = msg

    settings = load_settings()
    if voz:
        settings.voice.name = voz
    if voicefx.is_known(voz_fx):
        settings.voice_fx = voz_fx

    try:
        r = narracion.create_audio(
            guion, name, settings, speed=velocidad, reusar=reusar, on_progress=progress
        )
        with _lock:
            _jobs[job_id].update(
                estado="listo",
                mensaje="Listo",
                audio=f"/archivo/{name}/{r.mp3.name}",
                wav=f"/archivo/{name}/{r.wav.name}",
                escenas_srt=f"/archivo/{name}/{r.escenas_srt.name}",
                subtitulos_srt=f"/archivo/{name}/{r.subtitulos_srt.name}",
                datos=f"/archivo/{name}/{r.datos.name}",
                texto=f"/archivo/{name}/{r.texto.name}",
                duracion=round(r.duracion, 1),
                escenas=r.escenas,
                palabras=r.palabras,
                voz=("reusada" if r.reusada else "generada ahora"),
                carpeta=str(r.project.root),
            )
    except (FFmpegError, RuntimeError, ValueError) as e:
        with _lock:
            _jobs[job_id].update(estado="error", mensaje=str(e))
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        with _lock:
            _jobs[job_id].update(estado="error", mensaje=f"Error inesperado: {e}")


def _start_audio(guion: str, nombre: str, voz: str, voz_fx: str, velocidad: float) -> str:
    global _counter
    with _lock:
        _counter += 1
        job_id = str(_counter)
        _jobs[job_id] = {"estado": "trabajando", "mensaje": "Preparando"}
    name = _slug(nombre) if nombre.strip() else _default_name(guion)
    threading.Thread(
        target=_run_audio, args=(job_id, guion, name, voz, voz_fx, velocidad), daemon=True
    ).start()
    return job_id


def _run_montaje(
    job_id: str,
    guion: str,
    name: str,
    carpeta: str,
    formato: str,
    voz: str,
    voz_fx: str,
    musica: str,
    volumen: float | None,
    subtitulos: bool,
    reusar_voz: bool,
    voz_propia: str,
    voz_clon: str,
    por_parrafo: bool = False,
) -> None:
    """Módulo montaje: el guion + los medios del usuario -> video."""
    from .. import montaje
    from ..clonvoz import ClonError
    from ..medios import MediosError
    from ..vozpropia import VozPropiaError

    def progress(msg: str) -> None:
        with _lock:
            _jobs[job_id]["mensaje"] = msg

    settings = load_settings()
    settings.scene.por_parrafo = por_parrafo
    if voz:
        settings.voice.name = voz
    if voicefx.is_known(voz_fx):
        settings.voice_fx = voz_fx
    if volumen is not None:
        settings.music.volume = max(0.0, min(1.0, volumen))

    try:
        r = montaje.montar(
            guion,
            name,
            settings,
            Path(carpeta),
            formato=formato,
            music=music.elegir(musica, style=None, seed=name),
            subtitulos=subtitulos,
            reusar_voz=reusar_voz,
            voz_propia=Path(voz_propia) if voz_propia.strip() else None,
            voz_clon=Path(voz_clon) if voz_clon.strip() else None,
            on_progress=progress,
        )
        with _lock:
            _jobs[job_id].update(
                estado="listo",
                mensaje="Listo",
                video=f"/video/{name}/{r.video.name}",
                formato=f"{r.formato} ({r.tamaño[0]}x{r.tamaño[1]})",
                duracion=round(r.duracion, 1),
                escenas=len(r.project.scenes),
                voz=r.origen_voz,
                avisos=r.avisos,
                carpeta=str(r.project.root),
            )
    except (FFmpegError, MediosError, VozPropiaError, ClonError, RuntimeError, ValueError) as e:
        with _lock:
            _jobs[job_id].update(estado="error", mensaje=str(e))
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        with _lock:
            _jobs[job_id].update(estado="error", mensaje=f"Error inesperado: {e}")


def _start_montaje(datos: dict) -> str:
    global _counter
    guion = (datos.get("guion") or "").strip()
    with _lock:
        _counter += 1
        job_id = str(_counter)
        _jobs[job_id] = {"estado": "trabajando", "mensaje": "Preparando"}
    nombre = datos.get("nombre", "")
    name = _slug(nombre) if nombre.strip() else _default_name(guion)
    try:
        volumen = float(datos["volumen"]) if "volumen" in datos else None
    except (TypeError, ValueError):
        volumen = None
    threading.Thread(
        target=_run_montaje,
        args=(
            job_id,
            guion,
            name,
            datos.get("carpeta", ""),
            datos.get("formato", "vertical"),
            datos.get("voz", ""),
            datos.get("voz_fx", "none"),
            datos.get("musica", "no"),
            volumen,
            datos.get("subtitulos", True) is not False,
            datos.get("reusar_voz", True) is not False,
            datos.get("voz_propia", ""),
            datos.get("voz_clon", ""),
            datos.get("por_parrafo") is True,
        ),
        daemon=True,
    ).start()
    return job_id


def _run_plan(job_id: str, datos: dict) -> None:
    """Planea clips de duración fija narrando el guion con la voz real."""
    from .. import planclips

    def progress(msg: str) -> None:
        with _lock:
            _jobs[job_id]["mensaje"] = msg

    guion = (datos.get("guion") or "").strip()
    settings = load_settings()
    if datos.get("voz"):
        settings.voice.name = datos["voz"]
    if voicefx.is_known(datos.get("voz_fx", "none")):
        settings.voice_fx = datos.get("voz_fx", "none")
    try:
        max_seg = max(2.0, min(30.0, float(datos.get("max_seg") or 8)))
    except (TypeError, ValueError):
        max_seg = 8.0
    # El nombre debe ser el que el montaje deducirá del guion planeado: así
    # encuentra la narración ya hecha y no vuelve a narrar.
    nombre = (datos.get("nombre") or "").strip()
    name = _slug(nombre) if nombre else _default_name(" ".join(planclips._bloques(guion)))
    try:
        plan = planclips.planear(guion, name, settings, max_seg=max_seg, on_progress=progress)
        from .. import pausas

        with _lock:
            _jobs[job_id].update(
                estado="listo",
                mensaje="Listo",
                nombre=name,
                guion=plan.guion,
                total=round(plan.total, 1),
                max_seg=max_seg,
                voz=plan.voz,
                avisos=plan.avisos,
                escenas=[
                    {"n": i, "duracion": round(e.duracion, 2),
                     "texto": pausas.limpiar(e.texto), "corte": e.corte_forzado}
                    for i, e in enumerate(plan.escenas, 1)
                ],
                lista=f"/archivo/{name}/plan_clips.txt",
                guion_txt=f"/archivo/{name}/guion_planeado.txt",
                carpeta=str(plan.carpeta),
            )
    except (planclips.PlanError, FFmpegError, RuntimeError, ValueError, OSError) as e:
        with _lock:
            _jobs[job_id].update(estado="error", mensaje=str(e))
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        with _lock:
            _jobs[job_id].update(estado="error", mensaje=f"Error inesperado: {e}")


def _start_plan(datos: dict) -> str:
    global _counter
    with _lock:
        _counter += 1
        job_id = str(_counter)
        _jobs[job_id] = {"estado": "trabajando", "mensaje": "Preparando"}
    threading.Thread(target=_run_plan, args=(job_id, datos), daemon=True).start()
    return job_id


#: Filtros del diálogo de archivo, por tipo de campo.
_FILTROS = {
    "audio": "Audio|*.mp3;*.wav;*.m4a;*.aac;*.ogg;*.opus;*.flac;*.mpeg;*.wma|Todos|*.*",
    "imagen": "Imágenes|*.jpg;*.jpeg;*.png;*.webp;*.bmp|Todos|*.*",
    "video": "Video|*.mp4;*.mov;*.webm;*.mkv;*.avi|Todos|*.*",
    "texto": "Guiones|*.txt;*.md|Todos|*.*",
    "json": "JSON|*.json|Todos|*.*",
}

#: Abre el diálogo nativo de Windows. Se usa PowerShell con Windows Forms en
#: vez de tkinter porque tkinter no viaja dentro del .exe que se reparte, y
#: esto está en cualquier Windows. `-STA` es obligatorio: sin él, los diálogos
#: no se abren. El formulario invisible que hace de dueño es lo que evita que
#: la ventana salga DETRÁS del navegador, que parecería que no pasó nada.
_PS_ELEGIR = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.Opacity = 0
$owner.Show()
if ('{tipo}' -eq 'carpeta') {{
    $d = New-Object System.Windows.Forms.FolderBrowserDialog
    $d.Description = '{titulo}'
    $d.ShowNewFolderButton = $true
    if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {{ [Console]::Out.WriteLine($d.SelectedPath) }}
}} else {{
    $d = New-Object System.Windows.Forms.OpenFileDialog
    $d.Title = '{titulo}'
    $d.Filter = '{filtro}'
    if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {{ [Console]::Out.WriteLine($d.FileName) }}
}}
$owner.Close()
"""


def _elegir_ruta(tipo: str, filtro: str, titulo: str) -> str:
    """Abre el explorador de Windows y devuelve lo que el usuario elija."""
    import subprocess

    comando = _PS_ELEGIR.format(
        tipo="carpeta" if tipo == "carpeta" else "archivo",
        titulo=titulo.replace("'", " ")[:80] or "Elige",
        filtro=_FILTROS.get(filtro, _FILTROS["texto"]).replace("'", " "),
    )
    try:
        proc = subprocess.run(
            ["powershell", "-STA", "-NoProfile", "-NonInteractive", "-Command", comando],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return ""
    return (proc.stdout or "").strip().splitlines()[-1].strip() if proc.stdout.strip() else ""


def _proyectos_publicables() -> list[dict]:
    """Todo proyecto del que se puedan sacar capítulos.

    No solo los que tienen archivo de tiempos: los videos viejos conservan sus
    audios por escena y los subtítulos, y de ahí se reconstruye la línea de
    tiempo. Antes esos no aparecían en la lista y parecía que se habían
    perdido.
    """
    filas = []
    for carpeta in OUTPUT_DIR.iterdir() if OUTPUT_DIR.is_dir() else []:
        if not carpeta.is_dir() or carpeta.name.startswith("_"):
            continue

        fila = None
        for archivo in ("montaje.json", "escenas.json"):
            ruta = carpeta / archivo
            if not ruta.is_file():
                continue
            try:
                datos = json.loads(ruta.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            escenas = datos.get("escenas") or []
            if not escenas:
                continue
            ultima = escenas[-1]
            fila = {
                "nombre": carpeta.name,
                "escenas": len(escenas),
                "duracion": round(float(ultima.get("inicio", 0)) + float(ultima.get("duracion", 0))),
                "exacto": True,
                "fecha": ruta.stat().st_mtime,
            }
            break

        if fila is None:
            # Videos de "solo unir clips": no pasan por el guion, pero el
            # ensamblaje guarda la duración exacta de cada segmento.
            ruta = carpeta / "ensamblaje.json"
            if ruta.is_file():
                try:
                    datos = json.loads(ruta.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    datos = {}
                segmentos = datos.get("segmentos") or []
                if segmentos:
                    fila = {
                        "nombre": carpeta.name,
                        "escenas": len(segmentos),
                        "duracion": round(float(datos.get("duracion") or 0)),
                        "exacto": True,
                        "sin_texto": True,
                        "fecha": ruta.stat().st_mtime,
                    }

        if fila is None:
            # Sin archivo de tiempos: se mira si quedan los audios por escena.
            audios = sorted((carpeta / "audio").glob("escena_*.wav"))
            if not audios:
                continue
            video = carpeta / f"{carpeta.name}.mp4"
            referencia = video if video.is_file() else audios[-1]
            try:
                from ..ffmpeg import duration

                total = duration(video) if video.is_file() else 0.0
            except Exception:  # noqa: BLE001 - sin ffmpeg o archivo roto
                total = 0.0
            fila = {
                "nombre": carpeta.name,
                "escenas": len(audios),
                "duracion": round(total),
                "exacto": False,
                "fecha": referencia.stat().st_mtime,
            }

        fila["video"] = (carpeta / f"{carpeta.name}.mp4").is_file()
        fila["publicado"] = (carpeta / "publicacion.json").is_file()
        filas.append(fila)

    filas.sort(key=lambda f: f["fecha"], reverse=True)
    return filas


def _run_publicar(job_id: str, proyecto: str, guion: str, contexto: str) -> None:
    """Módulo publicar: título, descripción, capítulos y SEO para YouTube."""
    from .. import publicar
    from ..estudio.ia import IAError

    def progress(msg: str) -> None:
        with _lock:
            _jobs[job_id]["mensaje"] = msg

    try:
        paquete = publicar.generar(
            guion if guion.strip() else proyecto,
            contexto=contexto,
            es_guion=bool(guion.strip()),
            on_progress=progress,
        )
        with _lock:
            _jobs[job_id].update(estado="listo", mensaje="Listo", paquete=paquete)
    except (publicar.PublicarError, IAError, OSError, ValueError) as e:
        with _lock:
            _jobs[job_id].update(estado="error", mensaje=str(e))
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        with _lock:
            _jobs[job_id].update(estado="error", mensaje=f"Error inesperado: {e}")


def _start_publicar(proyecto: str, guion: str, contexto: str) -> str:
    global _counter
    with _lock:
        _counter += 1
        job_id = str(_counter)
        _jobs[job_id] = {"estado": "trabajando", "mensaje": "Preparando"}
    threading.Thread(
        target=_run_publicar, args=(job_id, proyecto, guion, contexto), daemon=True
    ).start()
    return job_id


def _run_union(job_id: str, carpeta: str, name: str, opciones: dict) -> None:
    """Une segmentos que ya traen su voz. Sin narración ni subtítulos."""
    from .. import ensamblar as ens

    def progress(msg: str) -> None:
        with _lock:
            _jobs[job_id]["mensaje"] = msg

    try:
        pista = music.elegir(opciones.get("musica", "no"), style=None, seed=name)
        r = ens.ensamblar(
            Path(carpeta),
            name,
            formato=opciones.get("formato", "original"),
            transicion=opciones.get("transicion", "ninguna"),
            duracion_transicion=float(opciones.get("duracion", 0.5) or 0.5),
            musica=pista,
            volumen_musica=float(opciones.get("volumen", 0.12) or 0.12),
            on_progress=progress,
        )
        with _lock:
            _jobs[job_id].update(
                estado="listo",
                mensaje="Listo",
                video=f"/video/{name}/{r.salida.name}",
                formato=f"{r.formato} ({r.tamaño[0]}x{r.tamaño[1]})",
                duracion=round(r.duracion, 1),
                segmentos=len(r.segmentos),
                recorte=round(r.suma_clips - r.duracion, 1),
                transicion=r.transicion,
                carpeta=str(r.salida.parent),
            )
    except (ens.EnsamblarError, FFmpegError, ValueError, OSError) as e:
        with _lock:
            _jobs[job_id].update(estado="error", mensaje=str(e))
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        with _lock:
            _jobs[job_id].update(estado="error", mensaje=f"Error inesperado: {e}")


def _start_union(carpeta: str, nombre: str, opciones: dict) -> str:
    global _counter
    with _lock:
        _counter += 1
        job_id = str(_counter)
        _jobs[job_id] = {"estado": "trabajando", "mensaje": "Preparando"}
    name = _slug(nombre) if nombre.strip() else _slug(Path(carpeta).name)
    threading.Thread(target=_run_union, args=(job_id, carpeta, name, opciones), daemon=True).start()
    return job_id


def _run_subida(job_id: str, proyecto: str, opciones: dict) -> None:
    """Sube el video a YouTube. Sale del PC del usuario: se confirma antes."""
    from .. import youtube_subir as yt

    def progress(msg: str) -> None:
        with _lock:
            _jobs[job_id]["mensaje"] = msg

    try:
        r = yt.subir(
            proyecto,
            privacidad=opciones.get("privacidad", "privado"),
            categoria=opciones.get("categoria", "educacion"),
            titulo=(opciones.get("titulo") or "").strip() or None,
            programar=(opciones.get("programar") or "").strip() or None,
            on_progress=progress,
        )
        with _lock:
            _jobs[job_id].update(estado="listo", mensaje="Listo", subida=r)
    except (yt.YouTubeError, OSError, ValueError) as e:
        with _lock:
            _jobs[job_id].update(estado="error", mensaje=str(e))
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        with _lock:
            _jobs[job_id].update(estado="error", mensaje=f"Error inesperado: {e}")


def _start_subida(proyecto: str, opciones: dict) -> str:
    global _counter
    with _lock:
        _counter += 1
        job_id = str(_counter)
        _jobs[job_id] = {"estado": "trabajando", "mensaje": "Preparando la subida"}
    threading.Thread(target=_run_subida, args=(job_id, proyecto, opciones), daemon=True).start()
    return job_id


def _run_estetica(job_id: str, canal: str, extra: str) -> None:
    """Genera el tablero visual de un canal ya analizado."""
    from ..estudio import estetica

    def progress(msg: str) -> None:
        with _lock:
            _jobs[job_id]["mensaje"] = msg

    try:
        t = estetica.crear(canal, extra=extra, on_progress=progress)
        base = f"/api/estudio/imagen?canal={quote(canal)}&pieza="
        with _lock:
            _jobs[job_id].update(
                estado="listo",
                mensaje="Listo",
                concepto=t.concepto,
                paleta=t.paleta,
                carpeta=str(t.carpeta),
                avisos=t.avisos,
                piezas=[
                    {**p, "url": base + quote(p["clave"])} for p in t.piezas
                ],
                personaje=(t.carpeta / "personaje.txt").read_text(encoding="utf-8").strip()
                if (t.carpeta / "personaje.txt").is_file() else "",
            )
    except (estetica.EsteticaError, RuntimeError, OSError) as e:
        with _lock:
            _jobs[job_id].update(estado="error", mensaje=str(e))
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        with _lock:
            _jobs[job_id].update(estado="error", mensaje=f"Error inesperado: {e}")


def _start_estetica(canal: str, extra: str) -> str:
    global _counter
    with _lock:
        _counter += 1
        job_id = str(_counter)
        _jobs[job_id] = {"estado": "trabajando", "mensaje": "Preparando"}
    threading.Thread(target=_run_estetica, args=(job_id, canal, extra), daemon=True).start()
    return job_id


def _run_analisis(job_id: str, url: str, videos: int, profundidad: int, usar_ia: bool) -> None:
    """Módulo estudio: analiza un video o canal en su propio hilo."""
    from ..estudio import analizador, youtube

    def progress(msg: str) -> None:
        with _lock:
            _jobs[job_id]["mensaje"] = msg

    try:
        tipo, _ = youtube.tipo_de_url(url)
        if tipo == "video":
            informe, carpeta = analizador.analizar_video(url, usar_ia, progress)
        else:
            informe, carpeta = analizador.analizar_canal(url, videos, profundidad, usar_ia, progress)
        with _lock:
            _jobs[job_id].update(estado="listo", mensaje="Listo", **_respuesta_informe(informe, carpeta))
    except youtube.YouTubeError as e:
        with _lock:
            _jobs[job_id].update(estado="error", mensaje=str(e))
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        with _lock:
            _jobs[job_id].update(estado="error", mensaje=f"Error inesperado: {e}")


def _respuesta_informe(informe: dict, carpeta: Path) -> dict:
    from ..estudio import analizador

    from .. import guionista

    analisis = informe.get("analisis") or {}
    return {
        "tipo": informe["tipo"],
        "md": (carpeta / "informe.md").read_text(encoding="utf-8"),
        "cabecera": analizador.cabecera_guion(analisis),
        # Las ideas que ya propuso el análisis: son el mejor punto de partida
        # para el guion, porque salieron de mirar lo que funciona en ese canal.
        "ideas": [
            {"titulo": i.get("titulo", ""), "angulo": i.get("angulo", ""),
             "gancho": i.get("gancho", "")}
            for i in (analisis.get("ideas") or [])
            if isinstance(i, dict) and i.get("titulo")
        ],
        "clave": f"{carpeta.parent.name}/{carpeta.name}",
        "tiene_formula": bool(guionista.desde_informe(informe)),
        "carpeta": str(carpeta),
        "aviso": informe.get("aviso", ""),
        "miniatura": (informe.get("video") or {}).get("miniatura", ""),
    }


def _start_analisis(url: str, videos: int, profundidad: int, usar_ia: bool) -> str:
    global _counter
    with _lock:
        _counter += 1
        job_id = str(_counter)
        _jobs[job_id] = {"estado": "trabajando", "mensaje": "Preparando"}
    threading.Thread(
        target=_run_analisis, args=(job_id, url, videos, profundidad, usar_ia), daemon=True
    ).start()
    return job_id


#: Frase de la muestra de voz: corta, con acentos y con una pausa, que es donde
#: se nota si la voz convence o no.
TEXTO_MUESTRA = "Así va a sonar la narración de tu video. ¿Te convence esta voz?"


def _muestra_voz(voz: str, voz_fx: str) -> str:
    """Genera (o reutiliza) un MP3 corto con esa voz y ese efecto.

    Es lo que evita montar un video entero para descubrir que la voz no era la
    que querías. Se guarda en proyectos/_muestras, así que la segunda vez que
    eliges la misma voz suena al instante.
    """
    from ..config import VoiceSettings
    from ..providers.tts.edge import EdgeTTS

    slug = _slug(f"{voz}-{voz_fx or 'none'}")
    carpeta = OUTPUT_DIR / "_muestras"
    carpeta.mkdir(parents=True, exist_ok=True)
    final = carpeta / f"{slug}.mp3"
    if final.is_file():
        return f"/archivo/_muestras/{final.name}"

    crudo = carpeta / f"{slug}_crudo.mp3"
    EdgeTTS(VoiceSettings(name=voz)).synthesize(TEXTO_MUESTRA, crudo)
    filtro = voicefx.resolve(voz_fx, None)
    if filtro:
        wav = carpeta / f"{slug}_fx.wav"
        from .. import ffmpeg as ff

        ff.apply_filter(crudo, wav, filtro)
        ff.run(["-i", str(wav.resolve()), "-c:a", "libmp3lame", "-b:a", "128k", str(final.resolve())])
        wav.unlink(missing_ok=True)
        crudo.unlink(missing_ok=True)
    else:
        crudo.replace(final)
    return f"/archivo/_muestras/{final.name}"


def _historial_estudio() -> list[dict]:
    """Análisis ya hechos, del más reciente al más antiguo."""
    from ..estudio.analizador import ESTUDIO_DIR

    filas = []
    for f in ESTUDIO_DIR.glob("*/*/informe.json"):
        try:
            inf = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        es_video = inf.get("tipo") == "video"
        filas.append(
            {
                "clave": f"{f.parent.parent.name}/{f.parent.name}",
                "tipo": inf.get("tipo", ""),
                "titulo": (inf.get("video") or {}).get("titulo") if es_video
                else (inf.get("canal") or {}).get("nombre"),
                "fecha": inf.get("fecha_analisis", ""),
            }
        )
    filas.sort(key=lambda x: x["fecha"], reverse=True)
    return filas


def _start_job(
    guion: str,
    nombre: str,
    voz: str,
    imagenes: str,
    modelo: str,
    estilo: str,
    director: bool,
    musica: str,
    volumen: float | None,
    voz_fx: str,
    portada_data: str,
    referencias_data: list,
    usar_referencia: bool,
    gratis: bool,
    voz_propia: str = "",
    voz_clon: str = "",
    formato: str = "vertical",
    por_parrafo: bool = False,
) -> str:
    global _counter
    with _lock:
        _counter += 1
        job_id = str(_counter)
        _jobs[job_id] = {"estado": "trabajando", "mensaje": "Preparando"}
    name = _slug(nombre) if nombre.strip() else _default_name(guion)
    threading.Thread(
        target=_run_job,
        args=(job_id, guion, name, voz, imagenes, modelo, estilo, director,
              musica, volumen, voz_fx, portada_data, referencias_data,
              usar_referencia, gratis, voz_propia, voz_clon, formato, por_parrafo),
        daemon=True,
    ).start()
    return job_id


#: Clave de acceso. Vacía = sin clave (uso normal en tu propio PC). En cuanto
#: la app sale a internet por un túnel hace falta: varios endpoints leen y
#: escriben rutas del disco (carpetas de medios, audios, proyectos), así que
#: publicarla sin clave es abrir el PC a quien adivine la dirección.
_clave: str = ""


class Handler(BaseHTTPRequestHandler):
    # Silencia el log por request: en una herramienta de un usuario es ruido.
    def log_message(self, *args) -> None:  # noqa: D401
        pass

    # --- utilidades de respuesta ---

    def _no_cache(self) -> None:
        """Prohíbe la caché del navegador.

        Sin esto, el navegador guarda index.html por su cuenta (no hay
        cabeceras que se lo impidan) y sigue mostrando la página vieja después
        de tocar el código: aparece un fallo fantasma donde una función nueva
        "no está". Lo mismo con /api/musica, que cambia cuando el usuario deja
        una pista en la carpeta. Es una app local: no hay nada que optimizar
        cacheando, y sí mucho que romper.
        """
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")

    def _json(self, obj: dict, code: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._no_cache()
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self._no_cache()
        self.end_headers()
        self.wfile.write(data)

    # --- media con soporte de rangos (para arrastrar la barra del reproductor) ---

    #: extensión -> content-type, para servir las pistas de audio a escucha.
    _AUDIO_TYPES = {
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
        ".aac": "audio/aac", ".ogg": "audio/ogg", ".opus": "audio/ogg",
        ".flac": "audio/flac",
    }

    #: Lo que el módulo de audio deja para descargar, además de las pistas.
    _ARCHIVO_TYPES = {
        ".srt": "text/plain; charset=utf-8", ".txt": "text/plain; charset=utf-8",
        ".json": "application/json; charset=utf-8", ".ass": "text/plain; charset=utf-8",
        ".mp4": "video/mp4", ".jpg": "image/jpeg", ".png": "image/png",
    }

    def _serve_media(self, base: Path, rel: str, content_type: str) -> None:
        target = (base / rel).resolve()
        # Nadie sube por encima de la carpeta base con un ../ en la URL.
        if not target.is_file() or base.resolve() not in target.parents:
            self._json({"error": "no encontrado"}, 404)
            return

        size = target.stat().st_size
        rango = self.headers.get("Range")
        with target.open("rb") as f:
            if rango and rango.startswith("bytes="):
                inicio_s, _, fin_s = rango[6:].partition("-")
                inicio = int(inicio_s) if inicio_s else 0
                fin = int(fin_s) if fin_s else size - 1
                fin = min(fin, size - 1)
                largo = fin - inicio + 1
                f.seek(inicio)
                data = f.read(largo)
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {inicio}-{fin}/{size}")
            else:
                data = f.read()
                self.send_response(200)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    def _serve_audio(self, rel: str) -> None:
        rel = unquote(rel)
        tipo = self._AUDIO_TYPES.get(Path(rel).suffix.lower(), "application/octet-stream")
        self._serve_media(music.MUSIC_DIR, rel, tipo)

    # --- rutas ---

    def _autorizado(self) -> bool:
        """Con clave puesta, exige autenticación básica del navegador.

        Se usa la del propio navegador (ventanita de usuario y contraseña) en
        vez de una pantalla propia: funciona igual en el móvil, se recuerda, y
        protege TODAS las rutas, incluidas las que sirven archivos.
        """
        if not _clave:
            return True

        # En tu propio PC no se pide nada. Lo que entra por el túnel también
        # llega desde 127.0.0.1 (cloudflared corre aquí), pero Cloudflare le
        # añade sus cabeceras: eso es lo que distingue "yo, en mi máquina" de
        # "alguien desde internet". Sin túnel no hay forma de llegar al puerto
        # desde fuera, así que nadie puede fingir ser local.
        de_fuera = any(
            self.headers.get(h) for h in ("Cf-Connecting-Ip", "Cf-Ray", "X-Forwarded-For")
        )
        if not de_fuera and self.client_address[0] in ("127.0.0.1", "::1"):
            return True

        cabecera = self.headers.get("Authorization", "")
        if cabecera.startswith("Basic "):
            try:
                usuario, _, contra = base64.b64decode(cabecera[6:]).decode().partition(":")
                if contra == _clave or usuario == _clave:
                    return True
            except (ValueError, UnicodeDecodeError):
                pass
        cuerpo = b"Hace falta la clave de acceso de CrearVideos."
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="CrearVideos"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)
        return False

    def do_GET(self) -> None:
        if not self._autorizado():
            return
        ruta = urlparse(self.path)
        if ruta.path == "/":
            self._send_file(INDEX, "text/html; charset=utf-8")
        elif ruta.path == "/api/estilos":
            from ..ensamblar import TRANSICIONES
            from ..providers.images import pollinations as poll

            self._json(
                {
                    "estilos": styles.options(),
                    "efectos": voicefx.options(),
                    "modelos": poll.model_options(),
                    "transiciones": [
                        {"value": k, "label": d} for k, (_, d) in TRANSICIONES.items()
                    ],
                    "modelo_actual": load_settings().image_model or poll.DEFAULT_MODEL,
                }
            )
        elif ruta.path == "/api/musica":
            # `carpetas` y `moods` van para que la página pueda avisar de que
            # "Automática" no encontrará nada para el estilo elegido, en vez de
            # entregar un video mudo sin explicación.
            opciones = music.options()
            for o in opciones:
                # URL para escuchar la pista antes de generar. auto/no no tienen.
                o["url"] = f"/audio/{o['rel']}" if o.get("rel") else ""
            self._json(
                {
                    "musica": opciones,
                    "carpeta": str(music.MUSIC_DIR),
                    "carpetas": {k: len(v) for k, v in music.library().items()},
                    "moods": music.MOODS,
                }
            )
        elif ruta.path == "/api/estado":
            qs = parse_qs(ruta.query)
            job_id = (qs.get("id") or [""])[0]
            with _lock:
                estado = dict(_jobs.get(job_id, {"estado": "desconocido"}))
            self._json(estado)
        elif ruta.path == "/api/voz-estado":
            from ..clonvoz import TAMANO_APROXIMADO_GB, disponible

            ok, motivo = disponible()
            self._json({"instalada": ok, "motivo": motivo, "gb": TAMANO_APROXIMADO_GB})
        elif ruta.path == "/api/ping":
            # Sirve para saber si en este puerto hay un CrearVideos y no otra
            # cosa. Lo usa el arranque para no abrir una segunda copia.
            self._json({"app": "crearvideos", "datos": str(OUTPUT_DIR.parent)})
        elif ruta.path == "/api/proyectos":
            from .. import youtube_subir as yt

            ok, motivo = yt.configurado()
            self._json(
                {
                    "proyectos": _proyectos_publicables(),
                    "youtube": {"configurado": ok, "autorizado": yt.autorizado(), "motivo": motivo},
                }
            )
        elif ruta.path == "/api/estudio":
            self._json({"analisis": _historial_estudio()})
        elif ruta.path == "/api/estudio/imagen":
            from ..estudio.analizador import ESTUDIO_DIR

            qs = parse_qs(ruta.query)
            canal = _slug((qs.get("canal") or [""])[0])
            pieza = _slug((qs.get("pieza") or [""])[0])
            for sub in ("canales", "videos"):
                carpeta = ESTUDIO_DIR / sub / canal / "estetica"
                if (carpeta / f"{pieza}.jpg").is_file():
                    self._serve_media(carpeta, f"{pieza}.jpg", "image/jpeg")
                    return
            self._json({"error": "no encontrada"}, 404)
        elif ruta.path == "/api/estudio/informe":
            from ..estudio.analizador import ESTUDIO_DIR

            clave = (parse_qs(ruta.query).get("clave") or [""])[0]
            carpeta = (ESTUDIO_DIR / clave).resolve()
            archivo = carpeta / "informe.json"
            # Nadie sale de estudio/ con un ../ en la clave.
            if ESTUDIO_DIR.resolve() not in carpeta.parents or not archivo.is_file():
                self._json({"error": "no encontrado"}, 404)
                return
            informe = json.loads(archivo.read_text(encoding="utf-8"))
            self._json(_respuesta_informe(informe, carpeta))
        elif ruta.path.startswith("/video/"):
            self._serve_media(OUTPUT_DIR, ruta.path[len("/video/") :], "video/mp4")
        elif ruta.path == "/api/oir":
            # Reproduce la grabación que el usuario señaló, para que confirme
            # que la ruta apunta al audio que cree. Solo audio, solo lectura.
            origen = Path(unquote((parse_qs(ruta.query).get("ruta") or [""])[0]))
            # set(...) explícito: `dict | set` es un TypeError, no una unión.
            permitidas = set(self._AUDIO_TYPES) | {".mpeg", ".mpga", ".webm"}
            if not origen.is_file() or origen.suffix.lower() not in permitidas:
                self._json({"error": "no es un archivo de audio"}, 404)
                return
            self._serve_media(
                origen.parent, origen.name, self._AUDIO_TYPES.get(origen.suffix.lower(), "audio/mpeg")
            )
        elif ruta.path.startswith("/archivo/"):
            rel = unquote(ruta.path[len("/archivo/") :])
            ext = Path(rel).suffix.lower()
            tipo = self._AUDIO_TYPES.get(ext) or self._ARCHIVO_TYPES.get(
                ext, "application/octet-stream"
            )
            self._serve_media(OUTPUT_DIR, rel, tipo)
        elif ruta.path.startswith("/audio/"):
            self._serve_audio(ruta.path[len("/audio/") :])
        else:
            self._json({"error": "ruta desconocida"}, 404)

    def do_POST(self) -> None:
        if not self._autorizado():
            return
        ruta = urlparse(self.path).path
        if ruta not in (
            "/api/crear", "/api/velocidad", "/api/analizar", "/api/audio",
            "/api/medios", "/api/montar", "/api/revisar", "/api/muestra-voz",
            "/api/revisar-clon", "/api/prueba-clon", "/api/publicar", "/api/subir",
            "/api/nichos", "/api/guion", "/api/catalogo", "/api/estetica", "/api/elegir",
            "/api/segmentos", "/api/unir",
            "/api/voces", "/api/voces/asignar", "/api/voces/generar", "/api/voces/unir",
            "/api/instalar-voz", "/api/planear",
        ):
            self._json({"error": "ruta desconocida"}, 404)
            return
        largo = int(self.headers.get("Content-Length", 0))
        crudo = self.rfile.read(largo) or b"{}"
        try:
            # El navegador manda UTF-8; se decodifica explícito para que un
            # cuerpo con otra codificación dé un 400 y no tumbe el handler.
            datos = json.loads(crudo.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json({"error": "cuerpo inválido (se espera JSON en UTF-8)"}, 400)
            return

        if ruta == "/api/elegir":
            # El explorador se abre en la MÁQUINA donde corre el programa. Desde
            # el móvil no tendría sentido: saldría una ventana en el PC que
            # nadie está mirando, así que se dice en vez de dejarlo colgado.
            if any(self.headers.get(h) for h in ("Cf-Connecting-Ip", "Cf-Ray", "X-Forwarded-For")):
                self._json(
                    {"error": "El explorador solo se puede abrir en el PC donde corre "
                              "CrearVideos. Desde el móvil, escribe la ruta a mano."},
                    400,
                )
                return
            self._json(
                {
                    "ruta": _elegir_ruta(
                        datos.get("tipo", "carpeta"),
                        datos.get("filtro", "texto"),
                        datos.get("titulo", "Elige"),
                    )
                }
            )
            return

        if ruta == "/api/estetica":
            canal = (datos.get("canal") or "").strip()
            if not canal:
                self._json({"error": "Falta el canal analizado."}, 400)
                return
            self._json({"id": _start_estetica(canal, datos.get("extra", ""))})
            return

        if ruta == "/api/catalogo":
            from ..estudio import nichos

            try:
                filas = nichos.explorar(int(datos.get("muestras", 6) or 6))
            except (ValueError, OSError) as e:
                self._json({"error": str(e)}, 400)
                return
            self._json({"nichos": filas})
            return

        if ruta == "/api/nichos":
            from ..estudio import nichos
            from ..estudio.youtube import YouTubeError

            consulta = (datos.get("consulta") or "").strip()
            if not consulta:
                self._json({"error": "Dime qué nicho quieres explorar."}, 400)
                return
            try:
                r = nichos.buscar(
                    consulta,
                    limite=max(4, min(int(datos.get("limite", 12) or 12), 24)),
                    orden=datos.get("orden", "virales"),
                    tipo=datos.get("tipo", "todos"),
                    solo_faceless=bool(datos.get("faceless")),
                    clasificar=datos.get("clasificar", True) is not False,
                )
            except (YouTubeError, ValueError) as e:
                self._json({"error": str(e)}, 400)
                return
            nichos.guardar(consulta, r)
            self._json(
                {
                    "consulta": consulta,
                    "orden": nichos.ORDENES.get(datos.get("orden", "virales"), ("",))[0],
                    "resultados": [h.to_dict() for h in r],
                    "resumen": {
                        k: v for k, v in nichos.resumen(r).items() if k != "mejor_pagado"
                    },
                }
            )
            return

        if ruta == "/api/guion":
            from .. import guionista
            from ..estudio.analizador import ESTUDIO_DIR
            from ..estudio.ia import IAError

            tema = (datos.get("tema") or "").strip()
            if not tema:
                self._json({"error": "Dime de qué va el video."}, 400)
                return

            referencia = datos.get("referencia") or None
            # Con `informe` se usa el ADN COMPLETO del análisis (gancho,
            # estructura, retención, tono, estilo visual), no solo el título.
            clave = (datos.get("informe") or "").strip()
            if clave:
                archivo = (ESTUDIO_DIR / clave / "informe.json").resolve()
                if ESTUDIO_DIR.resolve() not in archivo.parents or not archivo.is_file():
                    self._json({"error": "No encuentro ese análisis."}, 400)
                    return
                referencia = guionista.desde_informe(
                    json.loads(archivo.read_text(encoding="utf-8"))
                )
                if not referencia:
                    self._json(
                        {"error": "Ese análisis se hizo sin IA, así que no tiene fórmula "
                                  "que copiar. Vuelve a analizarlo con el análisis de IA."},
                        400,
                    )
                    return
            datos["referencia"] = referencia
            try:
                g = guionista.escribir(
                    tema,
                    minutos=float(datos.get("minutos", 3) or 3),
                    referencia=referencia,
                    contexto=datos.get("contexto", ""),
                )
            except (guionista.GuionistaError, IAError, ValueError) as e:
                self._json({"error": str(e)}, 400)
                return
            self._json(
                {
                    "titulo": g.titulo,
                    "gancho": g.gancho,
                    "texto": g.texto,
                    "escenas": g.escenas,
                    "palabras": g.palabras,
                    "duracion": g.duracion_estimada,
                    "ruta": str(g.ruta) if g.ruta else "",
                }
            )
            return

        if ruta == "/api/revisar":
            # Cuántas escenas y cuánto duraría, para la revisión previa. No
            # genera nada: solo parte el guion.
            from ..script import parse

            guion = (datos.get("guion") or "").strip()
            if not guion:
                self._json({"error": "El guion está vacío."}, 400)
                return
            try:
                escena = load_settings().scene
                escena.por_parrafo = datos.get("por_parrafo") is True
                scenes, _ = parse(guion, escena)
            except ValueError as e:
                self._json({"error": str(e)}, 400)
                return
            from .. import pausas

            palabras = sum(len(pausas.limpiar(s.text).split()) for s in scenes)
            silencio = sum(pausas.segundos_totales(s.text) for s in scenes)
            self._json(
                {
                    "escenas": len(scenes),
                    "palabras": palabras,
                    "duracion_estimada": round(palabras / 2.85 + silencio),
                    "pausas": round(silencio, 1),
                }
            )
            return

        if ruta == "/api/instalar-voz":
            global _counter
            with _lock:
                _counter += 1
                job_id = str(_counter)
                _jobs[job_id] = {"estado": "trabajando", "mensaje": "Preparando la instalación"}
            threading.Thread(target=_run_instalar_voz, args=(job_id,), daemon=True).start()
            self._json({"id": job_id})
            return

        if ruta.startswith("/api/voces"):
            from .. import voces as voces_lib

            try:
                if ruta == "/api/voces/generar":
                    # Generar sí tarda: va en su propio hilo, como el resto.
                    self._json({"id": _start_voces(datos)})
                    return

                project, settings, name = _proyecto_voces(datos)

                if ruta == "/api/voces/asignar":
                    voz = (datos.get("voz") or "").strip()
                    fx = datos.get("fx", "none")
                    tipo = datos.get("tipo") or voces_lib.GENERADA
                    if datos.get("todas"):
                        voces_lib.asignar_todas(
                            project,
                            voz,
                            fx,
                            tipo=tipo,
                            carpeta=(datos.get("carpeta") or "").strip(),
                            muestra=(datos.get("muestra") or "").strip(),
                            forzar=bool(datos.get("forzar")),
                        )
                    else:
                        for i in datos.get("escenas") or []:
                            voces_lib.asignar(
                                project,
                                int(i),
                                voz,
                                fx,
                                tipo=tipo,
                                archivo=(datos.get("archivo") or "").strip(),
                                muestra=(datos.get("muestra") or "").strip(),
                            )
                elif ruta == "/api/voces/unir":
                    mp3 = voces_lib.exportar_mp3(project)
                    self._json({"url": f"/archivo/{name}/{mp3.name}"})
                    return

                self._json(_respuesta_voces(project, settings, name))
            except (voces_lib.VocesError, FFmpegError, ValueError, OSError) as e:
                self._json({"error": str(e)}, 400)
            return

        if ruta == "/api/segmentos":
            from .. import ensamblar as ens

            carpeta = (datos.get("carpeta") or "").strip()
            if not carpeta:
                self._json({"error": "Falta la carpeta con los segmentos."}, 400)
                return
            try:
                segmentos = ens.revisar(Path(carpeta))
            except (ens.EnsamblarError, FFmpegError, OSError) as e:
                self._json({"error": str(e)}, 400)
                return
            self._json(
                {
                    "total": round(sum(s.duracion for s in segmentos), 1),
                    "sin_audio": sum(1 for s in segmentos if not s.tiene_audio and not s.es_imagen),
                    "tamaños": sorted({f"{s.ancho}x{s.alto}" for s in segmentos}),
                    "segmentos": [
                        {
                            "n": i + 1,
                            "archivo": s.path.name,
                            "duracion": round(s.duracion, 1),
                            "tamaño": f"{s.ancho}x{s.alto}",
                            "audio": s.tiene_audio,
                            "imagen": s.es_imagen,
                        }
                        for i, s in enumerate(segmentos)
                    ],
                }
            )
            return

        if ruta == "/api/unir":
            carpeta = (datos.get("carpeta") or "").strip()
            if not carpeta:
                self._json({"error": "Falta la carpeta con los segmentos."}, 400)
                return
            self._json({"id": _start_union(carpeta, datos.get("nombre", ""), datos)})
            return

        if ruta == "/api/subir":
            from .. import youtube_subir as yt

            proyecto = (datos.get("proyecto") or "").strip()
            if not proyecto:
                self._json({"error": "Falta el proyecto."}, 400)
                return
            ok, motivo = yt.configurado()
            if not ok:
                self._json({"error": motivo}, 400)
                return
            if not yt.autorizado():
                self._json(
                    {
                        "error": "Todavía no autorizaste tu canal. Abre una terminal en la "
                        "carpeta del proyecto y ejecuta:  python -m crearvideos youtube-conectar"
                    },
                    400,
                )
                return
            self._json({"id": _start_subida(proyecto, datos)})
            return

        if ruta == "/api/publicar":
            proyecto = (datos.get("proyecto") or "").strip()
            guion = (datos.get("guion") or "").strip()
            if not proyecto and not guion:
                self._json({"error": "Elige un proyecto o pega un guion."}, 400)
                return
            self._json({"id": _start_publicar(proyecto, guion, datos.get("contexto", ""))})
            return

        if ruta == "/api/revisar-clon":
            # Comprueba la ruta que escribió el usuario y dice qué se usaría,
            # sin generar nada: es instantáneo.
            from ..clonvoz import ClonError, MUESTRA_SEGUNDOS, revisar_muestra

            try:
                info = revisar_muestra(Path((datos.get("ruta") or "").strip()))
            except (ClonError, OSError, FFmpegError) as e:
                self._json({"error": str(e)}, 400)
                return
            self._json(
                {
                    "archivo": info["archivo"].name,
                    "ruta": str(info["archivo"]),
                    "duracion": round(info["duracion"], 1),
                    "usara": min(MUESTRA_SEGUNDOS, info["duracion"]),
                    "aviso": info["aviso"],
                    "escuchar": "/api/oir?ruta=" + quote(str(info["archivo"])),
                }
            )
            return

        if ruta == "/api/prueba-clon":
            from ..clonvoz import ClonError, prueba

            job = (datos.get("ruta") or "").strip()
            if not job:
                self._json({"error": "falta la muestra"}, 400)
                return
            try:
                mp3 = prueba(Path(job))
            except (ClonError, FFmpegError, OSError) as e:
                self._json({"error": str(e)}, 400)
                return
            self._json({"url": f"/archivo/_muestras/clon/{mp3.name}"})
            return

        if ruta == "/api/muestra-voz":
            voz = (datos.get("voz") or "").strip()
            if not voz:
                self._json({"error": "falta la voz"}, 400)
                return
            try:
                self._json({"url": _muestra_voz(voz, datos.get("voz_fx", "none"))})
            except (FFmpegError, RuntimeError) as e:
                self._json({"error": str(e)}, 400)
            return

        if ruta == "/api/medios":
            from .. import montaje
            from ..medios import MediosError

            guion = (datos.get("guion") or "").strip()
            carpeta = (datos.get("carpeta") or "").strip()
            if not guion or not carpeta:
                self._json({"error": "Hacen falta el guion y la carpeta de medios."}, 400)
                return
            try:
                ajustes = load_settings()
                ajustes.scene.por_parrafo = datos.get("por_parrafo") is True
                self._json(montaje.previsualizar(guion, Path(carpeta), ajustes))
            except (MediosError, ValueError) as e:
                self._json({"error": str(e)}, 400)
            return

        if ruta == "/api/planear":
            if not (datos.get("guion") or "").strip():
                self._json({"error": "El guion está vacío."}, 400)
                return
            self._json({"id": _start_plan(datos)})
            return

        if ruta == "/api/montar":
            if not (datos.get("guion") or "").strip():
                self._json({"error": "El guion está vacío."}, 400)
                return
            if not (datos.get("carpeta") or "").strip():
                self._json({"error": "Falta la carpeta con tus imágenes y videos."}, 400)
                return
            self._json({"id": _start_montaje(datos)})
            return

        if ruta == "/api/audio":
            guion = (datos.get("guion") or "").strip()
            if not guion:
                self._json({"error": "El guion está vacío."}, 400)
                return
            try:
                velocidad = max(0.5, min(2.0, float(datos.get("velocidad", 1.0))))
            except (TypeError, ValueError):
                velocidad = 1.0
            self._json(
                {
                    "id": _start_audio(
                        guion,
                        datos.get("nombre", ""),
                        datos.get("voz", ""),
                        datos.get("voz_fx", "none"),
                        velocidad,
                    )
                }
            )
            return

        if ruta == "/api/analizar":
            url = (datos.get("url") or "").strip()
            if not url:
                self._json({"error": "Pega la URL de un video o el @ de un canal."}, 400)
                return
            try:
                videos = max(5, min(100, int(datos.get("videos", 30))))
                profundidad = max(0, min(8, int(datos.get("profundidad", 3))))
            except (TypeError, ValueError):
                videos, profundidad = 30, 3
            self._json({"id": _start_analisis(url, videos, profundidad, datos.get("ia", True) is not False)})
            return

        if ruta == "/api/velocidad":
            proyecto = _slug(datos.get("proyecto", ""))
            try:
                factor = float(datos.get("factor"))
            except (TypeError, ValueError):
                self._json({"error": "factor inválido"}, 400)
                return
            if not proyecto:
                self._json({"error": "falta el proyecto"}, 400)
                return
            self._json({"id": _start_retime(proyecto, factor)})
            return

        guion = (datos.get("guion") or "").strip()
        if not guion:
            self._json({"error": "El guion está vacío."}, 400)
            return

        try:
            volumen = float(datos["volumen"]) if "volumen" in datos else None
        except (TypeError, ValueError):
            volumen = None  # un volumen ilegible no debe impedir crear el video

        job_id = _start_job(
            guion,
            datos.get("nombre", ""),
            datos.get("voz", ""),
            datos.get("imagenes", ""),
            datos.get("modelo", ""),
            datos.get("estilo", ""),
            datos.get("director", True) is not False,
            datos.get("musica", "auto"),
            volumen,
            datos.get("voz_fx", "auto"),
            datos.get("portada", ""),
            datos.get("referencias") or [],
            datos.get("usar_referencia", False) is True,
            datos.get("gratis", False) is True,
            datos.get("voz_propia", ""),
            datos.get("voz_clon", ""),
            # Un formato desconocido cae al vertical en vez de romper el trabajo.
            datos.get("formato") if datos.get("formato") in FORMATOS else "vertical",
            datos.get("por_parrafo") is True,
        )
        self._json({"id": job_id})


def serve(host: str = "127.0.0.1", port: int = 8000, abrir: bool = True, clave: str = "") -> None:
    """Levanta el servidor y (opcional) abre el navegador."""
    global _clave

    _clave = clave or os.getenv("CREARVIDEOS_CLAVE", "")
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}"
    if abrir:
        # Un respiro para que el socket esté escuchando antes de abrir la pestaña.
        threading.Timer(0.5, _abrir_navegador, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def _abrir_navegador(url: str) -> None:
    import webbrowser

    webbrowser.open(url)
