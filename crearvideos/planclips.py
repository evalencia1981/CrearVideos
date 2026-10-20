"""Planear clips de duración fija (8 s de Veo / Google Flow) con la voz real.

Contar palabras no basta para saber cuántos clips hay que generar: cada voz
tiene su ritmo, las comas de una enumeración son pausas, y las marcas
`(pausa)` añaden silencio. Con un guion de 20 párrafos de ~21 palabras,
la regla de "21 palabras = 8 s" prometía 20 clips y la voz real dio 3:06, con
18 párrafos por encima de 8 s: el clip se habría repetido en casi todas las
escenas.

Aquí se narra de verdad y se parte en escenas que duren COMO MÁXIMO
`max_seg`, cortando siempre al final de una oración (o en una coma si una
oración sola no cabe), nunca a media frase:

1. Los `---` del guion, si los hay, son fronteras que no se cruzan. Los
   párrafos no: con clips de 8 s, un párrafo de 9 s no cabe igual.
2. Cada oración se narra por separado para conocer su duración, y una que
   sola ya no cabe se corta en sus comas.
3. Las escenas se llenan de oraciones seguidas narrando cada unión tal como
   la narrará el montaje: se acepta solo si la duración real cabe.

El resultado es un guion con una escena por clip (separadas por `---`) y su
narración ya guardada en el proyecto con la misma huella que usa el montaje,
así que al montar se reutiliza en vez de sintetizarse otra vez.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import ffmpeg, importar, pausas, voicefx
from .config import FPS, OUTPUT_DIR, Settings
from .models import Project, Scene, Word
from .pipeline import _guardar_por_escena, firma_fuente
from .script import _IMAGE_RE, _SEPARATOR_RE, _extract_meta, _extract_style, _split_sentences

#: Al agrupar oraciones con duraciones medidas por separado, cada unión ahorra
#: el silencio de cola y de entrada de una síntesis suelta (~0,75 s) pero
#: añade la pausa natural entre oraciones (~0,4 s). La estimación solo decide
#: qué probar: la duración que vale es la que se mide después.
AHORRO_POR_UNION = 0.35
#: Cuánto puede pasarse la estimación y aun así probarse con la voz real: la
#: estimación es aproximada y no debe descartar uniones que sí caben.
HOLGURA = 0.8

_CLAUSULA_RE = re.compile(r"(?<=[,;:])\s+")


class PlanError(RuntimeError):
    """No se pudo planear (guion vacío, voz que no responde…)."""


@dataclass
class EscenaPlan:
    texto: str
    duracion: float
    #: True si hubo que cortar una oración (en una coma o, en último caso,
    #: entre palabras) porque sola ya duraba más que el máximo.
    corte_forzado: bool = False


@dataclass
class Plan:
    nombre: str
    escenas: list[EscenaPlan]
    max_seg: float
    voz: str
    carpeta: Path
    avisos: list[str] = field(default_factory=list)

    @property
    def total(self) -> float:
        return sum(e.duracion for e in self.escenas)

    @property
    def guion(self) -> str:
        """El guion listo para el montaje: una escena por clip."""
        return "\n\n---\n\n".join(e.texto for e in self.escenas)


def _bloques(raw: str) -> list[str]:
    """Los trozos del guion que nunca se mezclan entre sí."""
    raw = importar.convertir(raw)
    _, cuerpo = _extract_style(raw)
    _, cuerpo = _extract_meta(cuerpo)
    # Las indicaciones de imagen no se narran; en un plan de clips no pintan.
    cuerpo = _IMAGE_RE.sub(" ", cuerpo)
    # Solo los `---` son frontera fija: son una decisión explícita del autor.
    # Los párrafos NO: un párrafo de 9 s no cabe en un clip de 8 de todos
    # modos, y obligar a cortar en cada uno dejaba escenas de 4 s (el doble
    # de clips). Las oraciones se agrupan de corrido; la línea en blanco
    # solo vuelve a ser corte natural porque siempre cae al final de una.
    bloques = [b.strip() for b in _SEPARATOR_RE.split(cuerpo) if b.strip()]
    return [" ".join(b.split()) for b in bloques if pausas.limpiar(b)]


class _Medidor:
    """Narra textos como el montaje y recuerda lo ya medido."""

    def __init__(self, settings: Settings, tmp: Path, on_progress=None):
        from .providers.tts.edge import EdgeTTS

        self.settings = settings
        self.tts = EdgeTTS(settings.voice)
        self.filtro = voicefx.resolve(settings.voice_fx, None)
        self.tmp = tmp
        self.cache: dict[str, tuple[float, Path, list[Word]]] = {}
        self.on_progress = on_progress
        self.llamadas = 0
        self._lock = threading.Lock()

    def precargar(self, textos: list[str], hilos: int = 4) -> None:
        """Mide varios textos a la vez. La voz es un servicio remoto: esperar
        cuatro respuestas en paralelo tarda casi lo mismo que una."""
        pendientes = list(dict.fromkeys(t for t in textos if t not in self.cache))
        with ThreadPoolExecutor(max_workers=hilos) as pool:
            list(pool.map(self.medir, pendientes))

    def medir(self, texto: str) -> float:
        """Duración que tendría la escena en el video (con su respiro final)."""
        if texto not in self.cache:
            with self._lock:
                self.llamadas += 1
                n = self.llamadas
            if self.on_progress and n % 5 == 1:
                self.on_progress(f"Midiendo con la voz real ({n})")
            raw = self.tmp / f"raw_{n:04d}.mp3"
            _, words = self.tts.synthesize(texto, raw)
            fuente = raw
            if self.filtro:
                fuente = self.tmp / f"fx_{n:04d}.wav"
                ffmpeg.apply_filter(raw, fuente, self.filtro)
            real = ffmpeg.duration(fuente)
            # Mismo cálculo que pipeline.narrar_escenas: la duración manda el
            # audio, se suma el respiro y se cuadra al fotograma.
            objetivo = math.ceil((max(real, words[-1].end if words else 0.0)
                                  + self.settings.scene_gap) * FPS) / FPS
            final = self.tmp / f"esc_{n:04d}.wav"
            ffmpeg.pad_to_exact(fuente, final, objetivo)
            self.cache[texto] = (objetivo, final, words)
        return self.cache[texto][0]


def _trozos(oracion: str, medidor: _Medidor, max_seg: float) -> list[tuple[str, bool]]:
    """Una oración en piezas que quepan: entera, por comas, o por palabras."""
    if medidor.medir(oracion) <= max_seg:
        return [(oracion, False)]
    clausulas = [c for c in _CLAUSULA_RE.split(oracion) if c.strip()]
    if len(clausulas) > 1:
        piezas: list[tuple[str, bool]] = []
        for c in _agrupar(clausulas, medidor, max_seg):
            piezas.extend(_trozos(c, medidor, max_seg) if medidor.medir(c) > max_seg
                          else [(c, True)])
        return [(t, True) for t, _ in piezas]
    palabras = oracion.split()
    if len(palabras) < 2:
        return [(oracion, True)]  # una palabra sola no se puede partir más
    mitad = len(palabras) // 2
    return (_trozos(" ".join(palabras[:mitad]), medidor, max_seg)
            + _trozos(" ".join(palabras[mitad:]), medidor, max_seg))


def _agrupar(piezas: list[str], medidor: _Medidor, max_seg: float) -> list[str]:
    """Llena cada escena con piezas seguidas hasta donde quepa, MIDIENDO.

    La estimación solo descarta lo que claramente no cabe (para no narrar en
    balde); lo que podría caber se narra unido y se acepta solo si la
    duración real no pasa del máximo. Así las escenas quedan cerca de los
    8 s en vez de quedarse cortas por prudencia.
    """
    grupos: list[str] = []
    actual = ""
    for p in piezas:
        if not actual:
            actual = p
            continue
        candidato = f"{actual} {p}"
        estimado = medidor.medir(actual) + medidor.medir(p) - AHORRO_POR_UNION
        if estimado <= max_seg + HOLGURA and medidor.medir(candidato) <= max_seg:
            actual = candidato
        else:
            grupos.append(actual)
            actual = p
    if actual:
        grupos.append(actual)
    return grupos


def planear(
    raw: str,
    nombre: str,
    settings: Settings,
    *,
    max_seg: float = 8.0,
    output_dir: Path | None = None,
    on_progress=None,
) -> Plan:
    """Parte el guion en escenas de hasta `max_seg` medidas con la voz real."""
    if max_seg < 2:
        raise PlanError("El máximo por escena debe ser de al menos 2 segundos.")
    bloques = _bloques(raw)
    if not bloques:
        raise PlanError("El guion está vacío: no hay texto que narrar.")

    root = (output_dir or OUTPUT_DIR) / nombre
    root.mkdir(parents=True, exist_ok=True)
    avisos: list[str] = []

    with tempfile.TemporaryDirectory(prefix="plan_") as tmp:
        medidor = _Medidor(settings, Path(tmp), on_progress)
        medidor.precargar([o for b in bloques for o in _split_sentences(b)])
        escenas: list[EscenaPlan] = []
        for bloque in bloques:
            piezas: list[tuple[str, bool]] = []
            for oracion in _split_sentences(bloque):
                piezas.extend(_trozos(oracion, medidor, max_seg))
            forzadas = {t for t, f in piezas if f}
            for texto in _agrupar([t for t, _ in piezas], medidor, max_seg):
                escenas.append(EscenaPlan(
                    texto, medidor.medir(texto),
                    corte_forzado=any(f in texto for f in forzadas),
                ))

        if on_progress:
            on_progress("Guardando la narración para el montaje")
        _guardar_narracion(root, nombre, escenas, medidor, settings)

    largas = [i + 1 for i, e in enumerate(escenas) if e.duracion > max_seg]
    if largas:
        avisos.append(
            f"{len(largas)} escena(s) siguen pasando de {max_seg:g} s porque una sola "
            f"frase ya dura más ({', '.join(map(str, largas[:10]))}). En el montaje el "
            "clip se estirará o repetirá un poco."
        )
    forzadas = [i + 1 for i, e in enumerate(escenas) if e.corte_forzado]
    if forzadas:
        avisos.append(
            f"En {len(forzadas)} escena(s) hubo que cortar una oración larga en una coma "
            f"({', '.join(map(str, forzadas[:10]))}): revisa que el corte suene natural."
        )

    plan = Plan(nombre, escenas, max_seg, settings.voice.name, root, avisos)
    _exportar(plan, settings)
    return plan


def _guardar_narracion(root: Path, nombre: str, escenas: list[EscenaPlan],
                       medidor: _Medidor, settings: Settings) -> None:
    """Deja la narración medida en el proyecto, con la huella del montaje.

    El montaje la encuentra (mismo texto, misma voz, mismo efecto) y la
    reutiliza: no se narra dos veces.
    """
    project = Project(name=nombre, root=root,
                      scenes=[Scene(index=i, text=e.texto, image_prompt="", image_subject="")
                              for i, e in enumerate(escenas)])
    project.audio_dir.mkdir(parents=True, exist_ok=True)
    fuente = {"tipo": "generada", "voz": settings.voice.name, "fx": settings.voice_fx,
              "archivo": "", "muestra": ""}
    for scene, e in zip(project.scenes, escenas):
        duracion, wav, words = medidor.cache[e.texto]
        final = project.audio_dir / f"escena_{scene.index:03d}.wav"
        shutil.copy2(wav, final)
        scene.audio_path = final
        scene.duration = duracion
        scene.words = words
        scene._firma = firma_fuente(scene.text, fuente, settings)  # type: ignore[attr-defined]
    _guardar_por_escena(project)


def _exportar(plan: Plan, settings: Settings) -> None:
    """Archivos del plan: la lista para Flow, el guion para montar y el JSON."""
    lineas = [
        f"Plan de clips de {plan.max_seg:g} s · {len(plan.escenas)} escenas · "
        f"{int(plan.total // 60)}:{int(plan.total % 60):02d} de narración · voz {plan.voz}",
        "",
    ]
    for i, e in enumerate(plan.escenas, 1):
        lineas += [f"Escena {i:03d} · {e.duracion:.1f} s", pausas.limpiar(e.texto), ""]
    (plan.carpeta / "plan_clips.txt").write_text("\n".join(lineas), encoding="utf-8")
    (plan.carpeta / "guion_planeado.txt").write_text(plan.guion, encoding="utf-8")
    (plan.carpeta / "plan_clips.json").write_text(
        json.dumps(
            {
                "max_seg": plan.max_seg,
                "voz": plan.voz,
                "velocidad": settings.voice.rate,
                "efecto": settings.voice_fx,
                "total": round(plan.total, 3),
                "escenas": [
                    {"n": i, "duracion": round(e.duracion, 3), "texto": e.texto,
                     "corte_forzado": e.corte_forzado}
                    for i, e in enumerate(plan.escenas, 1)
                ],
                "avisos": plan.avisos,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
