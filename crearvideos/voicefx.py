"""Efectos de voz: darle carácter a la narración sin descargar nada.

edge-tts entrega una voz neutra y limpia. Aquí se procesa con ffmpeg para
volverla grave, cavernosa, fantasmal… Es lo que le da vida de terror a un short
sin cambiar de motor ni pagar nada.

Regla de oro: **el efecto no puede mover las palabras en el tiempo**. Toda la
sincronía del video (escenas y subtítulos) se apoya en los tiempos que reporta
edge-tts para la voz original. Por eso:

- El cambio de tono preserva la duración: se baja el tono con `asetrate` (que
  también alarga) y se compensa el largo con `atempo`. El inicio de cada palabra
  queda donde estaba; solo cambia el timbre.
- La reverberación y el eco solo añaden una cola al final, no adelantan nada. El
  pipeline vuelve a medir la duración real tras el efecto y ajusta la escena a
  esa cola, así que la reverb nunca se corta.

Nada de esto necesita internet ni claves: es puro ffmpeg, igual que el resto.
"""

from __future__ import annotations

# Frecuencia de trabajo. Coincide con la del resto del audio del proyecto.
SR = 44100


def _pitch(p: float) -> str:
    """Baja (p<1) o sube (p>1) el tono un factor `p`, SIN cambiar la duración.

    asetrate reinterpreta las muestras a otra frecuencia: baja el tono y alarga.
    aresample devuelve la señal a SR sin tocar tono ni largo, y atempo restaura
    la duración original. atempo solo acepta [0.5, 2.0], así que p vive en
    [0.5, 2.0]; en la práctica se usa 0.7–0.95.
    """
    return f"aresample={SR},asetrate={int(SR * p)},aresample={SR},atempo={1 / p:.4f}"


# clave -> (etiqueta visible, cadena de filtros ffmpeg o None).
# None = sin procesar (se deja la voz tal cual la entrega edge-tts).
PRESETS: dict[str, tuple[str, str | None]] = {
    "none": ("Sin efecto", None),
    "profunda": (
        "Voz profunda",
        # Un tono más grave, sin más adornos. Sienta bien a cualquier narración
        # seria, no solo de terror.
        _pitch(0.90),
    ),
    "terror": (
        "Terror · cripta",
        # Grave + reverb corta de sala de piedra + un techo de agudos que la
        # vuelve apagada, como narrada en un sótano.
        f"{_pitch(0.85)},aecho=0.8:0.85:55:0.35,lowpass=f=8000",
    ),
    "fantasma": (
        "Fantasma · susurro con eco",
        # Muy grave, eco largo y un temblor lento que la hace sonar de ultratumba.
        f"{_pitch(0.82)},aecho=0.8:0.9:200|380:0.35|0.25,tremolo=f=6:d=0.4,lowpass=f=6500",
    ),
    "demonio": (
        "Demonio · voz grave",
        # Tono muy bajo y una reverb seca. Amenazante sin volverse ininteligible.
        f"{_pitch(0.72)},aecho=0.85:0.8:45:0.3,lowpass=f=7000",
    ),
    "eco": (
        "Eco cavernoso",
        # Sin cambiar el tono: solo un eco grande, como de gruta o catedral.
        "aecho=0.8:0.88:350|700:0.4|0.3",
    ),
    "radio": (
        "Radio vieja · transmisión",
        # Recorta graves y agudos (banda de teléfono) y añade una pizca de
        # cuantización: suena a transmisión captada de lejos.
        "highpass=f=350,lowpass=f=3200,acrusher=level_in=1:level_out=1:bits=10:mode=log:aa=0",
    ),
}

# Estilo visual -> efecto por defecto. Solo los estilos donde el terror/tensión
# es el punto. En el resto no se procesa la voz, para no sorprender.
AUTO: dict[str, str] = {
    "terror": "terror",
    "suspenso": "terror",
    "scifi": "radio",
}

# Lo que el usuario puede escribir para decir "sin efecto".
NINGUNO = {"none", "no", "ninguno", "ninguna", "nada", "sin"}


def resolve(key: str | None, style: str | None = None) -> str | None:
    """Devuelve la cadena de filtros ffmpeg de un efecto, o None si no aplica.

    `key` puede ser 'auto' (sigue el estilo), un nombre de preset, o 'none'.
    """
    if key is None or key.strip().lower() == "auto":
        preset = AUTO.get((style or "").lower())
        return PRESETS[preset][1] if preset else None

    k = key.strip().lower()
    if k in NINGUNO:
        return None
    if k in PRESETS:
        return PRESETS[k][1]
    raise ValueError(
        f"Efecto de voz desconocido: '{key}'. "
        f"Opciones: auto, {', '.join(PRESETS)}"
    )


def is_known(key: str | None) -> bool:
    if not key:
        return False
    k = key.lower()
    return k == "auto" or k in NINGUNO or k in PRESETS


def label(key: str | None, style: str | None = None) -> str:
    """Etiqueta legible de lo que se aplicará, para mostrar en el panel/CLI."""
    if key is None or key.strip().lower() == "auto":
        preset = AUTO.get((style or "").lower())
        return f"auto · {PRESETS[preset][0]}" if preset else "auto · sin efecto"
    k = key.strip().lower()
    if k in NINGUNO:
        return "sin efecto"
    return PRESETS.get(k, ("desconocido", None))[0]


def options() -> list[dict[str, str]]:
    """Lista [{value, label}] para el selector de la web y el comando `efectos`."""
    opts = [{"value": "auto", "label": "Automático · según el estilo"}]
    opts += [{"value": k, "label": lbl} for k, (lbl, _) in PRESETS.items()]
    return opts
