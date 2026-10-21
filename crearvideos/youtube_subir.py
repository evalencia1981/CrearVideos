"""Subir el video a YouTube con lo que preparó el módulo Publicar.

Toma el MP4 del proyecto y su `publicacion.json` —título, descripción con
capítulos, etiquetas— y lo sube. Nada se inventa aquí: lo que se publica es lo
que ya revisaste en la pestaña 5.

Lo que hace falta una sola vez, y no lo puede hacer el programa por ti:

1. Crea un proyecto en https://console.cloud.google.com/
2. Activa la **YouTube Data API v3**.
3. Pantalla de consentimiento OAuth: tipo "Externo", y añádete a ti mismo como
   usuario de prueba con la cuenta del canal.
4. Credenciales → Crear → **ID de cliente de OAuth** → tipo "Aplicación de
   escritorio". Descarga el JSON y déjalo en la raíz como `client_secret.json`.

Dos límites de YouTube que conviene saber antes de automatizar nada:

- Subir cuesta **1600 unidades** de las 10.000 diarias por defecto: unos **6
  videos al día**.
- Mientras tu app esté sin verificar por Google, la API solo deja subir en
  **privado** aunque pidas público. No es un fallo de este código.

Por eso el valor por defecto aquí es privado: se sube, lo revisas en YouTube
Studio con su miniatura, y lo publicas tú.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import PROJECT_ROOT, OUTPUT_DIR, api_key

#: Solo subir: no se pide permiso para leer, borrar ni tocar nada más del canal.
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

CLIENT_SECRET = Path(api_key("YOUTUBE_CLIENT_SECRET") or (PROJECT_ROOT / "client_secret.json"))
TOKEN = PROJECT_ROOT / ".youtube-token.json"

#: Categorías de YouTube más usadas para este tipo de canal.
CATEGORIAS = {
    "educacion": "27",
    "ciencia": "28",
    "gente": "22",
    "entretenimiento": "24",
    "tutorial": "26",
    "noticias": "25",
    "salud": "27",   # YouTube no tiene "salud": va en Educación
}

PRIVACIDAD = {"privado": "private", "oculto": "unlisted", "publico": "public"}


class YouTubeError(RuntimeError):
    """No se pudo subir el video."""


def configurado() -> tuple[bool, str]:
    """¿Están las credenciales? Devuelve (sí/no, qué falta)."""
    if not CLIENT_SECRET.is_file():
        return False, (
            f"Falta {CLIENT_SECRET.name} en la raíz del proyecto.\n"
            "Créalo en https://console.cloud.google.com/ : activa YouTube Data API v3, "
            "pantalla de consentimiento OAuth (Externo, añádete como usuario de prueba) "
            "y Credenciales → ID de cliente de OAuth → Aplicación de escritorio. "
            "Descarga el JSON y déjalo aquí como client_secret.json"
        )
    return True, "listo"


def autorizado() -> bool:
    return TOKEN.is_file()


def _credenciales(interactivo: bool = True):
    """Devuelve credenciales válidas; abre el navegador la primera vez."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    ok, motivo = configurado()
    if not ok:
        raise YouTubeError(motivo)

    creds = None
    if TOKEN.is_file():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
        except (ValueError, json.JSONDecodeError):
            creds = None

    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN.write_text(creds.to_json(), encoding="utf-8")
            return creds
        except Exception:  # noqa: BLE001 - token revocado o caducado del todo
            TOKEN.unlink(missing_ok=True)

    if not interactivo:
        raise YouTubeError(
            "Falta autorizar la cuenta de YouTube. Hazlo desde la terminal con:  "
            "python -m crearvideos youtube-conectar"
        )

    flujo = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    # Puerto 0 = el que esté libre. Abre el navegador para que elijas la cuenta.
    creds = flujo.run_local_server(port=0, prompt="consent")
    TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return creds


def conectar() -> str:
    """Autoriza la cuenta y devuelve un mensaje para enseñar."""
    _credenciales(interactivo=True)
    return f"Cuenta autorizada. El permiso quedó guardado en {TOKEN.name} (no lo compartas)."


def desconectar() -> str:
    TOKEN.unlink(missing_ok=True)
    return "Permiso borrado. La próxima subida volverá a pedir autorización."


def _proyecto(nombre: str | Path) -> tuple[Path, dict]:
    raiz = Path(nombre)
    if not raiz.is_dir():
        raiz = OUTPUT_DIR / str(nombre)
    if not raiz.is_dir():
        raise YouTubeError(f"No encuentro el proyecto '{nombre}'.")

    publicacion = raiz / "publicacion.json"
    if not publicacion.is_file():
        raise YouTubeError(
            f"{raiz.name} no tiene publicacion.json. Genérala antes en la pestaña "
            "5 · Publicar: de ahí salen el título, la descripción y las etiquetas."
        )
    return raiz, json.loads(publicacion.read_text(encoding="utf-8"))


def _video(raiz: Path) -> Path:
    candidatos = sorted(raiz.glob("*.mp4"), key=lambda p: p.stat().st_size, reverse=True)
    principal = raiz / f"{raiz.name}.mp4"
    if principal.is_file():
        return principal
    if candidatos:
        return candidatos[0]
    raise YouTubeError(
        f"En {raiz.name} no hay ningún MP4. Monta el video antes (pestaña 4 · Montaje)."
    )


def _traducir_error(e: Exception) -> str:
    """Convierte el error de la API en algo que se pueda leer y arreglar."""
    from googleapiclient.errors import HttpError

    if not isinstance(e, HttpError):
        return str(e)
    try:
        detalle = json.loads(e.content.decode("utf-8"))["error"]
        razon = (detalle.get("errors") or [{}])[0].get("reason", "")
        mensaje = detalle.get("message", "")
    except (ValueError, KeyError, AttributeError):
        razon, mensaje = "", str(e)

    ayuda = {
        "quotaExceeded": (
            "Se acabó la cuota diaria de la API (subir cuesta 1600 de 10.000 unidades: "
            "unos 6 videos al día). Se renueva a medianoche, hora del Pacífico."
        ),
        "uploadLimitExceeded": "El canal llegó a su límite de subidas por hoy.",
        "youtubeSignupRequired": "Esa cuenta de Google no tiene canal de YouTube todavía.",
        "forbidden": "La cuenta autorizada no tiene permiso para subir a ese canal.",
        "invalidVideoMetadata": "YouTube rechazó los metadatos (título o descripción).",
        "invalidTitle": "El título no es válido: máximo 100 caracteres y sin < >.",
        "invalidDescription": "La descripción no es válida: máximo 5000 caracteres.",
        "invalidTags": "Las etiquetas pasan del límite de 500 caracteres.",
        "mediaBodyRequired": "No se encontró el archivo de video.",
    }
    return ayuda.get(razon, f"{razon or 'error'}: {mensaje}")


def subir(
    proyecto: str | Path,
    *,
    privacidad: str = "privado",
    categoria: str = "educacion",
    titulo: str | None = None,
    programar: str | None = None,
    miniatura: Path | None = None,
    idioma: str = "es",
    on_progress=None,
) -> dict:
    """Sube el video del proyecto. Por defecto, PRIVADO.

    `programar` es una fecha "AAAA-MM-DD HH:MM" en tu hora local: YouTube lo
    deja privado hasta ese momento y lo publica solo.
    """
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    raiz, pub = _proyecto(proyecto)
    archivo = _video(raiz)

    if privacidad not in PRIVACIDAD:
        raise YouTubeError(f"Privacidad desconocida: '{privacidad}'. Opciones: {', '.join(PRIVACIDAD)}")

    estado = {
        "privacyStatus": PRIVACIDAD[privacidad],
        # Obligatorio desde la ley COPPA: hay que declararlo sí o sí.
        "selfDeclaredMadeForKids": False,
    }
    if programar:
        try:
            cuando = datetime.strptime(programar, "%Y-%m-%d %H:%M").astimezone()
        except ValueError as err:
            raise YouTubeError("La fecha debe ser 'AAAA-MM-DD HH:MM', p. ej. 2026-10-02 18:30") from err
        if cuando <= datetime.now().astimezone():
            raise YouTubeError("La fecha de publicación tiene que ser futura.")
        # Programar exige que el video esté privado hasta ese momento.
        estado["privacyStatus"] = "private"
        estado["publishAt"] = cuando.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    cuerpo = {
        "snippet": {
            "title": (titulo or pub.get("titulo_recomendado") or "Sin título")[:100],
            "description": (pub.get("descripcion") or "")[:5000],
            "tags": pub.get("tags") or [],
            "categoryId": CATEGORIAS.get(categoria, "27"),
            "defaultLanguage": idioma,
            "defaultAudioLanguage": idioma,
        },
        "status": estado,
    }

    creds = _credenciales(interactivo=False)
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)

    if on_progress:
        on_progress(f"Subiendo {archivo.name} ({archivo.stat().st_size / 1024 / 1024:.0f} MB)")

    medio = MediaFileUpload(str(archivo), chunksize=4 * 1024 * 1024, resumable=True, mimetype="video/mp4")
    peticion = youtube.videos().insert(part="snippet,status", body=cuerpo, media_body=medio)

    respuesta = None
    try:
        while respuesta is None:
            estado_subida, respuesta = peticion.next_chunk()
            if estado_subida and on_progress:
                on_progress(f"Subiendo… {int(estado_subida.progress() * 100)}%")
    except HttpError as e:
        raise YouTubeError(_traducir_error(e)) from e

    video_id = respuesta["id"]
    url = f"https://youtu.be/{video_id}"

    # La miniatura hecha en la sección Miniatura (16:9, con título) manda; si
    # no hay, la portada de siempre.
    portada = miniatura or next(
        (raiz / n for n in ("miniatura.jpg", "portada.jpg") if (raiz / n).is_file()), None
    )
    aviso = ""
    if portada and Path(portada).is_file():
        try:
            if on_progress:
                on_progress("Subiendo la miniatura")
            youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(portada))).execute()
        except HttpError as e:
            # Poner miniatura propia exige canal verificado por teléfono. Que
            # falle no debe tirar una subida que ya salió bien.
            aviso = f"El video se subió, pero la miniatura no: {_traducir_error(e)}"

    resultado = {
        "id": video_id,
        "url": url,
        "estudio": f"https://studio.youtube.com/video/{video_id}/edit",
        "titulo": cuerpo["snippet"]["title"],
        "privacidad": estado["privacyStatus"],
        "programado": estado.get("publishAt", ""),
        "archivo": str(archivo),
        "aviso": aviso,
        "subido": datetime.now().isoformat(timespec="seconds"),
    }
    (raiz / "youtube.json").write_text(
        json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return resultado
