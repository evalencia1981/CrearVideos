"""Configuración central. Todo lo ajustable vive aquí o en .env"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

def _congelado() -> bool:
    """¿Estamos dentro del .exe que se reparte a otras personas?"""
    import sys

    return getattr(sys, "frozen", False)


def _recursos() -> Path:
    """Dónde están los archivos que vienen CON el programa (solo lectura).

    Ejecutando desde el código fuente es la raíz del repo. Dentro del .exe,
    PyInstaller descomprime todo en una carpeta temporal y la apunta en
    `sys._MEIPASS`: ahí viven la página web, ffmpeg y los ejemplos.
    """
    import sys

    if _congelado():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def _datos() -> Path:
    """Dónde se ESCRIBE: proyectos, estudio, música, claves.

    No puede ser la carpeta del programa: dentro del .exe es temporal y se
    borra al cerrar, y en Archivos de programa no hay permiso de escritura.
    Se usa `Documentos\\CrearVideos`, que el usuario encuentra solo, y se puede
    cambiar con CREARVIDEOS_DATOS para quien quiera otro disco.
    """
    import os as _os

    manual = _os.getenv("CREARVIDEOS_DATOS")
    if manual:
        return Path(manual).expanduser()
    if not _congelado():
        return Path(__file__).resolve().parent.parent

    documentos = Path.home() / "Documents"
    if not documentos.is_dir():  # Windows en otro idioma, o sin carpeta
        documentos = Path.home()
    return documentos / "CrearVideos"


#: Archivos que vienen con el programa (página web, ffmpeg, ejemplos).
RESOURCE_ROOT = _recursos()
#: Carpeta de trabajo del usuario: aquí se guarda y desde aquí se lee.
PROJECT_ROOT = _datos()
PROJECT_ROOT.mkdir(parents=True, exist_ok=True)
load_dotenv(PROJECT_ROOT / ".env")

# --- Formato de salida: vertical para TikTok / Reels / Shorts ---
WIDTH = 1080
HEIGHT = 1920
FPS = 30

#: Formatos de salida disponibles en el montaje con medios propios. El vertical
#: sigue siendo el de la app; el horizontal es para YouTube de formato largo.
FORMATOS: dict[str, tuple[int, int]] = {
    "vertical": (1080, 1920),
    "horizontal": (1920, 1080),
    "cuadrado": (1080, 1080),
}


def formato(nombre: str | None) -> tuple[int, int]:
    """Tamaño de salida por nombre. Sin nombre, el vertical de siempre."""
    if not nombre:
        return WIDTH, HEIGHT
    try:
        return FORMATOS[nombre.strip().lower()]
    except KeyError:
        raise ValueError(
            f"Formato desconocido: '{nombre}'. Opciones: {', '.join(FORMATOS)}"
        ) from None

# El zoom de Ken Burns se calcula sobre una versión ampliada de la imagen:
# con más pixeles de origen los pasos del zoom son sub-pixel y desaparece el
# temblor característico del filtro zoompan. 2x equilibra suavidad y CPU.
SUPERSAMPLE = 2

# Resolución a la que se dibujan las imágenes locales de relleno.
PLACEHOLDER_WIDTH = WIDTH * 4 // 3
PLACEHOLDER_HEIGHT = HEIGHT * 4 // 3

OUTPUT_DIR = PROJECT_ROOT / "proyectos"
ASSETS_DIR = PROJECT_ROOT / "assets"


@dataclass
class VoiceSettings:
    name: str = "es-MX-JorgeNeural"
    rate: str = "+8%"  # un poco acelerado: retiene mejor en shorts
    pitch: str = "+0Hz"
    volume: str = "+0%"


@dataclass
class SceneSettings:
    """Cuánto texto entra en cada escena (y por tanto en cada imagen)."""

    min_chars: int = 70
    max_chars: int = 170
    #: Cada párrafo (separado por una línea en blanco) es una escena, en vez
    #: de partir por largo. Es como se escribe un guion pensado imagen por
    #: imagen: un párrafo por imagen, sin tener que poner `---`.
    por_parrafo: bool = False


@dataclass
class SubtitleStyle:
    """Estilo tipo TikTok: pocas palabras, grandes, palabra activa resaltada."""

    font: str = "Arial Black"
    size: int = 92
    primary: str = "&H00FFFFFF"      # texto normal (blanco)  — ASS es &HAABBGGRR
    highlight: str = "&H0000E5FF"    # palabra activa (ámbar)
    outline_color: str = "&H00000000"
    outline: int = 6
    shadow: int = 3
    words_per_group: int = 3
    margin_v: int = 420              # distancia desde abajo


@dataclass
class MusicSettings:
    """Cómo suena la música de fondo bajo la narración.

    Con ducking la música baja sola cuando entra la voz y vuelve a subir en los
    silencios, así que puede ir mucho más alta que con volumen fijo: se oye
    entre frases sin tapar nunca la narración. `volume` es el nivel al que
    suena cuando la voz calla, no un promedio.
    """

    volume: float = 0.30
    duck: bool = True
    #: Nivel de voz a partir del cual la música empieza a bajar (0-1 lineal).
    #: Bajo a propósito: la voz debe disparar el compresor desde su primera
    #: sílaba, no solo en los picos.
    duck_threshold: float = 0.02
    #: Cuánto baja. 12:1 es agresivo, que es lo que se quiere aquí: la música
    #: es fondo, la voz es el contenido.
    duck_ratio: float = 12.0
    duck_attack: float = 15.0    # ms; rápido, para no dejar pasar el ataque
    duck_release: float = 350.0  # ms; lento, para que no "bombee" entre palabras
    fade_in: float = 0.8
    fade_out: float = 1.2


@dataclass
class Settings:
    voice: VoiceSettings = None
    scene: SceneSettings = None
    subtitle: SubtitleStyle = None
    music: MusicSettings = None
    image_provider: str = "placeholder"
    image_model: str = ""              # modelo de Pollinations; vacío = el por defecto del proveedor
    tts_provider: str = "edge"
    reference_image: str | None = None   # (compat) una sola imagen base
    reference_images: list[str] = None   # imágenes base para fijar el personaje (img2img)
    pollinations_free: bool = False       # ignora el token: modelo 'sana' gratis, para probar sin gastar pollen
    voice_fx: str = "auto"           # efecto sobre la voz: auto|none|terror|… (ver voicefx.py)
    formato: str = "vertical"        # vertical | horizontal | cuadrado (ver FORMATOS)
    scene_gap: float = 0.18          # respiro entre escenas, en segundos

    def __post_init__(self):
        self.voice = self.voice or VoiceSettings()
        self.scene = self.scene or SceneSettings()
        self.subtitle = self.subtitle or SubtitleStyle()
        self.music = self.music or MusicSettings()
        # Una lista vacía y None significan lo mismo aquí: sin referencias.
        self.reference_images = list(self.reference_images or [])
        if self.reference_image and self.reference_image not in self.reference_images:
            self.reference_images.append(self.reference_image)


def load_settings() -> Settings:
    s = Settings()
    s.image_provider = os.getenv("IMAGE_PROVIDER", s.image_provider)
    s.image_model = os.getenv("POLLINATIONS_MODEL", s.image_model)
    s.tts_provider = os.getenv("TTS_PROVIDER", s.tts_provider)
    s.voice_fx = os.getenv("VOICE_FX", s.voice_fx)
    s.voice.name = os.getenv("VOICE", s.voice.name)
    try:
        s.music.volume = float(os.getenv("MUSIC_VOLUME", s.music.volume))
    except ValueError:
        pass  # un valor mal escrito en .env no debe impedir crear el video
    return s


def api_key(name: str) -> str | None:
    return os.getenv(name)
