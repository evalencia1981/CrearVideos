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
    return genai.Client(api_key=clave, http_options=types.HttpOptions(timeout=HTTP_S * 1000))


def _motivo(e: Exception) -> str:
    """Mensaje corto de un error de la API, sin cabeceras ni la clave."""
    codigo = getattr(e, "code", None)
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


class GeminiOmni(VideoProvider):
    nombre = "gemini-omni"

    def estimar_costo(self, duracion: int, resolucion: str = "720p") -> float:
        # Google solo publica el equivalente por segundo a 720p; a otras
        # resoluciones se usa el mismo (1080p y 4k son reescalados).
        return round(self.modelo.resoluciones.get("720p", 0.10) * duracion, 4)

    def validar(self, duracion: int, resolucion: str) -> None:
        if resolucion not in ("360p", "720p", "1080p", "4k"):
            raise ErrorProveedor(f"omni-flash no admite {resolucion}")

    def _crear(self, cliente, entrada, aspecto, resolucion, duracion_api):
        formato = {"type": "video", "aspect_ratio": aspecto, "resolution": resolucion,
                   "delivery": "uri"}
        if duracion_api:
            formato["duration"] = duracion_api
        return cliente.interactions.create(
            model=self.modelo.id,
            input=entrada,
            response_format=formato,
            generation_config={"video_config": {"task": "image_to_video"}},
            background=True,
            timeout=HTTP_S,
        )

    def generar(self, imagen, prompt, duracion, aspecto, resolucion, destino) -> Generacion:
        self.validar(duracion, resolucion)
        cliente = _cliente()
        mime = mimetypes.guess_type(str(imagen))[0] or "image/jpeg"
        entrada = [
            {"type": "image", "data": base64.b64encode(Path(imagen).read_bytes()).decode(),
             "mime_type": mime},
            # La duración también va en el prompt: el campo no está documentado
            # y, si la API lo rechaza, es lo único que la pide.
            {"type": "text", "text": f"Use this image as the first frame. {duracion}-second clip. {prompt}"},
        ]

        # El formato del campo `duration` no está documentado. Se prueba "4s",
        # luego "4", y si no, sin el campo. Un 400 no genera nada ni se cobra.
        interaccion, avisos = None, []
        for duracion_api in (f"{duracion}s", str(duracion), None):
            _log(self.modelo.clave, f"enviando {Path(imagen).name} (duration={duracion_api!r})")
            try:
                interaccion = self._crear(cliente, entrada, aspecto, resolucion, duracion_api)
                break
            except Exception as e:  # noqa: BLE001
                if getattr(e, "code", None) == 400 or "400" in str(getattr(e, "status_code", "")):
                    avisos.append(f"duration={duracion_api!r} rechazado: {_motivo(e)}")
                    continue
                raise ErrorProveedor(_motivo(e)) from e
        if interaccion is None:
            raise ErrorProveedor("la API rechazó la petición: " + " | ".join(avisos))

        _anotar_envio(destino, self.modelo.clave, interaccion.id)
        _log(self.modelo.clave, f"aceptado ({interaccion.id}, {interaccion.status}); esperando")
        try:
            inicio = time.monotonic()
            while interaccion.status not in ("completed", "failed", "cancelled"):
                if time.monotonic() - inicio > TIEMPO_MAX_S:
                    raise ErrorProveedor(f"sin respuesta tras {TIEMPO_MAX_S // 60} min ({interaccion.id})")
                time.sleep(SONDEO_S)
                interaccion = cliente.interactions.get(interaccion.id, timeout=HTTP_S)
            _log(self.modelo.clave, f"{interaccion.status} en {time.monotonic() - inicio:.0f}s")
        except ErrorProveedor:
            raise
        except Exception as e:  # noqa: BLE001
            raise ErrorProveedor(_motivo(e)) from e

        if interaccion.status != "completed":
            errores = "; ".join(str(x) for x in (interaccion.errors or [])) or interaccion.status
            raise ErrorProveedor(f"la interacción terminó en {interaccion.status}: {errores}")

        video = interaccion.output_video
        if video is None:
            raise ErrorProveedor("la API no devolvió ningún video (¿filtro de seguridad?)")
        if video.data:
            destino.write_bytes(base64.b64decode(video.data))
        elif video.uri:
            self._descargar_uri(cliente, video.uri, destino)
        else:
            raise ErrorProveedor("el video no trae datos ni URI")

        costo, tokens = self._costo(interaccion.usage)
        return Generacion(
            mp4=destino,
            costo_real_usd=costo,
            costo_real_fuente="tokens informados por la API × precio publicado" if costo is not None
            else "la API no devolvió uso de tokens",
            extra={"interaccion": interaccion.id, "tokens": tokens,
                   **({"avisos_duracion": avisos} if avisos else {})},
        )

    @staticmethod
    def _descargar_uri(cliente, uri: str, destino: Path) -> None:
        nombre = uri.split("files/")[-1].split(":")[0].split("?")[0]
        inicio = time.monotonic()
        while True:
            archivo = cliente.files.get(name=f"files/{nombre}")
            estado = getattr(archivo.state, "name", str(archivo.state))
            if estado == "ACTIVE":
                break
            if estado == "FAILED":
                raise ErrorProveedor("el archivo del video falló al procesarse")
            if time.monotonic() - inicio > 300:
                raise ErrorProveedor("el archivo del video no quedó listo en 5 min")
            time.sleep(3)
        datos = cliente.files.download(file=uri)
        if datos:
            destino.write_bytes(datos)
        else:
            cliente.files.download(file=uri, destination=str(destino))

    @staticmethod
    def _costo(uso) -> tuple[float | None, dict]:
        if uso is None:
            return None, {}
        entrada = uso.total_input_tokens or 0
        salida = {}
        for m in uso.output_tokens_by_modality or []:
            salida[str(m.modality)] = salida.get(str(m.modality), 0) + (m.tokens or 0)
        if not salida and uso.total_output_tokens:
            salida = {"video": uso.total_output_tokens}
        if not entrada and not salida:
            return None, {}
        usd = entrada * config.OMNI_USD_M_ENTRADA / 1e6
        for modalidad, n in salida.items():
            usd += n * config.OMNI_USD_M_SALIDA.get(modalidad, config.OMNI_USD_M_SALIDA_OTRA) / 1e6
        return round(usd, 4), {"entrada": entrada, "salida": salida,
                               "total": uso.total_tokens}
