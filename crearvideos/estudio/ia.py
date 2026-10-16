"""Llamada a la IA de texto para los análisis del estudio.

Va por el mismo gateway de Pollinations que el director del módulo 1, pero con
un modelo más capaz y con visión: tiene que leer una transcripción entera y
mirar la miniatura. gpt-5.4-mini cuesta ~0.015 pollen por análisis de un video
largo, nada al lado de una sola imagen.

Sin token cae al modelo anónimo gratis, que no ve imágenes y se satura seguido:
el análisis sale, pero peor, y se avisa.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error

from .. import director

MODELO = "openai/gpt-5.4-mini"
#: Modelo de respaldo cuando el principal rechaza el texto por moderación.
#: Los de OpenAI van por Azure, cuyo filtro corta guiones de terror, crimen
#: real o salud —justo los nichos que más funcionan sin cámara—. Este no pasa
#: por ahí, así que salva el análisis en vez de dejarlo en un error.
MODELO_PERMISIVO = "deepseek/deepseek-v4.1-flash"
#: Señales de que el fallo fue moderación y no un problema de verdad.
_MODERACION = ("content management policy", "content_filter", "was filtered", "responsible ai")


class IAError(RuntimeError):
    """La IA no devolvió un análisis utilizable."""


def modelo() -> str:
    if director._token():
        return os.getenv("ESTUDIO_MODEL") or MODELO
    return director.MODEL


def con_vision() -> bool:
    return bool(director._token())


def pedir_json(sistema: str, usuario: str, imagenes: list[str] | None = None,
               max_tokens: int = 8000) -> dict:
    """Manda el prompt y devuelve el objeto JSON de la respuesta.

    Si el modelo principal rechaza el texto por moderación, se reintenta una
    vez con uno permisivo: un guion de terror o de crimen real no es un error
    del usuario, y es contenido que se publica en YouTube todos los días.
    """
    try:
        return _pedir(sistema, usuario, imagenes, max_tokens, modelo())
    except IAError as e:
        if not any(s in str(e).lower() for s in _MODERACION) or not con_vision():
            raise
        return _pedir(sistema, usuario, imagenes, max_tokens, MODELO_PERMISIVO)


#: El modelo que respondió de verdad la última vez. Lo leen los informes: si
#: hubo que caer al permisivo, el archivo tiene que decirlo y no el nombre del
#: que se intentó primero.
ultimo_modelo: str = ""


def _pedir(sistema: str, usuario: str, imagenes: list[str] | None,
           max_tokens: int, modelo_usado: str) -> dict:
    global ultimo_modelo

    ultimo_modelo = modelo_usado
    contenido: str | list = usuario
    if imagenes and con_vision():
        contenido = [{"type": "text", "text": usuario}] + [
            {"type": "image_url", "image_url": {"url": u}} for u in imagenes
        ]

    cuerpo = {
        "model": modelo_usado,
        "reasoning_effort": "low",
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": sistema},
            {"role": "user", "content": contenido},
        ],
    }
    try:
        data = director._post(json.dumps(cuerpo).encode(), timeout=300)
    except urllib.error.HTTPError as e:
        detalle = ""
        try:
            detalle = e.read().decode("utf-8", "ignore")[:200]
        except OSError:
            pass
        raise IAError(f"la IA respondió {e.code}: {detalle or e.reason}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise IAError(f"no se pudo contactar a la IA: {e}") from e

    choice = (data.get("choices") or [{}])[0]
    texto = (choice.get("message") or {}).get("content") or ""
    m = re.search(r"\{.*\}", texto, re.DOTALL)
    if not m:
        if "budget" in texto.lower() or "balance" in texto.lower():
            raise IAError(
                "Pollinations agotó el saldo de pollen. Recárgalo o reclama quests en "
                "https://enter.pollinations.ai"
            )
        raise IAError(
            f"la IA no devolvió JSON (finish={choice.get('finish_reason')}): {texto[:120]!r}"
        )
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise IAError(f"la IA devolvió un JSON roto: {e}") from e
