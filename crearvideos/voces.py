"""Una voz distinta para cada escena, antes de montar el video.

El caso: no quieres que todo el video lo narre la misma voz. Un diálogo entre
dos personajes, una sección con otro tono, un cierre con voz grave. Aquí eliges
la voz escena por escena, la escuchas, y solo cuando te convence montas.

Lo que lo hace usable es que **cada escena se guarda por separado y con su
huella**: si cambias la voz de la escena 7, se regenera la 7 y nada más. Las
otras 46 no se vuelven a sintetizar ni cambian de sonido.

    voces.json         qué voz lleva cada escena (lo que eliges tú)
    narracion.json     lo ya generado: tiempos, palabras y la huella de cada una
    audio/escena_NNN.wav

Cuando el montaje va a narrar, encuentra esas escenas ya hechas y las usa tal
cual: el video suena exactamente como lo que aprobaste aquí.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import ffmpeg, voicefx
from .config import OUTPUT_DIR, Settings, VoiceSettings
from .models import Project
from .pipeline import firma_fuente, narrar_escenas
from .script import parse

ARCHIVO = "voces.json"


class VocesError(RuntimeError):
    """No se pudo preparar o generar las voces."""


#: De dónde sale la voz de una escena.
GENERADA = "generada"   # edge-tts, la de siempre
PROPIA = "propia"       # un audio que grabaste tú
CLONADA = "clonada"     # tu voz, clonada a partir de una muestra
TIPOS = (GENERADA, PROPIA, CLONADA)


@dataclass
class EscenaVoz:
    """Una escena con la voz que le toca y lo que ya se generó de ella."""

    index: int
    texto: str
    voz: str
    fx: str
    generada: bool
    tipo: str = GENERADA
    archivo: str = ""      # el mp3/wav tuyo, si la escena usa tu grabación
    muestra: str = ""      # la muestra de voz, si la escena va clonada
    duracion: float = 0.0
    inicio: float = 0.0
    audio: str = ""
    aviso: str = ""        # p. ej. "falta el audio de esta escena"

    def to_dict(self) -> dict:
        return {
            "n": self.index + 1,
            "texto": self.texto,
            "voz": self.voz,
            "fx": self.fx,
            "tipo": self.tipo,
            "archivo": self.archivo,
            "muestra": self.muestra,
            "generada": self.generada,
            "duracion": round(self.duracion, 2),
            "inicio": round(self.inicio, 2),
            "audio": self.audio,
            "aviso": self.aviso,
        }


def _ruta(project: Project) -> Path:
    return project.root / ARCHIVO


def cargar(project: Project) -> dict:
    """Lo que el usuario eligió: voz por escena y voz por defecto."""
    try:
        return json.loads(_ruta(project).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"voz_base": "", "fx_base": "none", "escenas": {}}


def guardar(project: Project, datos: dict) -> None:
    project.root.mkdir(parents=True, exist_ok=True)
    _ruta(project).write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")


def asignar(
    project: Project,
    index: int,
    voz: str = "",
    fx: str = "none",
    *,
    tipo: str = GENERADA,
    archivo: str = "",
    muestra: str = "",
) -> dict:
    """Define de dónde sale la voz de UNA escena. No genera nada todavía."""
    if tipo not in TIPOS:
        raise VocesError(f"Tipo de voz desconocido: '{tipo}'. Opciones: {', '.join(TIPOS)}")
    datos = cargar(project)
    datos.setdefault("escenas", {})[str(index)] = {
        "tipo": tipo, "voz": voz, "fx": fx, "archivo": archivo, "muestra": muestra,
    }
    guardar(project, datos)
    return datos


def asignar_todas(
    project: Project,
    voz: str = "",
    fx: str = "none",
    *,
    tipo: str = GENERADA,
    carpeta: str = "",
    muestra: str = "",
    forzar: bool = False,
) -> dict:
    """Pone la misma fuente para todas: el punto de partida normal.

    Con `forzar` se borran las elecciones escena por escena; sin él, lo que ya
    habías ajustado a mano se respeta y esto solo cambia el resto.
    """
    datos = cargar(project)
    datos["tipo_base"] = tipo
    datos["voz_base"] = voz or datos.get("voz_base", "")
    datos["fx_base"] = fx
    datos["carpeta_base"] = carpeta
    datos["muestra_base"] = muestra
    if forzar:
        datos["escenas"] = {}
    guardar(project, datos)
    return datos


def _audio_de_carpeta(carpeta: str, index: int) -> str:
    """El audio que le toca a esta escena dentro de una carpeta.

    Se emparejan igual que los medios del montaje: por el número del nombre
    (`escena_03.mp3`) o, si no lo traen, por orden natural.
    """
    if not carpeta:
        return ""
    try:
        from .vozpropia import recolectar_audios

        audios = recolectar_audios(Path(carpeta))
    except Exception:  # noqa: BLE001 - carpeta que no existe o sin audios
        return ""
    from .medios import _numero

    por_numero = {_numero(p): p for p in audios if _numero(p) is not None}
    # Los nombres suelen empezar en 1; se acepta también empezar en 0.
    for candidato in (index + 1, index):
        if candidato in por_numero:
            return str(por_numero[candidato])
    return str(audios[index]) if index < len(audios) else ""


def voz_de(datos: dict, index: int, settings: Settings) -> dict:
    """De dónde sale la voz de esta escena: tipo, voz, efecto y archivos."""
    propia = (datos.get("escenas") or {}).get(str(index)) or {}
    tipo = propia.get("tipo") or datos.get("tipo_base") or GENERADA

    if tipo == PROPIA:
        archivo = propia.get("archivo") or _audio_de_carpeta(
            datos.get("carpeta_base", ""), index
        )
        return {"tipo": PROPIA, "voz": "", "fx": "none", "archivo": archivo, "muestra": ""}

    if tipo == CLONADA:
        muestra = propia.get("muestra") or datos.get("muestra_base", "")
        return {"tipo": CLONADA, "voz": "", "fx": propia.get("fx", "none"),
                "archivo": "", "muestra": muestra}

    return {
        "tipo": GENERADA,
        "voz": propia.get("voz") or datos.get("voz_base") or settings.voice.name,
        "fx": propia.get("fx") or datos.get("fx_base") or "none",
        "archivo": "",
        "muestra": "",
    }


def _cache(project: Project) -> dict[int, dict]:
    from .pipeline import NARRACION_CACHE

    try:
        datos = json.loads((project.root / NARRACION_CACHE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {i: e for i, e in enumerate(datos.get("escenas") or [])}


def preparar(
    guion: str | Path, nombre: str, settings: Settings, output_dir: Path | None = None
) -> tuple[Project, list[EscenaVoz]]:
    """Parte el guion en escenas y dice en qué estado está cada una."""
    raw = guion.read_text(encoding="utf-8") if isinstance(guion, Path) else guion
    scenes, _ = parse(raw, settings.scene)
    if not scenes:
        raise VocesError("El guion está vacío.")

    project = Project(name=nombre, root=(output_dir or OUTPUT_DIR) / nombre, scenes=scenes)
    project.audio_dir.mkdir(parents=True, exist_ok=True)
    return project, estado(project, settings)


def estado(project: Project, settings: Settings) -> list[EscenaVoz]:
    """Escena por escena: qué voz tiene y si su audio ya está hecho y al día."""
    datos = cargar(project)
    guardadas = _cache(project)
    salida: list[EscenaVoz] = []

    for scene in project.scenes:
        fuente = voz_de(datos, scene.index, settings)
        guardada = guardadas.get(scene.index) or {}
        archivo = project.audio_dir / (guardada.get("audio") or "")
        esperada = firma_fuente(scene.text, fuente, settings)
        # "Generada" no es solo que el archivo exista: tiene que corresponder a
        # ESTE texto con ESTA voz. Si cambias cualquiera de los dos, la escena
        # vuelve a estar pendiente y se ve en la tabla.
        vale = bool(guardada) and guardada.get("firma") == esperada and archivo.is_file()

        aviso = ""
        if fuente["tipo"] == PROPIA and not Path(fuente["archivo"] or "").is_file():
            aviso = "falta el audio de esta escena"
        elif fuente["tipo"] == CLONADA and not fuente["muestra"]:
            aviso = "falta la muestra de voz para clonar"

        salida.append(
            EscenaVoz(
                index=scene.index,
                texto=scene.text,
                voz=fuente["voz"],
                fx=fuente["fx"],
                tipo=fuente["tipo"],
                archivo=fuente["archivo"],
                muestra=fuente["muestra"],
                generada=vale,
                duracion=float(guardada.get("duration", 0.0)) if vale else 0.0,
                inicio=float(guardada.get("start", 0.0)) if vale else 0.0,
                audio=archivo.name if vale else "",
                aviso=aviso,
            )
        )
    return salida


def generar(
    project: Project,
    settings: Settings,
    *,
    solo: list[int] | None = None,
    rehacer: bool = False,
    on_progress=None,
) -> list[EscenaVoz]:
    """Genera las escenas que falten (o las que digas) con su voz asignada.

    `solo` son índices concretos: es lo que usa el botón "rehacer esta escena".
    `rehacer=True` fuerza aunque ya estén al día.
    """
    datos = cargar(project)
    asignaciones = {
        s.index: voz_de(datos, s.index, settings) for s in project.scenes
    }
    narrar_escenas(
        project,
        settings,
        asignaciones,
        solo=solo,
        rehacer=rehacer,
        on_progress=on_progress,
    )
    return estado(project, settings)


def muestra(project: Project, index: int) -> Path:
    """El audio de una escena, para escucharlo."""
    guardada = _cache(project).get(index) or {}
    archivo = project.audio_dir / (guardada.get("audio") or "")
    if not archivo.is_file():
        raise VocesError(f"La escena {index + 1} todavía no tiene audio generado.")
    return archivo


def resumen(escenas: list[EscenaVoz]) -> dict:
    """Cuántas están listas y cuánto duraría el video con lo que hay."""
    listas = [e for e in escenas if e.generada]
    voces = sorted({e.voz for e in escenas if e.voz})
    return {
        "total": len(escenas),
        "generadas": len(listas),
        "pendientes": len(escenas) - len(listas),
        "duracion": round(sum(e.duracion for e in listas), 1),
        "voces": voces,
        "propias": sum(1 for e in escenas if e.tipo == PROPIA),
        "clonadas": sum(1 for e in escenas if e.tipo == CLONADA),
        "avisos": [f"Escena {e.index + 1}: {e.aviso}" for e in escenas if e.aviso],
    }


def exportar_mp3(project: Project, on_progress=None) -> Path:
    """Une todas las escenas en un MP3, para oír el conjunto de una vez."""
    faltan = [e for e in estado(project, Settings()) if not e.generada]
    if faltan:
        raise VocesError(
            f"Faltan {len(faltan)} escenas por generar; genera todas antes de unir."
        )
    if on_progress:
        on_progress("Uniendo la narración")
    piezas = [project.audio_dir / f"escena_{s.index:03d}.wav" for s in project.scenes]
    ffmpeg.concat_demuxer(piezas, project.root / "audio.txt", project.narration_path)
    mp3 = project.root / "narracion.mp3"
    ffmpeg.run(
        ["-i", str(project.narration_path.resolve()), "-c:a", "libmp3lame", "-b:a", "192k",
         str(mp3.resolve())]
    )
    return mp3
