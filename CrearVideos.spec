# -*- mode: python ; coding: utf-8 -*-
"""Empaqueta CrearVideos en un solo .exe para repartir.

Se construye con:  .venv-build\\Scripts\\python -m PyInstaller CrearVideos.spec

Qué viaja dentro:

- El código de la app y sus dependencias de Python.
- `bin/ffmpeg.exe` y `bin/ffprobe.exe`: así nadie tiene que instalar ffmpeg,
  que es donde se atasca la mayoría.
- La página de la interfaz, el script de voz clonada y un guion de ejemplo.

Qué NO viaja, a propósito:

- El entorno de voz clonada (PyTorch + modelo, 5 GB). Quien lo quiera lo
  instala aparte; la app avisa y sigue funcionando sin él.
- Los proyectos y las claves: se crean en Documentos\\CrearVideos al arrancar.
"""

from pathlib import Path

RAIZ = Path(SPECPATH)

datos = [
    (str(RAIZ / "crearvideos" / "web" / "index.html"), "crearvideos/web"),
    (str(RAIZ / "tools" / "clonar_voz.py"), "tools"),
    (str(RAIZ / "guiones" / "ejemplo.txt"), "guiones"),
]
binarios = [
    (str(RAIZ / "bin" / "ffmpeg.exe"), "bin"),
    (str(RAIZ / "bin" / "ffprobe.exe"), "bin"),
]

a = Analysis(
    ["lanzador.py"],
    pathex=[str(RAIZ)],
    binaries=binarios,
    datas=datos,
    # PyInstaller no ve los import que se hacen dentro de funciones, y esta app
    # los usa mucho para arrancar rápido. Sin esta lista, el .exe compila pero
    # falla al pulsar un botón.
    hiddenimports=[
        "edge_tts",
        "dotenv",
        "PIL", "PIL.Image", "PIL.ImageDraw", "PIL.ImageFont",
        "yt_dlp",
        "googleapiclient.discovery", "googleapiclient.http", "googleapiclient.errors",
        "google_auth_oauthlib.flow", "google.oauth2.credentials",
        "google.auth.transport.requests",
        "crearvideos.providers.images.pollinations",
        "crearvideos.providers.images.pexels",
        "crearvideos.providers.images.placeholder",
        "crearvideos.providers.images.gemini",
        "crearvideos.providers.images.openai_images",
        "crearvideos.providers.tts.edge",
        "crearvideos.providers.tts.openai_tts",
    ],
    hookspath=[],
    runtime_hooks=[],
    # Fuera lo que abulta y no se usa: la app no dibuja ventanas propias ni
    # gráficas, toda su interfaz es la página web.
    excludes=["tkinter", "matplotlib", "numpy.testing", "pytest", "IPython"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CrearVideos",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Con consola a propósito: ahí se ve la dirección de la interfaz y el
    # avance de cada trabajo. Cerrar la ventana cierra el programa.
    console=True,
    icon=str(RAIZ / "assets" / "icono.ico") if (RAIZ / "assets" / "icono.ico").is_file() else None,
)
