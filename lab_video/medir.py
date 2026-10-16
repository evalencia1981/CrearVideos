"""Métricas de un clip: lo que dice ffprobe y el índice de movimiento local.

Índice de movimiento local
--------------------------
Por cada par de fotogramas seguidos se calcula el flujo óptico denso
(Farnebäck) y se le ajusta un modelo afín global, que es exactamente lo que
produce un Ken Burns: zoom, paneo y un poco de giro. Lo que el afín no explica
es movimiento propio de la escena (un brazo que se levanta, gente que camina,
humo). El índice es

    Σ |flujo − flujo afín|  /  Σ |flujo|

sumado sobre todos los pares. Cerca de 0 = solo cámara (zoom/paneo); alto =
el contenido se mueve por su cuenta. Referencia: ~0,5–0,9 en escenas animadas
de viani.pro; ~0,1–0,3 en los clips de CrearVideos con solo zoom.

El afín se ajusta con RANSAC para que un personaje grande moviéndose no
arrastre el ajuste (con mínimos cuadrados, el fondo "se mueve" para
compensarlo y el índice baja). Se calcula sobre clips sin subtítulos: un
texto que aparece cuenta como movimiento local.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

#: Ancho al que se reduce cada fotograma: el índice es una proporción, así
#: que no cambia con la resolución, y a 320 px el flujo se calcula rápido.
ANCHO = 320
#: Separación entre los dos fotogramas de cada par, en segundos.
SEPARACION_S = 0.125
#: Pares con menos movimiento medio que esto (px por par, a 320 px) no
#: cuentan: en un plano quieto el cociente sería puro ruido.
FLUJO_MINIMO = 0.05
#: Borde que se descarta (fracción): el flujo es poco fiable en los bordes.
BORDE = 0.05


def ffprobe(mp4: Path) -> dict:
    """Resolución, duración, fps y si trae audio, según ffprobe."""
    binario = shutil.which("ffprobe")
    if not binario:
        raise RuntimeError("No encuentro ffprobe en el PATH.")
    proc = subprocess.run(
        [binario, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(mp4)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe falló: {proc.stderr.strip()[:300]}")
    info = json.loads(proc.stdout or "{}")
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    fps_txt = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"
    try:
        fps = float(Fraction(fps_txt)) if fps_txt != "0/0" else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    duracion = float(info.get("format", {}).get("duration") or video.get("duration") or 0)
    return {
        "ancho": int(video.get("width") or 0),
        "alto": int(video.get("height") or 0),
        "resolucion": f"{video.get('width')}x{video.get('height')}",
        "duracion_s": round(duracion, 3),
        "fps": round(fps, 3),
        "fotogramas": int(video.get("nb_frames") or 0) or None,
        "codec": video.get("codec_name"),
        "audio": any(s.get("codec_type") == "audio" for s in info.get("streams", [])),
        "bytes": int(info.get("format", {}).get("size") or 0),
    }


def indice_movimiento(mp4: Path, separacion_s: float = SEPARACION_S) -> dict:
    """Índice de movimiento local del clip (ver docstring del módulo)."""
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(str(mp4))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV no puede abrir {mp4}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    # Se comparan fotogramas separados ~1/8 s, no consecutivos: un Ken Burns
    # mueve ~0,2 px por fotograma y a esa escala el ruido del flujo pesa
    # tanto como la señal (el índice de un zoom puro salía inflado). Con la
    # misma separación en segundos, clips de 24 y 30 fps son comparables.
    salto = max(1, round(fps * separacion_s))

    def leer():
        ok, f = cap.read()
        if not ok:
            return None
        h, w = f.shape[:2]
        f = cv2.resize(f, (ANCHO, max(2, round(h * ANCHO / w))), interpolation=cv2.INTER_AREA)
        return cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)

    def siguiente():
        """El fotograma `salto` posiciones más adelante (None al final)."""
        f = None
        for _ in range(salto):
            f = leer()
            if f is None:
                return None
        return f

    previo = leer()
    if previo is None:
        raise RuntimeError("el clip no tiene fotogramas legibles")
    h, w = previo.shape
    by, bx = int(h * BORDE), int(w * BORDE)
    ys, xs = np.mgrid[by:h - by, bx:w - bx].astype(np.float32)
    # Muestra en rejilla para el RANSAC: miles de puntos bastan.
    rej = (slice(None, None, 6), slice(None, None, 6))

    suma_res = suma_flujo = 0.0
    pares = pares_validos = 0
    flujos_medios = []
    while True:
        actual = siguiente()
        if actual is None:
            break
        flujo = cv2.calcOpticalFlowFarneback(previo, actual, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        previo = actual
        pares += 1

        fx = flujo[by:h - by, bx:w - bx, 0]
        fy = flujo[by:h - by, bx:w - bx, 1]
        mag = np.hypot(fx, fy)
        medio = float(mag.mean())
        flujos_medios.append(medio)
        if medio < FLUJO_MINIMO:
            continue

        origen = np.stack([xs[rej].ravel(), ys[rej].ravel()], axis=1)
        destino = origen + np.stack([fx[rej].ravel(), fy[rej].ravel()], axis=1)
        M, _ = cv2.estimateAffine2D(origen, destino, method=cv2.RANSAC,
                                    ransacReprojThreshold=0.5, maxIters=2000)
        if M is None:
            # Sin consenso: ajuste por mínimos cuadrados sobre la rejilla.
            A = np.hstack([origen, np.ones((len(origen), 1), np.float32)])
            sol, *_ = np.linalg.lstsq(A, destino, rcond=None)
            M = sol.T
        px = M[0, 0] * xs + M[0, 1] * ys + M[0, 2] - xs
        py = M[1, 0] * xs + M[1, 1] * ys + M[1, 2] - ys
        residuo = np.hypot(fx - px, fy - py)

        suma_res += float(residuo.sum())
        suma_flujo += float(mag.sum())
        pares_validos += 1
    cap.release()

    indice = (suma_res / suma_flujo) if suma_flujo > 0 else None
    return {
        "indice_movimiento_local": round(indice, 3) if indice is not None else None,
        # Movimiento total (cámara + escena) en px por segundo a 320 px de
        # ancho. Con un índice alto pero flujo ~0, "casi nada se mueve".
        "flujo_px_s": round(float(np.mean(flujos_medios)) / (salto / fps), 2) if flujos_medios else 0.0,
        "pares": pares,
        "pares_con_movimiento": pares_validos,
    }


def medir(mp4: Path) -> dict:
    datos = ffprobe(mp4)
    datos.update(indice_movimiento(mp4))
    return datos
