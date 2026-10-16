"""Buscador de nichos: qué se está viendo, quién lo hace y cuánto deja.

La idea es la del "buscador de nichos" de las apps de pago, pero sin su base de
datos ni su API: se busca en YouTube como lo haría cualquiera, se enriquece
cada resultado con lo que la página pública dice, y se ordena por señales que
sí significan algo.

Las tres señales que importan, y por qué:

- **Vistas por día**: un video de hace tres años con 2 M de vistas no dice lo
  mismo que uno de hace dos semanas con 300 K. Lo segundo es lo que está
  funcionando AHORA.
- **Vistas por suscriptor**: si un canal de 5 000 suscriptores hace 400 000
  vistas, ese video se salió de su audiencia — lo empujó el algoritmo, no su
  público. Es la señal de que la fórmula funciona sola.
- **Ingresos estimados**: las vistas no valen lo mismo en finanzas que en
  entretenimiento. Se cruzan con la tabla de RPM por nicho.

Nada de esto es exacto —YouTube no publica ni el RPM ni la retención— pero es
lo mismo con lo que trabajan las herramientas de pago, y aquí se ve de dónde
sale cada número.
"""

from __future__ import annotations

import concurrent.futures
import json
from dataclasses import asdict, dataclass, field
from datetime import date

from . import ia, metricas
from .youtube import SHORT_MAX_SECONDS, _extraer, YouTubeError

#: Cuántos resultados se piden a YouTube por búsqueda. Más no ayuda: a partir
#: del tercio de la lista, la relevancia cae y solo se gana espera.
POR_BUSQUEDA = 12
#: Hilos para enriquecer. YouTube tolera bien esto; subirlo empieza a dar 429.
HILOS = 6


@dataclass
class Hallazgo:
    """Un video encontrado, con todo lo que se pudo medir de él."""

    id: str
    titulo: str
    canal: str
    canal_url: str
    vistas: int
    duracion: int
    miniatura: str
    tipo: str = "largo"
    suscriptores: int | None = None
    fecha: str = ""
    dias: int | None = None
    vistas_dia: int | None = None
    vistas_por_suscriptor: float | None = None
    tags: list[str] = field(default_factory=list)
    descripcion: str = ""
    # Lo que decide la IA en una sola pasada para todos los resultados.
    nicho: str = ""
    nicho_rpm: str = "otro"
    faceless: str = ""
    por_que: str = ""
    ingresos: dict | None = None

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.id}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["url"] = self.url
        return d


def _flat(consulta: str, limite: int) -> list[Hallazgo]:
    """La búsqueda en sí: rápida, pero sin suscriptores ni fecha."""
    info, _ = _extraer(f"ytsearch{limite}:{consulta}", extract_flat=True)
    salida: list[Hallazgo] = []
    for e in info.get("entries") or []:
        if not e or not e.get("id") or not e.get("view_count"):
            continue
        duracion = int(e.get("duration") or 0)
        miniaturas = e.get("thumbnails") or []
        salida.append(
            Hallazgo(
                id=e["id"],
                titulo=e.get("title") or "",
                canal=e.get("channel") or e.get("uploader") or "",
                canal_url=e.get("channel_url") or "",
                vistas=int(e["view_count"]),
                duracion=duracion,
                # La miniatura buena, la misma que se ve en YouTube.
                miniatura=f"https://i.ytimg.com/vi/{e['id']}/hqdefault.jpg",
                tipo="short" if 0 < duracion <= SHORT_MAX_SECONDS else "largo",
            )
        )
    return salida


def _enriquecer(h: Hallazgo) -> Hallazgo:
    """Añade suscriptores y fecha, que la búsqueda no trae.

    Es una petición por video, así que se hacen en paralelo. Si una falla, se
    devuelve el hallazgo como estaba: perder la fecha de uno no puede tumbar
    la búsqueda entera.
    """
    try:
        info, _ = _extraer(h.url)
    except (YouTubeError, Exception):  # noqa: BLE001
        return h

    h.suscriptores = info.get("channel_follower_count")
    crudo = info.get("upload_date") or ""
    if len(crudo) == 8:
        h.fecha = f"{crudo[:4]}-{crudo[4:6]}-{crudo[6:]}"
        try:
            h.dias = max((date.today() - date.fromisoformat(h.fecha)).days, 1)
            h.vistas_dia = round(h.vistas / h.dias)
        except ValueError:
            pass
    if h.suscriptores:
        h.vistas_por_suscriptor = round(h.vistas / max(h.suscriptores, 1), 1)
    h.tags = (info.get("tags") or [])[:12]
    h.descripcion = (info.get("description") or "")[:400]
    return h


SISTEMA = (
    "Eres un analista de nichos de YouTube. Recibes una lista de videos que "
    "están funcionando y tienes que clasificarlos para un creador que hace "
    "canales faceless (sin mostrar la cara).\n\n"
    "Para CADA video devuelve:\n"
    '- "nicho": el nicho concreto en 2-4 palabras, en español.\n'
    f'- "nicho_rpm": EXACTAMENTE una de estas claves: {metricas.nichos_rpm()}\n'
    '- "faceless": "sí" si se puede hacer sin aparecer en cámara (voz en off, '
    'imágenes, animación, archivo), "no" si depende de una persona en pantalla '
    'o de su fama, "quizá" si no está claro.\n'
    '- "por_que": UNA frase de por qué este video funciona, mirando el título '
    "y el nicho. Concreta, nada de generalidades.\n\n"
    'Devuelve SOLO un JSON: {"videos": [{"id": "...", "nicho": "...", '
    '"nicho_rpm": "...", "faceless": "...", "por_que": "..."}]}\n'
    "Un objeto por cada video recibido, con su mismo id. Todo en español."
)


def _clasificar(hallazgos: list[Hallazgo]) -> None:
    """Una sola llamada a la IA para clasificar toda la tanda.

    Una llamada por video sería doce veces más caro y más lento, y la IA
    clasifica mejor viendo la lista junta: se le nota qué distingue a unos de
    otros.
    """
    if not hallazgos:
        return
    lineas = []
    for h in hallazgos:
        lineas.append(
            f"id={h.id} | {h.tipo} | {h.vistas} vistas | canal: {h.canal} | "
            f"título: {h.titulo} | tags: {', '.join(h.tags[:6])}"
        )
    try:
        datos = ia.pedir_json(SISTEMA, "\n".join(lineas), max_tokens=6000)
    except ia.IAError:
        return  # sin clasificación, pero con todos los números medidos

    por_id = {str(v.get("id")): v for v in (datos.get("videos") or [])}
    for h in hallazgos:
        v = por_id.get(h.id) or {}
        h.nicho = str(v.get("nicho", ""))
        h.nicho_rpm = str(v.get("nicho_rpm", "otro"))
        h.faceless = str(v.get("faceless", ""))
        h.por_que = str(v.get("por_que", ""))


#: Cómo se ordena la cuadrícula.
ORDENES = {
    "virales": ("vistas por día", lambda h: h.vistas_dia or 0),
    "vistas": ("vistas totales", lambda h: h.vistas),
    "despegue": ("vistas por suscriptor", lambda h: h.vistas_por_suscriptor or 0),
    "dinero": ("ingresos estimados", lambda h: (h.ingresos or {}).get("usd", [0, 0])[1]),
    "audiencia": ("suscriptores del canal", lambda h: h.suscriptores or 0),
}


def buscar(
    consulta: str,
    *,
    limite: int = POR_BUSQUEDA,
    orden: str = "virales",
    solo_faceless: bool = False,
    tipo: str = "todos",
    clasificar: bool = True,
    on_progress=None,
) -> list[Hallazgo]:
    """Busca videos del nicho y devuelve la cuadrícula ordenada."""
    if orden not in ORDENES:
        raise ValueError(f"Orden desconocido: '{orden}'. Opciones: {', '.join(ORDENES)}")

    def aviso(m: str) -> None:
        if on_progress:
            on_progress(m)

    aviso(f"Buscando «{consulta}» en YouTube")
    hallazgos = _flat(consulta, limite)
    if tipo in ("short", "largo"):
        hallazgos = [h for h in hallazgos if h.tipo == tipo]
    if not hallazgos:
        return []

    aviso(f"Midiendo {len(hallazgos)} resultados")
    with concurrent.futures.ThreadPoolExecutor(max_workers=HILOS) as pool:
        hallazgos = list(pool.map(_enriquecer, hallazgos))

    if clasificar:
        aviso("Clasificando el nicho de cada uno con IA")
        _clasificar(hallazgos)

    for h in hallazgos:
        h.ingresos = metricas.ingresos(h.vistas, h.nicho_rpm, h.tipo)

    if solo_faceless:
        hallazgos = [h for h in hallazgos if h.faceless in ("sí", "quizá", "")]

    hallazgos.sort(key=ORDENES[orden][1], reverse=True)
    return hallazgos


#: Catálogo de nichos que funcionan SIN dar la cara. La lista está escogida a
#: mano: son los que se pueden producir con voz en off e imágenes, que es lo
#: que hace esta app. Cada uno trae la búsqueda con la que se mide de verdad,
#: en vez de fiarse de una reputación.
#:
#: (nombre, búsqueda, clave de RPM, por qué / qué hace falta)
CATALOGO: list[tuple[str, str, str, str]] = [
    ("Finanzas personales", "finanzas personales como invertir", "finanzas",
     "El RPM más alto que existe. A cambio, la competencia es feroz y hay que ser riguroso."),
    ("Inversión y bolsa", "invertir en bolsa explicado", "finanzas",
     "Audiencia con dinero y anunciantes que pagan. Cuidado con parecer consejo financiero."),
    ("Negocios y casos reales", "caso de exito empresa documental", "negocios",
     "Historias de empresas: se narran con archivo y gráficos, sin aparecer."),
    ("Inteligencia artificial", "inteligencia artificial explicada", "tecnologia",
     "Tema caliente y RPM alto, pero caduca rápido: hay que publicar seguido."),
    ("Tecnología y gadgets", "como funciona tecnologia explicado", "tecnologia",
     "Explicadores con animación o metraje de stock."),
    ("Ciencia y espacio", "documental espacio universo", "educacion",
     "Muy visual, encaja con imágenes generadas y dura años viéndose."),
    ("Salud y nutrición", "alimentacion salud metabolica", "salud",
     "RPM bueno y búsqueda constante. Exige avisos y no prometer curas."),
    ("Fitness y hábitos", "rutina habitos disciplina", "salud",
     "Funciona en Shorts y en largo; mucha competencia."),
    ("Historia y documental", "documental historia antigua", "historia",
     "El clásico faceless: voz en off, imágenes y duración larga."),
    ("Crimen real", "caso criminal resuelto documental", "true_crime",
     "Retención altísima. Ojo con datos de personas reales."),
    ("Misterios sin resolver", "misterios sin resolver historia", "true_crime",
     "Curiosidad pura, fácil de encadenar en listas."),
    ("Terror y leyendas", "leyendas de terror narradas", "terror",
     "Muy barato de producir y muy adictivo; RPM bajo."),
    ("Curiosidades y top 10", "datos curiosos que no sabias", "curiosidades",
     "Entrada fácil, mucha competencia y RPM discreto."),
    ("Motivación y estoicismo", "estoicismo filosofia para la vida", "motivacion",
     "Audiencia fiel; el reto es no sonar a lo mismo de siempre."),
    ("Desarrollo personal", "habitos productividad explicado", "motivacion",
     "Se combina bien con finanzas y salud."),
    ("Psicología aplicada", "psicologia comportamiento humano", "educacion",
     "Explicadores que envejecen bien."),
    ("Geografía y mapas", "geografia paises mapas explicado", "educacion",
     "Datos + mapas: casi todo es imagen generada."),
    ("Animales y naturaleza", "documental animales naturaleza", "animales",
     "Muy visual, pero depende de metraje con licencia."),
    ("Mitología", "mitologia griega historias", "historia",
     "Historias conocidas, buen material para ilustrar."),
    ("Religión y espiritualidad", "historia biblica explicada", "religion",
     "Audiencia muy fiel; RPM bajo-medio."),
    ("Autos y motor", "historia de un auto legendario", "entretenimiento",
     "Nicho con anunciantes propios."),
    ("Videojuegos e historia", "historia de un videojuego documental", "gaming",
     "Se hace con metraje del juego; RPM bajo."),
    ("Cine y series", "analisis pelicula explicada", "entretenimiento",
     "Cuidado con el copyright del metraje."),
    ("Cocina y recetas", "receta facil paso a paso", "cocina",
     "Necesita grabar comida: es el menos faceless de la lista."),
    ("Viajes y lugares", "lugares increibles del mundo", "viajes",
     "Vive del metraje; con imágenes generadas queda raro."),
    ("Relatos para dormir", "historias para dormir relajante", "musica",
     "Videos larguísimos, producción mínima, RPM bajo pero muchas horas."),
]


def _medir_nicho(entrada: tuple[str, str, str, str], por_nicho: int) -> dict:
    """Mide un nicho del catálogo con una búsqueda rápida.

    Solo la búsqueda plana, sin entrar en cada video: así 26 nichos tardan
    segundos en vez de minutos. Lo que se busca aquí no es el detalle de un
    video, sino el pulso del nicho.
    """
    nombre, consulta, rpm, nota = entrada
    fila = {
        "nombre": nombre, "consulta": consulta, "rpm": rpm, "nota": nota,
        "vistas_mediana": None, "vistas_max": None, "ingresos": None,
        "rpm_rango": metricas.RPM_NICHO.get(rpm, metricas.RPM_NICHO["otro"])[:2],
        "ejemplo": "", "miniatura": "",
    }
    try:
        hallazgos = _flat(consulta, por_nicho)
    except Exception:  # noqa: BLE001 - un nicho que falle no tumba la tabla
        return fila
    if not hallazgos:
        return fila

    vistas = sorted(h.vistas for h in hallazgos)
    fila["vistas_mediana"] = vistas[len(vistas) // 2]
    mejor = max(hallazgos, key=lambda h: h.vistas)
    fila["vistas_max"] = mejor.vistas
    fila["ejemplo"] = mejor.titulo
    fila["miniatura"] = mejor.miniatura
    # Lo que dejaría un video TÍPICO del nicho, no el mejor: es lo que se puede
    # esperar de verdad al empezar.
    fila["ingresos"] = metricas.ingresos(fila["vistas_mediana"], rpm, "largo")
    return fila


def explorar(por_nicho: int = 6, on_progress=None) -> list[dict]:
    """Mide todo el catálogo y lo ordena por lo que deja un video típico."""
    if on_progress:
        on_progress(f"Midiendo {len(CATALOGO)} nichos en YouTube")

    with concurrent.futures.ThreadPoolExecutor(max_workers=HILOS) as pool:
        filas = list(pool.map(lambda e: _medir_nicho(e, por_nicho), CATALOGO))

    filas.sort(key=lambda f: (f["ingresos"] or {}).get("usd", [0, 0])[1], reverse=True)
    return filas


def resumen(hallazgos: list[Hallazgo]) -> dict:
    """Lo que se aprende mirando la tanda entera, no video a video."""
    if not hallazgos:
        return {"total": 0}
    nichos: dict[str, int] = {}
    for h in hallazgos:
        if h.nicho:
            nichos[h.nicho] = nichos.get(h.nicho, 0) + 1
    con_dia = [h.vistas_dia for h in hallazgos if h.vistas_dia]
    return {
        "total": len(hallazgos),
        "faceless": sum(1 for h in hallazgos if h.faceless == "sí"),
        "vistas_dia_mediana": sorted(con_dia)[len(con_dia) // 2] if con_dia else None,
        "nichos": sorted(nichos.items(), key=lambda x: -x[1])[:5],
        "mejor_pagado": max(
            (h for h in hallazgos if h.ingresos), default=None,
            key=lambda h: h.ingresos["usd"][1],
        ),
    }


def guardar(consulta: str, hallazgos: list[Hallazgo]) -> "Path":  # noqa: F821
    """Deja la búsqueda en estudio/nichos para poder volver a ella."""
    import re
    import unicodedata
    from pathlib import Path

    from .analizador import ESTUDIO_DIR

    slug = unicodedata.normalize("NFKD", consulta).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^\w\s-]", "", slug).strip().lower()
    slug = re.sub(r"[\s_-]+", "-", slug) or "busqueda"

    carpeta = ESTUDIO_DIR / "nichos"
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / f"{slug}.json"
    destino.write_text(
        json.dumps(
            {"consulta": consulta, "resultados": [h.to_dict() for h in hallazgos]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return destino
