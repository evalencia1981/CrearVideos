"""Miniatura del video: fondo (IA, imagen propia o externa) + título bien puesto.

Los modelos de imagen escriben mal las letras ("HUNNDIR O CURETORR"), así que
aquí la imagen se pide SIN texto y el título se compone después en local, con
una tipografía de miniatura, borde, sombra y un degradado que garantiza que se
lea aunque el fondo sea claro. Así el texto sale perfecto y se puede cambiar
sin volver a generar (ni pagar) el fondo.

El fondo puede venir de tres sitios:

- generado con la IA de imagen de la app (Pollinations), ya en la proporción
  de la miniatura y con espacio libre del lado del texto;
- una imagen tuya (una escena del video, una foto, lo que hiciste en otra IA);
- ninguno todavía: `prompt()` da el texto para pedirlo en una IA externa.

En el texto, las palabras entre *asteriscos* salen resaltadas en color.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

#: Formatos de miniatura: YouTube pide 1280x720; la vertical es la portada de
#: Shorts / TikTok / Reels.
FORMATOS: dict[str, tuple[int, int]] = {
    "youtube": (1280, 720),
    "vertical": (1080, 1920),
}

#: Estilos de texto. Las fuentes vienen en cualquier Windows, así que también
#: funcionan en el .exe que se reparte; se prueban en orden.
ESTILOS: dict[str, dict] = {
    "impacto": {
        "nombre": "Impacto · blanco y amarillo",
        "fuentes": ["impact.ttf", "ariblk.ttf", "seguibl.ttf"],
        "texto": "#FFFFFF", "resalte": "#FFD400", "borde": "#000000",
        "degradado": (0, 0, 0),
    },
    "elegante": {
        "nombre": "Elegante · blanco limpio",
        "fuentes": ["seguibl.ttf", "bahnschrift.ttf", "ariblk.ttf"],
        "texto": "#FFFFFF", "resalte": "#8FD8FF", "borde": "#0A0A10",
        "degradado": (8, 10, 20),
    },
    "esperanza": {
        "nombre": "Esperanza · crema y dorado",
        "fuentes": ["ariblk.ttf", "seguibl.ttf", "impact.ttf"],
        "texto": "#FFF6E2", "resalte": "#FFC23D", "borde": "#2E1A08",
        "degradado": (36, 20, 6),
    },
    "alerta": {
        "nombre": "Alerta · blanco y rojo",
        "fuentes": ["impact.ttf", "ariblk.ttf"],
        "texto": "#FFFFFF", "resalte": "#FF3B30", "borde": "#000000",
        "degradado": (0, 0, 0),
    },
}

LADOS = ("izquierda", "derecha", "centro")

# La puntuación pegada al cierre ("*presidente*?") va con su palabra; si no,
# el signo queda suelto en otra posición.
_RESALTE_RE = re.compile(r"\*([^*]+)\*([^\s*]*)")


class MiniaturaError(RuntimeError):
    """No se pudo crear la miniatura."""


def opciones() -> dict:
    """Lo que la web necesita para pintar los selectores."""
    return {
        "estilos": [{"value": k, "label": v["nombre"]} for k, v in ESTILOS.items()],
        "formatos": [
            {"value": "youtube", "label": "YouTube 16:9 (1280×720)"},
            {"value": "vertical", "label": "Vertical 9:16 · Shorts / TikTok"},
        ],
    }


# --------------------------------------------------------------- el prompt

def prompt(idea: str, *, estilo_visual: str | None = None, formato: str = "youtube",
           lado: str = "izquierda") -> str:
    """Prompt para generar el FONDO, en la app o en una IA externa.

    Va en inglés porque los modelos de imagen lo siguen mejor, salvo la idea,
    que se deja tal cual la escribió el usuario (los modelos la entienden).
    """
    idea = " ".join((idea or "").split()).strip(" .")
    if not idea:
        raise MiniaturaError("Describe qué debe verse en la miniatura.")
    if formato == "vertical":
        espacio = "Keep the top third of the frame clean and darker for a big title"
        proporcion = "vertical 9:16 composition"
    elif lado == "centro":
        espacio = "Keep the lower third clean and darker for a big title"
        proporcion = "horizontal 16:9 composition"
    else:
        libre = "left" if lado == "izquierda" else "right"
        foco = "right" if libre == "left" else "left"
        espacio = (f"Main subject on the {foco} side; keep the {libre} 45% of the frame "
                   f"clean, simple and darker for a big title")
        proporcion = "horizontal 16:9 composition"
    partes = [
        f"YouTube thumbnail background: {idea}",
        "one strong focal point, expressive emotion, high contrast, cinematic lighting, "
        "vivid but natural colors, sharp focus, shallow depth of field",
        espacio,
        proporcion,
    ]
    if estilo_visual:
        partes.insert(2, estilo_visual)
    partes.append("no text, no letters, no words, no logo, no watermark")
    return ". ".join(p.rstrip(". ") for p in partes) + "."


def generar_fondo(texto_prompt: str, destino: Path, settings, *, formato: str = "youtube",
                  semilla: int = 0) -> Path:
    """Genera el fondo con la IA de imagen configurada, en la proporción pedida."""
    from .providers import get_image_provider

    settings.formato = "vertical" if formato == "vertical" else "horizontal"
    proveedor = get_image_provider(settings)
    proveedor.preflight()
    destino.parent.mkdir(parents=True, exist_ok=True)
    proveedor.generate(texto_prompt, destino, index=semilla, subject=texto_prompt)
    return destino


# --------------------------------------------------------------- componer

def _fuente(nombres: list[str], tamaño: int) -> ImageFont.FreeTypeFont:
    carpetas = [Path(__file__).resolve().parent.parent / "assets" / "fuentes",
                Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]
    for nombre in nombres:
        for c in carpetas:
            ruta = c / nombre
            if ruta.is_file():
                return ImageFont.truetype(str(ruta), tamaño)
    return ImageFont.load_default(size=tamaño)


def _palabras(texto: str) -> list[tuple[str, bool]]:
    """[(palabra, resaltada)]. Sin asteriscos, se resalta la última palabra."""
    texto = " ".join(texto.split())
    if not texto:
        return []
    marcadas: list[tuple[str, bool]] = []
    cursor = 0
    for m in _RESALTE_RE.finditer(texto):
        marcadas += [(w, False) for w in texto[cursor:m.start()].split()]
        resaltadas = m.group(1).split()
        if resaltadas and m.group(2):
            resaltadas[-1] += m.group(2)
        marcadas += [(w, True) for w in resaltadas]
        cursor = m.end()
    marcadas += [(w, False) for w in texto[cursor:].split()]
    if not any(r for _, r in marcadas):
        marcadas[-1] = (marcadas[-1][0], True)
    return [(w.upper(), r) for w, r in marcadas]


def _lineas(palabras, fuente, ancho_max: float, espacio: float, max_lineas: int):
    """Reparte las palabras en líneas que quepan; None si no caben."""
    lineas: list[list[tuple[str, bool]]] = [[]]
    ancho = 0.0
    for w, r in palabras:
        largo = fuente.getlength(w)
        extra = largo if not lineas[-1] else espacio + largo
        if lineas[-1] and ancho + extra > ancho_max:
            lineas.append([(w, r)])
            ancho = largo
        else:
            lineas[-1].append((w, r))
            ancho += extra
        if largo > ancho_max:
            return None
    return lineas if len(lineas) <= max_lineas else None


def _degradado(tamaño: tuple[int, int], lado: str, color: tuple[int, int, int],
               vertical: bool) -> Image.Image:
    """Capa oscura que va de opaca (lado del texto) a transparente."""
    w, h = tamaño
    if vertical or lado == "centro":
        largo, cubre = h, 0.62
    else:
        largo, cubre = w, 0.68
    rampa = Image.new("L", (largo, 1))
    for i in range(largo):
        t = i / (largo * cubre)
        rampa.putpixel((i, 0), int(215 * max(0.0, 1 - t) ** 1.6))
    if vertical:
        alfa = rampa.rotate(-90, expand=True).resize((w, h))           # arriba
    elif lado == "centro":
        alfa = rampa.rotate(90, expand=True).resize((w, h))            # abajo
    elif lado == "derecha":
        alfa = rampa.transpose(Image.FLIP_LEFT_RIGHT).resize((w, h))
    else:
        alfa = rampa.resize((w, h))
    capa = Image.new("RGBA", (w, h), color + (0,))
    capa.putalpha(alfa)
    return capa


def componer(fondo: Path, texto: str, salida: Path, *, formato: str = "youtube",
             estilo: str = "impacto", lado: str = "izquierda") -> Path:
    """Pone el título sobre el fondo y guarda la miniatura en JPEG."""
    w, h = FORMATOS.get(formato, FORMATOS["youtube"])
    e = ESTILOS.get(estilo, ESTILOS["impacto"])
    lado = lado if lado in LADOS else "izquierda"
    vertical = formato == "vertical"
    palabras = _palabras(texto)
    if not palabras:
        raise MiniaturaError("Escribe el texto de la miniatura.")

    with Image.open(fondo) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        # Una imagen más alta que el destino se recorta tirando hacia arriba:
        # las caras suelen estar en el tercio superior, y recortar por el
        # centro las decapitaba.
        mas_alta = img.height / img.width > h / w
        base = ImageOps.fit(img, (w, h), Image.LANCZOS,
                            centering=(0.5, 0.3) if mas_alta else (0.5, 0.5))
    lienzo = base.convert("RGBA")
    lienzo.alpha_composite(_degradado((w, h), lado, e["degradado"], vertical))

    # Zona donde va el texto.
    if vertical:
        zona = (int(w * 0.07), int(h * 0.06), int(w * 0.93), int(h * 0.36))
        max_lineas = 4
    elif lado == "centro":
        zona = (int(w * 0.06), int(h * 0.52), int(w * 0.94), int(h * 0.94))
        max_lineas = 2
    else:
        x0 = int(w * 0.05) if lado == "izquierda" else int(w * 0.47)
        zona = (x0, int(h * 0.08), x0 + int(w * 0.48), int(h * 0.92))
        max_lineas = 3
    zw, zh = zona[2] - zona[0], zona[3] - zona[1]

    # El tamaño de letra más grande con el que el texto cabe en la zona.
    elegido = None
    for tam in range(int(h * (0.26 if not vertical else 0.11)), 18, -2):
        fuente = _fuente(e["fuentes"], tam)
        borde = max(2, int(tam * 0.07))
        lineas = _lineas(palabras, fuente, zw - 2 * borde, fuente.getlength(" "), max_lineas)
        if lineas and len(lineas) * tam * 1.02 <= zh:
            elegido = (fuente, tam, borde, lineas)
            break
    if elegido is None:
        raise MiniaturaError("El texto es demasiado largo para la miniatura: usa 3 a 6 palabras.")
    fuente, tam, borde, lineas = elegido
    espacio = fuente.getlength(" ")
    alto_linea = tam * 1.02
    y = zona[1] + (zh - alto_linea * len(lineas)) / 2

    sombra = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    capa = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ds, dt = ImageDraw.Draw(sombra), ImageDraw.Draw(capa)
    for linea in lineas:
        ancho = sum(fuente.getlength(p) for p, _ in linea) + espacio * (len(linea) - 1)
        if vertical or lado == "centro":
            x = zona[0] + (zw - ancho) / 2
        elif lado == "derecha":
            x = zona[2] - ancho - borde
        else:
            x = zona[0] + borde
        for palabra, resaltada in linea:
            off = max(3, int(tam * 0.05))
            ds.text((x + off, y + off), palabra, font=fuente, fill=(0, 0, 0, 200),
                    stroke_width=borde, stroke_fill=(0, 0, 0, 200))
            dt.text((x, y), palabra, font=fuente,
                    fill=e["resalte"] if resaltada else e["texto"],
                    stroke_width=borde, stroke_fill=e["borde"])
            x += fuente.getlength(palabra) + espacio
        y += alto_linea

    lienzo.alpha_composite(sombra.filter(ImageFilter.GaussianBlur(max(2, tam * 0.05))))
    lienzo.alpha_composite(capa)
    salida.parent.mkdir(parents=True, exist_ok=True)
    lienzo.convert("RGB").save(salida, "JPEG", quality=93)
    return salida
