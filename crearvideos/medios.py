"""Empareja una carpeta de imágenes y videos con las escenas del guion.

La idea: tú generas los medios donde quieras (Google Flow, Midjourney, stock,
tu cámara) y los dejas en una carpeta. Aquí se decide qué archivo acompaña a
qué escena.

Dos formas de emparejar, en este orden:

1. **Por número en el nombre.** Si los archivos traen el número de escena
   —`escena_0007.mp4`, `07.png`, `shot-7-final.jpg`— manda ese número. Es lo
   que sale de un export de StoryFlow Pro, y sobrevive a que falte uno o a que
   el explorador los ordene raro.
2. **Por orden natural.** Si no hay números útiles, se usa el orden en que los
   verías en la carpeta, con `imagen2` antes que `imagen10` (que es donde el
   orden alfabético de toda la vida se equivoca).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

IMAGENES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
VIDEOS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}

_NUMERO_RE = re.compile(r"\d+")


class MediosError(RuntimeError):
    """La carpeta no sirve: no existe, está vacía o no tiene medios."""


@dataclass
class Medio:
    path: Path
    numero: int | None
    tipo: str  # "imagen" | "video"

    @property
    def es_video(self) -> bool:
        return self.tipo == "video"


def _clave_natural(p: Path) -> list:
    """Ordena como el explorador: imagen2 antes que imagen10."""
    partes = _NUMERO_RE.split(p.name.lower())
    numeros = [int(n) for n in _NUMERO_RE.findall(p.name)]
    return [x for par in zip(partes + [""], numeros + [0]) for x in par]


def _numero(p: Path) -> int | None:
    """El primer número del nombre, que es el de escena cuando lo hay.

    Se mira solo el nombre, no la carpeta: una ruta como `2026/proyecto/a.png`
    no debe dar el año como número de escena.
    """
    m = _NUMERO_RE.search(p.stem)
    return int(m.group()) if m else None


def recolectar(carpeta: Path) -> list[Medio]:
    """Todos los medios de la carpeta, en orden natural."""
    if not carpeta.is_dir():
        raise MediosError(f"No existe la carpeta de medios: {carpeta}")

    archivos = [
        p
        for p in carpeta.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGENES | VIDEOS
    ]
    if not archivos:
        raise MediosError(
            f"En {carpeta} no hay imágenes ni videos. "
            f"Acepta: {', '.join(sorted(IMAGENES | VIDEOS))}"
        )
    archivos.sort(key=_clave_natural)
    return [
        Medio(
            path=p,
            numero=_numero(p),
            tipo="video" if p.suffix.lower() in VIDEOS else "imagen",
        )
        for p in archivos
    ]


def _por_numero(medios: list[Medio], escenas: int) -> list[Medio | None] | None:
    """Empareja por el número del nombre, o None si esos números no sirven.

    Se exige que casi todos tengan número, que no se repitan y que caigan
    dentro del rango de escenas. Si algo de eso falla, el número del nombre
    significaba otra cosa (una fecha, una resolución) y es mejor el orden.
    """
    con_numero = [m for m in medios if m.numero is not None]
    if len(con_numero) < len(medios) * 0.8:
        return None

    numeros = [m.numero for m in con_numero]
    if len(set(numeros)) != len(numeros):
        return None
    # Los números suelen empezar en 1; si empiezan en 0, se acepta también.
    base = 0 if min(numeros) == 0 else 1
    if max(numeros) - base + 1 > escenas or min(numeros) < 0:
        return None

    salida: list[Medio | None] = [None] * escenas
    for m in con_numero:
        salida[m.numero - base] = m
    return salida


@dataclass
class Emparejado:
    """El resultado: qué medio va en cada escena y qué hay que avisar."""

    medios: list[Medio | None]
    metodo: str            # "numero" | "orden"
    avisos: list[str]

    @property
    def faltantes(self) -> list[int]:
        """Números de escena (empezando en 1) que se quedaron sin medio."""
        return [i + 1 for i, m in enumerate(self.medios) if m is None]


def emparejar(medios: list[Medio], escenas: int) -> Emparejado:
    """Decide qué archivo acompaña a cada escena."""
    avisos: list[str] = []

    por_numero = _por_numero(medios, escenas)
    if por_numero is not None:
        metodo = "numero"
        salida = por_numero
    else:
        metodo = "orden"
        salida = [medios[i] if i < len(medios) else None for i in range(escenas)]
        if len(medios) > escenas:
            avisos.append(
                f"Sobran {len(medios) - escenas} archivos: el guion tiene {escenas} escenas."
            )

    faltan = [i for i, m in enumerate(salida) if m is None]
    if faltan:
        avisos.append(
            f"{len(faltan)} escena(s) sin medio propio "
            f"({', '.join(str(i + 1) for i in faltan[:8])}"
            f"{'…' if len(faltan) > 8 else ''}): se repite el anterior."
        )
        # Rellenar con el medio anterior (o el primero, si falta el principio)
        # evita cortar el montaje a mitad por un archivo que no llegó.
        ultimo: Medio | None = next((m for m in salida if m is not None), None)
        for i, m in enumerate(salida):
            if m is None:
                salida[i] = ultimo
            else:
                ultimo = m

    return Emparejado(medios=salida, metodo=metodo, avisos=avisos)


def resumen(emp: Emparejado, escenas: int) -> list[dict]:
    """Tabla escena -> archivo, para enseñarla antes de montar."""
    return [
        {
            "escena": i + 1,
            "archivo": m.path.name if m else "—",
            "tipo": m.tipo if m else "",
        }
        for i, m in enumerate(emp.medios[:escenas])
    ]
