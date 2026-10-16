"""Paquete de publicación para YouTube: título, descripción, capítulos y SEO.

Lo que separa esto de pedírselo a un chat: **los capítulos son medidos**. El
proyecto ya tiene el segundo exacto en que empieza cada escena (lo fijó la
narración), así que la IA solo decide DÓNDE cortar los capítulos y cómo
llamarlos; los tiempos los pone Python. Un capítulo que no cuadra con el video
es peor que no tener capítulos.

Genera, para pegar tal cual en YouTube:

- 5 títulos para probar (A/B), con el porqué de cada uno
- la descripción completa, con capítulos, avisos y llamadas a la acción
- tags, hashtags y el comentario fijado con las fuentes
- texto e idea visual para la miniatura (eso es lo que mueve el CTR)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .config import OUTPUT_DIR
from .estudio import ia
from .importar import es_storyflow, parse_storyflow

#: YouTube exige al menos 3 capítulos, que el primero empiece en 00:00 y que
#: cada uno dure 10 segundos o más. Si no, no los muestra y no dice por qué.
MIN_CAPITULOS = 3
MIN_SEGUNDOS_CAPITULO = 10.0
#: Topes de la plataforma.
MAX_TITULO = 100
MAX_DESCRIPCION = 5000
MAX_TAGS_CARACTERES = 460  # el tope real es 500; se deja aire


class PublicarError(RuntimeError):
    """No hay datos suficientes para armar la publicación."""


def mmss(segundos: float) -> str:
    s = int(segundos)
    if s >= 3600:
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
    return f"{s // 60:02d}:{s % 60:02d}"


def cargar_proyecto(nombre_o_ruta: str | Path) -> dict:
    """Lee los tiempos ya medidos de un proyecto (montaje.json o escenas.json)."""
    raiz = Path(nombre_o_ruta)
    if not raiz.is_dir():
        raiz = OUTPUT_DIR / str(nombre_o_ruta)
    if not raiz.is_dir():
        raise PublicarError(
            f"No encuentro el proyecto '{nombre_o_ruta}'. Genera antes el audio "
            "(pestaña Audio) o el video (pestaña Montaje)."
        )

    for archivo in ("montaje.json", "escenas.json"):
        ruta = raiz / archivo
        if not ruta.is_file():
            continue
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        escenas = [
            {
                "n": e.get("n"),
                "inicio": float(e.get("inicio", 0.0)),
                "texto": e.get("narracion", ""),
                "pantalla": e.get("texto_pantalla", ""),
            }
            for e in datos.get("escenas", [])
        ]
        if escenas:
            total = max(e["inicio"] for e in escenas)
            ultima = datos["escenas"][-1]
            total = float(ultima.get("inicio", 0)) + float(ultima.get("duracion", 0))
            return {"raiz": raiz, "escenas": escenas, "duracion": total, "fuente": archivo}

    unidos = _desde_ensamblaje(raiz)
    if unidos:
        return unidos

    reconstruido = _reconstruir(raiz)
    if reconstruido:
        return reconstruido

    raise PublicarError(
        f"El proyecto {raiz.name} no tiene tiempos guardados ni audio por escena. "
        "Genera el audio o el video primero: de ahí salen los capítulos."
    )


def _desde_ensamblaje(raiz: Path) -> dict | None:
    """Línea de tiempo de un video hecho uniendo clips que ya traían voz.

    Estos no pasan por el guion, así que no hay texto que contar; pero la
    duración de cada segmento está medida y el solape de la transición ya
    está descontado, de modo que los minutos son exactos. El tema del video
    tendrá que ponerlo el usuario en el contexto: aquí no hay nada que leer.
    """
    ruta = raiz / "ensamblaje.json"
    if not ruta.is_file():
        return None
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    segmentos = datos.get("segmentos") or []
    if not segmentos:
        return None

    # Cada transición se come `duracion_transicion` segundos del clip anterior:
    # sin restarlo, los capítulos se irían corriendo hacia el final.
    solape = float(datos.get("duracion_transicion") or 0.0)
    escenas: list[dict] = []
    cursor = 0.0
    for i, seg in enumerate(segmentos):
        escenas.append(
            {
                "n": i + 1,
                "inicio": cursor,
                "duracion": float(seg.get("duracion", 0.0)),
                "texto": "",
                "pantalla": str(seg.get("archivo", "")),
            }
        )
        cursor += float(seg.get("duracion", 0.0)) - (solape if i < len(segmentos) - 1 else 0.0)

    return {
        "raiz": raiz,
        "escenas": escenas,
        "duracion": float(datos.get("duracion") or cursor),
        "fuente": "ensamblaje.json (clips unidos)",
        "sin_texto": True,
    }


#: Una línea de subtítulo del .ass, ya sin las etiquetas de color y escala.
_DIALOGO = re.compile(r"^Dialogue:\s*\d+,(\d+:\d\d:\d\d\.\d\d),", re.MULTILINE)
_ETIQUETAS = re.compile(r"\{[^}]*\}")


def _segundos(marca: str) -> float:
    h, m, s = marca.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _reconstruir(raiz: Path) -> dict | None:
    """Recupera la línea de tiempo de un proyecto viejo, sin archivo de tiempos.

    Los videos hechos antes de que se guardaran los tiempos conservan, aun así,
    todo lo necesario: `audio/escena_NNN.wav` da la duración EXACTA de cada
    escena, y `subtitulos.ass` guarda lo que se dice y en qué segundo. Con eso
    se rehace la tabla sin estimar nada.
    """
    audios = sorted((raiz / "audio").glob("escena_*.wav"))
    if not audios:
        return None

    from . import ffmpeg

    escenas: list[dict] = []
    cursor = 0.0
    for i, wav in enumerate(audios):
        try:
            dur = ffmpeg.duration(wav)
        except Exception:  # noqa: BLE001 - un wav roto no debe tumbar el resto
            continue
        escenas.append({"n": i + 1, "inicio": cursor, "duracion": dur, "texto": "", "pantalla": ""})
        cursor += dur
    if not escenas:
        return None

    # El texto sale de los subtítulos: cada evento repite el grupo de palabras
    # con una resaltada, así que se queda solo con los grupos distintos.
    ass = raiz / "subtitulos.ass"
    if ass.is_file():
        grupos: list[tuple[float, str]] = []
        for linea in ass.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            if not linea.startswith("Dialogue:"):
                continue
            partes = linea.split(",", 9)
            if len(partes) < 10:
                continue
            texto = " ".join(_ETIQUETAS.sub("", partes[9]).split())
            if texto and (not grupos or grupos[-1][1] != texto):
                grupos.append((_segundos(partes[1].strip()), texto))

        for escena in escenas:
            fin = escena["inicio"] + escena["duracion"]
            suyos = [t for s, t in grupos if escena["inicio"] <= s < fin]
            escena["texto"] = " ".join(suyos)

    return {
        "raiz": raiz,
        "escenas": escenas,
        "duracion": cursor,
        "fuente": "reconstruido del audio y los subtítulos",
    }


def desde_guion(texto: str) -> dict:
    """Sin proyecto: solo el guion. Habrá SEO, pero no capítulos con tiempos."""
    if es_storyflow(texto):
        bloques = parse_storyflow(texto)
        escenas = [
            {"n": i + 1, "inicio": None, "texto": b.narracion, "pantalla": b.pantalla}
            for i, b in enumerate(bloques)
        ]
    else:
        partes = [p.strip() for p in re.split(r"^\s*-{3,}\s*$", texto, flags=re.M) if p.strip()]
        escenas = [
            {"n": i + 1, "inicio": None, "texto": re.sub(r"\[imagen:[^\]]*\]", "", p).strip(), "pantalla": ""}
            for i, p in enumerate(partes)
        ]
    if not escenas:
        raise PublicarError("El guion está vacío.")
    return {"raiz": None, "escenas": escenas, "duracion": None, "fuente": "guion"}


SISTEMA = (
    "Eres un estratega de YouTube que prepara la publicación de un video ya "
    "terminado: el título, la descripción, los capítulos y el SEO. Tu objetivo "
    "es que el video se entienda en un segundo, dé clic (CTR) y retenga.\n\n"
    "Recibes el guion partido en escenas NUMERADAS. Si las escenas traen "
    "minuto, el video ya está montado y esos tiempos son reales.\n\n"
    "Forma exacta del JSON:\n"
    "{\n"
    ' "titulos": [{"titulo": "máx 100 caracteres", "tipo": "curiosidad|dato|negación|pregunta|lista", '
    '"por_que": "por qué daría clic"}],\n'
    ' "titulo_recomendado": "uno de los de arriba, copiado literal",\n'
    ' "gancho_descripcion": "2-3 frases que abren la descripción y repiten la promesa con '
    'las palabras que alguien buscaría",\n'
    ' "capitulos": [{"escena": 1, "titulo": "nombre del capítulo, corto y con gancho"}],\n'
    ' "cuerpo": "el resto de la descripción: qué se cubre, aviso si hace falta, '
    'llamadas a la acción. SIN los capítulos, que se insertan aparte",\n'
    ' "tags": ["15-25 etiquetas, de lo específico a lo general"],\n'
    ' "hashtags": ["#tres", "#como", "#máximo"],\n'
    ' "comentario_fijado": "el comentario que el autor fija: fuentes, resumen o pregunta",\n'
    ' "miniatura": {"texto": "3-5 PALABRAS EN MAYÚSCULAS", "idea_visual": "qué se ve", '
    '"alternativas": ["otro texto", "otro más"]},\n'
    ' "publico": "a quién le habla este video",\n'
    ' "palabras_clave": ["las que de verdad buscaría alguien"]\n'
    "}\n\n"
    "Reglas:\n"
    "- Escribe TODO en español neutro.\n"
    "- Da 5 títulos distintos entre sí: no cinco variantes de la misma frase. "
    "Ninguno de más de 100 caracteres. Nada de MAYÚSCULAS GRITADAS ni de "
    "promesas que el guion no cumple: un título que engaña destruye la "
    "retención y el canal.\n"
    "- Los capítulos se identifican por el NÚMERO DE ESCENA donde empiezan, "
    "nunca por un tiempo: los tiempos los pone el programa. El primero es "
    "SIEMPRE la escena 1. Da entre 5 y 9 capítulos, repartidos por el video, "
    "no todos al principio.\n"
    "- El 'cuerpo' se pega tal cual en YouTube: escribe saltos de línea reales, "
    "sin markdown, y puedes usar emojis como separadores de sección.\n"
    "- Si el tema es de salud, dinero o derecho, incluye en el cuerpo un aviso "
    "de que es contenido informativo y no sustituye a un profesional.\n"
    "- Basa todo en lo que dice el guion. No inventes estudios, cifras ni "
    "fuentes: si el guion cita datos, reutilízalos; si no, no los menciones.\n"
    "Devuelve SOLO el objeto JSON."
)


def _usuario(datos: dict, extra: str = "") -> str:
    lineas = []
    if datos["duracion"]:
        lineas.append(f"Duración real del video: {mmss(datos['duracion'])}")

    if datos.get("sin_texto"):
        # Video armado uniendo clips: no hay narración que leer. Se dan los
        # cortes para poder colocar capítulos, y se avisa de que todo lo que
        # se sepa del tema está en el contexto, para que no se invente nada.
        lineas.append(
            f"Este video se armó uniendo {len(datos['escenas'])} clips que ya traían "
            "su propia voz: NO hay guion transcrito. Guíate solo por el contexto de "
            "abajo. No inventes datos del contenido que no estén ahí.\n"
            "Cortes disponibles para los capítulos:"
        )
        for e in datos["escenas"]:
            lineas.append(f"{e['n']}. [{mmss(e['inicio'])}] clip {e['pantalla']} "
                          f"({e['duracion']:.0f} s)")
    else:
        lineas.append(f"{len(datos['escenas'])} escenas:")
        for e in datos["escenas"]:
            marca = f"[{mmss(e['inicio'])}] " if e["inicio"] is not None else ""
            pantalla = f"  (texto en pantalla: {e['pantalla']})" if e.get("pantalla") else ""
            lineas.append(f"{e['n']}. {marca}{e['texto']}{pantalla}")
    if extra.strip():
        lineas.append("\nContexto del canal (mándalo sobre el tono):\n" + extra.strip())
    return "\n".join(lineas)


def _capitulos(datos: dict, crudos: list[dict]) -> tuple[list[dict], str]:
    """Convierte 'capítulo en la escena N' en tiempos reales y válidos.

    Aquí es donde se gana la confianza: la IA elige dónde cortar, pero el
    minuto sale de la narración medida. Además se aplican las reglas de
    YouTube, que si no se cumplen hacen que no aparezca ningún capítulo.
    """
    por_escena = {e["n"]: e for e in datos["escenas"]}
    if datos["duracion"] is None:
        return [], (
            "Sin capítulos: este guion todavía no tiene audio generado, así que no "
            "hay tiempos reales. Genera el audio o el video y vuelve a pedirlo."
        )

    capitulos: list[dict] = []
    for c in crudos:
        escena = por_escena.get(int(c.get("escena", 0) or 0))
        titulo = str(c.get("titulo", "")).strip()
        if not escena or not titulo or escena["inicio"] is None:
            continue
        capitulos.append({"segundos": float(escena["inicio"]), "titulo": titulo})

    capitulos.sort(key=lambda c: c["segundos"])
    # El primero SIEMPRE en 00:00, aunque la IA haya elegido otra escena.
    if capitulos:
        capitulos[0]["segundos"] = 0.0
    # Fuera los que quedan a menos de 10 s del anterior.
    limpios: list[dict] = []
    for c in capitulos:
        if limpios and c["segundos"] - limpios[-1]["segundos"] < MIN_SEGUNDOS_CAPITULO:
            continue
        limpios.append(c)

    if len(limpios) < MIN_CAPITULOS:
        return [], (
            f"Sin capítulos: salieron {len(limpios)} y YouTube exige al menos "
            f"{MIN_CAPITULOS}, con el primero en 00:00 y 10 s entre uno y otro."
        )
    return [{"tiempo": mmss(c["segundos"]), "titulo": c["titulo"], "segundos": c["segundos"]} for c in limpios], ""


def _recortar_tags(tags: list[str]) -> list[str]:
    """YouTube corta las etiquetas a 500 caracteres contando las comas."""
    salida: list[str] = []
    total = 0
    for t in tags:
        t = " ".join(str(t).split())
        if not t:
            continue
        if total + len(t) + 1 > MAX_TAGS_CARACTERES:
            break
        salida.append(t)
        total += len(t) + 1
    return salida


def _descripcion(paquete: dict, capitulos: list[dict]) -> str:
    partes = [paquete.get("gancho_descripcion", "").strip()]
    if capitulos:
        partes.append(
            "⏱️ CAPÍTULOS\n"
            + "\n".join(f"{c['tiempo']} {c['titulo']}" for c in capitulos)
        )
    cuerpo = paquete.get("cuerpo", "").strip()
    if cuerpo:
        partes.append(cuerpo)
    hashtags = " ".join(paquete.get("hashtags") or [])
    if hashtags:
        partes.append(hashtags)
    texto = "\n\n".join(p for p in partes if p)
    return texto[:MAX_DESCRIPCION]


def generar(
    origen: str | Path,
    *,
    contexto: str = "",
    es_guion: bool = False,
    on_progress=None,
) -> dict:
    """Arma el paquete de publicación. `origen` es un proyecto o un guion."""
    if on_progress:
        on_progress("Leyendo los tiempos del proyecto")
    datos = desde_guion(str(origen)) if es_guion else cargar_proyecto(origen)

    # Sin guion y sin contexto no hay de dónde sacar el tema: la IA se lo
    # inventaría y el título no tendría nada que ver con el video.
    if datos.get("sin_texto") and not contexto.strip():
        raise PublicarError(
            f"«{datos['raiz'].name}» se armó uniendo clips que ya traían voz, así que "
            "no hay guion del que sacar el tema. Escribe en «Contexto del canal» de qué "
            "va el video (dos o tres frases bastan) y vuelve a darle."
        )

    if on_progress:
        on_progress("Escribiendo título, descripción y SEO con IA")
    paquete = ia.pedir_json(SISTEMA, _usuario(datos, contexto), max_tokens=9000)

    capitulos, aviso = _capitulos(datos, paquete.get("capitulos") or [])
    titulos = [
        {**t, "caracteres": len(str(t.get("titulo", "")))}
        for t in (paquete.get("titulos") or [])
        if str(t.get("titulo", "")).strip()
    ]
    largos = [t["titulo"] for t in titulos if t["caracteres"] > MAX_TITULO]

    resultado = {
        "titulos": titulos,
        "titulo_recomendado": paquete.get("titulo_recomendado", titulos[0]["titulo"] if titulos else ""),
        "descripcion": _descripcion(paquete, capitulos),
        "capitulos": capitulos,
        "tags": _recortar_tags(paquete.get("tags") or []),
        "hashtags": paquete.get("hashtags") or [],
        "comentario_fijado": paquete.get("comentario_fijado", ""),
        "miniatura": paquete.get("miniatura") or {},
        "publico": paquete.get("publico", ""),
        "palabras_clave": paquete.get("palabras_clave") or [],
        "duracion": datos["duracion"],
        "fuente": datos["fuente"],
        "aviso": aviso,
        "modelo": ia.ultimo_modelo or ia.modelo(),
    }
    if largos:
        resultado["aviso"] = (resultado["aviso"] + " " if resultado["aviso"] else "") + (
            f"{len(largos)} título(s) pasan de {MAX_TITULO} caracteres; YouTube los corta."
        )

    if datos["raiz"] is not None:
        (datos["raiz"] / "publicacion.json").write_text(
            json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (datos["raiz"] / "publicacion.md").write_text(markdown(resultado), encoding="utf-8")
        resultado["carpeta"] = str(datos["raiz"])
    return resultado


def markdown(p: dict) -> str:
    mini = p.get("miniatura") or {}
    lineas = [
        "# Publicación en YouTube",
        f"_Duración: {mmss(p['duracion']) if p.get('duracion') else '¿?'} · "
        f"público: {p.get('publico', '')}_",
        "",
        "## Títulos para probar",
        *[
            f"{i + 1}. **{t['titulo']}** ({t['caracteres']} car., {t.get('tipo', '')})\n"
            f"   _{t.get('por_que', '')}_"
            for i, t in enumerate(p.get("titulos") or [])
        ],
        "",
        f"**Recomendado:** {p.get('titulo_recomendado', '')}",
        "",
        "## Miniatura (lo que mueve el CTR)",
        f"- Texto: **{mini.get('texto', '')}**",
        f"- Imagen: {mini.get('idea_visual', '')}",
        f"- Alternativas: {', '.join(mini.get('alternativas') or []) or '—'}",
        "",
        "## Descripción (pegar tal cual)",
        "```",
        p.get("descripcion", ""),
        "```",
        "",
        "## Etiquetas",
        "```",
        ", ".join(p.get("tags") or []),
        "```",
        "",
        "## Comentario fijado",
        "```",
        p.get("comentario_fijado", ""),
        "```",
        "",
        "## Palabras clave",
        ", ".join(p.get("palabras_clave") or []),
    ]
    if p.get("aviso"):
        lineas += ["", f"> {p['aviso']}"]
    return "\n".join(lineas) + "\n"
