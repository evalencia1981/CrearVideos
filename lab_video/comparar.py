"""comparar.html: los clips lado a lado por imagen, con sus métricas.

Página estática que vive junto a los clips (rutas relativas): se abre con
doble clic y se puede mover la carpeta entera sin que se rompa.
"""

from __future__ import annotations

import html
import shutil
from pathlib import Path

from . import config

ORDEN_BASE = "ken-burns-actual"


def _e(x) -> str:
    return html.escape("" if x is None else str(x))


def _usd(x) -> str:
    return "—" if x is None else f"${x:.3f}"


def _tarjeta(c: dict) -> str:
    titulo = "Ken Burns actual" if c.get("linea_base") else c["modelo"]
    sub = "CrearVideos hoy · gratis" if c.get("linea_base") else c.get("modelo_id", "")
    if not c.get("exito"):
        return (
            f'<figure class="clip fallo"><div class="hueco">Falló</div>'
            f'<figcaption><b>{_e(titulo)}</b><span class="id">{_e(sub)}</span>'
            f'<p class="motivo">{_e(c.get("motivo"))}</p>'
            f'<dl><dt>Tiempo</dt><dd>{_e(c.get("tiempo_generacion_s"))} s</dd></dl>'
            f"</figcaption></figure>"
        )
    indice = c.get("indice_movimiento_local")
    nivel = "" if indice is None else ("alto" if indice >= 0.5 else "medio" if indice >= 0.3 else "bajo")
    costo = c.get("costo_real_usd")
    costo_txt = (f"{_usd(costo)} <small>API</small>" if costo is not None and not c.get("linea_base")
                 else _usd(c.get("costo_facturable_usd")) + (" <small>tarifa</small>" if not c.get("linea_base") else ""))
    return (
        f'<figure class="clip">'
        f'<video src="{_e(c["archivo"])}" autoplay muted loop playsinline controls preload="metadata"></video>'
        f'<figcaption><b>{_e(titulo)}</b><span class="id">{_e(sub)}</span>'
        f'<div class="indice {nivel}" title="Residuo del flujo tras quitar zoom/paneo, sobre el flujo total">'
        f'movimiento local <strong>{_e(indice if indice is not None else "—")}</strong></div>'
        f"<dl>"
        f'<dt>Real</dt><dd>{_e(c.get("resolucion_real"))} · {_e(c.get("duracion_real_s"))} s · {_e(c.get("fps"))} fps</dd>'
        f'<dt>Generación</dt><dd>{_e(c["tiempo_generacion_s"]) + " s" if c.get("tiempo_generacion_s") else "—"}</dd>'
        f'<dt>Costo</dt><dd>{costo_txt}</dd>'
        f'<dt>USD/s</dt><dd>{_usd(c.get("usd_por_segundo"))}</dd>'
        f'<dt>Flujo</dt><dd>{_e(c.get("flujo_px_s"))} px/s</dd>'
        f"</dl>"
        + (f'<p class="aviso">reusado de {_e(c["reusado_de"])}</p>' if c.get("reusado_de") else "")
        + "</figcaption></figure>"
    )


def _resumen(clips: list[dict]) -> str:
    por_modelo: dict[str, list[dict]] = {}
    for c in clips:
        por_modelo.setdefault(c["modelo"], []).append(c)
    filas = []
    for modelo, cs in sorted(por_modelo.items(), key=lambda kv: (kv[0] != ORDEN_BASE, kv[0])):
        ok = [c for c in cs if c.get("exito")]
        segs = sum(c.get("duracion_real_s") or 0 for c in ok)
        fact = sum(c.get("costo_facturable_usd") or 0 for c in ok)
        reales = [c["costo_real_usd"] for c in ok if c.get("costo_real_usd") is not None]
        idx = [c["indice_movimiento_local"] for c in ok if c.get("indice_movimiento_local") is not None]
        tiempos = [c["tiempo_generacion_s"] for c in ok if c.get("tiempo_generacion_s")]
        filas.append(
            f"<tr><th>{_e(modelo)}</th><td>{len(ok)}</td><td class='{ 'mal' if len(cs) - len(ok) else ''}'>{len(cs) - len(ok)}</td>"
            f"<td>{_usd(sum(c.get('costo_estimado_usd') or 0 for c in ok))}</td>"
            f"<td>{_usd(sum(reales)) if reales else '—'}</td>"
            f"<td>{f'${fact / segs:.3f}' if segs else '—'}</td>"
            f"<td>{f'{sum(idx) / len(idx):.2f}' if idx else '—'}</td>"
            f"<td>{f'{sum(tiempos) / len(tiempos):.0f} s' if tiempos else '—'}</td></tr>"
        )
    return (
        "<div class='tabla'><table><thead><tr><th>Modelo</th><th>OK</th><th>Fallos</th>"
        "<th>Estimado</th><th>Real (API)</th><th>USD/s real</th><th>Índice medio</th>"
        "<th>Tiempo medio</th></tr></thead><tbody>" + "".join(filas) + "</tbody></table></div>"
    )


def escribir_html(carpeta: Path, datos: dict, entradas: dict[str, dict]) -> Path:
    clips = datos.get("clips", [])
    por_imagen: dict[str, list[dict]] = {}
    for c in clips:
        por_imagen.setdefault(c["imagen_id"], []).append(c)

    secciones = []
    for imagen_id in sorted(por_imagen):
        ent = entradas.get(imagen_id, {})
        miniatura = f"entrada_{imagen_id}.jpg"
        origen = config.RAIZ / ent.get("archivo", "")
        if origen.is_file() and not (carpeta / miniatura).is_file():
            shutil.copy2(origen, carpeta / miniatura)
        cs = sorted(por_imagen[imagen_id],
                    key=lambda c: (not c.get("linea_base"), c["modelo"]))
        secciones.append(
            f"<section><h2>{_e(imagen_id)}</h2>"
            f"<p class='narr'>{_e(ent.get('narracion'))}</p>"
            f"<p class='prompt'><b>Prompt</b> {_e(ent.get('prompt'))}</p>"
            f"<div class='fila'><figure class='clip entrada'><img src='{_e(miniatura)}' alt='imagen de entrada {_e(imagen_id)}'>"
            f"<figcaption><b>Entrada</b><span class='id'>primer fotograma</span></figcaption></figure>"
            + "".join(_tarjeta(c) for c in cs)
            + "</div></section>"
        )

    pagina = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Laboratorio de video</title>
<style>
:root {{ --bg:#f6f5f2; --panel:#fff; --ink:#1d1c1a; --muted:#6b6862; --line:#e3e0da;
  --alto:#1f7a4d; --medio:#9a6a00; --bajo:#a33a2b; --acento:#2f5bd3; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg:#141413; --panel:#1e1d1b; --ink:#ecebe7; --muted:#a29f98; --line:#33312d;
  --alto:#5fcf95; --medio:#e5b54a; --bajo:#f08a7a; --acento:#8fa9ff; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }}
main {{ max-width:1500px; margin:0 auto; padding:24px 16px 64px; }}
h1 {{ margin:0 0 4px; font-size:24px; }}
h2 {{ margin:0 0 4px; font-size:18px; }}
.sub {{ color:var(--muted); margin:0 0 20px; }}
.tabla {{ overflow-x:auto; margin-bottom:28px; }}
table {{ border-collapse:collapse; width:100%; background:var(--panel); border:1px solid var(--line); }}
th,td {{ padding:8px 12px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap;
  font-variant-numeric:tabular-nums; }}
th:first-child {{ text-align:left; }}
thead th {{ color:var(--muted); font-weight:600; font-size:13px; }}
td.mal {{ color:var(--bajo); font-weight:600; }}
section {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:16px;
  margin-bottom:20px; }}
.narr {{ margin:0 0 4px; font-style:italic; }}
.prompt {{ margin:0 0 14px; color:var(--muted); font-size:13px; }}
.fila {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(200px, 1fr)); gap:14px; }}
.clip {{ margin:0; min-width:0; }}
.clip video, .clip img, .hueco {{ width:100%; aspect-ratio:9/16; object-fit:contain; background:#000;
  border-radius:6px; display:block; }}
.hueco {{ display:flex; align-items:center; justify-content:center; color:var(--bajo); font-weight:600;
  background:repeating-linear-gradient(45deg, transparent 0 8px, var(--line) 8px 9px); }}
figcaption {{ padding-top:8px; font-size:13px; }}
figcaption b {{ display:block; font-size:14px; }}
.id {{ color:var(--muted); font-size:12px; word-break:break-all; }}
.indice {{ margin:6px 0; padding:4px 8px; border-radius:4px; border:1px solid var(--line); }}
.indice strong {{ font-size:16px; font-variant-numeric:tabular-nums; }}
.indice.alto strong {{ color:var(--alto); }} .indice.medio strong {{ color:var(--medio); }}
.indice.bajo strong {{ color:var(--bajo); }}
dl {{ display:grid; grid-template-columns:auto 1fr; gap:2px 8px; margin:6px 0 0; }}
dt {{ color:var(--muted); }} dd {{ margin:0; font-variant-numeric:tabular-nums; }}
.motivo {{ color:var(--bajo); margin:6px 0; word-break:break-word; }}
.aviso {{ color:var(--muted); font-size:12px; margin:4px 0 0; }}
.leyenda {{ color:var(--muted); font-size:13px; margin:-12px 0 24px; }}
</style></head>
<body><main>
<h1>Laboratorio de imagen a video</h1>
<p class="sub">{_e(carpeta.name)} · {len(clips)} clips · actualizado {_e(datos.get("actualizado", ""))}</p>
{_resumen(clips)}
<p class="leyenda">Índice de movimiento local: residuo del flujo óptico tras quitar el zoom/paneo
global, sobre el flujo total. ~0,1–0,3 = solo cámara (Ken Burns); 0,5 o más = la escena se mueve.
"Real (API)" solo existe cuando la API informa uso (Omni, por tokens); en Veo el costo es la
tarifa publicada × segundos, que es lo que Google cobra si el video se genera.</p>
{"".join(secciones)}
</main></body></html>
"""
    salida = carpeta / "comparar.html"
    salida.write_text(pagina, encoding="utf-8")
    return salida
