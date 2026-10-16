"""Genera subtítulos .ass con resaltado palabra por palabra.

El efecto es el de TikTok: se ven 2-4 palabras a la vez y la que suena en
ese instante se pinta de otro color. Eso es lo que sostiene la atención, y
es la razón de que todo el pipeline cargue tiempos por palabra.

La técnica: por cada palabra activa se emite un evento Dialogue que dura
exactamente lo que esa palabra, y que contiene el grupo completo con esa
palabra coloreada mediante etiquetas inline. N palabras -> N eventos.
"""

from __future__ import annotations

from pathlib import Path

from .config import HEIGHT, WIDTH, SubtitleStyle
from .models import Word

_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{font},{size},{primary},{primary},{outline_color},&H80000000,-1,0,0,0,100,100,0,0,1,{outline},{shadow},2,90,90,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

# El Format de Events debe declarar los diez campos, MarginV incluido. Con
# nueve, libass desplaza todo un campo: toma el margen como Effect y arrastra
# la coma sobrante al principio del texto, que aparece quemada en el video.


def _timestamp(seconds: float) -> str:
    """Formato ASS: h:mm:ss.cc (centésimas, no milésimas)."""
    seconds = max(seconds, 0.0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    cs = int(round((s - int(s)) * 100))
    s = int(s)
    if cs == 100:  # el redondeo puede desbordar
        cs, s = 0, s + 1
        if s == 60:
            s, m = 0, m + 1
    return f"{int(h):d}:{int(m):02d}:{s:02d}.{cs:02d}"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def group_words(words: list[Word], size: int) -> list[list[Word]]:
    """Parte las palabras en grupos, sin cruzar pausas largas.

    Una pausa marca un cambio de escena o de idea: arrastrar palabras de un
    lado a otro haría que el subtítulo mostrara texto que ya no se escucha.
    """
    groups: list[list[Word]] = []
    current: list[Word] = []
    for word in words:
        if current:
            gap = word.start - current[-1].end
            if len(current) >= size or gap > 0.6:
                groups.append(current)
                current = []
        current.append(word)
    if current:
        groups.append(current)
    return groups


def build_ass(
    words: list[Word],
    style: SubtitleStyle | None = None,
    size: tuple[int, int] | None = None,
) -> str:
    style = style or SubtitleStyle()
    w, h = size or (WIDTH, HEIGHT)
    lines = [
        _HEADER.format(
            w=w,
            h=h,
            font=style.font,
            size=style.size,
            primary=style.primary,
            outline_color=style.outline_color,
            outline=style.outline,
            shadow=style.shadow,
            margin_v=style.margin_v,
        )
    ]

    groups = group_words(words, style.words_per_group)
    for g, group in enumerate(groups):
        next_start = groups[g + 1][0].start if g + 1 < len(groups) else None

        for i, active in enumerate(group):
            parts = []
            for j, w in enumerate(group):
                token = _escape(w.text)
                if j == i:
                    # \c color de relleno, \fscx/\fscy escala: la palabra activa
                    # crece un poco además de cambiar de color.
                    parts.append(
                        f"{{\\c{style.highlight}\\fscx108\\fscy108}}{token}"
                        f"{{\\c{style.primary}\\fscx100\\fscy100}}"
                    )
                else:
                    parts.append(token)
            text = " ".join(parts)

            start = active.start
            # Cada evento se estira hasta el siguiente, para que los
            # micro-silencios entre palabras no hagan parpadear el subtítulo.
            if i + 1 < len(group):
                end = group[i + 1].start
            elif next_start is not None:
                # La última palabra del grupo se sostiene hasta el grupo
                # siguiente, pero no a través de una pausa larga: ahí el
                # subtítulo debe irse porque ya no se está diciendo nada.
                end = min(next_start, active.end + 0.4)
            else:
                end = active.end + 0.4
            if end <= start:
                end = start + 0.08

            lines.append(
                f"Dialogue: 0,{_timestamp(start)},{_timestamp(end)},Main,,0,0,0,,{text}"
            )

    return "\n".join(lines) + "\n"


def _srt_timestamp(seconds: float) -> str:
    """Formato SRT: hh:mm:ss,mmm (milésimas y coma decimal)."""
    seconds = max(seconds, 0.0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    ms = int(round((s - int(s)) * 1000))
    s = int(s)
    if ms == 1000:
        ms, s = 0, s + 1
        if s == 60:
            s, m = 0, m + 1
    return f"{int(h):02d}:{int(m):02d}:{s:02d},{ms:03d}"


def build_srt(entradas: list[tuple[float, float, str]]) -> str:
    """SRT a partir de (inicio, fin, texto). Para editores de video."""
    bloques = []
    for i, (inicio, fin, texto) in enumerate(entradas, 1):
        fin = max(fin, inicio + 0.08)
        bloques.append(
            f"{i}\n{_srt_timestamp(inicio)} --> {_srt_timestamp(fin)}\n{texto.strip()}\n"
        )
    return "\n".join(bloques)


def write_srt(entradas: list[tuple[float, float, str]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Sin BOM: Premiere y Resolve lo leen mejor así que el .ass.
    path.write_text(build_srt(entradas), encoding="utf-8")
    return path


def words_to_srt(words: list[Word], size: int = 5) -> list[tuple[float, float, str]]:
    """Agrupa palabras en líneas de subtítulo con sus tiempos."""
    entradas = []
    for grupo in group_words(words, size):
        entradas.append(
            (grupo[0].start, grupo[-1].end, " ".join(w.text for w in grupo))
        )
    return entradas


def write_ass(
    words: list[Word],
    path: Path,
    style: SubtitleStyle | None = None,
    size: tuple[int, int] | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_ass(words, style, size), encoding="utf-8-sig")
    return path


def escalar_estilo(style: SubtitleStyle, size: tuple[int, int]) -> SubtitleStyle:
    """Ajusta tamaño de letra y margen al alto real del video.

    Los valores por defecto están pensados para 1920 px de alto (vertical). En
    un 16:9 de 1080 px, la misma letra ocuparía media pantalla y el margen la
    dejaría flotando en el centro.
    """
    from dataclasses import replace

    factor = size[1] / HEIGHT
    if abs(factor - 1.0) < 0.01:
        return style
    return replace(
        style,
        size=max(int(round(style.size * factor)), 24),
        outline=max(int(round(style.outline * factor)), 2),
        shadow=max(int(round(style.shadow * factor)), 1),
        # En horizontal el subtítulo baja: no hay que dejar sitio al pulgar ni
        # a la interfaz de TikTok, y abajo es donde se espera leerlo.
        margin_v=max(int(round(style.margin_v * factor * 0.45)), 40),
    )
