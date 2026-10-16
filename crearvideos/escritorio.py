"""Arranque de la versión instalable: doble clic y se abre la interfaz.

Quien recibe el programa no escribe comandos: abre CrearVideos.exe, se levanta
el servidor local y se abre el navegador. Nada sale de su PC.

Detalles que parecen pequeños y son los que hacen que funcione en el equipo de
otra persona:

- **Puerto ocupado**: si el 8090 está tomado se prueba el siguiente, y la
  dirección real se escribe en pantalla, en el registro y en un acceso directo.
- **Primera vez**: se crean las carpetas de trabajo en Documentos\\CrearVideos y
  se deja un .env de ejemplo, para que las claves opcionales tengan dónde ir.
- **Registro de todo**: cuanto pasa por la consola queda además en
  `registro.log`. Si la ventana se cierra sola —o alguien la cierra sin
  querer—, ahí está el motivo. Es lo único que permite arreglar un fallo que
  ocurre en el PC de otro.
"""

from __future__ import annotations

import io
import os
import platform
import shutil
import socket
import sys
import traceback
from datetime import datetime
from pathlib import Path

from .config import PROJECT_ROOT, RESOURCE_ROOT

PUERTO_INICIAL = 8090
ARRANQUE = PROJECT_ROOT / "arranque.log"
REGISTRO = PROJECT_ROOT / "registro.log"
ACCESO = PROJECT_ROOT / "Abrir CrearVideos.url"
#: Si el registro pasa de esto, se empieza uno nuevo: nadie quiere un log de
#: 300 MB después de un mes usando el programa.
MAX_REGISTRO = 5 * 1024 * 1024

PLANTILLA_ENV = """# Claves opcionales de CrearVideos. Todo funciona sin ellas.
#
# Token de Pollinations: mejora mucho las imágenes y el director de escenas.
# Es gratis, se saca en https://enter.pollinations.ai
POLLINATIONS_TOKEN=

# Voz por defecto (mira las disponibles en la interfaz)
VOICE=es-MX-JorgeNeural
"""


class _Espejo(io.TextIOBase):
    """Escribe a la consola y al registro a la vez.

    Sin esto, todo lo que el programa cuenta mientras trabaja vive solo en la
    ventana negra y desaparece con ella. Con esto, cuando alguien reporta "se
    cerró", hay un archivo que dice en qué estaba.
    """

    def __init__(self, consola, destino: Path):
        self.consola = consola
        self.destino = destino

    def write(self, texto: str) -> int:
        if self.consola is not None:
            try:
                self.consola.write(texto)
                self.consola.flush()
            except Exception:  # noqa: BLE001 - consola cerrada o sin codec
                pass
        try:
            with self.destino.open("a", encoding="utf-8", errors="replace") as f:
                f.write(texto)
        except OSError:
            pass
        return len(texto)

    def flush(self) -> None:
        if self.consola is not None:
            try:
                self.consola.flush()
            except Exception:  # noqa: BLE001
                pass


def _rotar_registro() -> None:
    try:
        if REGISTRO.is_file() and REGISTRO.stat().st_size > MAX_REGISTRO:
            REGISTRO.replace(REGISTRO.with_suffix(".log.anterior"))
    except OSError:
        pass


def _anotar(mensaje: str, archivo: Path | None = None) -> None:
    try:
        with (archivo or ARRANQUE).open("a", encoding="utf-8", errors="replace") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {mensaje}\n")
    except OSError:
        pass


def _es_crearvideos(puerto: int) -> bool:
    """¿Lo que responde en este puerto es otro CrearVideos ya abierto?"""
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/api/ping", timeout=1) as r:
            return json.loads(r.read()).get("app") == "crearvideos"
    except Exception:  # noqa: BLE001 - cualquier otra cosa no es nuestro programa
        return False


def _ya_abierto(inicial: int = PUERTO_INICIAL, intentos: int = 20) -> int | None:
    """Puerto donde YA hay un CrearVideos corriendo, si lo hay.

    Abrir el programa dos veces era la trampa: la segunda copia se iba a otro
    puerto, y la pestaña que el usuario tenía abierta —o su marcador— quedaba
    apuntando a un servidor que ya no existía. Se veía como "no se puede
    acceder a este sitio" con el programa aparentemente abierto.
    """
    for puerto in range(inicial, inicial + intentos):
        if _es_crearvideos(puerto):
            return puerto
    return None


def _puerto_libre(inicial: int = PUERTO_INICIAL, intentos: int = 20) -> int:
    for puerto in range(inicial, inicial + intentos):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", puerto)) != 0:
                return puerto
    raise RuntimeError(
        f"No hay ningún puerto libre entre {inicial} y {inicial + intentos}."
    )


def diagnostico(puerto: int | None = None) -> str:
    """Todo lo que hace falta saber para entender un fallo en otro PC."""
    libre = "?"
    try:
        _, _, libre_bytes = shutil.disk_usage(PROJECT_ROOT)
        libre = f"{libre_bytes / 1024 / 1024 / 1024:.1f} GB"
    except OSError:
        pass

    ffmpeg_estado = "no encontrado"
    try:
        from .ffmpeg import _binary

        ffmpeg_estado = _binary("ffmpeg")
    except Exception as e:  # noqa: BLE001
        ffmpeg_estado = f"ERROR: {e}"

    escribible = "sí"
    try:
        prueba = PROJECT_ROOT / ".permiso"
        prueba.write_text("ok", encoding="utf-8")
        prueba.unlink()
    except OSError as e:
        escribible = f"NO ({e})"

    lineas = [
        f"CrearVideos · {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"Windows      {platform.platform()}",
        f"Python       {sys.version.split()[0]} · empaquetado: {getattr(sys, 'frozen', False)}",
        f"Programa     {sys.executable}",
        f"Recursos     {RESOURCE_ROOT}",
        f"Datos        {PROJECT_ROOT} · se puede escribir: {escribible}",
        f"Disco libre  {libre}",
        f"Temporal     {os.getenv('TEMP', '?')}",
        f"ffmpeg       {ffmpeg_estado}",
    ]
    if puerto:
        lineas.append(f"Puerto       {puerto}")
    return "\n".join(lineas)


def preparar_carpetas() -> None:
    """Crea la carpeta de trabajo la primera vez, con sus subcarpetas."""
    for sub in ("proyectos", "guiones", "estudio", "assets/musica/general"):
        (PROJECT_ROOT / sub).mkdir(parents=True, exist_ok=True)

    env = PROJECT_ROOT / ".env"
    if not env.exists():
        env.write_text(PLANTILLA_ENV, encoding="utf-8")

    ejemplo = PROJECT_ROOT / "guiones" / "ejemplo.txt"
    origen = RESOURCE_ROOT / "guiones" / "ejemplo.txt"
    if not ejemplo.exists() and origen.is_file():
        ejemplo.write_text(origen.read_text(encoding="utf-8"), encoding="utf-8")


def _acceso_directo(url: str) -> None:
    """Deja un acceso directo con la dirección REAL en la carpeta de datos.

    El puerto puede cambiar si el 8090 está ocupado. Un marcador viejo del
    navegador apuntaría al puerto equivocado y daría "no se puede acceder",
    que es exactamente el error que parece un programa roto sin estarlo.
    """
    try:
        ACCESO.write_text(f"[InternetShortcut]\nURL={url}\n", encoding="utf-8")
    except OSError:
        pass


def main() -> int:
    _rotar_registro()
    # Desde aquí, todo lo que se imprima queda también en registro.log.
    sys.stdout = _Espejo(sys.stdout, REGISTRO)
    sys.stderr = _Espejo(sys.stderr, REGISTRO)

    try:
        preparar_carpetas()

        abierto = _ya_abierto()
        if abierto is not None:
            url = f"http://127.0.0.1:{abierto}"
            _acceso_directo(url)
            print()
            print("=" * 64)
            print("  CrearVideos YA ESTABA ABIERTO")
            print("=" * 64)
            print(f"  Se usa la ventana que ya tenías: {url}")
            print("  (no hace falta abrirlo dos veces; esta ventana se cierra sola)")
            print("=" * 64)
            _anotar(f"Ya había una copia en el puerto {abierto}; no se abrió otra.")
            import webbrowser

            webbrowser.open(url)
            import time

            time.sleep(4)
            return 0

        puerto = _puerto_libre()
        url = f"http://127.0.0.1:{puerto}"
        _acceso_directo(url)

        print()
        print("=" * 64)
        print("  CrearVideos")
        print("=" * 64)
        print(f"  Interfaz:  {url}")
        if puerto != PUERTO_INICIAL:
            print(f"  (el puerto {PUERTO_INICIAL} estaba ocupado, se usó el {puerto};")
            print("   si tenías un marcador guardado, ya no sirve: usa esta dirección)")
        print(f"  Tus datos: {PROJECT_ROOT}")
        print()
        print("  NO CIERRES esta ventana mientras uses el programa:")
        print("  es el programa. Al cerrarla, la página deja de responder.")
        print("=" * 64)
        print()
        print(diagnostico(puerto))
        print()

        from .web.server import serve

        serve(port=puerto, abrir=True)
        print("\nCrearVideos se cerró con normalidad.")
        return 0
    except Exception as e:  # noqa: BLE001 - aquí muere todo o no arranca nada
        detalle = traceback.format_exc()
        _anotar("ERROR: " + detalle)
        print("\n" + "=" * 64)
        print("  No se pudo arrancar CrearVideos.")
        print("=" * 64)
        print(f"\nMotivo: {e}\n")
        print(detalle)
        print(f"\nEste detalle quedó guardado en:\n  {REGISTRO}")
        print("Mándale ese archivo a quien te pasó el programa.")
        try:
            input("\nPulsa Enter para cerrar…")
        except EOFError:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
