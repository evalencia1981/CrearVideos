"""La estética de un canal, en imágenes: el aspecto antes que el contenido.

Lo que engancha a alguien que hace scroll no es el tema, es cómo se ve. Este
módulo coge el análisis de un canal que funciona —su estilo visual, su paleta,
cómo son sus miniaturas— y genera un **tablero de estética** para el canal que
vas a crear:

    personaje.jpg    el narrador o protagonista recurrente, siempre igual
    escena.jpg       una escena típica del nicho, con ese look
    miniatura.jpg    la composición de miniatura que usa ese nicho (16:9)
    avatar.jpg       la imagen de perfil del canal (1:1)
    portada.jpg      la cabecera del canal (16:9 panorámico)

El personaje sirve para algo más que mirarlo: es la **imagen de referencia**
que el módulo de producción usa para que salga la misma persona en todas las
escenas de todos los videos. Ahí es donde un canal empieza a parecer un canal.

Las imágenes NO copian las del canal analizado: de ese análisis se toman el
tipo de luz, la paleta y el encuadre —lo que hace que un nicho se vea como se
ve— y con eso se construye una identidad propia.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings, load_settings
from . import ia
from .analizador import ESTUDIO_DIR

#: Las piezas del tablero: (clave, título, tamaño, qué se le pide a la IA).
PIEZAS: list[tuple[str, str, tuple[int, int], str]] = [
    (
        "personaje", "Personaje / narrador", (1080, 1920),
        "el protagonista recurrente del canal, de cuerpo entero o medio cuerpo, "
        "mirando a cámara, con ropa y rasgos concretos y memorables; fondo "
        "sencillo del mundo del canal. Debe poder repetirse igual en cada video",
    ),
    (
        "escena", "Escena típica", (1080, 1920),
        "una escena representativa de lo que se ve en estos videos, sin "
        "personas mirando a cámara, con la luz y la paleta del canal",
    ),
    (
        "miniatura", "Miniatura", (1280, 720),
        "una miniatura de YouTube: un solo foco de atención, mucho contraste, "
        "expresión o elemento potente, espacio libre a un lado para poner texto "
        "después. SIN texto ni letras en la imagen",
    ),
    (
        "avatar", "Avatar del canal", (1024, 1024),
        "el icono del canal: un símbolo o emblema simple, legible a tamaño "
        "diminuto, centrado, fondo plano. Nada de texto",
    ),
    (
        "portada", "Portada del canal", (1920, 1080),
        "la cabecera del canal: composición amplia y panorámica del mundo del "
        "canal, con el centro despejado. Sin texto",
    ),
]


#: Cola que se pega a TODOS los prompts. Va en código, no en el prompt de la
#: IA: pedirle "sin texto" acierta casi siempre, y ese "casi" es un avatar con
#: letras inventadas.
SIN_TEXTO = "no text, no letters, no watermark, no logo"
#: Las piezas donde tiene que salir el MISMO personaje. Sin repetir su
#: descripción literal en cada prompt, cada imagen inventa una persona distinta
#: —es lo que pasaba al principio— y el canal deja de parecer un canal.
CON_PERSONAJE = ("personaje", "escena", "miniatura", "portada")
#: El avatar es la excepción: un icono de canal con una cara no se lee a
#: tamaño diminuto, y el modelo tira a retrato si no se le prohíbe.
SOLO_SIMBOLO = "flat emblem icon, symbol only, centered, no people, no faces, no portrait"


#: Palabras que delatan que un prompt de avatar describe a una persona.
_PERSONAS = ("woman", "man", "person", "portrait", "face", "people", "host", "doctor")


def _componer(prompt: str, clave: str, look: str, nicho: str = "", paleta: str = "") -> str:
    """Prompt final: lo que diseñó la IA más lo que no se puede dejar al azar."""
    if not prompt:
        return ""

    if clave == "avatar" and any(p in prompt.lower() for p in _PERSONAS):
        # El avatar se reescribe entero cuando la IA ha descrito a alguien. Una
        # cara no se lee a 48 píxeles, que es el tamaño al que se ve de verdad
        # un icono de canal, y pedir "sin personas" al final no basta: el
        # modelo sigue la descripción larga que va delante.
        prompt = (
            f"Flat vector emblem icon for a {nicho or 'YouTube'} channel, "
            f"one simple bold symbol, {paleta}, solid background, minimal, "
            f"high contrast, legible at tiny size"
        )

    trozos = [prompt.rstrip(". ")]
    if look and clave in CON_PERSONAJE:
        trozos.append(f"The recurring character, always identical: {look}")
    if clave == "avatar":
        trozos.append(SOLO_SIMBOLO)
    trozos.append(SIN_TEXTO)
    return ". ".join(trozos) + "."


class EsteticaError(RuntimeError):
    """No se pudo crear el tablero de estética."""


@dataclass
class Tablero:
    nombre: str
    carpeta: Path
    concepto: str = ""
    paleta: list[str] = field(default_factory=list)
    piezas: list[dict] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


SISTEMA = (
    "Eres un director de arte que define la identidad visual de un canal de "
    "YouTube sin cámara. Recibes el análisis de un canal que ya funciona en ese "
    "nicho y tienes que diseñar una estética PROPIA para un canal nuevo: que se "
    "note del mismo mundo, pero que no sea una copia.\n\n"
    "Forma exacta del JSON:\n"
    "{\n"
    ' "concepto": "en dos frases, qué se ve en este canal y qué sensación deja",\n'
    ' "paleta": ["#RRGGBB", "#RRGGBB", "#RRGGBB", "#RRGGBB"],\n'
    ' "personaje": {"nombre": "cómo se le llama", "descripcion": "descripción '
    'física EXACTA en 30-40 palabras: edad, complexión, piel, pelo, un rasgo '
    'distintivo, ropa con material y color. Tan concreta que dos ilustradores '
    'distintos dibujen lo mismo"},\n'
    ' "prompts": {"personaje": "...", "escena": "...", "miniatura": "...", '
    '"avatar": "...", "portada": "..."}\n'
    "}\n\n"
    "Sobre los prompts:\n"
    "- Van en INGLÉS: los modelos de imagen lo siguen mucho mejor.\n"
    "- Entre 40 y 70 palabras cada uno. Descripción visual pura: sujeto, "
    "encuadre, luz, materiales, color. Nada de explicaciones ni de marcas.\n"
    "- Los cinco tienen que verse de la MISMA familia visual: misma luz, misma "
    "paleta, mismo medio (fotografía, ilustración, 3D…).\n"
    "- En el de 'personaje' incorpora literalmente la descripción física; es la "
    "imagen que después fija a ese personaje en todos los videos.\n"
    "- Termina cada prompt con 'no text, no letters, no watermark'.\n"
    "Devuelve SOLO el objeto JSON."
)


def _brief(informe: dict) -> str:
    """Lo que el análisis del canal aporta al director de arte."""
    analisis = informe.get("analisis") or {}
    ev = analisis.get("estilo_visual") or {}
    mini = analisis.get("miniaturas") or analisis.get("miniatura") or {}
    canal = informe.get("canal") or informe.get("video") or {}

    lineas = [
        f"CANAL ANALIZADO: {canal.get('nombre') or canal.get('canal', '')}",
        f"Nicho: {analisis.get('nicho', '')}",
        f"Audiencia: {analisis.get('audiencia', '')}",
        f"Tono: {analisis.get('tono', '')}",
        f"Estilo visual: {ev.get('estilo', '')}",
        f"Paleta que usa: {ev.get('paleta', '')}",
        f"Lo que evita: {ev.get('evitar', '')}",
        f"Sus miniaturas: {mini.get('patron') or mini.get('descripcion', '')} · "
        f"colores {mini.get('colores', '')}",
        f"Producción: {(analisis.get('formato') or {}).get('produccion', '')}",
    ]
    return "\n".join(l for l in lineas if l.split(": ", 1)[-1].strip())


def _informe(nombre: str) -> tuple[dict, Path]:
    """Busca el análisis ya hecho de ese canal o video."""
    for sub in ("canales", "videos"):
        carpeta = ESTUDIO_DIR / sub / nombre
        archivo = carpeta / "informe.json"
        if archivo.is_file():
            return json.loads(archivo.read_text(encoding="utf-8")), carpeta
    raise EsteticaError(
        f"No encuentro el análisis de '{nombre}'. Analiza antes el canal o el "
        "video en la pestaña 1 · Estudio: de ahí sale la estética."
    )


def disenar(informe: dict, extra: str = "") -> dict:
    """Pide al director de arte la identidad visual. No genera imágenes."""
    peticion = _brief(informe)
    if extra.strip():
        peticion += "\n\nLO QUE QUIERE EL AUTOR (manda sobre lo demás):\n" + extra.strip()
    return ia.pedir_json(SISTEMA, peticion, max_tokens=4000)


def crear(
    nombre: str,
    *,
    extra: str = "",
    piezas: list[str] | None = None,
    settings: Settings | None = None,
    on_progress=None,
) -> Tablero:
    """Diseña y genera el tablero de estética de un canal analizado."""
    from ..providers.images.pollinations import PollinationsImages

    def aviso(m: str) -> None:
        if on_progress:
            on_progress(m)

    settings = settings or load_settings()
    informe, origen = _informe(nombre)

    aviso("Diseñando la identidad visual con IA")
    diseño = disenar(informe, extra)
    prompts = diseño.get("prompts") or {}
    personaje = diseño.get("personaje") or {}

    carpeta = origen / "estetica"
    carpeta.mkdir(parents=True, exist_ok=True)

    proveedor = PollinationsImages(model=settings.image_model or None)
    if not proveedor.has_token:
        # Sin token el endpoint anónimo ignora el tamaño y marca la imagen con
        # agua: el tablero saldría inservible como identidad de canal.
        raise EsteticaError(
            "La estética necesita POLLINATIONS_TOKEN en el .env: sin token las "
            "imágenes salen pequeñas y con marca de agua. Es gratis, se saca en "
            "https://enter.pollinations.ai"
        )

    elegidas = [p for p in PIEZAS if not piezas or p[0] in piezas]
    generadas: list[dict] = []
    avisos: list[str] = []
    look = str(personaje.get("descripcion", "")).strip()

    for i, (clave, titulo, tamaño, _) in enumerate(elegidas):
        prompt = _componer(
            str(prompts.get(clave, "")).strip(), clave, look,
            nicho=str((informe.get("analisis") or {}).get("nicho", "")),
            paleta=", ".join(str(c) for c in (diseño.get("paleta") or [])[:4]),
        )
        if not prompt:
            avisos.append(f"Sin prompt para «{titulo}»")
            continue
        aviso(f"Generando {titulo.lower()} ({i + 1}/{len(elegidas)})")
        destino = carpeta / f"{clave}.jpg"
        try:
            crudo = proveedor._fetch(proveedor._url(prompt, i, tamaño=tamaño))
            _guardar(crudo, destino)
        except Exception as e:  # noqa: BLE001 - una pieza fallida no tumba el tablero
            avisos.append(f"{titulo}: {e}")
            continue
        generadas.append(
            {"clave": clave, "titulo": titulo, "archivo": destino.name,
             "prompt": prompt, "tamaño": f"{tamaño[0]}x{tamaño[1]}"}
        )

    if not generadas:
        raise EsteticaError("No se pudo generar ninguna imagen: " + "; ".join(avisos))

    tablero = Tablero(
        nombre=nombre,
        carpeta=carpeta,
        concepto=str(diseño.get("concepto", "")),
        paleta=[str(c) for c in (diseño.get("paleta") or [])][:6],
        piezas=generadas,
        avisos=avisos,
    )
    (carpeta / "estetica.json").write_text(
        json.dumps(
            {
                "canal": nombre,
                "concepto": tablero.concepto,
                "paleta": tablero.paleta,
                "personaje": personaje,
                "piezas": generadas,
                "avisos": avisos,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    # La ficha del personaje, aparte y en texto: es lo que se pega en la
    # cabecera de un guion para que salga siempre el mismo protagonista.
    if personaje.get("descripcion"):
        (carpeta / "personaje.txt").write_text(
            f"protagonistas: {personaje.get('nombre', 'el narrador')} — "
            f"{personaje['descripcion']}\n",
            encoding="utf-8",
        )
    return tablero


def _guardar(crudo: bytes, destino: Path) -> None:
    """Guarda la imagen tal como vino, solo pasándola a JPEG.

    Sin recortes: aquí el formato pedido ya es el bueno (16:9 la miniatura, 1:1
    el avatar), al revés que en el pipeline, donde todo acaba en 9:16.
    """
    import io

    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(crudo)) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.save(destino, "JPEG", quality=92)
