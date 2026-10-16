"""Línea de comandos del laboratorio.

    python -m lab_video generar --solo-ver                 # qué se generaría y cuánto cuesta
    python -m lab_video generar --modelos veo-3.1-fast --imagenes p000
    python -m lab_video comparar                           # métricas + comparar.html del día
    python -m lab_video medir clip.mp4 [...]               # índice de movimiento de cualquier clip

Control de gasto: --solo-ver no gasta; --max-usd (5 por defecto) corta antes
de empezar si el estimado lo supera; siempre se pide confirmación (o --si);
lo que ya se generó con las mismas entradas no se vuelve a pedir; nunca más
de dos generaciones a la vez.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from . import config
from .proveedores import ErrorProveedor, proveedor_para

_lock = threading.Lock()


# ---------------------------------------------------------------- utilidades

def _entradas() -> list[dict]:
    datos = json.loads(config.ENTRADAS.read_text(encoding="utf-8"))
    return datos["imagenes"]


def _huella(imagen: Path, prompt: str, modelo_id: str, duracion: int, aspecto: str, res: str) -> str:
    """Identifica un clip por TODO lo que lo determina: si algo cambia, es otro."""
    h = hashlib.sha256(imagen.read_bytes())
    h.update("|".join([prompt, modelo_id, str(duracion), aspecto, res]).encode())
    return h.hexdigest()[:16]


def _nombre_clip(imagen_id: str, modelo: str, duracion: int, aspecto: str, res: str) -> str:
    return f"{imagen_id}__{modelo}__{duracion}s_{aspecto.replace(':', 'x')}_{res}.mp4"


def _cargar(carpeta: Path) -> dict:
    ruta = carpeta / "resultados.json"
    if ruta.is_file():
        return json.loads(ruta.read_text(encoding="utf-8"))
    return {"carpeta": str(carpeta), "clips": []}


def _guardar(carpeta: Path, datos: dict) -> None:
    datos["actualizado"] = datetime.now().isoformat(timespec="seconds")
    tmp = carpeta / "resultados.json.tmp"
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(carpeta / "resultados.json")


def _registrar(carpeta: Path, fila: dict) -> None:
    """Añade (o reemplaza, por huella) un clip en resultados.json."""
    with _lock:
        datos = _cargar(carpeta)
        datos["clips"] = [c for c in datos["clips"] if c.get("clave") != fila["clave"]] + [fila]
        _guardar(carpeta, datos)


def _ya_generado(clave: str) -> tuple[Path, dict] | None:
    """Busca un clip exitoso con esa huella en cualquier corrida anterior."""
    for res in sorted(config.RESULTADOS_DIR.glob("*/resultados.json"), reverse=True):
        try:
            datos = json.loads(res.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for c in datos.get("clips", []):
            if c.get("clave") == clave and c.get("exito") and c.get("archivo"):
                mp4 = res.parent / c["archivo"]
                if mp4.is_file():
                    return mp4, c
    return None


def _tabla(filas: list[list], cabecera: list[str]) -> str:
    anchos = [max(len(str(x)) for x in col) for col in zip(cabecera, *filas)]
    linea = lambda fila: "  ".join(str(x).ljust(a) for x, a in zip(fila, anchos))  # noqa: E731
    return "\n".join([linea(cabecera), linea(["-" * a for a in anchos])] + [linea(f) for f in filas])


def _usd(x: float | None) -> str:
    return "—" if x is None else f"${x:.3f}"


# ---------------------------------------------------------------- generar

def _plan(args) -> list[dict]:
    imagenes = _entradas()
    if args.imagenes:
        pedidas = [x.strip() for x in args.imagenes.split(",")]
        desconocidas = set(pedidas) - {i["id"] for i in imagenes}
        if desconocidas:
            raise SystemExit(f"Imágenes desconocidas: {', '.join(sorted(desconocidas))}")
        imagenes = [i for i in imagenes if i["id"] in pedidas]
    modelos = [m.strip() for m in args.modelos.split(",")] if args.modelos else list(config.MODELOS_DEFECTO)
    desconocidos = set(modelos) - set(config.MODELOS)
    if desconocidos:
        raise SystemExit(
            f"Modelos desconocidos: {', '.join(sorted(desconocidos))}. "
            f"Opciones: {', '.join(config.MODELOS)}"
        )

    plan = []
    for img in imagenes:
        ruta = config.RAIZ / img["archivo"]
        for clave_modelo in modelos:
            prov = proveedor_para(clave_modelo)
            dur = prov.duracion_valida(args.duracion)
            tarea = {
                "imagen_id": img["id"], "imagen": img["archivo"], "prompt": img["prompt"],
                "modelo": clave_modelo, "modelo_id": prov.modelo_id, "proveedor": prov.nombre,
                "duracion_pedida": args.duracion, "duracion": dur, "aspecto": args.aspecto,
                "resolucion": args.resolucion, "prov": prov, "ruta": ruta,
                "costo_estimado_usd": 0.0, "estado": "generar", "motivo": "",
            }
            if not ruta.is_file():
                tarea.update(estado="no válido", motivo=f"no existe {img['archivo']}")
            else:
                try:
                    prov.validar(dur, args.resolucion)
                    tarea["costo_estimado_usd"] = prov.estimar_costo(dur, args.resolucion)
                    tarea["clave"] = _huella(ruta, img["prompt"], prov.modelo_id, dur,
                                             args.aspecto, args.resolucion)
                    previo = _ya_generado(tarea["clave"])
                    if previo:
                        tarea.update(estado="ya existe", previo=previo, costo_estimado_usd=0.0)
                except ErrorProveedor as e:
                    tarea.update(estado="no válido", motivo=str(e))
            plan.append(tarea)
    return plan


def _ejecutar(t: dict, carpeta: Path) -> dict:
    nombre = _nombre_clip(t["imagen_id"], t["modelo"], t["duracion"], t["aspecto"], t["resolucion"])
    destino = carpeta / nombre
    fila = {
        "clave": t["clave"], "imagen_id": t["imagen_id"], "imagen": t["imagen"],
        "prompt": t["prompt"], "proveedor": t["proveedor"], "modelo": t["modelo"],
        "modelo_id": t["modelo_id"], "duracion_pedida_s": t["duracion_pedida"],
        "duracion_solicitada_s": t["duracion"], "aspecto": t["aspecto"],
        "resolucion_pedida": t["resolucion"], "costo_estimado_usd": t["costo_estimado_usd"],
        "fecha": datetime.now().isoformat(timespec="seconds"),
    }
    inicio = time.monotonic()
    try:
        g = t["prov"].generar(t["ruta"], t["prompt"], t["duracion"], t["aspecto"],
                              t["resolucion"], destino)
        fila.update(
            exito=True, motivo="", archivo=nombre,
            tiempo_generacion_s=round(time.monotonic() - inicio, 1),
            costo_real_usd=g.costo_real_usd, costo_real_fuente=g.costo_real_fuente,
            extra=g.extra,
        )
    except ErrorProveedor as e:
        fila.update(exito=False, motivo=str(e), archivo=None,
                    tiempo_generacion_s=round(time.monotonic() - inicio, 1),
                    costo_real_usd=None, costo_real_fuente="no se cobra si no se genera")
        _registrar(carpeta, fila)
        return fila
    except Exception as e:  # noqa: BLE001 - un bug no debe tumbar los otros clips
        fila.update(exito=False, motivo=f"error inesperado: {type(e).__name__}: {e}"[:500],
                    archivo=None, tiempo_generacion_s=round(time.monotonic() - inicio, 1),
                    costo_real_usd=None, costo_real_fuente="")
        _registrar(carpeta, fila)
        return fila

    _completar_metricas(fila, carpeta)
    _registrar(carpeta, fila)
    return fila


def _completar_metricas(fila: dict, carpeta: Path) -> None:
    """ffprobe + índice de movimiento + costo facturable y por segundo."""
    from .medir import medir

    try:
        m = medir(carpeta / fila["archivo"])
        fila.update(
            resolucion_real=m["resolucion"], ancho=m["ancho"], alto=m["alto"],
            duracion_real_s=m["duracion_s"], fps=m["fps"], audio=m["audio"],
            bytes=m["bytes"], indice_movimiento_local=m["indice_movimiento_local"],
            flujo_px_s=m["flujo_px_s"],
        )
    except Exception as e:  # noqa: BLE001
        fila["error_metricas"] = str(e)[:300]

    modelo = config.MODELOS.get(fila.get("modelo", ""))
    if modelo and modelo.proveedor == "gemini-veo":
        # Veo cobra precio/s × segundos pedidos, solo si se genera. No hay
        # cifra de la API: es la tarifa aplicada, y se dice así.
        fila["costo_facturable_usd"] = round(
            modelo.resoluciones[fila["resolucion_pedida"]] * fila["duracion_solicitada_s"], 4)
    elif fila.get("costo_real_usd") is not None:
        fila["costo_facturable_usd"] = fila["costo_real_usd"]
    costo = fila.get("costo_facturable_usd")
    dur = fila.get("duracion_real_s")
    fila["usd_por_segundo"] = round(costo / dur, 4) if costo is not None and dur else None


def cmd_generar(args) -> int:
    if args.concurrencia > config.CONCURRENCIA_MAX:
        print(f"Concurrencia limitada a {config.CONCURRENCIA_MAX}.")
    concurrencia = max(1, min(args.concurrencia, config.CONCURRENCIA_MAX))
    max_usd = args.max_usd if args.max_usd is not None else config.max_usd_defecto()

    plan = _plan(args)
    filas = [
        [t["imagen_id"], t["modelo"], t["modelo_id"],
         f"{t['duracion']}s" + (f" (pedido {t['duracion_pedida']:g})" if t["duracion"] != t["duracion_pedida"] else ""),
         t["resolucion"], t["aspecto"], t["estado"] + (f": {t['motivo']}" if t["motivo"] else ""),
         _usd(t["costo_estimado_usd"]) if t["estado"] == "generar" else "—"]
        for t in plan
    ]
    print(_tabla(filas, ["imagen", "modelo", "id API", "duración", "res", "aspecto", "estado", "costo est."]))

    pendientes = [t for t in plan if t["estado"] == "generar"]
    total = round(sum(t["costo_estimado_usd"] for t in pendientes), 3)
    print(f"\nA generar: {len(pendientes)} clip(s) · estimado {_usd(total)} · tope {_usd(max_usd)}"
          f" · concurrencia {concurrencia}")
    print(f"Clave de Gemini: {'presente' if config.clave_api() else 'FALTA'} (no se muestra)")
    for clave in {t["modelo"] for t in plan}:
        if config.MODELOS[clave].nota:
            print(f"  nota {clave}: {config.MODELOS[clave].nota}")

    if args.solo_ver:
        print("\n--solo-ver: no se generó nada.")
        return 0

    carpeta = config.RESULTADOS_DIR / (args.fecha or datetime.now().strftime("%Y-%m-%d"))
    carpeta.mkdir(parents=True, exist_ok=True)

    # Lo que ya existe en otra corrida se copia aquí para que la comparación
    # de esta carpeta esté completa, sin volver a pagarlo.
    for t in plan:
        if t["estado"] == "ya existe":
            mp4, previa = t["previo"]
            if mp4.parent != carpeta:
                shutil.copy2(mp4, carpeta / mp4.name)
                _registrar(carpeta, {**previa, "reusado_de": str(mp4.parent.name)})

    if not pendientes:
        print("Nada que generar.")
        _escribir_comparacion(carpeta)
        return 0
    if total > max_usd:
        print(f"\nEl estimado ({_usd(total)}) supera el tope de {_usd(max_usd)}. "
              "Reduce imágenes/modelos o sube --max-usd.")
        return 2
    if not config.clave_api():
        print("\nFalta GEMINI_API_KEY (o LAB_VIDEO_GEMINI_API_KEY) en .env.")
        return 2
    if not args.si:
        if not sys.stdin.isatty():
            print("\nSin terminal interactiva: confirma con --si para generar.")
            return 2
        try:
            respuesta = input(f"\n¿Generar {len(pendientes)} clip(s) por ~{_usd(total)}? [s/N] ")
        except EOFError:
            respuesta = ""   # sin nadie que conteste, la respuesta es no
        if respuesta.strip().lower() not in ("s", "si", "sí", "y"):
            print("Cancelado.")
            return 1

    print(f"\nGenerando en {carpeta} …")
    resultados = []
    with ThreadPoolExecutor(max_workers=concurrencia) as pool:
        futuros = {pool.submit(_ejecutar, t, carpeta): t for t in pendientes}
        for f in as_completed(futuros):
            r = f.result()
            resultados.append(r)
            estado = "OK " if r["exito"] else "FALLO"
            extra = (f"{r.get('resolucion_real')} {r.get('duracion_real_s')}s {r.get('fps')}fps "
                     f"índice {r.get('indice_movimiento_local')}") if r["exito"] else r["motivo"]
            print(f"  [{estado}] {r['imagen_id']} · {r['modelo']} · {r['tiempo_generacion_s']}s · {extra}")

    _escribir_comparacion(carpeta)
    _resumen(_cargar(carpeta)["clips"])
    print(f"\nResultados: {carpeta / 'resultados.json'}\nComparar:   {carpeta / 'comparar.html'}")
    return 0


# ---------------------------------------------------------------- comparar

def _linea_base(carpeta: Path, imagen_ids: set[str]) -> None:
    """Añade el clip Ken Burns que hoy hace CrearVideos para cada imagen."""
    for img in _entradas():
        if img["id"] not in imagen_ids or not img.get("clip_actual"):
            continue
        origen = config.RAIZ / img["clip_actual"]
        if not origen.is_file():
            continue
        nombre = f"{img['id']}__ken-burns-actual.mp4"
        clave = f"base-{img['id']}"
        datos = _cargar(carpeta)
        if any(c.get("clave") == clave and c.get("indice_movimiento_local") is not None
               for c in datos["clips"]):
            continue
        shutil.copy2(origen, carpeta / nombre)
        fila = {
            "clave": clave, "imagen_id": img["id"], "imagen": img["archivo"],
            "prompt": "(zoom Ken Burns de CrearVideos, sin subtítulos)",
            "proveedor": "crearvideos", "modelo": "ken-burns-actual", "modelo_id": "ken-burns",
            "linea_base": True, "exito": True, "motivo": "", "archivo": nombre,
            "costo_estimado_usd": 0.0, "costo_real_usd": 0.0, "costo_real_fuente": "local",
            "tiempo_generacion_s": None, "fecha": datetime.now().isoformat(timespec="seconds"),
        }
        _completar_metricas(fila, carpeta)
        fila["costo_facturable_usd"], fila["usd_por_segundo"] = 0.0, 0.0
        _registrar(carpeta, fila)


def _escribir_comparacion(carpeta: Path) -> None:
    from .comparar import escribir_html

    datos = _cargar(carpeta)
    # Métricas que falten (p. ej. si se cortó a medias) se completan aquí.
    for c in datos["clips"]:
        if c.get("exito") and c.get("archivo") and c.get("indice_movimiento_local") is None \
                and (carpeta / c["archivo"]).is_file():
            _completar_metricas(c, carpeta)
            _registrar(carpeta, c)
    _linea_base(carpeta, {c["imagen_id"] for c in datos["clips"]})
    escribir_html(carpeta, _cargar(carpeta), {i["id"]: i for i in _entradas()})


def _resumen(clips: list[dict]) -> None:
    por_modelo: dict[str, list[dict]] = {}
    for c in clips:
        por_modelo.setdefault(c["modelo"], []).append(c)
    filas = []
    for modelo, cs in por_modelo.items():
        ok = [c for c in cs if c.get("exito")]
        segs = sum(c.get("duracion_real_s") or 0 for c in ok)
        fact = sum(c.get("costo_facturable_usd") or 0 for c in ok)
        reales = [c["costo_real_usd"] for c in ok if c.get("costo_real_usd") is not None]
        indices = [c["indice_movimiento_local"] for c in ok if c.get("indice_movimiento_local") is not None]
        tiempos = [c["tiempo_generacion_s"] for c in ok if c.get("tiempo_generacion_s")]
        filas.append([
            modelo, len(ok), len(cs) - len(ok),
            _usd(sum(c.get("costo_estimado_usd") or 0 for c in ok)),
            _usd(sum(reales)) if reales else "—",
            f"${fact / segs:.3f}/s" if segs else "—",
            f"{sum(indices) / len(indices):.2f}" if indices else "—",
            f"{sum(tiempos) / len(tiempos):.0f}s" if tiempos else "—",
        ])
    print("\n" + _tabla(filas, ["modelo", "ok", "fallos", "estimado", "real API",
                                "USD/s real", "índice mov.", "t. medio"]))


def cmd_comparar(args) -> int:
    if args.carpeta:
        carpeta = Path(args.carpeta)
    else:
        carpetas = sorted(p for p in config.RESULTADOS_DIR.glob("*") if (p / "resultados.json").is_file())
        if not carpetas:
            print("No hay resultados todavía.")
            return 1
        carpeta = carpetas[-1]
    _escribir_comparacion(carpeta)
    _resumen(_cargar(carpeta)["clips"])
    print(f"\nComparar: {carpeta / 'comparar.html'}")
    return 0


def cmd_medir(args) -> int:
    from .medir import medir

    for ruta in args.clips:
        m = medir(Path(ruta))
        print(f"{Path(ruta).name}: {m['resolucion']} · {m['duracion_s']}s · {m['fps']}fps · "
              f"índice {m['indice_movimiento_local']} · flujo {m['flujo_px_s']} px/s")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m lab_video", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generar", help="genera (o solo muestra) los clips del plan")
    g.add_argument("--modelos", help=f"separados por coma (por defecto {','.join(config.MODELOS_DEFECTO)}); "
                                     f"opciones: {', '.join(config.MODELOS)}")
    g.add_argument("--imagenes", help="ids de entradas.json separados por coma (por defecto todas)")
    g.add_argument("--duracion", type=float, default=4, help="segundos objetivo (3-5); se ajusta a lo que admita cada modelo")
    g.add_argument("--aspecto", choices=["9:16", "16:9"], default="9:16")
    g.add_argument("--resolucion", default="720p")
    g.add_argument("--solo-ver", action="store_true", help="muestra qué se generaría y el costo, sin gastar")
    g.add_argument("--max-usd", type=float, help=f"tope de gasto de la corrida (por defecto {config.MAX_USD_DEFECTO})")
    g.add_argument("--concurrencia", type=int, default=2, help="máximo 2")
    g.add_argument("--si", action="store_true", help="no preguntar antes de generar")
    g.add_argument("--fecha", help="carpeta de resultados (por defecto, la de hoy)")
    g.set_defaults(func=cmd_generar)

    c = sub.add_parser("comparar", help="completa métricas y escribe comparar.html")
    c.add_argument("carpeta", nargs="?", help="carpeta de resultados (por defecto, la más reciente)")
    c.set_defaults(func=cmd_comparar)

    m = sub.add_parser("medir", help="índice de movimiento y ffprobe de uno o varios clips")
    m.add_argument("clips", nargs="+")
    m.set_defaults(func=cmd_medir)

    args = p.parse_args(argv)
    return args.func(args)
