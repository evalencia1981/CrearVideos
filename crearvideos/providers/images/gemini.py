"""Imágenes vía Gemini Image ("Nano Banana"), la API de imagen de Google.

Reemplaza a Imagen 4 Fast, que Google retiró para cuentas nuevas (devuelve 404
`no longer available to new users`). El modelo actual se usa por otra ruta
—`generateContent`, no `:predict`— y devuelve la imagen incrustada en la
respuesta, no como una predicción aparte.

Cuesta ~$0.039 por imagen (la salida son ~1290 tokens a $30/millón). Un short de
8 escenas sale por ~$0.31. Sigue muy por debajo de la mensualidad de ClipShort,
pero ya no es tan barato como el Imagen 4 Fast que documentamos al inicio.

OJO: la generación de imágenes NO entra en el tier gratis de la API de Gemini.
Con una clave sin facturación activada, la API responde 429
(`generate_content_free_tier_requests`, cuota cero). Hay que habilitar
facturación en el proyecto de Google Cloud de la clave.
"""

from __future__ import annotations

import base64
import io
import json
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

from ...config import PLACEHOLDER_HEIGHT, PLACEHOLDER_WIDTH, api_key
from .base import ImageProvider

# Nano Banana estable. Alternativas que la clave suele exponer:
# gemini-3.1-flash-image (más nuevo) y gemini-3-pro-image (mejor y más caro).
MODEL = "gemini-2.5-flash-image"


def _endpoint(model: str) -> str:
    return (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )


class GeminiImages(ImageProvider):
    name = "gemini"
    cost_per_image = 0.039

    def __init__(self, model: str = MODEL) -> None:
        self.model = model

    def _key(self) -> str:
        key = api_key("GEMINI_API_KEY") or api_key("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError(
                "Falta GEMINI_API_KEY en .env para el proveedor de imágenes 'gemini'.\n"
                "Consíguela en https://aistudio.google.com/apikey — y recuerda que la\n"
                "generación de imágenes requiere facturación activada en el proyecto."
            )
        return key

    def preflight(self) -> None:
        self._key()

    def _request(self, body: dict) -> dict:
        req = urllib.request.Request(
            _endpoint(self.model),
            data=json.dumps(body).encode(),
            headers={"x-goog-api-key": self._key(), "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detalle = e.read().decode()
            if e.code == 429 and "free_tier" in detalle:
                raise RuntimeError(
                    "Gemini rechazó la petición: la clave no tiene facturación activada "
                    "(429).\nLa generación de imágenes no entra en el tier gratis. "
                    "Actívala en\nhttps://aistudio.google.com/ (Get API key, luego "
                    "Billing) o usa --imagenes pexels."
                ) from e
            raise RuntimeError(f"Gemini respondió {e.code}: {detalle[:400]}") from e

    def _extract(self, body: dict, index: int) -> bytes:
        """Saca los bytes de la imagen de la respuesta de generateContent."""
        for cand in body.get("candidates", []):
            for part in cand.get("content", {}).get("parts", []):
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    return base64.b64decode(inline["data"])
        # Sin imagen: casi siempre el filtro de contenido bloqueó el prompt.
        motivo = json.dumps(body.get("promptFeedback") or body)[:300]
        raise RuntimeError(
            f"Gemini no devolvió imagen para la escena {index + 1}. "
            f"Suele ser el filtro de contenido. Detalle: {motivo}"
        )

    def generate(
        self, prompt: str, out_path: Path, *, index: int = 0, subject: str = ""
    ) -> Path:
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                # Pedir 9:16 al modelo, cuando lo respeta, evita recortar y
                # perder parte de la escena. Si el modelo lo ignora, el `fit`
                # de abajo endereza el encuadre igual.
                "imageConfig": {"aspectRatio": "9:16"},
            },
        }
        try:
            data = self._request(body)
        except RuntimeError as e:
            # Modelos viejos no aceptan imageConfig y responden 400: se reintenta
            # sin él antes de rendirse.
            if "400" in str(e) and "imageConfig" in str(e):
                body["generationConfig"].pop("imageConfig", None)
                data = self._request(body)
            else:
                raise

        raw = self._extract(data, index)

        # La imagen puede venir en 1:1 o en otra proporción según el modelo.
        # `fit` la lleva al 9:16 exacto que espera el montaje, sin deformar.
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(io.BytesIO(raw)) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            img = ImageOps.fit(
                img, (PLACEHOLDER_WIDTH, PLACEHOLDER_HEIGHT), Image.LANCZOS
            )
            img.save(out_path, "JPEG", quality=92)
        return out_path
