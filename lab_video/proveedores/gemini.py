"""Proveedores de la API de Gemini: Veo 3.1 (generate_videos) y Omni Flash
(Interactions). Los dos usan la imagen como primer fotograma."""

from __future__ import annotations

import base64
import mimetypes
import time
from pathlib import Path

from .. import config
from .base import ErrorProveedor, Generacion, VideoProvider

#: Cuánto se espera a un clip antes de darlo por perdido. Google documenta
#: hasta 6 minutos en hora punta; se deja margen.
TIEMPO_MAX_S = 15 * 60
SONDEO_S = 10
#: Tope de cada llamada HTTP. Sin él, una conexión colgada bloquea el hilo
#: para siempre y el tope de arriba nunca llega a comprobarse (pasó en la
#: primera prueba de Omni: 28 minutos sin salir de una llamada).
HTTP_S = 120


def _log(modelo: str, msg: str) -> None:
    print(f"    [{time.strftime('%H:%M:%S')}] {modelo} · {msg}", flush=True)


def _anotar_envio(destino: Path, modelo: str, ident: str) -> None:
    """Deja el id de la petición en disco nada más enviarla.

    Si algo se cuelga o se corta DESPUÉS de enviar, el video ya se está
    pagando: con este id se puede recuperar (Veo guarda 2 días).
    """
    with (destino.parent / "envios.log").open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{modelo}\t{destino.name}\t{ident}\n")


def _cliente():
    from google import genai
    from google.genai import types

    clave = config.clave_api()
    if not clave:
        raise ErrorProveedor(
            "Falta la clave de Gemini (GEMINI_API_KEY o LAB_VIDEO_GEMINI_API_KEY en .env)."
        )
    # Un solo intento: el cliente de Interactions reintenta los 429 por su
    # cuenta y en silencio, y con el proyecto en nivel gratuito eso parecía un
    # cuelgue (28 minutos sin salir de una llamada). Los reintentos que tengan
    # sentido los decide el laboratorio, a la vista.
    return genai.Client(
        api_key=clave,
        http_options=types.HttpOptions(
            timeout=HTTP_S * 1000, retry_options=types.HttpRetryOptions(attempts=1)
        ),
    )


def _codigo(e: Exception) -> int | None:
    """Código HTTP de un error, venga del cliente de Veo o del de Interactions."""
    for attr in ("code", "status_code"):
        valor = getattr(e, attr, None)
        if isinstance(valor, int):
            return valor
    return None


def _es_tier_gratis(e: Exception) -> bool:
    texto = str(getattr(e, "message", None) or e)
    return _codigo(e) == 429 and ("Free Tier" in texto or "limit: 0" in texto)


TIER_GRATIS = (
    "el proyecto de la clave está en el nivel GRATUITO de la API de Gemini, donde Veo y "
    "Omni tienen cuota 0 (Google responde 429 'limit: 0 … on Free Tier'). Activa la "
    "facturación del proyecto en https://aistudio.google.com/apikey (Plan de pago), o pon "
    "en LAB_VIDEO_GEMINI_API_KEY la clave de un proyecto que ya la tenga. No se cobró nada."
)


SIN_SALDO = (
    "el proyecto de la clave usa facturación de PREPAGO y no le queda saldo (Google "
    "responde 402 'prepayment credits are depleted'). Carga crédito en "
    "https://ai.studio/projects (Facturación del proyecto) y vuelve a lanzar. No se cobró nada."
)


def _motivo(e: Exception) -> str:
    """Mensaje corto de un error de la API, sin cabeceras ni la clave."""
    if _es_tier_gratis(e):
        return TIER_GRATIS
    if _codigo(e) == 402 and "prepay" in str(getattr(e, "message", None) or e).lower():
        return SIN_SALDO
    codigo = _codigo(e)
    texto = getattr(e, "message", None) or str(e)
    texto = " ".join(str(texto).split())[:400]
    return f"[{codigo}] {texto}" if codigo else texto


class GeminiVeo(VideoProvider):
    nombre = "gemini-veo"

    def generar(self, imagen, prompt, duracion, aspecto, resolucion, destino) -> Generacion:
        from google.genai import types

        self.validar(duracion, resolucion)
        cliente = _cliente()
        _log(self.modelo.clave, f"enviando {Path(imagen).name}")
        try:
            operacion = cliente.models.generate_videos(
                model=self.modelo.id,
                prompt=prompt,
                image=types.Image.from_file(location=str(imagen)),
                config=types.GenerateVideosConfig(
                    number_of_videos=1,
                    duration_seconds=duracion,
                    aspect_ratio=aspecto,
                    resolution=resolucion,
                    # Con imagen de entrada es el único valor que acepta.
                    person_generation="allow_adult",
                ),
            )
            _anotar_envio(destino, self.modelo.clave, operacion.name)
            _log(self.modelo.clave, f"aceptado ({operacion.name}); esperando")
            inicio = time.monotonic()
            while not operacion.done:
                if time.monotonic() - inicio > TIEMPO_MAX_S:
                    raise ErrorProveedor(f"sin respuesta tras {TIEMPO_MAX_S // 60} min ({operacion.name})")
                time.sleep(SONDEO_S)
                operacion = cliente.operations.get(operacion)
            _log(self.modelo.clave, f"listo en {time.monotonic() - inicio:.0f}s; descargando")
        except ErrorProveedor:
            raise
        except Exception as e:  # noqa: BLE001 - cualquier fallo de la API es un resultado
            raise ErrorProveedor(_motivo(e)) from e

        if operacion.error:
            raise ErrorProveedor(f"la operación falló: {operacion.error}")
        respuesta = operacion.response or operacion.result
        videos = (respuesta.generated_videos if respuesta else None) or []
        if not videos:
            razones = getattr(respuesta, "rai_media_filtered_reasons", None) if respuesta else None
            raise ErrorProveedor(
                "bloqueado por el filtro de seguridad: " + "; ".join(razones)
                if razones else "la API no devolvió ningún video"
            )

        datos = cliente.files.download(file=videos[0].video)
        if datos:
            destino.write_bytes(datos)
        else:
            videos[0].video.save(str(destino))
        return Generacion(
            mp4=destino,
            costo_real_fuente=(
                "la API de Veo no informa costo; Google cobra precio/s × segundos "
                "solo si el video se genera"
            ),
            extra={"operacion": operacion.name},
        )


class _ErrorHTTP(Exception):
    """Error de la API REST con su código, para que _codigo/_motivo lo lean."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


#: API REST de Interactions. Se llama directamente con httpx en vez del
#: cliente del SDK: en google-genai 2.31 ese cliente se quedaba colgado
#: (reintentos internos que no respetan ni timeout ni retry_options), mientras
#: la misma llamada en crudo responde en menos de un segundo.
INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"


def _rest(metodo: str, url: str, **kwargs) -> dict:
    import httpx

    clave = config.clave_api()
    if not clave:
        raise ErrorProveedor(
            "Falta la clave de Gemini (GEMINI_API_KEY o LAB_VIDEO_GEMINI_API_KEY en .env)."
        )
    try:
        r = httpx.request(metodo, url, headers={"x-goog-api-key": clave},
                          timeout=httpx.Timeout(HTTP_S, connect=20), **kwargs)
    except httpx.HTTPError as e:
        raise ErrorProveedor(f"sin conexión con la API: {type(e).__name__}") from e
    if r.status_code >= 400:
        try:
            cuerpo = r.json()
        except ValueError:
            cuerpo = None
        # Google devuelve el error como objeto o como lista de objetos.
        if isinstance(cuerpo, list) and cuerpo:
            cuerpo = cuerpo[0]
        error = cuerpo.get("error") if isinstance(cuerpo, dict) else None
        mensaje = (error.get("message") if isinstance(error, dict) else error) or r.text
        raise _ErrorHTTP(r.status_code, " ".join(str(mensaje).split())[:400])
    datos = r.json()
    if isinstance(datos, list):
        datos = datos[0] if datos else {}
    return datos


def _campo(d: dict, *nombres, defecto=None):
    """Lee un campo aceptando snake_case o camelCase."""
    for n in nombres:
        if n in d:
            return d[n]
    return defecto


class GeminiOmni(VideoProvider):
    nombre = "gemini-omni"

    def estimar_costo(self, duracion: int, resolucion: str = "720p") -> float:
        # Google solo publica el equivalente por segundo a 720p; a otras
        # resoluciones se usa el mismo (1080p y 4k son reescalados).
        return round(self.modelo.resoluciones.get("720p", 0.10) * duracion, 4)

    def validar(self, duracion: int, resolucion: str) -> None:
        if resolucion not in ("360p", "720p", "1080p", "4k"):
            raise ErrorProveedor(f"omni-flash no admite {resolucion}")

    def generar(self, imagen, prompt, duracion, aspecto, resolucion, destino) -> Generacion:
        self.validar(duracion, resolucion)
        mime = mimetypes.guess_type(str(imagen))[0] or "image/jpeg"
        entrada = [
            {"type": "image", "data": base64.b64encode(Path(imagen).read_bytes()).decode(),
             "mime_type": mime},
            # La duración también va en el prompt: el campo no está documentado
            # y, si la API lo rechaza, es lo único que la pide.
            {"type": "text", "text": f"Use this image as the first frame. {duracion}-second clip. {prompt}"},
        ]

        # El formato de `duration` no está documentado; "4s" fue aceptado en
        # la primera prueba. Si no, "4" y luego sin el campo. Un 400 no
        # genera nada ni se cobra.
        datos, avisos = None, []
        for duracion_api in (f"{duracion}s", str(duracion), None):
            formato = {"type": "video", "aspect_ratio": aspecto, "resolution": resolucion}
            if duracion_api:
                formato["duration"] = duracion_api
            _log(self.modelo.clave, f"enviando {Path(imagen).name} (duration={duracion_api!r})")
            try:
                datos = _rest("POST", INTERACTIONS_URL, json={
                    "model": self.modelo.id,
                    "input": entrada,
                    "response_format": formato,
                    "generation_config": {"video_config": {"task": "image_to_video"}},
                    "background": True,
                })
                break
            except _ErrorHTTP as e:
                if e.code == 400:
                    avisos.append(f"duration={duracion_api!r} rechazado: {e.message}")
                    continue
                raise ErrorProveedor(_motivo(e)) from e
        if datos is None:
            raise ErrorProveedor("la API rechazó la petición: " + " | ".join(avisos))

        ident = datos.get("id", "")
        _anotar_envio(destino, self.modelo.clave, ident)
        _log(self.modelo.clave, f"aceptado ({ident}, {datos.get('status')}); esperando")
        inicio = time.monotonic()
        while datos.get("status") not in ("completed", "failed", "cancelled"):
            if time.monotonic() - inicio > TIEMPO_MAX_S:
                raise ErrorProveedor(f"sin respuesta tras {TIEMPO_MAX_S // 60} min ({ident})")
            time.sleep(SONDEO_S)
            try:
                datos = _rest("GET", f"{INTERACTIONS_URL}/{ident}")
            except _ErrorHTTP as e:
                # Un 429 pasajero al consultar no es un fallo: se vuelve a
                # mirar en el siguiente sondeo. El de nivel gratuito sí lo es.
                if e.code == 429 and not _es_tier_gratis(e):
                    _log(self.modelo.clave, "429 al consultar; reintento en el siguiente sondeo")
                    continue
                raise ErrorProveedor(_motivo(e)) from e
        estado = datos.get("status")
        _log(self.modelo.clave, f"{estado} en {time.monotonic() - inicio:.0f}s")

        if estado != "completed":
            errores = "; ".join(str(x) for x in (datos.get("errors") or [])) or estado
            raise ErrorProveedor(f"la interacción terminó en {estado}: {errores}")

        video = self._video(datos)
        if video is None:
            raise ErrorProveedor("la API no devolvió ningún video (¿filtro de seguridad?)")
        if video.get("data"):
            destino.write_bytes(base64.b64decode(video["data"]))
        elif video.get("uri"):
            self._descargar(video["uri"], destino)
        else:
            raise ErrorProveedor("el video no trae datos ni URI")

        costo, tokens = self._costo(datos.get("usage") or {})
        return Generacion(
            mp4=destino,
            costo_real_usd=costo,
            costo_real_fuente="tokens informados por la API × precio publicado" if costo is not None
            else "la API no devolvió uso de tokens",
            extra={"interaccion": ident, "tokens": tokens,
                   **({"avisos_duracion": avisos} if avisos else {})},
        )

    @staticmethod
    def _video(datos: dict) -> dict | None:
        """El último bloque de video de la salida del modelo (como hace el SDK)."""
        for paso in reversed(datos.get("steps") or []):
            if paso.get("type") == "user_input":
                break
            if paso.get("type") != "model_output":
                continue
            for item in reversed(paso.get("content") or []):
                if item.get("type") == "video":
                    return item
        salida = _campo(datos, "output_video", "outputVideo")
        return salida if isinstance(salida, dict) else None

    @staticmethod
    def _descargar(uri: str, destino: Path) -> None:
        import httpx

        try:
            with httpx.stream("GET", uri, params={"alt": "media"},
                              headers={"x-goog-api-key": config.clave_api() or ""},
                              timeout=httpx.Timeout(HTTP_S, connect=20),
                              follow_redirects=True) as r:
                if r.status_code >= 400:
                    raise ErrorProveedor(f"no se pudo descargar el video [{r.status_code}]")
                with destino.open("wb") as f:
                    for trozo in r.iter_bytes():
                        f.write(trozo)
        except httpx.HTTPError as e:
            raise ErrorProveedor(f"fallo al descargar el video: {type(e).__name__}") from e

    @staticmethod
    def _costo(uso: dict) -> tuple[float | None, dict]:
        entrada = _campo(uso, "total_input_tokens", "totalInputTokens", defecto=0) or 0
        salida: dict[str, int] = {}
        for m in _campo(uso, "output_tokens_by_modality", "outputTokensByModality", defecto=[]) or []:
            modalidad = str(m.get("modality", "video")).lower()
            salida[modalidad] = salida.get(modalidad, 0) + int(m.get("tokens") or 0)
        total_salida = _campo(uso, "total_output_tokens", "totalOutputTokens")
        if not salida and total_salida:
            salida = {"video": int(total_salida)}
        if not entrada and not salida:
            return None, {}
        usd = entrada * config.OMNI_USD_M_ENTRADA / 1e6
        for modalidad, n in salida.items():
            usd += n * config.OMNI_USD_M_SALIDA.get(modalidad, config.OMNI_USD_M_SALIDA_OTRA) / 1e6
        return round(usd, 4), {"entrada": entrada, "salida": salida,
                               "total": _campo(uso, "total_tokens", "totalTokens")}
