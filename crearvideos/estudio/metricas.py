"""Números que se calculan, no se le preguntan a la IA.

Todo lo que se puede medir se mide en Python: la IA solo interpreta. Así un
"este video hizo 14× lo normal del canal" es un dato y no una opinión inventada.
"""

from __future__ import annotations

import statistics
from datetime import date

from .youtube import Entrada

#: Rango de RPM en USD (lo que cobra el creador por cada 1000 vistas) según el
#: nicho, para videos LARGOS con audiencia mayoritaria en EE.UU./Europa.
#:
#: OJO: YouTube no publica el RPM de ningún canal. Estos rangos son promedios
#: que circulan entre creadores y herramientas del sector — sirven para comparar
#: nichos entre sí, no para prometer ingresos. Una audiencia en español de
#: Latinoamérica cobra típicamente entre la mitad y un tercio de esto.
RPM_NICHO: dict[str, tuple[float, float, str]] = {
    "finanzas": (12.0, 30.0, "Finanzas personales, inversión, cripto"),
    "negocios": (8.0, 20.0, "Negocios, emprendimiento, marketing"),
    "tecnologia": (5.0, 15.0, "Tecnología, IA, software, gadgets"),
    "educacion": (4.0, 12.0, "Educación, ciencia, explicadores"),
    "salud": (5.0, 14.0, "Salud, fitness, nutrición"),
    "legal_seguros": (10.0, 25.0, "Legal, seguros, inmobiliario"),
    "historia": (3.0, 9.0, "Historia, documental"),
    "true_crime": (3.0, 8.0, "Crimen real, misterios"),
    "terror": (2.0, 6.0, "Terror, leyendas, creepypasta"),
    "curiosidades": (2.0, 6.0, "Curiosidades, top 10, datos"),
    "motivacion": (2.5, 7.0, "Motivación, desarrollo personal, estoicismo"),
    "religion": (2.0, 5.0, "Religión, espiritualidad"),
    "entretenimiento": (1.5, 5.0, "Entretenimiento, reacciones, farándula"),
    "animales": (2.0, 6.0, "Animales, naturaleza"),
    "cocina": (3.0, 8.0, "Cocina, recetas"),
    "viajes": (3.0, 9.0, "Viajes, lugares"),
    "gaming": (1.5, 5.0, "Videojuegos"),
    "musica": (0.5, 2.5, "Música, relajación, sonidos para dormir"),
    "infantil": (0.5, 2.0, "Infantil (contenido para niños, anuncios limitados)"),
    "otro": (1.5, 6.0, "Otro"),
}

#: El RPM de Shorts se reparte de un fondo común y es mucho más bajo.
RPM_SHORTS = (0.03, 0.10)


def nichos_rpm() -> str:
    """Lista de claves para que la IA elija el nicho de la tabla."""
    return ", ".join(f"{k} ({v[2]})" for k, v in RPM_NICHO.items())


def ingresos(vistas: int | None, nicho: str, tipo: str) -> dict | None:
    """Rango de ingresos estimado para un número de vistas."""
    if not vistas:
        return None
    lo, hi = RPM_SHORTS if tipo == "short" else RPM_NICHO.get(nicho, RPM_NICHO["otro"])[:2]
    return {
        "rpm_usd": [lo, hi],
        "usd": [round(vistas / 1000 * lo), round(vistas / 1000 * hi)],
        "nota": "estimación por rango de RPM del nicho; YouTube no publica el RPM real",
    }


def mediana(entradas: list[Entrada], tipo: str) -> float | None:
    vistas = [e.vistas for e in entradas if e.tipo == tipo and e.vistas]
    # Con menos de 5 videos la mediana no dice nada sobre "lo normal" del canal.
    return statistics.median(vistas) if len(vistas) >= 5 else None


def outliers(entradas: list[Entrada]) -> list[dict]:
    """Cada video con su múltiplo sobre la mediana de su tipo, de mayor a menor.

    La mediana, no el promedio: un solo video viral infla el promedio y hace que
    todo lo demás parezca un fracaso.
    """
    medianas = {t: mediana(entradas, t) for t in ("largo", "short")}
    filas = []
    for e in entradas:
        m = medianas.get(e.tipo)
        if not e.vistas or not m:
            continue
        filas.append(
            {
                "id": e.id,
                "titulo": e.titulo,
                "tipo": e.tipo,
                "vistas": e.vistas,
                "duracion": e.duracion,
                "multiplo": round(e.vistas / m, 1),
            }
        )
    filas.sort(key=lambda f: f["multiplo"], reverse=True)
    return filas


def veredicto(multiplo: float | None) -> str:
    if multiplo is None:
        return "sin datos suficientes del canal"
    if multiplo >= 10:
        return "outlier fuerte"
    if multiplo >= 3:
        return "outlier"
    if multiplo >= 1.5:
        return "por encima de lo normal"
    if multiplo >= 0.7:
        return "normal para el canal"
    return "por debajo de lo normal"


def vistas_por_dia(vistas: int | None, fecha: str) -> int | None:
    if not vistas or not fecha:
        return None
    try:
        dias = (date.today() - date.fromisoformat(fecha)).days
    except ValueError:
        return None
    return round(vistas / max(dias, 1))


def palabras_por_minuto(trozos: list[tuple[float, str]], duracion: int | None) -> int | None:
    if not trozos or not duracion:
        return None
    palabras = sum(len(t.split()) for _, t in trozos)
    return round(palabras / (duracion / 60))


def engagement(likes: int | None, comentarios: int | None, vistas: int | None) -> float | None:
    """(likes + comentarios) / vistas, en %. Sobre 4% es muy bueno en largos."""
    if not vistas or likes is None:
        return None
    return round((likes + (comentarios or 0)) / vistas * 100, 2)
