"""Modelos, precios y rutas del laboratorio.

Todo lo que sale de la documentación de Google está aquí, con la fecha en que
se verificó, para que sea fácil ver cuándo hay que revisarlo:

- https://ai.google.dev/gemini-api/docs/veo      (verificado 2026-10-16)
- https://ai.google.dev/gemini-api/docs/omni     (verificado 2026-10-16)
- https://ai.google.dev/gemini-api/docs/pricing  (verificado 2026-10-16)

Veo 3.1 / Fast / Lite: duración 4, 6 u 8 s; 720p con cualquier duración,
1080p y 4k solo con 8 s; 9:16 o 16:9; imagen como primer fotograma; con
imagen, personGeneration solo admite "allow_adult". Audio siempre activo.
Google cobra por segundo y solo si el video se genera.

Gemini Omni Flash: va por la API de Interactions y se cobra por tokens
($1,50/M de entrada, $17,50/M de salida de video). Google estima ~$0,10/s a
720p (5.792 tokens de salida por segundo). La página no documenta la
duración; el SDK (google-genai 2.31) sí acepta `response_format.duration`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

LAB_DIR = Path(__file__).resolve().parent
RAIZ = LAB_DIR.parent
RESULTADOS_DIR = LAB_DIR / "resultados"
ENTRADAS = LAB_DIR / "entradas.json"

#: Tope de gasto por corrida si no se pasa --max-usd.
MAX_USD_DEFECTO = 5.0
#: Pedido explícito: nunca más de dos generaciones a la vez.
CONCURRENCIA_MAX = 2


@dataclass(frozen=True)
class Modelo:
    clave: str                      # nombre corto para la línea de comandos
    proveedor: str                  # qué VideoProvider lo atiende
    id: str                         # nombre del modelo en la API
    duraciones: tuple[int, ...]     # segundos permitidos
    resoluciones: dict[str, float] = field(default_factory=dict)  # res -> USD/s
    #: Resoluciones que exigen la duración máxima (1080p/4k en Veo).
    solo_8s: tuple[str, ...] = ()
    nota: str = ""


MODELOS: dict[str, Modelo] = {
    "veo-3.1": Modelo(
        "veo-3.1", "gemini-veo", "veo-3.1-generate-preview",
        (4, 6, 8), {"720p": 0.40, "1080p": 0.40, "4k": 0.60}, ("1080p", "4k"),
    ),
    "veo-3.1-fast": Modelo(
        "veo-3.1-fast", "gemini-veo", "veo-3.1-fast-generate-preview",
        (4, 6, 8), {"720p": 0.10, "1080p": 0.12, "4k": 0.30}, ("1080p", "4k"),
    ),
    "veo-3.1-lite": Modelo(
        "veo-3.1-lite", "gemini-veo", "veo-3.1-lite-generate-preview",
        (4, 6, 8), {"720p": 0.05, "1080p": 0.08}, ("1080p",),
        nota="no estaba en la lista pedida; disponible por si se quiere la opción barata",
    ),
    "omni-flash": Modelo(
        "omni-flash", "gemini-omni", "gemini-omni-1.1-flash",
        # 3-10 s por generación según fuentes secundarias; Google no lo
        # documenta. Se mide la duración real de lo que devuelve.
        tuple(range(3, 11)), {"720p": 0.10},
        nota="precio por tokens; el estimado por segundo es el de Google a 720p",
    ),
}

#: Los que se prueban si no se pide otra cosa (los de la tarea).
MODELOS_DEFECTO = ("veo-3.1", "veo-3.1-fast", "omni-flash")

#: Precio por token de Omni Flash (USD por millón).
OMNI_USD_M_ENTRADA = 1.50
OMNI_USD_M_SALIDA = {"video": 17.50, "text": 9.00}
#: Salidas sin precio propio publicado (audio dentro del video, imagen): se
#: cobran como video, que es lo caro, para no subestimar.
OMNI_USD_M_SALIDA_OTRA = 17.50


def clave_api() -> str | None:
    """La clave de Gemini, sin mostrarla nunca.

    Se carga el .env del proyecto en el entorno del proceso; el laboratorio no
    lo lee de otra forma ni imprime su contenido. LAB_VIDEO_GEMINI_API_KEY
    permite usar otra clave (otro proyecto de facturación) solo para el lab.
    """
    try:
        from dotenv import load_dotenv

        load_dotenv(RAIZ / ".env", override=False)
    except ImportError:
        pass
    return os.getenv("LAB_VIDEO_GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY") or None


def max_usd_defecto() -> float:
    try:
        return float(os.getenv("LAB_VIDEO_MAX_USD", MAX_USD_DEFECTO))
    except ValueError:
        return MAX_USD_DEFECTO
