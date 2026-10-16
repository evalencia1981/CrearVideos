"""Voz clonada: narra el guion con TU voz, a partir de una muestra.

Le das 10-30 segundos de tu voz grabada y genera toda la narración imitándola.
Por dentro usa Chatterbox (Resemble AI, licencia MIT: se puede monetizar) en un
entorno aparte, `.venv-voz`, porque arrastra PyTorch y varios gigas de modelo.

Cómo encaja con el resto:

- Se genera **una escena por archivo**, así que la duración de cada escena se
  mide del audio real, igual que con edge-tts. La imagen nunca se despega.
- Lo que no da el modelo son los tiempos por palabra. Se reparten dentro de
  cada escena según el largo de cada palabra ([[vozpropia]]), así que el
  subtítulo entra y sale en su sitio y solo el resaltado es aproximado.
- El modelo se carga una sola vez para todo el video: cargarlo tarda más que
  generar una escena corta.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from . import pausas, vozpropia
from .config import PROJECT_ROOT, RESOURCE_ROOT, Settings
from .models import Project

#: El entorno de voz clonada se instala en la carpeta de datos del usuario: son
#: 5 GB que no tiene sentido meter dentro del programa ni pedirle a quien no
#: vaya a clonar voces.
PYTHON_VOZ = PROJECT_ROOT / ".venv-voz" / "Scripts" / "python.exe"
#: El script sí viaja con el programa.
SCRIPT = RESOURCE_ROOT / "tools" / "clonar_voz.py"

#: Duración mínima razonable de la muestra. Con menos, la clonación sale
#: irreconocible y no merece la pena esperar la generación entera.
MUESTRA_MIN_SEGUNDOS = 5.0

#: Cuánto tarda en generar, por cada segundo de audio. Medido en una RTX A500
#: de 4 GB. Sin tarjeta gráfica es bastante peor; con una buena, mejor. Sirve
#: para avisar ANTES de que alguien lance un guion de veinte minutos y crea
#: que el programa se colgó.
SEGUNDOS_POR_SEGUNDO = 8.0


def estimar(segundos_audio: float) -> dict:
    """Cuánto tardaría clonar esa cantidad de narración."""
    total = segundos_audio * SEGUNDOS_POR_SEGUNDO
    return {
        "segundos": round(total),
        "texto": (
            f"{total / 60:.0f} min" if total < 5400 else f"{total / 3600:.1f} horas"
        ),
        "nota": "medido en una gráfica modesta; sin GPU tarda bastante más",
    }


class ClonError(RuntimeError):
    """No se pudo clonar la voz."""


def disponible() -> tuple[bool, str]:
    """¿Está instalado el entorno de voz clonada? Devuelve (sí/no, motivo)."""
    if not PYTHON_VOZ.is_file():
        return False, (
            "La voz clonada todavía no está instalada en este equipo. "
            "Son unos 5 GB que se descargan una sola vez; puedes instalarla "
            "desde el botón «Instalar voz clonada». El resto del programa "
            "funciona igual sin ella."
        )
    if not SCRIPT.is_file():
        return False, f"Falta el script {SCRIPT}"
    return True, "listo"


# --- instalación automática ------------------------------------------------
#
# Quien recibe el .exe no tiene Python ni uv, y pedirle que abra una terminal y
# pegue tres comandos es perderlo. `uv` es un único ejecutable sin
# dependencias, y además sabe descargarse su propio Python: con eso se puede
# montar el entorno entero desde el programa.

UV_URL = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
HERRAMIENTAS = PROJECT_ROOT / "herramientas"
UV = HERRAMIENTAS / "uv.exe"
#: Lo que ocupa todo junto, para avisar antes de empezar.
TAMANO_APROXIMADO_GB = 5


def _uv() -> Path:
    """Descarga uv la primera vez. Es un solo .exe de unos 20 MB."""
    import io
    import urllib.request
    import zipfile

    if UV.is_file():
        return UV
    HERRAMIENTAS.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(UV_URL, headers={"User-Agent": "CrearVideos"})
    with urllib.request.urlopen(req, timeout=120) as r:
        datos = r.read()
    with zipfile.ZipFile(io.BytesIO(datos)) as z:
        for nombre in z.namelist():
            if nombre.endswith("uv.exe"):
                UV.write_bytes(z.read(nombre))
                return UV
    raise ClonError("El paquete de uv no traía uv.exe; prueba otra vez más tarde.")


def instalar(on_progress=None) -> str:
    """Monta el entorno de voz clonada de cero, sin que el usuario toque nada.

    Descarga uv, que a su vez se descarga Python 3.12 (la versión con la que
    todas las dependencias tienen rueda compilada), y con él instala PyTorch y
    Chatterbox. Tarda bastante: son varios gigas.
    """
    import subprocess

    def aviso(m: str) -> None:
        if on_progress:
            on_progress(m)

    aviso("Descargando el instalador (uv)")
    uv = _uv()

    pasos = [
        (
            "Preparando Python 3.12 (se descarga solo)",
            [str(uv), "venv", "--python", "3.12", str(PROJECT_ROOT / ".venv-voz")],
        ),
        (
            "Descargando PyTorch · es la parte larga, unos 3 GB",
            [
                str(uv), "pip", "install", "--python", str(PYTHON_VOZ),
                "torch==2.6.0", "torchaudio==2.6.0",
                "--index-url", "https://download.pytorch.org/whl/cu124",
            ],
        ),
        (
            "Instalando Chatterbox (el modelo de voz)",
            [str(uv), "pip", "install", "--python", str(PYTHON_VOZ), "chatterbox-tts"],
        ),
    ]

    for texto, cmd in pasos:
        aviso(texto)
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(PROJECT_ROOT),
        )
        if proc.returncode != 0:
            cola = "\n".join((proc.stderr or proc.stdout or "").strip().splitlines()[-8:])
            raise ClonError(f"Falló en «{texto}»:\n{cola}")

    ok, motivo = disponible()
    if not ok:
        raise ClonError(f"La instalación terminó pero algo falta: {motivo}")
    aviso("Voz clonada instalada")
    return (
        "Listo. Ya puedes usar la voz clonada. La primera vez que la uses "
        "descargará además el modelo (unos minutos)."
    )


#: Lo que se le da al modelo como referencia. Más no mejora: el modelo se queda
#: con el timbre en los primeros segundos, y una muestra larga solo añade
#: variaciones de tono que despistan.
MUESTRA_SEGUNDOS = 25.0
#: Se saltan los primeros segundos: en una grabación casera ahí suele estar el
#: "a ver, probando" y el ruido del micrófono al arrancar.
MUESTRA_DESDE = 1.0

AUDIOS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".wma", ".mpeg", ".mpga", ".webm"}


def _elegir_archivo(origen: Path) -> Path:
    """Si dan una carpeta, se usa el audio más largo que haya dentro."""
    if origen.is_file():
        return origen
    if not origen.is_dir():
        raise ClonError(f"No existe la muestra de voz: {origen}")

    from . import ffmpeg

    candidatos = [p for p in origen.iterdir() if p.is_file() and p.suffix.lower() in AUDIOS]
    if not candidatos:
        raise ClonError(
            f"En {origen} no hay ningún audio. Deja ahí tu grabación "
            f"({', '.join(sorted(AUDIOS))}) o apunta directamente al archivo."
        )
    if len(candidatos) == 1:
        return candidatos[0]
    return max(candidatos, key=lambda p: ffmpeg.duration(p))


def revisar_muestra(muestra: Path) -> dict:
    """Comprueba la muestra y describe lo que se va a usar."""
    from . import ffmpeg

    archivo = _elegir_archivo(Path(muestra))
    dur = ffmpeg.duration(archivo)
    if dur < MUESTRA_MIN_SEGUNDOS:
        raise ClonError(
            f"La muestra dura {dur:.1f}s y hacen falta al menos "
            f"{MUESTRA_MIN_SEGUNDOS:.0f}s. Con 15-30 segundos de voz limpia, "
            "sin música ni ruido, clona mucho mejor."
        )
    aviso = ""
    if dur > MUESTRA_SEGUNDOS * 2:
        aviso = (
            f"Tu grabación dura {dur / 60:.1f} min; se usarán "
            f"{MUESTRA_SEGUNDOS:.0f} s. Más no clona mejor."
        )
    return {"archivo": archivo, "duracion": dur, "aviso": aviso}


def preparar_muestra(muestra: Path) -> Path:
    """Deja la muestra como la quiere el modelo: WAV mono 24 kHz y 25 segundos.

    Aquí se arregla de una vez lo que trae una grabación real: el formato raro
    (un audio de WhatsApp viene en .mpeg), el estéreo, la frecuencia de
    muestreo y, sobre todo, la duración.
    """
    from . import ffmpeg
    from .config import OUTPUT_DIR

    info = revisar_muestra(muestra)
    archivo: Path = info["archivo"]

    destino_dir = OUTPUT_DIR / "_muestras" / "clon"
    destino_dir.mkdir(parents=True, exist_ok=True)
    sello = int(archivo.stat().st_mtime)
    destino = destino_dir / f"{archivo.stem[:40]}_{sello}.wav".replace(" ", "_")
    if destino.is_file():
        return destino

    desde = MUESTRA_DESDE if info["duracion"] > MUESTRA_SEGUNDOS + MUESTRA_DESDE else 0.0
    ffmpeg.run(
        [
            "-ss", f"{desde:.2f}",
            "-t", f"{MUESTRA_SEGUNDOS:.2f}",
            "-i", str(archivo.resolve()),
            "-ac", "1", "-ar", "24000",
            "-af", "highpass=f=70,loudnorm=I=-20:TP=-2",
            "-c:a", "pcm_s16le",
            str(destino.resolve()),
        ]
    )
    return destino


def marca(muestra: Path, idioma: str, exageracion: float, cfg: float) -> str:
    """Identifica esta voz clonada para poder reusarla sin regenerarla.

    Lleva la fecha del archivo: si regrabas la muestra con el mismo nombre, la
    voz cambia y hay que volver a generar.
    """
    muestra = Path(muestra)
    try:
        sello = int(muestra.stat().st_mtime)
    except OSError:
        sello = 0
    return f"clon:{muestra.resolve()}:{sello}:{idioma}:{exageracion}:{cfg}"


def generar(
    project: Project,
    muestra: Path,
    settings: Settings,
    *,
    idioma: str = "es",
    device: str = "auto",
    exageracion: float = 0.5,
    cfg: float = 0.5,
    on_progress=None,
) -> str:
    """Narra todas las escenas con la voz de `muestra`. Devuelve el modelo usado."""
    ok, motivo = disponible()
    if not ok:
        raise ClonError(motivo)
    muestra = preparar_muestra(muestra)

    crudos = project.audio_dir / "clon"
    crudos.mkdir(parents=True, exist_ok=True)
    entrada = crudos / "_escenas.json"
    entrada.write_text(
        # Sin las marcas "(pausa)": el modelo de clonación las leería en voz
        # alta. (Las pausas en sí aún no se aplican a la voz clonada.)
        json.dumps([{"index": s.index, "texto": pausas.limpiar(s.text)} for s in project.scenes],
                   ensure_ascii=False),
        encoding="utf-8",
    )

    cmd = [
        str(PYTHON_VOZ), str(SCRIPT),
        "--muestra", str(muestra.resolve()),
        "--entrada", str(entrada.resolve()),
        "--salida-dir", str(crudos.resolve()),
        "--idioma", idioma,
        "--device", device,
        "--exageracion", f"{exageracion}",
        "--cfg", f"{cfg}",
    ]
    if on_progress:
        on_progress("Clonando tu voz (la primera vez descarga el modelo)")

    # El progreso del script sale por stderr línea a línea; se reenvía tal cual
    # para que la espera no sea una pantalla en blanco de varios minutos.
    import os

    entorno = dict(os.environ)
    # Las barras de tqdm escriben miles de líneas por escena; sin esto, el
    # progreso de la app parpadea con porcentajes en vez de decir qué hace.
    entorno["TQDM_DISABLE"] = "1"
    entorno["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(PROJECT_ROOT),
        env=entorno,
    )
    ultimas: list[str] = []
    assert proc.stderr is not None
    for linea in proc.stderr:
        linea = linea.strip()
        if not linea:
            continue
        ultimas.append(linea)
        del ultimas[:-12]
        # Solo lo que dice el script; el resto (avisos de torch, restos de
        # barras) se guarda por si hay que explicar un fallo, pero no se enseña.
        if on_progress and linea.startswith(("Escena ", "Modelo ", "Cargando", "Descargando")):
            on_progress(f"Voz clonada · {linea}")
    salida = proc.stdout.read() if proc.stdout else ""
    if proc.wait() != 0:
        raise ClonError(
            "Falló la generación con voz clonada:\n" + "\n".join(ultimas[-6:])
        )

    try:
        datos = json.loads(salida[salida.index("{"):])
    except (ValueError, json.JSONDecodeError) as e:
        raise ClonError(f"El generador no devolvió un resultado legible: {e}") from e

    # A partir de aquí es exactamente el camino de "un audio por escena": se
    # mide cada archivo y se reparten las palabras dentro de su escena.
    cursor = 0.0
    for scene in project.scenes:
        src = crudos / f"escena_{scene.index:03d}.wav"
        if not src.is_file():
            raise ClonError(f"No se generó el audio de la escena {scene.index + 1}.")
        destino = project.audio_dir / f"escena_{scene.index:03d}.wav"
        duracion = vozpropia._preparar(src, destino, settings)
        scene.audio_path = destino
        scene.duration = duracion
        scene.start = cursor
        scene.words = vozpropia._palabras(scene.text, cursor, duracion - settings.scene_gap)
        cursor += duracion

    return str(datos.get("modelo", "chatterbox"))


#: Frase de la prueba: corta, con acentos y una pregunta, que es donde se nota
#: si la entonación clonada convence.
TEXTO_PRUEBA = (
    "Así va a sonar tu voz en el video. Esta es una frase de prueba, "
    "con su pausa y su pregunta: ¿te convence?"
)


def prueba(muestra: Path, texto: str = TEXTO_PRUEBA, on_progress=None) -> Path:
    """Genera UNA frase con la voz clonada, para oírla antes de lanzarlo todo.

    Tarda alrededor de un minuto (cargar el modelo y generar), frente a la hora
    larga que puede costar un guion entero. Se cachea por muestra: pedirla dos
    veces no la regenera.
    """
    import json as _json

    ok, motivo = disponible()
    if not ok:
        raise ClonError(motivo)

    from . import ffmpeg
    from .config import OUTPUT_DIR

    lista = preparar_muestra(muestra)
    destino_dir = OUTPUT_DIR / "_muestras" / "clon"
    final = destino_dir / f"prueba_{lista.stem}.mp3"
    if final.is_file():
        return final

    crudos = destino_dir / f"_tmp_{lista.stem}"
    crudos.mkdir(parents=True, exist_ok=True)
    entrada = crudos / "_escenas.json"
    entrada.write_text(
        _json.dumps([{"index": 0, "texto": texto}], ensure_ascii=False), encoding="utf-8"
    )

    import os
    import subprocess as sp

    entorno = dict(os.environ)
    entorno["TQDM_DISABLE"] = "1"
    entorno["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    if on_progress:
        on_progress("Clonando una frase de prueba (tarda ~1 minuto)")
    proc = sp.run(
        [
            str(PYTHON_VOZ), str(SCRIPT),
            "--muestra", str(lista.resolve()),
            "--entrada", str(entrada.resolve()),
            "--salida-dir", str(crudos.resolve()),
            "--idioma", "es",
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(PROJECT_ROOT), env=entorno,
    )
    generado = crudos / "escena_000.wav"
    if proc.returncode != 0 or not generado.is_file():
        cola = "\n".join((proc.stderr or "").strip().splitlines()[-5:])
        raise ClonError(f"No se pudo generar la prueba de voz:\n{cola}")

    ffmpeg.run(["-i", str(generado.resolve()), "-c:a", "libmp3lame", "-b:a", "128k", str(final.resolve())])
    import shutil

    shutil.rmtree(crudos, ignore_errors=True)
    return final


if __name__ == "__main__":  # pragma: no cover - ayuda rápida
    ok, motivo = disponible()
    print(motivo)
    sys.exit(0 if ok else 1)
