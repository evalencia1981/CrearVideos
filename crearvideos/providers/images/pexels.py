"""Fotos de stock reales vía Pexels. Gratis, con una API key sin tarjeta.

Es el camino gratis recomendado. La API de Gemini no tiene tier gratis para
imágenes — ni Imagen 4 ni Nano Banana — así que "IA gratis" no existe hoy por
ese lado. Una foto real de stock se ve infinitamente mejor que un degradado.

El trueque frente a la IA: no se controla la imagen exacta, se busca la más
cercana. A cambio cuesta cero.

Licencia Pexels: uso libre, comercial incluido, sin atribución obligatoria.
Aun así se deja un `creditos.txt` junto a las imágenes: cuesta nada y es lo
correcto con el fotógrafo (y algunas plataformas sí la piden).
"""

from __future__ import annotations

import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

from ...config import api_key
from .base import ImageProvider

ENDPOINT = "https://api.pexels.com/v1/search"

# Palabras que no aportan nada como término de búsqueda. Sin esto, "de" y "la"
# se comen los cuatro huecos de la consulta y la foto sale de cualquier cosa.
STOPWORDS = {
    "a", "al", "algo", "algun", "alguna", "algunas", "alguno", "algunos", "ante",
    "aun", "aunque", "bajo", "bien", "cada", "como", "con", "contra", "cual",
    "cuando", "de", "del", "desde", "donde", "dos", "e", "el", "ella", "ellas",
    "ellos", "en", "entre", "era", "eran", "es", "esa", "esas", "ese", "eso",
    "esos", "esta", "estan", "estas", "este", "esto", "estos", "ha", "hacia",
    "han", "hasta", "hay", "la", "las", "le", "les", "lo", "los", "mas", "me",
    "mi", "mientras", "muy", "ni", "no", "nos", "o", "para", "pero", "por",
    "porque", "que", "se", "segun", "sin", "sobre", "solo", "son", "su", "sus",
    "tan", "te", "tiene", "todo", "todos", "tras", "tu", "un", "una", "unas",
    "uno", "unos", "y", "ya",
    # Locuciones que en español cargan preposición ("frente a", "dentro de").
    "dentro", "frente", "fuera", "lado", "medio", "traves",
    # Palabras de encuadre: describen la foto, no lo que sale en ella.
    "escena", "fondo", "foto", "fotografia", "imagen", "plano", "toma",
}

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)

# Cuántas palabras entran en la búsqueda. El buscador de stock hace AND, así
# que cada término extra estrecha el resultado hacia cero. Tres es el punto
# donde la consulta aún es específica pero suele devolver algo al primer
# intento: los adjetivos que vienen después ("inmenso", "diminuto") casi nunca
# existen como etiqueta de una foto y solo gastan reintentos.
MAX_TERMS = 3


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def _terms(subject: str) -> list[str]:
    """Saca las palabras con contenido del sujeto, en orden.

    Se corta en la primera coma: en "océano oscuro al atardecer, sin tierra a
    la vista" lo que importa es la primera cláusula. El resto son matices que
    un buscador de stock no entiende y que solo estrechan el resultado a cero.
    """
    head = subject.split(",")[0]
    words = _PUNCT_RE.sub(" ", _normalize(head)).split()
    return [w for w in words if w not in STOPWORDS and len(w) > 2]


def _big_enough(photo: dict, destino: tuple[int, int]) -> bool:
    return (
        photo.get("width", 0) >= destino[0]
        and photo.get("height", 0) >= destino[1]
    )


def _best(photos: list[dict], destino: tuple[int, int]) -> dict:
    """Elige la primera foto que tenga resolución de sobra.

    Pexels ya devuelve por relevancia, así que se respeta ese orden y solo se
    descartan las que habría que ampliar. Si ninguna llega, se coge la más
    grande: ampliar un poco se nota menos que perder la escena.
    """
    return next((p for p in photos if _big_enough(p, destino)), None) or max(
        photos, key=lambda p: p.get("width", 0) * p.get("height", 0)
    )


class PexelsImages(ImageProvider):
    name = "pexels"
    cost_per_image = 0.0

    def __init__(self) -> None:
        # Pexels devuelve las mismas fotos populares para consultas parecidas.
        # Sin memoria, dos escenas del mismo tema comparten imagen y el video
        # parece roto. Se recuerda lo ya usado para no repetir.
        self._used: set[int] = set()

    def _key(self) -> str:
        key = api_key("PEXELS_API_KEY")
        if not key:
            raise RuntimeError(
                "Falta PEXELS_API_KEY en .env para el proveedor de imágenes 'pexels'.\n"
                "Es gratis y no pide tarjeta: https://www.pexels.com/api/\n"
                "Mientras tanto puedes usar --imagenes placeholder."
            )
        return key

    def preflight(self) -> None:
        self._key()

    def _search(self, query: str) -> list[dict]:
        params = urllib.parse.urlencode(
            {
                "query": query,
                # La orientación del video: así llegan fotos que no hay que
                # recortar a la mitad.
                "orientation": {"9:16": "portrait", "16:9": "landscape"}.get(
                    self.relacion, "square"),
                # Sin filtro `size`: el 'large' de Pexels son 24 MP y aquí
                # sobran 3,7. Pedirlo tiraría casi todo el catálogo y dejaría
                # búsquedas vacías. El tamaño se juzga foto a foto en _find.
                "locale": "es-ES",          # el guion está en español
                "per_page": 30,             # margen para descartar repetidas
            }
        )
        # El User-Agent es obligatorio: sin él urllib manda 'Python-urllib/…' y
        # el Cloudflare de Pexels lo bloquea con un 403 (error 1010). Con
        # cualquier UA normal la API responde igual.
        req = urllib.request.Request(
            f"{ENDPOINT}?{params}",
            headers={"Authorization": self._key(), "User-Agent": "CrearVideos/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read()).get("photos") or []
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise RuntimeError(
                    "Pexels rechazó la PEXELS_API_KEY (401). Revisa que esté bien "
                    "copiada en .env."
                ) from e
            if e.code == 429:
                raise RuntimeError(
                    "Pexels cortó por límite de peticiones (429). El tope gratis es "
                    "200 por hora; espera un rato y reintenta."
                ) from e
            raise RuntimeError(f"Pexels respondió {e.code}: {e.read().decode()[:300]}") from e

    def _take(self, photo: dict) -> dict:
        """Marca la foto como usada y la devuelve.

        Reservar es parte de elegir: si el marcado viviera en quien llama,
        olvidarlo no rompería nada visiblemente — solo haría que dos escenas
        salieran con la misma foto, que es justo lo que esto evita.
        """
        self._used.add(photo["id"])
        return photo

    def _find(self, subject: str, index: int) -> dict:
        """Busca una foto sin usar, acortando la consulta hasta encontrar algo."""
        terms = _terms(subject)[:MAX_TERMS]
        if not terms:
            terms = ["abstracto"]

        # De específico a general: si "abismo submarino oscuro" no da nada, se
        # prueba "abismo submarino" y luego "abismo". Es preferible una foto
        # aproximada a reventar el video entero por una escena.
        for n in range(len(terms), 0, -1):
            photos = self._search(" ".join(terms[:n]))
            fresh = [p for p in photos if p["id"] not in self._used]
            if fresh:
                return self._take(_best(fresh, self.destino))
            # Todas repetidas: mejor repetir una foto que quedarse sin escena.
            if photos and n == 1:
                return self._take(_best(photos, self.destino))

        raise RuntimeError(
            f"Pexels no encontró ninguna foto para la escena {index + 1} "
            f"({subject[:60]!r}).\nPrueba con un [imagen: ...] más concreto y en "
            f"términos que existan como foto de stock."
        )

    def _download(self, url: str) -> bytes:
        # Las URLs de imagen de Pexels son públicas: no llevan la API key.
        req = urllib.request.Request(url, headers={"User-Agent": "CrearVideos/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read()

    def _credit(self, out_path: Path, photo: dict) -> None:
        line = (
            f"{out_path.name}: foto de {photo['photographer']} "
            f"({photo['photographer_url']}) en Pexels — {photo['url']}\n"
        )
        credits = out_path.parent / "creditos.txt"
        with credits.open("a", encoding="utf-8") as f:
            f.write(line)

    def generate(
        self, prompt: str, out_path: Path, *, index: int = 0, subject: str = ""
    ) -> Path:
        # Sin sujeto se cae al prompt, pero arrastra el estilo y busca peor.
        photo = self._find(subject or prompt, index)
        raw = self._download(photo["src"]["original"])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(".download")
        tmp.write_bytes(raw)

        # La foto viene en cualquier proporción y tamaño. `fit` recorta al
        # centro hasta la proporción exacta del video, sin deformar.
        with Image.open(tmp) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            img = ImageOps.fit(img, self.destino, Image.LANCZOS)
            img.save(out_path, "JPEG", quality=92)
        tmp.unlink(missing_ok=True)

        self._credit(out_path, photo)
        return out_path
