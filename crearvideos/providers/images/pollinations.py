"""Imágenes por IA vía Pollinations.ai. Gratis, sin clave, sin tarjeta.

Es lo más cercano a "Gemini gratis": genera una imagen a medida del prompt, en
vertical 9:16, sin registrarse ni pagar. Usa modelos abiertos (Flux) detrás de
una URL: se pide la imagen y se descarga, sin API que administrar.

El trueque frente a Gemini/OpenAI de pago: es un servicio comunitario gratuito,
así que ni la disponibilidad ni la latencia están garantizadas — puede tardar o
caerse en horas punta. Por eso el código reintenta y, si aun así falla, deja un
mensaje claro para caer a `pexels` o `placeholder`.

A diferencia de Pexels (que busca una foto y solo quiere el sujeto), aquí sí se
manda el prompt completo con el estilo: cuanto más detalle, mejor la imagen.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from PIL import Image, ImageOps

from ...config import api_key
from .base import ImageProvider

# Se pide en la proporción nativa del video (ImageProvider.salida). El modelo
# suele devolver algo más pequeño pero con la misma proporción; el `fit` de
# _guardar lo lleva al tamaño exacto del montaje.

# Dos endpoints, dos mundos:
# - LEGACY: uso anónimo, sin token. Ignora el modelo y sirve SIEMPRE 'sana'
#   (básico). Es lo que ve quien no tiene token.
# - GATEWAY: el sistema nuevo con token (sk_...). Aquí sí se respeta el modelo
#   (gptimage, flux…), a cambio de gastar "pollen" del saldo de la cuenta. Los
#   precios son ínfimos (gptimage ~0.000006, flux ~0.0018 pollen por imagen) y
#   hay pollen gratis por "quests". El token se saca en https://enter.pollinations.ai
LEGACY_BASE = "https://image.pollinations.ai/prompt/"
GATEWAY_BASE = "https://gen.pollinations.ai/image/"
# Endpoint de edición (compatible con OpenAI): recibe una imagen de referencia
# por multipart y devuelve otra que la reinterpreta según el prompt. Es lo que
# permite mantener el mismo personaje/estilo en todas las escenas.
EDIT_ENDPOINT = "https://gen.pollinations.ai/v1/images/edits"

# Modelo de edición img2img. FLUX.1 Kontext conserva muy bien al personaje de la
# referencia mientras cambia la escena. Cuesta ~0.04 pollen/imagen (más que flux,
# pero sigue siendo poco). Necesita token.
EDIT_MODEL = "kontext"

# Modelo por defecto con token. FLUX.2 Klein es la generación siguiente a
# FLUX.1 Schnell ('flux'), que era el defecto anterior: schnell es un destilado
# de 4 pasos y por eso sus imágenes salen blandas y con poca adherencia al
# prompt — el motivo real de que estos shorts se vieran poco profesionales.
# Klein cuesta 0.005 pollen/imagen frente a 0.002, o sea 0.035 por un short de
# 7 escenas: la diferencia es irrelevante y el salto de calidad no lo es.
DEFAULT_MODEL = "flux-klein"

#: Catálogo de lo que tiene sentido elegir desde la interfaz, del más barato al
#: mejor: (etiqueta, pollen por imagen, cuántas imágenes de referencia acepta).
#: Un 0 en referencias significa que el modelo es solo texto→imagen y no puede
#: fijar un personaje; la interfaz lo usa para avisar antes de generar.
MODELS: dict[str, tuple[str, float, int]] = {
    "flux": ("FLUX.1 Schnell — rápido y blando", 0.002, 0),
    "flux-klein": ("FLUX.2 Klein — equilibrado (recomendado)", 0.005, 10),
    # El gateway solo acepta este con su prefijo de autor: 'phoenix-1.0' a secas
    # no es un alias registrado y devuelve 400.
    "MarcosFRG/phoenix-1.0": ("Leonardo Phoenix — muy detallado", 0.016, 0),
    "gpt-image-1.5": ("GPT Image 1.5 — máxima fidelidad al prompt", 0.031, 16),
}


#: Lado máximo de una referencia antes de subirla. Una foto de móvil de 12 MP
#: no mejora el parecido del personaje y sí engorda cada petición (y son una
#: por escena), así que se reduce antes de mandarla.
REF_MAX_SIDE = 1024


def _a_jpeg(ruta: Path) -> bytes:
    """Lee una referencia y la devuelve como JPEG, reducida si hace falta.

    El multipart declara `image/jpeg`; subir un PNG o un WEBP con esa etiqueta
    se gana un 400. Convertir aquí acepta cualquier formato que abra Pillow, que
    es lo que el usuario va a arrastrar desde el navegador.
    """
    with Image.open(ruta) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((REF_MAX_SIDE, REF_MAX_SIDE), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=90)
        return buf.getvalue()


def max_references(model: str) -> int:
    """Cuántas imágenes de referencia acepta un modelo. 0 = ninguna."""
    entrada = MODELS.get(model)
    return entrada[2] if entrada else 0

# Modelo de respaldo cuando el elegido rechaza un prompt por moderación (400).
# flux es permisivo, así que salva las escenas que gptimage no acepta.
FALLBACK_MODEL = "flux"


def model_options() -> list[dict]:
    """Lista [{key, label, pollen, refs}] para pintar el selector en la web."""
    return [
        {"key": k, "label": label, "pollen": pollen, "refs": refs}
        for k, (label, pollen, refs) in MODELS.items()
    ]


class InsufficientPollen(RuntimeError):
    """El gateway rechazó por saldo de pollen insuficiente (HTTP 402)."""


class ContentRejected(RuntimeError):
    """El modelo rechazó el prompt por moderación (HTTP 400)."""


class PollinationsImages(ImageProvider):
    name = "pollinations"
    cost_per_image = 0.0

    def __init__(
        self,
        model: str | None = None,
        token: str | None = None,
        reference: Path | str | None = None,
        references: list[Path | str] | None = None,
        free: bool = False,
    ) -> None:
        # Imágenes de referencia: si hay alguna, cada escena se genera a partir
        # de ellas, así el mismo personaje sale en todo el video. Sin ellas,
        # generación normal desde el texto. `reference` en singular se acepta
        # por compatibilidad con quien ya llamaba así.
        refs = list(references or ([reference] if reference else []))
        self.references = [Path(r) for r in refs if r]
        self.model = model or api_key("POLLINATIONS_MODEL") or DEFAULT_MODEL
        # El token manda al gateway nuevo, donde el modelo SÍ se respeta (gasta
        # pollen). Sin él, se cae al legacy anónimo: 'sana', gratis e ilimitado.
        # `free=True` fuerza ese camino gratis aunque haya token en el .env, para
        # probar sin gastar.
        self.token = None if free else (token or api_key("POLLINATIONS_TOKEN"))

    @property
    def has_token(self) -> bool:
        return bool(self.token)

    @property
    def edit_model(self) -> str:
        """Qué modelo hace la edición con referencia.

        El elegido, si acepta referencias. Si no (flux schnell, phoenix), se cae
        a kontext, que solo admite una: mejor eso que ignorar en silencio la
        imagen que el usuario subió para fijar al personaje.
        """
        return self.model if max_references(self.model) > 0 else EDIT_MODEL

    @property
    def usa_referencias(self) -> bool:
        """El modo referencia necesita token: va por el gateway nuevo."""
        return bool(self.references) and bool(self.token)

    @property
    def fingerprint_extra(self) -> str:
        # El modelo y las imágenes de referencia forman parte de cómo se generó
        # la imagen: si cambian, la caché debe regenerar.
        modo = self.edit_model if self.usa_referencias else self.model
        huellas = []
        if self.usa_referencias:
            for r in self.references:
                if r.is_file():
                    huellas.append(hashlib.sha256(r.read_bytes()).hexdigest()[:12])
        return f"{modo}:{'+'.join(huellas)}"

    def _url(
        self, prompt: str, index: int, model: str | None = None,
        tamaño: tuple[int, int] | None = None,
    ) -> str:
        # La semilla fija (el índice) hace que repetir el montaje dé la misma
        # imagen: el video es reproducible y no se regenera al reintentar.
        model = model or self.model
        # Por defecto, el formato del video (9:16, 16:9 o 1:1); la estética del
        # canal pide sus propios tamaños (miniatura, avatar).
        ancho, alto = tamaño or self.salida
        if self.token:
            # Gateway: el token va en la cabecera (ver _fetch); aquí el modelo
            # es obligatorio y no lleva 'nologo' (el gateway no marca de agua).
            base = GATEWAY_BASE
            params = {
                "model": model,
                "width": ancho,
                "height": alto,
                "seed": index,
            }
        else:
            base = LEGACY_BASE
            params = {
                "width": ancho,
                "height": alto,
                "seed": index,
                "model": model,
                "nologo": "true",
            }
        # El prompt va en la ruta; quote deja fuera '/' y demás para no romper la URL.
        return f"{base}{urllib.parse.quote(prompt, safe='')}?{urllib.parse.urlencode(params)}"

    def _fetch(self, url: str) -> bytes:
        headers = {"User-Agent": "CrearVideos/1.0"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                ctype = resp.headers.get("Content-Type", "")
                data = resp.read()
        except urllib.error.HTTPError as e:
            # 402: no hay pollen. No tiene sentido reintentar; se avisa claro.
            if e.code == 402:
                raise InsufficientPollen(
                    "Pollinations rechazó por saldo de pollen insuficiente. "
                    "Añade pollen o reclama 'quests' en https://enter.pollinations.ai "
                    f"(el modelo '{self.model}' cuesta una fracción ínfima por imagen). "
                    "O quita POLLINATIONS_TOKEN del .env para usar 'sana' gratis."
                ) from e
            # 400: el modelo censuró el prompt (típico de gptimage con terror).
            # Se marca aparte para reintentar con un modelo permisivo (flux).
            if e.code == 400:
                raise ContentRejected(f"modelo rechazó el prompt (400): {url}") from e
            raise
        if not ctype.startswith("image/"):
            # A veces devuelve texto (cola llena, prompt filtrado): no es imagen.
            raise RuntimeError(f"respuesta no-imagen ({ctype}): {data[:150]!r}")
        return data

    def _fetch_reintentando(self, prompt: str, index: int, model: str | None) -> bytes:
        """Pide la imagen con reintentos ante saturación (429/red). Propaga
        InsufficientPollen y ContentRejected para que los maneje quien llama."""
        url = self._url(prompt, index, model)
        ultimo: Exception | None = None
        for intento in range(4):
            try:
                return self._fetch(url)
            except (InsufficientPollen, ContentRejected):
                raise  # los maneja generate(); reintentar no ayudaría
            except (urllib.error.URLError, RuntimeError, TimeoutError) as e:
                ultimo = e
                if intento < 3:
                    time.sleep(3 * (intento + 1))  # 3s, 6s, 9s
        raise RuntimeError(
            f"Pollinations no entregó la imagen de la escena {index + 1} "
            f"tras varios intentos ({ultimo}).\nA veces se satura: espera un "
            f"momento y reintenta, o usa --imagenes pexels / placeholder."
        )

    def _tamaño_edicion(self, modelo: str) -> str:
        if modelo == EDIT_MODEL:
            return {"9:16": "1024x1536", "16:9": "1536x1024"}.get(self.relacion, "1024x1024")
        w, h = self.salida
        return f"{w}x{h}"

    def _edit(self, prompt: str) -> bytes:
        """Genera una escena a partir de las imágenes de referencia (img2img).

        Usa el endpoint compatible con OpenAI: se suben las referencias por
        multipart junto al prompt de la escena, y devuelve una imagen que
        conserva a los personajes pero recompone la escena. La respuesta trae la
        imagen en base64.

        El modelo elegido hace la edición si la soporta (flux-klein admite 10
        referencias, gpt-image 16). Antes esto iba siempre por kontext, que solo
        admite una y cuesta 6x más.
        """
        modelo = self.edit_model
        tope = max_references(modelo) or 1
        refs = [r for r in self.references if r.is_file()][:tope]

        boundary = f"----crearvideos{uuid.uuid4().hex}"
        campos = {
            "prompt": prompt,
            "model": modelo,
            # kontext solo traga 2:3 / 3:2 / 1:1 (pedirle 1080x1920 da 400); los
            # modelos que aceptan varias referencias sí dan la proporción del
            # video casi exacta, así que se les pide y se ahorra un recorte.
            "size": self._tamaño_edicion(modelo),
            "n": "1",
        }
        cuerpo = bytearray()
        for clave, valor in campos.items():
            cuerpo += f"--{boundary}\r\n".encode()
            cuerpo += f'Content-Disposition: form-data; name="{clave}"\r\n\r\n'.encode()
            cuerpo += f"{valor}\r\n".encode()
        # Una sola referencia va como 'image'; varias, como 'image[]'.
        campo = "image[]" if len(refs) > 1 else "image"
        for i, ref in enumerate(refs):
            cuerpo += f"--{boundary}\r\n".encode()
            cuerpo += (
                f'Content-Disposition: form-data; name="{campo}"; '
                f'filename="ref{i}.jpg"\r\n'
            ).encode()
            cuerpo += b"Content-Type: image/jpeg\r\n\r\n"
            cuerpo += _a_jpeg(ref) + b"\r\n"
        cuerpo += f"--{boundary}--\r\n".encode()

        req = urllib.request.Request(
            EDIT_ENDPOINT,
            data=bytes(cuerpo),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "CrearVideos/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 402:
                raise InsufficientPollen(
                    "Pollinations rechazó por saldo de pollen insuficiente. "
                    f"La edición con '{self.edit_model}' gasta pollen por imagen; "
                    "añade pollen o reclama 'quests' en https://enter.pollinations.ai."
                ) from e
            if e.code == 400:
                raise ContentRejected(f"{self.edit_model} rechazó el prompt (400)") from e
            raise
        return base64.b64decode(data["data"][0]["b64_json"])

    def _edit_reintentando(self, prompt: str, index: int) -> bytes:
        # Los modelos de edición rechazan al azar (400) por moderación. Un
        # reintento suele pasar, y los intentos fallidos NO gastan pollen (no
        # entregan imagen), así que se reintenta también ante ContentRejected.
        ultimo: Exception | None = None
        for intento in range(5):
            try:
                return self._edit(prompt)
            except InsufficientPollen:
                raise  # sin saldo no hay nada que hacer
            except (ContentRejected, urllib.error.URLError, RuntimeError,
                    TimeoutError, KeyError) as e:
                ultimo = e
                if intento < 4:
                    time.sleep(2 * (intento + 1))
        raise RuntimeError(
            f"Pollinations no editó la imagen de la escena {index + 1} con las "
            f"referencias tras varios intentos ({ultimo}). El modelo de edición "
            f"rechaza a veces prompts muy macabros; prueba un texto de escena más "
            f"suave, o genera sin --referencia."
        )

    def generate(
        self, prompt: str, out_path: Path, *, index: int = 0, subject: str = ""
    ) -> Path:
        # Modo referencia: cada escena se genera a partir de las imágenes base
        # para mantener al personaje. Requiere token (el gateway nuevo).
        if self.usa_referencias:
            if self.edit_model == EDIT_MODEL:
                # kontext censura con dureza: las palabras de estilo ("gore,
                # dread, eerie…") le disparan un 400. Se le manda el texto plano
                # de la escena y punto; el look lo pone la referencia.
                escena = subject.strip() or prompt
            else:
                # Los modelos que aceptan varias referencias sí tragan el prompt
                # dirigido entero, y eso es lo que se quiere: la referencia fija
                # la CARA del personaje y el prompt del director aporta la
                # biblia visual (época, paleta, prohibiciones). Antes había que
                # elegir una de las dos cosas.
                escena = prompt
            raw = self._edit_reintentando(escena, index)
            self._guardar(raw, out_path)
            return out_path

        try:
            raw = self._fetch_reintentando(prompt, index, self.model)
        except ContentRejected:
            # El modelo censuró el prompt (gptimage con terror, típico). Se
            # reintenta con flux, que es permisivo. Si ya era flux, no hay más.
            if not self.token or self.model == FALLBACK_MODEL:
                raise RuntimeError(
                    f"El modelo '{self.model}' rechazó el prompt de la escena "
                    f"{index + 1} por moderación. Prueba con otro texto de escena, "
                    f"o cambia POLLINATIONS_MODEL a un modelo más permisivo."
                )
            raw = self._fetch_reintentando(prompt, index, FALLBACK_MODEL)

        self._guardar(raw, out_path)
        return out_path

    def _guardar(self, raw: bytes, out_path: Path) -> None:
        """Recorta a la proporción del video y guarda como JPEG, sin inventar píxeles.

        Antes se ampliaba siempre a 1440x2560. Con el camino gratis, que
        devuelve 576x1024, eso era un upscale de 2.5x hecho aquí y otro
        remuestreo después en ffmpeg: dos interpolaciones encima de una imagen
        que ya venía pequeña, y de ahí el aspecto blando. Ahora solo se reduce
        si la imagen es mayor que el destino; si es menor se recorta a 9:16 y se
        deja en su tamaño nativo, y ffmpeg la escala una sola vez.
        """
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(io.BytesIO(raw)) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            destino = self.destino
            if img.width < destino[0] or img.height < destino[1]:
                # Misma proporción, a la mayor resolución que la imagen da de sí.
                alto = min(img.height, img.width * destino[1] // destino[0])
                destino = (alto * destino[0] // destino[1], alto)
            img = ImageOps.fit(img, destino, Image.LANCZOS)
            img.save(out_path, "JPEG", quality=92)
