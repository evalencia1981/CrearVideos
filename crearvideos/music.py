"""Biblioteca de música de fondo.

Las pistas viven en `assets/musica/`, agrupadas por ambiente:

    assets/musica/
      general/     sirve para cualquier video
      tension/     terror, suspenso
      epico/       cinematográfico, fantasía
      alegre/      anime, cartoon, cómic
      electronico/ ciencia ficción
      calmado/     acuarela, óleo, minimalista

No se descarga nada: la carpeta la llena el usuario con pistas que tenga
derecho a usar. Es deliberado — subir música ajena a TikTok/YouTube es lo que
hace que un canal se caiga, y ninguna fuente gratuita sin clave da garantías de
licencia que se puedan verificar desde aquí.

`elegir("auto", …)` mira el ambiente que corresponde al estilo visual y, si esa
carpeta está vacía, cae a `general/`. La elección es determinista sobre el
nombre del proyecto: rehacer el mismo video conserva su pista, en vez de sonar
distinto en cada corrida.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .config import ASSETS_DIR

MUSIC_DIR = ASSETS_DIR / "musica"

# Formatos que ffmpeg abre sin quejarse y que la gente tiene a mano.
EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".opus", ".flac", ".aac"}

# Estilo visual -> carpeta de ambiente. Varios estilos comparten ambiente: el
# look de la imagen y el ánimo de la música no son la misma dimensión, pero
# correlacionan lo bastante como para acertar sin preguntar.
MOODS: dict[str, str] = {
    "terror": "tension",
    "suspenso": "tension",
    "cinematografico": "epico",
    "documental": "epico",
    "fotorrealista": "epico",
    "fantasia": "epico",
    "anime": "alegre",
    "cartoon": "alegre",
    "comic": "alegre",
    "pixel": "alegre",
    "scifi": "electronico",
    "acuarela": "calmado",
    "oleo": "calmado",
    "minimal": "calmado",
}

FOLDERS = ["general", *sorted(set(MOODS.values()))]

# Lo que el usuario puede escribir para decir "sin música".
NINGUNA = {"no", "ninguna", "nada", "sin", "none", ""}


def _tracks(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in EXTS)


def library() -> dict[str, list[Path]]:
    """Las pistas disponibles, por carpeta. Solo carpetas con algo dentro."""
    found = {name: _tracks(MUSIC_DIR / name) for name in FOLDERS}
    return {name: paths for name, paths in found.items() if paths}


def _auto(style: str | None, seed: str) -> Path | None:
    """Pista del ambiente del estilo; si no hay, de `general/`."""
    candidates: list[Path] = []
    if style:
        key = style.lower()
        # Una carpeta con el nombre exacto del estilo gana: es la forma de
        # decir "para terror quiero esta música", sin tocar el mapa de arriba.
        candidates = _tracks(MUSIC_DIR / key) or _tracks(MUSIC_DIR / MOODS.get(key, ""))
    candidates = candidates or _tracks(MUSIC_DIR / "general")
    if not candidates:
        return None

    # Determinista sobre el nombre: el mismo proyecto suena siempre igual.
    n = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    return candidates[n % len(candidates)]


def elegir(spec: str | None, *, style: str | None = None, seed: str = "") -> Path | None:
    """Resuelve lo que pidió el usuario a un archivo concreto (o a nada).

    `spec` es "auto", "no"/"ninguna", o la ruta de una pista.
    """
    if spec is None or spec.strip().lower() in NINGUNA:
        return None

    spec = spec.strip()
    if spec.lower() == "auto":
        return _auto(style, seed)

    path = Path(spec)
    if not path.is_file():
        # Un nombre pelado ("lofi.mp3") se busca en la biblioteca antes de
        # rendirse: es como la gente se refiere a sus propias pistas.
        for folder in FOLDERS:
            tentativa = MUSIC_DIR / folder / spec
            if tentativa.is_file():
                return tentativa
        raise ValueError(
            f"No se encontró la pista '{spec}'. "
            f"Usa una ruta a un archivo, o deja pistas en {MUSIC_DIR}"
        )
    if path.suffix.lower() not in EXTS:
        raise ValueError(
            f"'{path.name}' no parece audio. Formatos: {', '.join(sorted(EXTS))}"
        )
    return path


def options() -> list[dict[str, str]]:
    """Lista [{value, label, rel}] para pintar el selector en la web.

    `rel` es la ruta de la pista relativa a la biblioteca, en formato URL. La
    web la usa para pedir el archivo y dejar escucharlo antes de generar.
    `auto` y `no` no tienen archivo que sonar, así que van sin `rel`.
    """
    opts = [
        {"value": "auto", "label": "Automática · según el estilo", "rel": ""},
        {"value": "no", "label": "Sin música", "rel": ""},
    ]
    for folder, paths in library().items():
        for p in paths:
            rel = p.relative_to(MUSIC_DIR).as_posix()
            opts.append({"value": str(p), "label": f"{folder} · {p.stem}", "rel": rel})
    return opts


def ensure_dirs() -> None:
    """Crea el esqueleto de la biblioteca para que se vea dónde dejar las pistas."""
    for name in FOLDERS:
        (MUSIC_DIR / name).mkdir(parents=True, exist_ok=True)
