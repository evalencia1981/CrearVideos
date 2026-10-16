"""Analizador de videos y canales: de una URL a un informe con su "ADN".

Qué hace con un VIDEO:
  metadatos + transcripción + comparación con los últimos videos de su canal
  (¿rindió 1× o 20× lo normal?) + la IA leyendo la transcripción y mirando la
  miniatura para explicar gancho, estructura, fórmula de título y por qué
  funcionó.

Qué hace con un CANAL:
  lista sus últimos videos y shorts, calcula cuáles son outliers, analiza a
  fondo los mejores y saca el ADN del canal: fórmulas de título, ganchos,
  estructura típica, patrón de miniaturas y qué separa a sus éxitos del resto.

Todo queda en estudio/ como JSON (para que lo usen el guionista y las
miniaturas después) y como Markdown (para leerlo).
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from ..config import PROJECT_ROOT
from . import ia, metricas, youtube

ESTUDIO_DIR = PROJECT_ROOT / "estudio"

#: Tope de transcripción que se le manda a la IA. Un guion largo de 35.000
#: caracteres entra entero; por encima se manda el principio (donde está el
#: gancho y la estructura) y el final (el cierre y la llamada a la acción).
MAX_TRANSCRIPCION = 60_000
MAX_TRANSCRIPCION_CANAL = 12_000  # por video, al analizar varios a la vez


def _slug(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    texto = re.sub(r"[^\w\s-]", "", texto).strip().lower()
    return re.sub(r"[\s_-]+", "-", texto) or "canal"


def _recortar(texto: str, maximo: int) -> str:
    if len(texto) <= maximo:
        return texto
    cabeza = int(maximo * 0.8)
    return texto[:cabeza] + "\n[… transcripción recortada …]\n" + texto[-(maximo - cabeza):]


def _texto_transcripcion(trozos: list[tuple[float, str]]) -> str:
    return "\n".join(f"[{youtube.mmss(t)}] {x}" for t, x in youtube.agrupar(trozos))


def _num(n: int | None) -> str:
    return f"{n:,}".replace(",", ".") if isinstance(n, int) else "—"


# --- prompts -----------------------------------------------------------------

_REGLAS = (
    "Reglas:\n"
    "- Escribe TODO en español, aunque el video esté en otro idioma.\n"
    "- Basa cada afirmación en evidencia: cita frases literales de la "
    "transcripción con su minuto [m:ss] o títulos reales.\n"
    "- No inventes números: las métricas ya vienen medidas. Si algo no se puede "
    "saber con lo que tienes, dilo en vez de suponerlo.\n"
    "- 'como_replicarlo' / 'como_competir' son PATRONES transferibles (estructura, "
    "tipo de gancho, empaque), nunca copiar frases, el tema exacto ni el guion: "
    "YouTube desmonetiza el contenido reutilizado.\n"
    "- Las 'ideas' son videos ORIGINALES para otro canal que aplican la misma "
    "fórmula con un tema o ángulo propio.\n"
    "- 'estilo_visual' describe el look para generar imágenes con IA en ese "
    "nicho (medio, iluminación, encuadre), no el video analizado plano por plano.\n"
    f"- 'nicho_rpm' debe ser EXACTAMENTE una de estas claves: {metricas.nichos_rpm()}\n"
    "- 'faceless' es 'sí' si nunca se ve la cara de un presentador, 'no' si sí, "
    "'parcial' si a ratos, 'incierto' si no hay forma de saberlo. Usa la "
    "miniatura y la transcripción como evidencia.\n"
    "Devuelve SOLO el objeto JSON, sin texto alrededor."
)

SISTEMA_VIDEO = (
    "Eres un estratega de contenido de YouTube que hace ingeniería inversa de "
    "videos exitosos para creadores de canales faceless. Recibes los datos "
    "medidos de un video, su transcripción con minutos y su miniatura. Explica "
    "por qué funcionó y cómo aplicar la fórmula en otro canal.\n\n"
    "Forma exacta del JSON:\n"
    "{\n"
    ' "resumen": "2-3 frases: de qué va y por qué rindió como rindió",\n'
    ' "nicho": "nicho concreto en pocas palabras",\n'
    ' "nicho_rpm": "clave",\n'
    ' "formato": {"faceless": "sí|no|parcial|incierto", "evidencia": "...", '
    '"produccion": "cómo está hecho: voz en off, stock, IA, animación, cámara…"},\n'
    ' "promesa_central": "qué se le promete al espectador para que se quede",\n'
    ' "gancho": {"cita": "texto literal de los primeros segundos", "tecnica": "...", '
    '"por_que_funciona": "..."},\n'
    ' "estructura": [{"minuto": "m:ss", "parte": "...", "funcion": "qué logra en la retención"}],\n'
    ' "retencion": ["técnica concreta + minuto donde aparece"],\n'
    ' "titulo": {"formula": "plantilla con [huecos]", "disparadores": ["curiosidad", "..."], '
    '"por_que_funciona": "..."},\n'
    ' "miniatura": {"descripcion": "...", "texto": "texto visible o vacío", "por_que_funciona": "..."},\n'
    ' "tono": "voz y ritmo de la narración",\n'
    ' "por_que_funciono": ["..."],\n'
    ' "debilidades": ["qué se podría hacer mejor"],\n'
    ' "como_replicarlo": ["..."],\n'
    ' "ideas": [{"titulo": "...", "gancho": "primera frase del video", "angulo": "..."}],\n'
    ' "estilo_visual": {"estilo": "...", "paleta": "...", "evitar": "..."},\n'
    ' "palabras_clave": ["para SEO"]\n'
    "}\n"
    "Da 5 ideas y entre 4 y 9 partes de estructura.\n\n" + _REGLAS
)

SISTEMA_CANAL = (
    "Eres un estratega de contenido de YouTube que hace ingeniería inversa de "
    "canales exitosos para creadores de canales faceless. Recibes los títulos de "
    "los últimos videos con sus vistas y cuántas veces superan la mediana del "
    "canal (el 'múltiplo'), y el detalle de sus outliers: transcripción y "
    "miniatura. Extrae el ADN del canal: lo que repite a propósito y lo que "
    "separa a sus éxitos de sus videos normales.\n\n"
    "Forma exacta del JSON:\n"
    "{\n"
    ' "resumen": "3-4 frases sobre el canal y su estrategia",\n'
    ' "nicho": "nicho concreto en pocas palabras",\n'
    ' "nicho_rpm": "clave",\n'
    ' "audiencia": "a quién le habla",\n'
    ' "formato": {"faceless": "sí|no|parcial|incierto", "evidencia": "...", "produccion": "..."},\n'
    ' "propuesta": "qué promete el canal a quien se suscribe",\n'
    ' "formulas_titulo": [{"formula": "plantilla con [huecos]", "ejemplo": "título real"}],\n'
    ' "ganchos": [{"patron": "...", "ejemplo": "cita literal con [m:ss]"}],\n'
    ' "estructura_tipica": ["paso 1…", "paso 2…"],\n'
    ' "tono": "...",\n'
    ' "miniaturas": {"patron": "...", "colores": "...", "texto": "cómo usan el texto"},\n'
    ' "por_que_funciona": ["..."],\n'
    ' "que_separa_outliers": "qué tienen los outliers que no tienen los videos normales, con ejemplos",\n'
    ' "debilidades": ["huecos que un competidor puede aprovechar"],\n'
    ' "como_competir": ["..."],\n'
    ' "ideas": [{"titulo": "...", "gancho": "primera frase del video", "angulo": "..."}],\n'
    ' "estilo_visual": {"estilo": "...", "paleta": "...", "evitar": "..."},\n'
    ' "palabras_clave": ["..."]\n'
    "}\n"
    "Da entre 3 y 6 fórmulas de título, entre 2 y 5 ganchos y 8 ideas.\n\n" + _REGLAS
)


# --- video -------------------------------------------------------------------


def _progreso(cb, msg: str) -> None:
    if cb:
        cb(msg)


def analizar_video(url: str, usar_ia: bool = True, on_progress=None) -> tuple[dict, Path]:
    """Analiza un video. Devuelve (informe, carpeta donde quedó guardado)."""
    _progreso(on_progress, "Leyendo el video")
    v, info = youtube.video(url)

    _progreso(on_progress, "Descargando la transcripción")
    trozos, idioma_subs = youtube.transcripcion(info)

    # Comparar con el canal es lo que convierte "1,2 M de vistas" en "14× lo
    # normal". Si el canal no se deja leer, el análisis sigue sin ese dato.
    _progreso(on_progress, "Comparando con los últimos videos del canal")
    multiplo, mediana_canal, n_canal = None, None, 0
    if v.canal_url:
        try:
            c = youtube.canal(v.canal_url, limite=30)
            mediana_canal = metricas.mediana(c.videos, v.tipo)
            n_canal = sum(1 for e in c.videos if e.tipo == v.tipo)
            if mediana_canal and v.vistas:
                multiplo = round(v.vistas / mediana_canal, 1)
        except youtube.YouTubeError:
            pass

    medidas = {
        "multiplo": multiplo,
        "veredicto": metricas.veredicto(multiplo),
        "mediana_canal": round(mediana_canal) if mediana_canal else None,
        "videos_comparados": n_canal,
        "vistas_por_dia": metricas.vistas_por_dia(v.vistas, v.fecha),
        "engagement_pct": metricas.engagement(v.likes, v.comentarios, v.vistas),
        "palabras_por_minuto": metricas.palabras_por_minuto(trozos, v.duracion),
        "transcripcion": bool(trozos),
        "idioma_transcripcion": idioma_subs,
    }

    carpeta = ESTUDIO_DIR / "videos" / v.id
    carpeta.mkdir(parents=True, exist_ok=True)
    transcripcion = _texto_transcripcion(trozos)
    if transcripcion:
        (carpeta / "transcripcion.txt").write_text(transcripcion, encoding="utf-8")
    youtube.descargar_miniatura(v.id, carpeta / "miniatura.jpg")

    informe = {
        "tipo": "video",
        "fecha_analisis": datetime.now().isoformat(timespec="seconds"),
        "video": v.to_dict(),
        "medidas": medidas,
        "analisis": None,
        "ingresos": None,
        "aviso": "",
    }

    if usar_ia:
        _progreso(on_progress, "Analizando con IA (lee la transcripción y mira la miniatura)")
        try:
            informe["analisis"] = ia.pedir_json(
                SISTEMA_VIDEO,
                _usuario_video(v, medidas, transcripcion),
                imagenes=[v.miniatura],
            )
            nicho = str(informe["analisis"].get("nicho_rpm", "otro"))
            informe["ingresos"] = metricas.ingresos(v.vistas, nicho, v.tipo)
            informe["modelo"] = ia.ultimo_modelo or ia.modelo()
        except ia.IAError as e:
            informe["aviso"] = f"Sin análisis de IA: {e}. Los datos medidos sí están."
        if not ia.con_vision():
            informe["aviso"] = (informe["aviso"] + " " if informe["aviso"] else "") + (
                "Sin POLLINATIONS_TOKEN la IA no ve la miniatura."
            )

    _guardar(carpeta, informe, markdown_video(informe))
    return informe, carpeta


def _usuario_video(v: youtube.Video, m: dict, transcripcion: str) -> str:
    lineas = [
        f"Título: {v.titulo}",
        f"Canal: {v.canal} ({_num(v.suscriptores)} suscriptores)",
        f"Tipo: {v.tipo} · duración {youtube.mmss(v.duracion or 0)} · publicado {v.fecha or '¿?'}",
        f"Vistas: {_num(v.vistas)} · likes {_num(v.likes)} · comentarios {_num(v.comentarios)}",
        f"Engagement: {m['engagement_pct']}% · vistas/día: {_num(m['vistas_por_dia'])}",
        f"Rendimiento vs canal: {m['multiplo'] or '¿?'}× la mediana de sus últimos "
        f"{m['videos_comparados']} {v.tipo}s ({m['veredicto']})",
        f"Palabras por minuto de la narración: {m['palabras_por_minuto'] or '¿?'}",
        f"Idioma: {v.idioma or '¿?'} · categorías: {', '.join(v.categorias) or '—'}",
        f"Tags: {', '.join(v.tags[:30]) or '—'}",
    ]
    if v.capitulos:
        lineas.append(
            "Capítulos: " + " | ".join(f"{youtube.mmss(c['inicio'])} {c['titulo']}" for c in v.capitulos)
        )
    lineas.append(f"\nDescripción:\n{v.descripcion[:2000] or '—'}")
    lineas.append(
        "\nTranscripción:\n"
        + (_recortar(transcripcion, MAX_TRANSCRIPCION) if transcripcion else
           "(no disponible: analiza con título, descripción y miniatura, y dilo)")
    )
    return "\n".join(lineas)


# --- canal -------------------------------------------------------------------


def analizar_canal(
    url: str, limite: int = 30, profundidad: int = 3, usar_ia: bool = True, on_progress=None
) -> tuple[dict, Path]:
    """Analiza un canal. `profundidad` = cuántos outliers se leen a fondo."""
    _progreso(on_progress, "Leyendo los últimos videos del canal")
    c = youtube.canal(url, limite=limite)
    tabla = metricas.outliers(c.videos)

    carpeta = ESTUDIO_DIR / "canales" / _slug(c.handle.lstrip("@") or c.nombre)
    (carpeta / "transcripciones").mkdir(parents=True, exist_ok=True)
    (carpeta / "miniaturas").mkdir(parents=True, exist_ok=True)

    detalle: list[dict] = []
    for i, fila in enumerate(tabla[:profundidad]):
        _progreso(on_progress, f"Leyendo a fondo el outlier {i + 1} de {min(profundidad, len(tabla))}")
        try:
            v, info = youtube.video(fila["id"])
        except youtube.YouTubeError:
            continue
        trozos, _ = youtube.transcripcion(info)
        texto = _texto_transcripcion(trozos)
        if texto:
            (carpeta / "transcripciones" / f"{v.id}.txt").write_text(texto, encoding="utf-8")
        youtube.descargar_miniatura(v.id, carpeta / "miniaturas" / f"{v.id}.jpg")
        detalle.append(
            {
                "video": v.to_dict(),
                "multiplo": fila["multiplo"],
                "vistas_por_dia": metricas.vistas_por_dia(v.vistas, v.fecha),
                "engagement_pct": metricas.engagement(v.likes, v.comentarios, v.vistas),
                "palabras_por_minuto": metricas.palabras_por_minuto(trozos, v.duracion),
                "_transcripcion": texto,
            }
        )

    medianas = {t: metricas.mediana(c.videos, t) for t in ("largo", "short")}
    informe = {
        "tipo": "canal",
        "fecha_analisis": datetime.now().isoformat(timespec="seconds"),
        "canal": {
            "nombre": c.nombre,
            "url": c.url,
            "handle": c.handle,
            "suscriptores": c.suscriptores,
            "descripcion": c.descripcion,
            "largos_leidos": sum(1 for e in c.videos if e.tipo == "largo"),
            "shorts_leidos": sum(1 for e in c.videos if e.tipo == "short"),
            "mediana_largos": round(medianas["largo"]) if medianas["largo"] else None,
            "mediana_shorts": round(medianas["short"]) if medianas["short"] else None,
        },
        "outliers": tabla,
        "a_fondo": [{k: val for k, val in d.items() if not k.startswith("_")} for d in detalle],
        "analisis": None,
        "ingresos": None,
        "aviso": "",
    }
    if not tabla:
        informe["aviso"] = (
            "El canal tiene menos de 5 videos de cada tipo: no hay base para saber qué es "
            "normal y qué es un outlier."
        )

    if usar_ia and (tabla or c.videos):
        _progreso(on_progress, "Extrayendo el ADN del canal con IA")
        try:
            informe["analisis"] = ia.pedir_json(
                SISTEMA_CANAL,
                _usuario_canal(c, tabla, detalle, medianas),
                imagenes=[d["video"]["miniatura"] for d in detalle],
                max_tokens=12000,
            )
            nicho = str(informe["analisis"].get("nicho_rpm", "otro"))
            informe["ingresos"] = {
                t: metricas.ingresos(round(medianas[t]), nicho, t)
                for t in ("largo", "short") if medianas[t]
            }
            informe["modelo"] = ia.ultimo_modelo or ia.modelo()
        except ia.IAError as e:
            informe["aviso"] = f"Sin análisis de IA: {e}. Los datos medidos sí están."

    _guardar(carpeta, informe, markdown_canal(informe))
    return informe, carpeta


def _usuario_canal(c: youtube.Canal, tabla: list[dict], detalle: list[dict], medianas: dict) -> str:
    lineas = [
        f"Canal: {c.nombre} ({c.handle}) · {_num(c.suscriptores)} suscriptores",
        f"Mediana de vistas: largos {_num(round(medianas['largo'])) if medianas['largo'] else '—'}"
        f" · shorts {_num(round(medianas['short'])) if medianas['short'] else '—'}",
        f"Descripción del canal: {c.descripcion[:800] or '—'}",
        "\nÚltimos videos (tipo | vistas | múltiplo | título):",
    ]
    por_id = {f["id"]: f for f in tabla}
    for e in c.videos:
        f = por_id.get(e.id)
        lineas.append(f"{e.tipo} | {_num(e.vistas)} | {f['multiplo'] if f else '—'}× | {e.titulo}")
    for i, d in enumerate(detalle, 1):
        v = d["video"]
        lineas += [
            f"\n=== Outlier {i}: {v['titulo']} ===",
            f"{v['tipo']} · {youtube.mmss(v['duracion'] or 0)} · {_num(v['vistas'])} vistas · "
            f"{d['multiplo']}× la mediana · publicado {v['fecha'] or '¿?'} · "
            f"{d['palabras_por_minuto'] or '¿?'} palabras/min · engagement {d['engagement_pct']}%",
            f"(miniatura: imagen {i} adjunta)",
            f"Descripción: {v['descripcion'][:600] or '—'}",
            "Transcripción:\n"
            + (_recortar(d["_transcripcion"], MAX_TRANSCRIPCION_CANAL) if d["_transcripcion"] else "(no disponible)"),
        ]
    return "\n".join(lineas)


# --- guardado y markdown -----------------------------------------------------


def _guardar(carpeta: Path, informe: dict, md: str) -> None:
    (carpeta / "informe.json").write_text(
        json.dumps(informe, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (carpeta / "informe.md").write_text(md, encoding="utf-8")


def cabecera_guion(analisis: dict | None) -> str:
    """La cabecera lista para pegar en un guion del módulo 1."""
    ev = (analisis or {}).get("estilo_visual") or {}
    lineas = [f"{k}: {ev[k]}" for k in ("estilo", "paleta", "evitar") if ev.get(k)]
    return "\n".join(lineas)


def _lista(items, fmt=lambda x: str(x)) -> str:
    return "\n".join(f"- {fmt(i)}" for i in (items or [])) or "- —"


def _bloque_ingresos(ing: dict | None, etiqueta: str) -> str:
    if not ing:
        return ""
    lo, hi = ing["usd"]
    rlo, rhi = ing["rpm_usd"]
    rango = "~$1 USD o menos" if hi <= 1 else f"${_num(lo)} – ${_num(hi)} USD"
    return f"- {etiqueta}: **{rango}** (RPM ${rlo}–${rhi}) — _estimación_"


def markdown_video(inf: dict) -> str:
    v, m, a = inf["video"], inf["medidas"], inf.get("analisis") or {}
    partes = [
        f"# {v['titulo']}",
        f"[{v['url']}]({v['url']}) · **{v['canal']}** ({_num(v['suscriptores'])} subs) · "
        f"{v['tipo']} · {youtube.mmss(v['duracion'] or 0)} · {v['fecha']}",
        "",
        "## Datos medidos",
        f"- Vistas: **{_num(v['vistas'])}** · likes {_num(v['likes'])} · comentarios {_num(v['comentarios'])}",
        f"- Rendimiento: **{m['multiplo'] or '¿?'}×** la mediana del canal "
        f"({_num(m['mediana_canal'])}, últimos {m['videos_comparados']}) — {m['veredicto']}",
        f"- Vistas por día: {_num(m['vistas_por_dia'])} · engagement {m['engagement_pct']}% · "
        f"{m['palabras_por_minuto'] or '¿?'} palabras/min",
    ]
    if inf.get("ingresos"):
        partes.append(_bloque_ingresos(inf["ingresos"], "Ingresos estimados del video"))
    if inf.get("aviso"):
        partes += ["", f"> {inf['aviso']}"]
    if not a:
        return "\n".join(partes) + "\n"

    fmt, g, t, mi = a.get("formato") or {}, a.get("gancho") or {}, a.get("titulo") or {}, a.get("miniatura") or {}
    partes += [
        "", "## Resumen", a.get("resumen", ""),
        f"\n**Nicho:** {a.get('nicho', '')} · **Faceless:** {fmt.get('faceless', '¿?')} — {fmt.get('evidencia', '')}",
        f"\n**Producción:** {fmt.get('produccion', '')}",
        f"\n**Promesa central:** {a.get('promesa_central', '')}",
        "", "## Gancho", f"> {g.get('cita', '')}", "",
        f"**Técnica:** {g.get('tecnica', '')}", "", g.get("por_que_funciona", ""),
        "", "## Estructura",
        "| Minuto | Parte | Función |", "|---|---|---|",
        *[f"| {p.get('minuto', '')} | {p.get('parte', '')} | {p.get('funcion', '')} |" for p in a.get("estructura") or []],
        "", "## Retención", _lista(a.get("retencion")),
        "", "## Título", f"**Fórmula:** `{t.get('formula', '')}`",
        f"\n**Disparadores:** {', '.join(t.get('disparadores') or [])}", "", t.get("por_que_funciona", ""),
        "", "## Miniatura", mi.get("descripcion", ""),
        f"\n**Texto:** {mi.get('texto') or '(sin texto)'}", "", mi.get("por_que_funciona", ""),
        "", "## Tono", a.get("tono", ""),
        "", "## Por qué funcionó", _lista(a.get("por_que_funciono")),
        "", "## Debilidades", _lista(a.get("debilidades")),
        "", "## Cómo replicarlo (sin copiar)", _lista(a.get("como_replicarlo")),
        "", "## Ideas de videos", _lista(a.get("ideas"), _idea),
        "", "## Palabras clave", ", ".join(a.get("palabras_clave") or []),
    ]
    partes += _seccion_cabecera(a)
    return "\n".join(partes) + "\n"


def markdown_canal(inf: dict) -> str:
    c, a = inf["canal"], inf.get("analisis") or {}
    partes = [
        f"# {c['nombre']} ({c['handle']})",
        f"[{c['url']}]({c['url']}) · **{_num(c['suscriptores'])} suscriptores**",
        "",
        "## Datos medidos",
        f"- Leídos: {c['largos_leidos']} largos, {c['shorts_leidos']} shorts",
        f"- Mediana de vistas: largos **{_num(c['mediana_largos'])}** · shorts **{_num(c['mediana_shorts'])}**",
    ]
    for tipo, ing in (inf.get("ingresos") or {}).items():
        partes.append(_bloque_ingresos(ing, f"Ingresos de un {tipo} típico"))
    if inf.get("aviso"):
        partes += ["", f"> {inf['aviso']}"]
    partes += [
        "", "## Outliers", "| Múltiplo | Tipo | Vistas | Título |", "|---|---|---|---|",
        *[f"| {f['multiplo']}× | {f['tipo']} | {_num(f['vistas'])} | [{f['titulo']}](https://youtu.be/{f['id']}) |"
          for f in inf["outliers"][:15]],
    ]
    if not a:
        return "\n".join(partes) + "\n"

    fmt, mi = a.get("formato") or {}, a.get("miniaturas") or {}
    partes += [
        "", "## Resumen", a.get("resumen", ""),
        f"\n**Nicho:** {a.get('nicho', '')} · **Audiencia:** {a.get('audiencia', '')}",
        f"\n**Faceless:** {fmt.get('faceless', '¿?')} — {fmt.get('evidencia', '')}",
        f"\n**Producción:** {fmt.get('produccion', '')}",
        f"\n**Propuesta:** {a.get('propuesta', '')}",
        "", "## Fórmulas de título",
        _lista(a.get("formulas_titulo"), lambda f: f"`{f.get('formula', '')}` — ej.: {f.get('ejemplo', '')}"),
        "", "## Ganchos",
        _lista(a.get("ganchos"), lambda g: f"**{g.get('patron', '')}** — {g.get('ejemplo', '')}"),
        "", "## Estructura típica", _lista(a.get("estructura_tipica")),
        "", "## Tono", a.get("tono", ""),
        "", "## Miniaturas", mi.get("patron", ""),
        f"\n**Colores:** {mi.get('colores', '')} · **Texto:** {mi.get('texto', '')}",
        "", "## Por qué funciona", _lista(a.get("por_que_funciona")),
        "", "## Qué separa a los outliers", a.get("que_separa_outliers", ""),
        "", "## Debilidades (huecos para competir)", _lista(a.get("debilidades")),
        "", "## Cómo competir", _lista(a.get("como_competir")),
        "", "## Ideas de videos", _lista(a.get("ideas"), _idea),
        "", "## Palabras clave", ", ".join(a.get("palabras_clave") or []),
    ]
    partes += _seccion_cabecera(a)
    return "\n".join(partes) + "\n"


def _idea(i: dict) -> str:
    return f"**{i.get('titulo', '')}** — {i.get('angulo', '')}\n  _Gancho:_ {i.get('gancho', '')}"


def _seccion_cabecera(a: dict) -> list[str]:
    cab = cabecera_guion(a)
    if not cab:
        return []
    return ["", "## Cabecera para el guion (módulo 1)", "```", cab, "---", "```"]
