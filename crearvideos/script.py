"""Convierte un guion de texto en escenas listas para generar.

Acepta dos formatos y detecta cuál es solo:

1. Texto corrido — se parte en escenas automáticamente por oraciones.
2. Escenas explícitas — separadas por una línea con `---`.

En ambos casos hay una cabecera opcional que no se narra: describe el mundo del
video, y es lo que mantiene las imágenes dentro del mismo universo.

    estilo: cinematográfico, iluminación tenue, tonos fríos
    epoca: Paleolítico superior, Europa, 30.000 a.C.
    protagonistas: una banda de seis cazadores-recolectores; seguimos a los mismos
    paleta: ocre, hueso, pizarra y verde musgo apagado
    evitar: metal, cerámica, agricultura, ropa moderna, texto
    ---
    El océano guarda más secretos de los que imaginamos.
    [imagen: vista submarina de un abismo oscuro, rayos de luz desde arriba]
    ---
    A once mil metros, la presión aplastaría un submarino.

`estilo:` es el look. Las otras cuatro son el contexto que el director necesita
para que un video histórico no derive: acotan la época, fijan a quién seguimos,
unifican el color y listan los anacronismos a prohibir. Todas son opcionales —
sin ellas el director las deduce del propio guion.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import importar, pausas
from .config import SceneSettings
from .models import Scene, ScriptMeta

DEFAULT_STYLE = "fotografía cinematográfica, iluminación dramática, altamente detallada, composición vertical"

_STYLE_RE = re.compile(r"^\s*estilo\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
#: Las cabeceras de contexto, aparte del estilo. Se aceptan con y sin tilde
#: porque nadie escribe "época" con tilde en un .txt a las dos de la mañana.
_META_FIELDS = {
    "epoca": ("epoca", "época", "era"),
    "protagonistas": ("protagonistas", "personajes", "reparto"),
    "paleta": ("paleta", "colores"),
    "evitar": ("evitar", "prohibido", "nunca"),
}
_IMAGE_RE = re.compile(r"\[\s*imagen\s*:\s*(.+?)\s*\]", re.IGNORECASE | re.DOTALL)
_SEPARATOR_RE = re.compile(r"^\s*-{3,}\s*$", re.MULTILINE)
# Corta después de . ! ? … seguidos de espacio y mayúscula/apertura.
_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+(?=[¿¡\"'A-ZÁÉÍÓÚÑ0-9])")


def _extract_style(raw: str) -> tuple[str, str]:
    """Saca la cabecera `estilo:` del texto y la devuelve aparte."""
    m = _STYLE_RE.search(raw)
    if not m:
        return DEFAULT_STYLE, raw
    return m.group(1).strip(), raw[: m.start()] + raw[m.end() :]


def _extract_meta(raw: str) -> tuple[ScriptMeta, str]:
    """Saca las cabeceras de contexto del texto y las devuelve aparte.

    Se quitan del cuerpo siempre: son instrucciones para la imagen, y narrarlas
    en voz alta ("época dos puntos paleolítico superior") arruinaría el video.
    """
    meta = ScriptMeta()
    body = raw
    for campo, alias in _META_FIELDS.items():
        patron = re.compile(
            rf"^\s*(?:{'|'.join(alias)})\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE
        )
        m = patron.search(body)
        if not m:
            continue
        setattr(meta, campo, m.group(1).strip())
        body = body[: m.start()] + body[m.end() :]
    return meta, body


def _split_sentences(text: str) -> list[str]:
    return [p.strip() for p in _SENTENCE_RE.split(text) if p.strip()]


def _group_sentences(sentences: list[str], cfg: SceneSettings) -> list[str]:
    """Agrupa oraciones en bloques dentro del rango de caracteres deseado.

    Una oración más larga que max_chars se deja sola en vez de partirla:
    partirla a la mitad rompería la narración a media frase.
    """
    groups: list[str] = []
    buf = ""
    for sentence in sentences:
        candidate = f"{buf} {sentence}".strip()
        if buf and len(candidate) > cfg.max_chars:
            groups.append(buf)
            buf = sentence
        else:
            buf = candidate
        if len(buf) >= cfg.min_chars:
            groups.append(buf)
            buf = ""
    if buf:
        # Una cola muy corta se pega a la escena anterior en lugar de
        # generar una imagen para media frase.
        if groups and len(buf) < cfg.min_chars // 2:
            groups[-1] = f"{groups[-1]} {buf}".strip()
        else:
            groups.append(buf)
    return groups


def _build_prompt(narration: str, override: str | None, style: str) -> tuple[str, str]:
    """Devuelve (prompt completo con estilo, sujeto solo).

    Las marcas de pausa son para la voz: a la IA de imagen no le dicen nada.
    """
    subject = (override or pausas.limpiar(narration)).strip()
    return f"{subject}. {style}", subject


def parse(
    raw: str, cfg: SceneSettings | None = None, style_override: str | None = None
) -> tuple[list[Scene], ScriptMeta]:
    """Parte el guion en escenas y devuelve también su cabecera de contexto."""
    cfg = cfg or SceneSettings()
    # Un export de otra herramienta (StoryFlow Pro) se traduce antes de nada, y
    # así todas las entradas —web, CLI, audio— lo aceptan tal cual se pegó.
    raw = importar.convertir(raw)
    style, body = _extract_style(raw)
    meta, body = _extract_meta(body)
    # Un estilo elegido (preset de la web o --estilo) gana sobre el de por
    # defecto. La cabecera `estilo:` del guion se sigue quitando del cuerpo para
    # no narrarla, pero el preset manda: es lo que el usuario acaba de elegir.
    if style_override:
        style = style_override
    meta.estilo = style

    blocks = [b.strip() for b in _SEPARATOR_RE.split(body) if b.strip()]
    explicit = len(blocks) > 1

    if not explicit:
        text = blocks[0] if blocks else ""
        image_override = None
        m = _IMAGE_RE.search(text)
        if m:
            image_override = m.group(1)
            text = _IMAGE_RE.sub("", text)
        narrations = _group_sentences(_split_sentences(text), cfg)
        blocks = narrations
        overrides = [image_override] * len(blocks)
    else:
        overrides = []
        cleaned = []
        for b in blocks:
            m = _IMAGE_RE.search(b)
            overrides.append(m.group(1) if m else None)
            cleaned.append(_IMAGE_RE.sub("", b).strip())
        blocks = cleaned

    scenes: list[Scene] = []
    for i, (text, override) in enumerate(zip(blocks, overrides)):
        text = " ".join(text.split())
        # Una escena hecha solo de "(pausa)" no tiene nada que narrar ni
        # ilustrar: se descarta en vez de dejar una imagen muda.
        if not pausas.limpiar(text):
            continue
        prompt, subject = _build_prompt(text, override, style)
        scenes.append(
            Scene(
                index=len(scenes),
                text=text,
                image_prompt=prompt,
                image_subject=subject,
            )
        )
    if not scenes:
        raise ValueError("El guion está vacío: no se encontró texto para narrar.")
    return scenes, meta


def load(
    path: Path, cfg: SceneSettings | None = None, style_override: str | None = None
) -> tuple[list[Scene], ScriptMeta]:
    return parse(path.read_text(encoding="utf-8"), cfg, style_override)


def parse_script(
    raw: str, cfg: SceneSettings | None = None, style_override: str | None = None
) -> list[Scene]:
    """Solo las escenas, para quien no necesita la cabecera."""
    return parse(raw, cfg, style_override)[0]


def load_script(
    path: Path, cfg: SceneSettings | None = None, style_override: str | None = None
) -> list[Scene]:
    return parse_script(path.read_text(encoding="utf-8"), cfg, style_override)
