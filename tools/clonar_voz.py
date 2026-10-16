"""Genera la narración con una voz clonada (Chatterbox, de Resemble AI).

Corre en su PROPIO entorno (.venv-voz), no en el de la app: arrastra PyTorch y
unos gigas de modelo, y no tiene sentido cargar con eso el camino normal, que
es ligero y funciona sin GPU.

La app lo llama así, una sola vez por video:

    .venv-voz\\Scripts\\python tools\\clonar_voz.py --muestra voz.wav \\
        --entrada escenas.json --salida-dir carpeta [--idioma es] [--device cuda]

`escenas.json` es [{"index": 0, "texto": "..."}, ...] y por cada escena deja
`escena_000.wav` en la carpeta de salida. Al terminar imprime en stdout un JSON
con lo generado. El modelo se carga UNA vez para todas las escenas: cargarlo
cuesta más que generar una escena corta.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

#: Chatterbox se atraganta con textos largos: pierde el hilo y a veces repite el
#: final. Se parte por frases en trozos de este tamaño y se pegan los audios.
MAX_CHARS = 280
_FIN_FRASE = re.compile(r"(?<=[.!?…])\s+")


def trocear(texto: str) -> list[str]:
    """Parte un texto largo en trozos por frases, sin cortar a mitad de frase."""
    texto = " ".join(texto.split())
    if len(texto) <= MAX_CHARS:
        return [texto]
    trozos: list[str] = []
    actual = ""
    for frase in _FIN_FRASE.split(texto):
        if actual and len(actual) + len(frase) + 1 > MAX_CHARS:
            trozos.append(actual)
            actual = frase
        else:
            actual = f"{actual} {frase}".strip()
    if actual:
        trozos.append(actual)
    # Una frase sola más larga que el tope se parte por comas antes que dejarla
    # entera: el modelo la arruinaría igual.
    salida: list[str] = []
    for t in trozos:
        if len(t) <= MAX_CHARS * 1.5:
            salida.append(t)
            continue
        buf = ""
        for parte in t.split(", "):
            if buf and len(buf) + len(parte) + 2 > MAX_CHARS:
                salida.append(buf)
                buf = parte
            else:
                buf = f"{buf}, {parte}".strip(", ")
        if buf:
            salida.append(buf)
    return salida


def cargar_modelo(device: str, idioma: str):
    """Devuelve (modelo, funcion_generar, etiqueta).

    Se prefiere el modelo multilingüe, que es el que habla español de verdad.
    Si la versión instalada no lo trae, se cae al modelo inglés y se avisa.
    """
    import torch

    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"

    try:
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS

        modelo = ChatterboxMultilingualTTS.from_pretrained(device=device)

        def generar(texto: str, muestra: str, exageracion: float, cfg: float):
            return modelo.generate(
                texto,
                language_id=idioma,
                audio_prompt_path=muestra,
                exaggeration=exageracion,
                cfg_weight=cfg,
            )

        return modelo, generar, f"multilingüe ({idioma}) en {device}"
    except ImportError:
        from chatterbox.tts import ChatterboxTTS

        modelo = ChatterboxTTS.from_pretrained(device=device)

        def generar(texto: str, muestra: str, exageracion: float, cfg: float):
            return modelo.generate(
                texto,
                audio_prompt_path=muestra,
                exaggeration=exageracion,
                cfg_weight=cfg,
            )

        return modelo, generar, f"solo inglés en {device} (no hay modelo multilingüe)"


def main() -> int:
    p = argparse.ArgumentParser(description="Narra un guion con una voz clonada")
    p.add_argument("--muestra", required=True, help="WAV/MP3 con la voz a clonar")
    p.add_argument("--entrada", required=True, help="JSON con las escenas")
    p.add_argument("--salida-dir", required=True)
    p.add_argument("--idioma", default="es")
    p.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    p.add_argument("--exageracion", type=float, default=0.5, help="0.3 sobrio, 0.7 enfático")
    p.add_argument("--cfg", type=float, default=0.5, help="Bajo = sigue más el ritmo de tu muestra")
    args = p.parse_args()

    import os

    # Igual que en el lado de la app: nada de barras de progreso, que aquí solo
    # sirven para ensuciar la salida que lee el programa que nos llamó.
    os.environ.setdefault("TQDM_DISABLE", "1")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

    escenas = json.loads(Path(args.entrada).read_text(encoding="utf-8"))
    destino = Path(args.salida_dir)
    destino.mkdir(parents=True, exist_ok=True)

    # El progreso va por stderr para que stdout quede limpio con el JSON final.
    print(f"Cargando el modelo…", file=sys.stderr, flush=True)
    import torch
    import torchaudio

    modelo, generar, etiqueta = cargar_modelo(args.device, args.idioma)
    print(f"Modelo listo: {etiqueta}", file=sys.stderr, flush=True)

    generadas = []
    for i, escena in enumerate(escenas, 1):
        indice = int(escena["index"])
        trozos = trocear(escena["texto"])
        print(f"Escena {i}/{len(escenas)} ({len(trozos)} trozo(s))", file=sys.stderr, flush=True)

        ondas = []
        for trozo in trozos:
            onda = generar(trozo, args.muestra, args.exageracion, args.cfg)
            ondas.append(onda.detach().cpu() if hasattr(onda, "detach") else onda)
            if len(trozos) > 1:
                # Respiro entre trozos de una misma escena: sin él, dos frases
                # generadas por separado suenan pegadas y se atropellan.
                ondas.append(torch.zeros(1, int(modelo.sr * 0.18)))

        onda = torch.cat(ondas, dim=1) if len(ondas) > 1 else ondas[0]
        salida = destino / f"escena_{indice:03d}.wav"
        torchaudio.save(str(salida), onda, modelo.sr)
        generadas.append({"index": indice, "archivo": salida.name})

    print(json.dumps({"ok": True, "modelo": etiqueta, "escenas": generadas}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
