"""Lectura de YouTube sin API key, a través de yt-dlp.

yt-dlp lee la página pública igual que un navegador: da vistas, likes,
suscriptores, duración, tags, descripción y subtítulos (manuales o automáticos)
sin cuota. El listado de un canal en modo "plano" da vistas por video pero no la
fecha; la fecha solo sale al pedir cada video completo, así que eso se reserva
para los pocos videos que de verdad se analizan.
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

#: Duración a partir de la cual un video vertical deja de contar como Short.
SHORT_MAX_SECONDS = 180

_VIDEO_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)|youtu\.be/)([\w-]{11})"
)
_CHANNEL_RE = re.compile(r"youtube\.com/(@[\w.\-]+|channel/[\w-]+|c/[\w.\-]+|user/[\w.\-]+)")


class YouTubeError(RuntimeError):
    """No se pudo leer el video o canal (privado, borrado, URL mal escrita…)."""


@dataclass
class Video:
    id: str
    titulo: str
    canal: str = ""
    canal_url: str = ""
    handle: str = ""
    suscriptores: int | None = None
    vistas: int | None = None
    likes: int | None = None
    comentarios: int | None = None
    duracion: int | None = None
    fecha: str = ""              # AAAA-MM-DD
    tipo: str = "largo"          # "short" | "largo"
    idioma: str = ""
    categorias: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    descripcion: str = ""
    capitulos: list[dict] = field(default_factory=list)
    miniatura: str = ""

    @property
    def url(self) -> str:
        base = "shorts/" if self.tipo == "short" else "watch?v="
        return f"https://www.youtube.com/{base}{self.id}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["url"] = self.url
        return d


@dataclass
class Entrada:
    """Un video del listado de un canal: lo mínimo para medir outliers."""

    id: str
    titulo: str
    vistas: int | None
    duracion: int | None
    tipo: str


def tipo_de_url(texto: str) -> tuple[str, str]:
    """Clasifica lo que pegó el usuario: ("video", id) o ("canal", url base)."""
    texto = texto.strip()
    m = _VIDEO_RE.search(texto)
    if m:
        return "video", m.group(1)
    if re.fullmatch(r"[\w-]{11}", texto) and not texto.startswith("@"):
        return "video", texto
    m = _CHANNEL_RE.search(texto)
    if m:
        return "canal", f"https://www.youtube.com/{m.group(1)}"
    if re.fullmatch(r"@[\w.\-]+", texto):
        return "canal", f"https://www.youtube.com/{texto}"
    raise YouTubeError(
        f"No reconozco '{texto}' como video ni canal de YouTube. "
        "Pega la URL del video o el @ del canal."
    )


class _Silencio:
    def debug(self, msg: str) -> None:
        pass

    info = warning = error = debug


def _ydl(**extra):
    try:
        import yt_dlp
    except ImportError as e:  # pragma: no cover - depende del entorno
        raise YouTubeError(
            "Falta yt-dlp. Instálalo con: .venv\\Scripts\\python -m pip install yt-dlp"
        ) from e
    opts = {
        "quiet": True, "no_warnings": True, "skip_download": True, "noprogress": True,
        # yt-dlp imprime sus errores aunque vaya en quiet; aquí se capturan como
        # excepción y se explican, así que el eco en la terminal sobra.
        "logger": _Silencio(),
    }
    opts.update(extra)
    return yt_dlp.YoutubeDL(opts)


def _extraer(url: str, **extra) -> tuple[dict, object]:
    import yt_dlp

    ydl = _ydl(**extra)
    try:
        return ydl.extract_info(url, download=False), ydl
    except yt_dlp.utils.DownloadError as e:
        # El mensaje de yt-dlp trae el prefijo "ERROR: [youtube] id:"; se deja
        # solo la explicación.
        msg = re.sub(r"^ERROR:\s*(\[[^\]]+\]\s*[\w-]+:\s*)?", "", str(e))
        raise YouTubeError(msg) from e


def _fecha(yyyymmdd: str | None) -> str:
    if not yyyymmdd or len(yyyymmdd) != 8:
        return ""
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"


def _tipo(info: dict) -> str:
    dur = info.get("duration") or 0
    alto, ancho = info.get("height") or 0, info.get("width") or 0
    vertical = alto > ancho if alto and ancho else "/shorts/" in (info.get("webpage_url") or "")
    return "short" if vertical and dur <= SHORT_MAX_SECONDS else "largo"


def video(id_o_url: str) -> tuple[Video, dict]:
    """Metadatos completos de un video. Devuelve también el info crudo de yt-dlp."""
    _, vid = tipo_de_url(id_o_url)
    info, ydl = _extraer(f"https://www.youtube.com/watch?v={vid}")
    v = Video(
        id=info["id"],
        titulo=info.get("title") or "",
        canal=info.get("channel") or info.get("uploader") or "",
        canal_url=info.get("channel_url") or "",
        handle=info.get("uploader_id") or "",
        suscriptores=info.get("channel_follower_count"),
        vistas=info.get("view_count"),
        likes=info.get("like_count"),
        comentarios=info.get("comment_count"),
        duracion=info.get("duration"),
        fecha=_fecha(info.get("upload_date")),
        tipo=_tipo(info),
        idioma=info.get("language") or "",
        categorias=info.get("categories") or [],
        tags=info.get("tags") or [],
        descripcion=info.get("description") or "",
        capitulos=[
            {"inicio": int(c.get("start_time") or 0), "titulo": c.get("title") or ""}
            for c in (info.get("chapters") or [])
        ],
        # jpg y no webp: es lo que aceptan todos los modelos de visión.
        miniatura=f"https://i.ytimg.com/vi/{info['id']}/hqdefault.jpg",
    )
    info["_ydl"] = ydl
    return v, info


# --- transcripción ---------------------------------------------------------


def _pistas(info: dict) -> list[tuple[str, list[dict]]]:
    """Pistas de subtítulos en orden de preferencia.

    Primero las manuales en el idioma del video (las escribió una persona), luego
    las automáticas del idioma original ("xx-orig" o "xx"), y al final cualquier
    manual o automática en español o inglés. Las automáticas traducidas a otro
    idioma quedan fuera: son una traducción de máquina de otra transcripción.
    """
    idioma = (info.get("language") or "").split("-")[0]
    manuales = info.get("subtitles") or {}
    autos = info.get("automatic_captions") or {}
    orden: list[tuple[str, list[dict]]] = []

    def agregar(fuente: dict, clave: str) -> None:
        if clave in fuente and all(clave != k for k, _ in orden):
            orden.append((clave, fuente[clave]))

    for pref in [idioma, "es", "en"] if idioma else ["es", "en"]:
        if not pref:
            continue
        for k in manuales:
            if k.split("-")[0] == pref:
                agregar(manuales, k)
        agregar(autos, f"{pref}-orig")
        if pref == idioma or not idioma:
            agregar(autos, pref)
    return orden


def _texto_json3(data: dict) -> list[tuple[float, str]]:
    trozos = []
    for ev in data.get("events") or []:
        texto = "".join(s.get("utf8", "") for s in ev.get("segs") or []).strip()
        if texto and texto != "\n":
            trozos.append((ev.get("tStartMs", 0) / 1000, " ".join(texto.split())))
    return trozos


def transcripcion(info: dict) -> tuple[list[tuple[float, str]], str]:
    """Transcripción como [(segundo, texto)] y el idioma de la pista usada.

    Devuelve ([], "") si el video no tiene subtítulos o YouTube no los entrega
    (a veces responde 429 por exceso de peticiones): el análisis sigue con el
    título y la descripción, solo que menos fino.
    """
    ydl = info.get("_ydl")
    for idioma, formatos in _pistas(info):
        fmt = next((f for f in formatos if f.get("ext") == "json3"), None)
        if not fmt or ydl is None:
            continue
        try:
            crudo = ydl.urlopen(fmt["url"]).read()
            trozos = _texto_json3(json.loads(crudo))
        except Exception:  # noqa: BLE001 - un subtítulo roto no tumba el análisis
            continue
        if trozos:
            return trozos, idioma
    return [], ""


def agrupar(trozos: list[tuple[float, str]], cada: float = 20.0) -> list[tuple[float, str]]:
    """Junta las líneas en bloques de ~`cada` segundos para leerlas de corrido."""
    bloques: list[tuple[float, str]] = []
    inicio, buf = None, []
    for t, texto in trozos:
        if inicio is None:
            inicio = t
        if t - inicio >= cada and buf:
            bloques.append((inicio, " ".join(buf)))
            inicio, buf = t, []
        buf.append(texto)
    if buf:
        bloques.append((inicio or 0.0, " ".join(buf)))
    return bloques


def mmss(segundos: float) -> str:
    s = int(segundos)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


# --- canal -----------------------------------------------------------------


@dataclass
class Canal:
    nombre: str
    url: str
    handle: str = ""
    suscriptores: int | None = None
    descripcion: str = ""
    videos: list[Entrada] = field(default_factory=list)


def canal(url: str, limite: int = 30) -> Canal:
    """Los últimos `limite` videos largos y `limite` shorts del canal, con vistas.

    Se leen las dos pestañas por separado porque sus vistas no son comparables:
    un short normal saca muchas más vistas que un video largo normal, y
    mezclarlos haría que todos los shorts parecieran outliers.
    """
    tipo, base = tipo_de_url(url)
    if tipo != "canal":
        raise YouTubeError("Eso es un video, no un canal.")

    resultado: Canal | None = None
    for pestaña, tipo_video in (("videos", "largo"), ("shorts", "short")):
        try:
            info, _ = _extraer(
                f"{base}/{pestaña}", extract_flat="in_playlist", playlistend=limite
            )
        except YouTubeError:
            # Un canal sin shorts (o sin videos largos) no tiene esa pestaña.
            continue
        if resultado is None:
            resultado = Canal(
                nombre=info.get("channel") or info.get("uploader") or info.get("title") or "",
                url=info.get("channel_url") or base,
                handle=info.get("uploader_id") or base.rsplit("/", 1)[-1],
                suscriptores=info.get("channel_follower_count"),
                descripcion=info.get("description") or "",
            )
        for e in (info.get("entries") or [])[:limite]:
            if not e or not e.get("id"):
                continue
            resultado.videos.append(
                Entrada(
                    id=e["id"],
                    titulo=e.get("title") or "",
                    vistas=e.get("view_count"),
                    duracion=e.get("duration"),
                    tipo=tipo_video,
                )
            )
    if resultado is None:
        raise YouTubeError(f"No se pudo leer el canal {base} (¿existe? ¿está bien escrito?).")
    return resultado


def descargar_miniatura(video_id: str, destino: Path) -> Path | None:
    """Guarda la miniatura en la mejor resolución disponible.

    maxresdefault no existe en todos los videos (YouTube devuelve una imagen gris
    de 120 px con código 404), así que se prueba de mayor a menor.
    """
    for nombre in ("maxresdefault", "sddefault", "hqdefault"):
        try:
            req = urllib.request.Request(
                f"https://i.ytimg.com/vi/{video_id}/{nombre}.jpg",
                headers={"User-Agent": "Mozilla/5.0 CrearVideos/1.0"},
            )
            with urllib.request.urlopen(req, timeout=20) as r:
                datos = r.read()
            if len(datos) > 5000:
                destino.write_bytes(datos)
                return destino
        except OSError:
            continue
    return None
