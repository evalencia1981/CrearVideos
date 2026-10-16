"""Director de prompts: convierte cada escena en una descripción visual rica.

Es la pieza que separa un video con imágenes sueltas de uno que se ve pensado,
y lo que hace ClipShort por dentro. En vez de mandarle a la IA de imagen la
narración cruda ("La llaman la Madremonte"), una IA de TEXTO expande cada escena
en un prompt detallado en inglés y —lo importante— mantiene idénticos a los
personajes, criaturas y escenarios que se repiten entre escenas. Esa coherencia
es el problema número uno de este tipo de video.

Usa la API de texto de Pollinations: gratis, sin clave. Si falla, se cae al
prompt simple (narración + estilo) y el video se genera igual, solo con
imágenes menos dirigidas — nunca bloquea la creación.

El resultado se cachea por (escenas + estilo): rehacer el montaje no vuelve a
llamar a la IA, y así los prompts son estables y las imágenes se reutilizan.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT = "https://text.pollinations.ai/openai"
#: Endpoint con token. El anónimo va por cuota compartida por IP y hoy suele
#: responder "the API key has reached its budget" — un texto plano que no es
#: JSON y que el director interpretaba como "no hay prompts".
GATEWAY_ENDPOINT = "https://gen.pollinations.ai/v1/chat/completions"

# El único modelo del tier gratis anónimo. Es de razonamiento: si no se le baja
# el esfuerzo y se le da margen de tokens, gasta todo el presupuesto "pensando"
# y devuelve la respuesta vacía. Con reasoning_effort=low sí emite el JSON.
MODEL = "openai-fast"
#: Con token se usa un modelo de verdad. Dirigir un short entero son ~6000
#: tokens: a 0.00000015 pollen/token sale por ~0.001 pollen, o sea nada al lado
#: de lo que cuestan las imágenes. Vale la pena pagarlo para que no falle.
TOKEN_MODEL = "openai/gpt-5.4-nano"
# Sin User-Agent, Cloudflare corta con 403. Con uno de navegador, pasa.
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CrearVideos/1.0"
DEFAULT_STYLE = "cinematic film still, dramatic lighting, natural colors, 35mm"

#: Proveedores de imagen a los que tiene sentido dirigirles el prompt. Pexels
#: busca fotos por palabras clave —un prompt largo lo empeora— y placeholder no
#: mira el prompt, así que ambos quedan fuera.
GENERATIVE = ("pollinations", "gemini", "openai")

SYSTEM = (
    "You are a visual art director for faceless vertical short videos (9:16). "
    "You receive a video's narration split into ordered scenes, a visual style, "
    "and optionally a brief from the author. Your job has two parts.\n\n"
    "WRITE EVERYTHING IN ENGLISH, even when the narration and the brief are in "
    "another language. Image models follow English far better.\n\n"
    "PART 1 — the bible. Decide ONCE, for the whole video: the era/period and "
    "place, the world (terrain, climate, architecture, vegetation), the exact "
    "color palette, the lighting, and the camera/medium. Keep each of these to "
    "one short phrase, not a paragraph. Then define the recurring CAST: every "
    "person, creature or landmark that appears in more than one scene, at most "
    "five of them. Give each one an id and a physical description of AT MOST "
    "25 words covering age, build, skin, hair, one distinguishing mark, and "
    "clothing material. Be specific enough that two different illustrators "
    "would draw the same thing. Also list what must NEVER appear — "
    "anachronisms and objects that would break the period.\n\n"
    "PART 2 — the scenes. For EACH scene write ONE English image prompt "
    "(40-70 words) describing only what is HAPPENING in that exact moment: "
    "subject, action, framing, distance. Do NOT restate the palette, lighting "
    "or style — those are appended automatically. For each scene list the ids "
    "of AT MOST THREE cast members — only those who are actually the focus of "
    "that shot. Do not list the whole cast every time: their descriptions get "
    "injected verbatim and a prompt stuffed with six people produces mush.\n\n"
    "Everything must stay inside the era and the world you defined in PART 1. "
    "If the narration mentions a modern concept only as a contrast (for "
    "example 'before supermarkets existed'), still depict the PERIOD scene, "
    "never the modern object.\n\n"
    "Apply this visual style: {style}\n\n"
    "Return ONLY a JSON object, no prose, with exactly this shape:\n"
    '{{"bible": {{"era": "...", "world": "...", "palette": "...", '
    '"lighting": "...", "medium": "...", "avoid": "..."}}, '
    '"cast": [{{"id": "short_slug", "look": "dense physical description"}}], '
    '"scenes": [{{"cast": ["id", ...], "prompt": "..."}}]}}\n'
    "The scenes array must have exactly the same number of items as the input, "
    "in the same order."
)

class QuotaExhausted(RuntimeError):
    """La IA de texto agotó su cuota: no se pudo dirigir las imágenes."""


#: Cola que se pega a TODOS los prompts. Va en código, no en el prompt de la
#: IA, porque pedirle "no pongas texto" escena por escena falla una de cada
#: tantas; concatenarlo aquí no falla nunca.
TAIL = "vertical 9:16 composition, no text, no letters, no captions, no logo, no watermark"


def _hash(subjects: list[str], style: str | None, brief: str = "") -> str:
    h = hashlib.sha256()
    h.update(MODEL.encode())
    h.update((style or "").encode())
    h.update(b"\x01")
    h.update(brief.encode())
    for s in subjects:
        h.update(b"\x00")
        h.update(s.encode())
    return h.hexdigest()[:16]


def _token() -> str | None:
    from .config import api_key

    return api_key("POLLINATIONS_TOKEN")


def _post(body: bytes, tries: int = 3, timeout: int = 120) -> dict:
    """POST con reintentos: la API gratuita devuelve 502/503 a ratos."""
    token = _token()
    endpoint = GATEWAY_ENDPOINT if token else ENDPOINT
    headers = {"Content-Type": "application/json", "User-Agent": UA}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    ultimo: Exception | None = None
    for intento in range(tries):
        try:
            req = urllib.request.Request(endpoint, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            ultimo = e
            if e.code in (502, 503, 429) and intento < tries - 1:
                time.sleep(2 * (intento + 1))
                continue
            raise
    raise ultimo or RuntimeError("sin respuesta")  # pragma: no cover


#: Techos duros del prompt final. Van en código porque pedirlos en el prompt
#: del sistema no basta: el modelo obedece "máximo 25 palabras" casi siempre, y
#: ese "casi" es un prompt de 700 palabras donde la acción de la escena queda
#: sepultada bajo seis descripciones de personaje y el modelo de imagen dibuja
#: puré. Recortar aquí no falla nunca.
MAX_CAST_PER_SCENE = 3
MAX_CAST_WORDS = 28
MAX_ANCHOR_WORDS = 45
MAX_ACTION_WORDS = 75


def _clip(text: str, max_words: int) -> str:
    """Recorta a un número de palabras, cortando en la última coma si puede."""
    palabras = text.split()
    if len(palabras) <= max_words:
        return text.strip()
    corte = " ".join(palabras[:max_words])
    # Cortar en una coma deja una frase que se lee entera en vez de una a medias.
    coma = corte.rfind(",")
    return (corte[:coma] if coma > len(corte) // 2 else corte).strip(" ,.")


def _anchor(bible: dict) -> str:
    """El bloque de mundo que se repite IDÉNTICO en todas las escenas.

    Este es el mecanismo real de coherencia. La versión anterior le pedía al
    modelo "mantén todo igual" y el modelo derivaba igual ("mujer con ropa
    humilde" -> "silueta con un mechón despeinado"). Aquí el texto se concatena
    en Python, así que es literalmente el mismo string en las 7 escenas y no
    hay margen de deriva.
    """
    campos = ("era", "world", "palette", "lighting", "medium")
    partes = [str(bible.get(k, "")).strip() for k in campos]
    anchor = _clip(", ".join(p for p in partes if p), MAX_ANCHOR_WORDS)
    evitar = _clip(str(bible.get("avoid", "")).strip(), 18)
    if evitar:
        anchor += f". Must not appear: {evitar}"
    return anchor


def _compose(bible: dict, cast: list[dict], escenas: list[dict], total: int) -> list[str]:
    """Une acción de escena + descripción literal del reparto + ancla + cola."""
    looks = {
        str(c.get("id", "")).strip(): _clip(str(c.get("look", "")).strip(), MAX_CAST_WORDS)
        for c in cast
        if isinstance(c, dict)
    }
    anchor = _anchor(bible)

    prompts: list[str] = []
    for esc in escenas[:total]:
        accion = _clip(str(esc.get("prompt", "")).strip(), MAX_ACTION_WORDS)
        if not accion:
            prompts.append("")
            continue
        # Solo el reparto que de verdad sale en esta escena, y con su
        # descripción tal cual la definió la biblia: sin reescribirla. El tope
        # evita que meter a todo el elenco entierre la acción de la escena.
        presentes = [
            looks[i] for i in (esc.get("cast") or []) if isinstance(i, str) and looks.get(i)
        ][:MAX_CAST_PER_SCENE]
        trozos = [accion]
        if presentes:
            trozos.append("Consistent characters: " + "; ".join(presentes))
        if anchor:
            trozos.append(anchor)
        trozos.append(TAIL)
        prompts.append(". ".join(t.rstrip(". ") for t in trozos) + ".")
    return prompts


def _call(subjects: list[str], style: str | None, brief: str = "") -> list[str]:
    user = ""
    if brief.strip():
        # El brief del autor manda sobre lo que la IA deduzca por su cuenta.
        user += "Author's brief (authoritative, follow it exactly):\n" + brief.strip() + "\n\n"
    user += "Scenes:\n" + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(subjects))
    body = json.dumps(
        {
            "model": TOKEN_MODEL if _token() else MODEL,
            "seed": 1,  # estable: mismo guion -> mismos prompts
            # El modelo razona; sin esto se queda sin tokens antes de responder.
            "reasoning_effort": "low",
            "max_tokens": min(16000, 4000 + 900 * len(subjects)),
            "messages": [
                {"role": "system", "content": SYSTEM.format(style=style or DEFAULT_STYLE)},
                {"role": "user", "content": user},
            ],
        }
    ).encode()

    data = _post(body)
    choice = (data.get("choices") or [{}])[0]
    txt = (choice.get("message") or {}).get("content")
    if not txt:
        # El modelo gastó el presupuesto razonando y no emitió respuesta.
        raise ValueError(f"la IA no devolvió texto (finish={choice.get('finish_reason')})")
    # El modelo a veces envuelve el objeto en ```json ... ```; se extrae el {...}.
    m = re.search(r"\{.*\}", txt, re.DOTALL)
    if not m:
        # Pollinations responde 200 con un texto explicativo cuando la cuota se
        # agotó. Sin este caso, el director lo leía como "no hay prompts" y caía
        # al prompt simple sin decir nada — y el video salía incoherente sin que
        # nadie supiera por qué.
        if "budget" in txt.lower() or "balance" in txt.lower():
            raise QuotaExhausted(
                "La IA de texto de Pollinations agotó su cuota, así que el "
                "director no pudo dirigir las imágenes. Añade pollen o reclama "
                "quests en https://enter.pollinations.ai"
            )
        raise ValueError(f"la IA de texto no devolvió un JSON de prompts: {txt[:120]!r}")
    data = json.loads(m.group(0))
    if not isinstance(data, dict) or not isinstance(data.get("scenes"), list):
        raise ValueError("formato inesperado del director")
    return _compose(
        data.get("bible") or {},
        data.get("cast") or [],
        data["scenes"],
        len(subjects),
    )


def enrich(
    scenes, style: str | None, cache_path: Path, on_progress=None, brief: str = ""
) -> bool:
    """Rellena scene.image_prompt con un prompt dirigido y coherente.

    `brief` es la cabecera del guion (época, protagonistas, paleta, evitar)
    ya formateada. Si viene, manda sobre lo que la IA deduzca por su cuenta.

    Devuelve True si se aplicó el director, False si se usó el prompt simple.
    """
    subjects = [(s.image_subject or s.text) for s in scenes]
    clave = _hash(subjects, style, brief)

    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    except (json.JSONDecodeError, OSError):
        cache = {}

    guardados = cache.get("prompts", [])
    if cache.get("clave") == clave and len(guardados) >= len(scenes):
        for scene, p in zip(scenes, guardados):
            scene.image_prompt = p
        return True

    if on_progress:
        on_progress("Dirigiendo las imágenes con IA")
    # Un fallo aquí no bloquea el video, pero SÍ se avisa. Callarlo fue el peor
    # error de la versión anterior: las imágenes salían de la narración cruda
    # ("antes de los supermercados" -> un supermercado) y nada lo delataba.
    try:
        prompts = _call(subjects, style, brief)
    except QuotaExhausted as e:
        if on_progress:
            on_progress(f"Sin director: {e}")
        return False
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as e:
        if on_progress:
            on_progress(f"Sin director (la IA de texto falló: {e}). Imágenes menos dirigidas.")
        return False
    if len(prompts) < len(scenes):
        # Respuesta incompleta: mejor todo simple que media coherencia.
        if on_progress:
            on_progress("Sin director: la IA devolvió menos prompts que escenas.")
        return False

    prompts = prompts[: len(scenes)]
    for scene, p in zip(scenes, prompts):
        if p:
            scene.image_prompt = p
    try:
        cache_path.write_text(
            json.dumps({"clave": clave, "prompts": prompts}), encoding="utf-8"
        )
    except OSError:
        pass
    return True
