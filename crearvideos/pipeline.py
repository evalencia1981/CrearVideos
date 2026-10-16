"""El pipeline completo: guion de texto -> MP4 listo para publicar.

Orden de las etapas y por qué:

    guion -> escenas -> voz (da la duración real de cada escena)
                     -> imágenes (en paralelo con nada; solo cuesta dinero)
                     -> línea de tiempo -> subtítulos -> montaje

La voz va antes que el montaje porque es ella la que decide cuánto dura cada
escena. Nadie adivina la duración: se mide el audio generado.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from . import compose, director, ffmpeg, voicefx
from .config import FPS, OUTPUT_DIR, Settings, VoiceSettings
from .models import Project, Scene, Word
from .providers import get_image_provider, get_tts_provider
from .providers.images.base import ImageProvider
from .providers.tts.base import TTSProvider
from .script import load, parse
from .subtitles import write_ass


def _quantize(seconds: float) -> float:
    """Redondea una duración hacia arriba hasta el frame exacto más cercano.

    Audio y video se cortan en la misma rejilla. Si no, el clip (que solo
    puede durar un número entero de frames) y su narración difieren hasta
    33 ms por escena, y el error se acumula a lo largo del video.
    """
    return math.ceil(seconds * FPS) / FPS


#: Dónde se guarda la narración ya hecha: los tiempos que el audio no contiene.
NARRACION_CACHE = "narracion.json"


def _clave_narracion(project: Project, settings: Settings, voice_filter: str | None) -> str:
    """Huella de la voz: si algo de esto cambia, hay que volver a narrar.

    Entra todo lo que altera el audio o su duración —voz, ritmo, tono, efecto,
    respiro entre escenas— y el texto de cada escena. No entra nada visual: la
    narración es la misma se monte con imágenes generadas o con las tuyas.
    """
    v = settings.voice
    h = hashlib.sha256()
    for parte in (v.name, v.rate, v.pitch, v.volume, voice_filter or "", f"{settings.scene_gap}"):
        h.update(parte.encode())
        h.update(b"\x00")
    for scene in project.scenes:
        h.update(scene.text.encode())
        h.update(b"\x01")
    return h.hexdigest()[:16]


def firma_escena(
    texto: str, voz: VoiceSettings, voice_filter: str | None, gap: float
) -> str:
    """Huella de UNA escena: su texto con esa voz exacta.

    Es lo que permite regenerar solo lo que cambió. Con una huella por video
    (como antes), tocar la voz de una escena obligaba a volver a sintetizar el
    guion entero; con una por escena, se rehace esa y ya.
    """
    h = hashlib.sha256()
    for parte in (voz.name, voz.rate, voz.pitch, voz.volume, voice_filter or "", f"{gap}"):
        h.update(parte.encode())
        h.update(b"\x00")
    h.update(texto.encode())
    return h.hexdigest()[:16]


def firma_fuente(texto: str, fuente: dict, settings: Settings) -> str:
    """Huella de una escena venga su voz de donde venga.

    Es la misma idea para los tres orígenes: si cambia algo que altere el
    audio, cambia la huella y esa escena —solo esa— se vuelve a hacer.
    """
    tipo = fuente.get("tipo", "generada")

    if tipo == "propia":
        archivo = Path(fuente.get("archivo") or "")
        try:
            sello = f"{archivo.resolve()}:{int(archivo.stat().st_mtime)}"
        except OSError:
            sello = str(archivo)  # no existe: la huella no casará y se avisará
        return firma_escena(texto, VoiceSettings(name=f"propia:{sello}"), None, settings.scene_gap)

    if tipo == "clonada":
        from . import clonvoz

        marca = clonvoz.marca(Path(fuente.get("muestra") or ""), "es", 0.5, 0.5)
        return firma_escena(texto, VoiceSettings(name=marca), None, settings.scene_gap)

    ajustes = VoiceSettings(
        name=fuente.get("voz") or settings.voice.name,
        rate=settings.voice.rate, pitch=settings.voice.pitch, volume=settings.voice.volume,
    )
    return firma_escena(
        texto, ajustes, voicefx.resolve(fuente.get("fx", "none"), None), settings.scene_gap
    )


def _fuente(valor) -> dict:
    """Acepta tanto ('voz', 'fx') como el diccionario completo."""
    if isinstance(valor, dict):
        return valor
    voz, fx = valor
    return {"tipo": "generada", "voz": voz, "fx": fx, "archivo": "", "muestra": ""}


def narrar_escenas(
    project: Project,
    settings: Settings,
    asignaciones: dict,
    *,
    solo: list[int] | None = None,
    rehacer: bool = False,
    on_progress=None,
) -> int:
    """Narra cada escena con SU voz, saltando las que ya están al día.

    `asignaciones` es {índice: (voz, efecto)}. Devuelve cuántas se generaron.

    Los tiempos se recalculan siempre al final, aunque no se genere nada: si
    una escena cambió de duración, todas las siguientes se corren, y con ellas
    los subtítulos.
    """
    from .providers.tts.edge import EdgeTTS

    project.audio_dir.mkdir(parents=True, exist_ok=True)
    guardadas = {}
    try:
        previo = json.loads((project.root / NARRACION_CACHE).read_text(encoding="utf-8"))
        guardadas = {i: e for i, e in enumerate(previo.get("escenas") or [])}
    except (OSError, json.JSONDecodeError):
        pass

    motores: dict[str, EdgeTTS] = {}
    generadas = 0

    pendientes_clon: list[Scene] = []

    for scene in project.scenes:
        fuente_voz = _fuente(
            asignaciones.get(scene.index, (settings.voice.name, settings.voice_fx))
        )
        tipo = fuente_voz.get("tipo", "generada")
        voz = fuente_voz.get("voz") or settings.voice.name
        ajustes = VoiceSettings(
            name=voz, rate=settings.voice.rate, pitch=settings.voice.pitch,
            volume=settings.voice.volume,
        )
        filtro = voicefx.resolve(fuente_voz.get("fx", "none"), None)
        firma = firma_fuente(scene.text, fuente_voz, settings)

        guardada = guardadas.get(scene.index) or {}
        final = project.audio_dir / f"escena_{scene.index:03d}.wav"
        al_dia = (
            guardada.get("firma") == firma
            and guardada.get("texto") == scene.text
            and final.is_file()
        )
        toca = (solo is None or scene.index in solo) and (rehacer or not al_dia)

        if al_dia and not toca:
            # Las palabras se guardan RELATIVAS al inicio de su escena, así que
            # recuperarlas es leerlas tal cual; se colocan en la línea de
            # tiempo al final, cuando ya se sabe dónde empieza cada escena.
            scene.audio_path = final
            scene.duration = float(guardada["duration"])
            scene.words = [Word(t, float(s), float(d)) for t, s, d in guardada.get("words", [])]
            scene._firma = firma  # type: ignore[attr-defined]
            continue

        if not toca:
            # Ni está al día ni toca generarla ahora (se pidió solo otra): se
            # deja sin audio y quien monte el video lo notará antes de empezar.
            scene.audio_path = None
            scene.duration = 0.0
            scene.words = []
            scene._firma = ""  # type: ignore[attr-defined]
            continue

        if tipo == "clonada":
            # Clonar carga un modelo de varios gigas: se juntan todas las
            # escenas clonadas y se hace UNA sola pasada al final.
            scene._fuente = fuente_voz   # type: ignore[attr-defined]
            scene._firma = firma         # type: ignore[attr-defined]
            pendientes_clon.append(scene)
            continue

        if tipo == "propia":
            from . import vozpropia

            origen = Path(fuente_voz.get("archivo") or "")
            if not origen.is_file():
                raise vozpropia.VozPropiaError(
                    f"La escena {scene.index + 1} usa tu grabación, pero no encuentro el "
                    f"archivo: {origen or '(ninguno)'}"
                )
            if on_progress:
                on_progress(
                    f"Escena {scene.index + 1}/{len(project.scenes)} · tu grabación "
                    f"({origen.name})"
                )
            objetivo = vozpropia._preparar(origen, final, settings)
            scene.audio_path = final
            scene.duration = objetivo
            # Un audio tuyo no trae tiempos de palabra: se reparten dentro de
            # la escena por el largo de cada una.
            scene.words = vozpropia._palabras(scene.text, 0.0, objetivo - settings.scene_gap)
            scene._firma = firma  # type: ignore[attr-defined]
            generadas += 1
            continue

        if on_progress:
            on_progress(f"Narrando escena {scene.index + 1}/{len(project.scenes)} · {voz}")

        if voz not in motores:
            motores[voz] = EdgeTTS(ajustes)
        raw = project.audio_dir / f"raw_{scene.index:03d}.mp3"
        _, words = motores[voz].synthesize(scene.text, raw)

        fuente = raw
        if filtro:
            fuente = project.audio_dir / f"fx_{scene.index:03d}.wav"
            ffmpeg.apply_filter(raw, fuente, filtro)

        real = ffmpeg.duration(fuente)
        objetivo = _quantize(max(real, words[-1].end if words else 0.0) + settings.scene_gap)
        ffmpeg.pad_to_exact(fuente, final, objetivo)

        scene.audio_path = final
        scene.duration = objetivo
        scene.words = words           # se desplazan abajo, al fijar la línea de tiempo
        scene._firma = firma          # type: ignore[attr-defined]
        generadas += 1

    if pendientes_clon:
        generadas += _clonar_escenas(project, settings, pendientes_clon, on_progress)

    # Línea de tiempo: cada escena empieza donde termina la anterior. Hasta
    # aquí las palabras de todas las escenas van desde cero; ahora se desplazan
    # de golpe, que es lo que mantiene los subtítulos cuadrados aunque una
    # escena del medio haya cambiado de duración.
    cursor = 0.0
    for scene in project.scenes:
        if scene.audio_path is None:
            continue
        scene.start = cursor
        cursor += scene.duration

    _guardar_por_escena(project)

    for scene in project.scenes:
        if scene.audio_path is not None and scene.start:
            scene.words = [w.shifted(scene.start) for w in scene.words]
    return generadas


def _clonar_escenas(
    project: Project, settings: Settings, escenas: list[Scene], on_progress=None
) -> int:
    """Clona la voz de varias escenas en una sola pasada del modelo.

    Cargar el modelo tarda más que generar una escena corta, así que juntarlas
    es la diferencia entre un minuto y uno por escena. Se apoya en un proyecto
    temporal con solo esas escenas, y luego se copian sus audios a su sitio.
    """
    from . import clonvoz

    por_muestra: dict[str, list[Scene]] = {}
    for scene in escenas:
        muestra = (getattr(scene, "_fuente", {}) or {}).get("muestra") or ""
        por_muestra.setdefault(muestra, []).append(scene)

    hechas = 0
    for muestra, grupo in por_muestra.items():
        if not muestra:
            raise clonvoz.ClonError(
                f"La escena {grupo[0].index + 1} está marcada como voz clonada, pero no "
                "hay ninguna muestra de voz indicada."
            )
        if on_progress:
            on_progress(f"Clonando tu voz en {len(grupo)} escena(s)")

        temporal = Project(name=project.name, root=project.root, scenes=grupo)
        clonvoz.generar(temporal, Path(muestra), settings, on_progress=on_progress)
        # clonvoz deja cada escena con su audio, duración y palabras; solo falta
        # marcar la huella que ya calculamos para poder reusarlas después.
        for scene in grupo:
            scene.words = [Word(w.text, w.start - scene.start, w.duration) for w in scene.words]
            scene.start = 0.0
        hechas += len(grupo)
    return hechas


def _guardar_por_escena(project: Project) -> None:
    """Escribe narracion.json con la huella de cada escena.

    Se llama ANTES de desplazar las palabras: en el archivo van relativas a su
    escena, para que mover una escena no invalide las demás.
    """
    escenas = []
    for scene in project.scenes:
        escenas.append(
            {
                "texto": scene.text,
                "firma": getattr(scene, "_firma", ""),
                "audio": scene.audio_path.name if scene.audio_path else "",
                "start": scene.start,
                "duration": scene.duration,
                "words": [[w.text, w.start, w.duration] for w in scene.words],
            }
        )
    try:
        (project.root / NARRACION_CACHE).write_text(
            json.dumps({"clave": "por-escena", "escenas": escenas}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def guardar_narracion(project: Project, settings: Settings, voice_filter: str | None) -> None:
    """Deja los tiempos por palabra junto a los WAV, para poder reusarlos.

    El audio por sí solo no basta: los subtítulos necesitan cuándo empieza cada
    palabra, y eso solo lo sabe el motor de voz en el momento de sintetizar.
    """
    firmas = {
        s.index: firma_escena(s.text, settings.voice, voice_filter, settings.scene_gap)
        for s in project.scenes
    }
    guardar_firmas(project, firmas)


def guardar_firmas(project: Project, firmas: dict[int, str]) -> None:
    """Guarda lo generado poniéndole a cada escena la huella que le toca.

    Lo usa la voz clonada, que no pasa por edge-tts pero deja el mismo rastro:
    así el montaje la reutiliza igual que cualquier otra narración.
    """
    originales = [list(s.words) for s in project.scenes]
    for scene in project.scenes:
        scene._firma = firmas.get(scene.index, "")  # type: ignore[attr-defined]
        # En el archivo las palabras van relativas a su escena; aquí llegan ya
        # desplazadas a la línea de tiempo, así que se devuelven a cero.
        scene.words = [Word(w.text, w.start - scene.start, w.duration) for w in scene.words]
    _guardar_por_escena(project)
    for scene, palabras in zip(project.scenes, originales):
        scene.words = palabras


def reusar_narracion(
    project: Project, settings: Settings, voice_filter: str | None = None
) -> bool:
    """Reusa la narración hecha con UNA sola voz (la del proyecto o un clon)."""
    if isinstance(voice_filter, dict):
        return restaurar_narracion(project, voice_filter)
    firmas = {
        s.index: firma_escena(s.text, settings.voice, voice_filter, settings.scene_gap)
        for s in project.scenes
    }
    return restaurar_narracion(project, firmas)


def restaurar_narracion(project: Project, esperadas: dict[int, str]) -> bool:
    """Recupera la narración ya hecha si TODAS las escenas están al día.

    `esperadas` es la huella que debería tener cada escena. Si alguna no
    coincide —cambió el texto, la voz o el efecto— devuelve False, y quien
    llama decide si regenera todo o solo lo que falta.
    """
    try:
        datos = json.loads((project.root / NARRACION_CACHE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    guardadas = datos.get("escenas") or []
    if len(guardadas) != len(project.scenes):
        return False

    preparadas = []
    for scene, g in zip(project.scenes, guardadas):
        audio = project.audio_dir / (g.get("audio") or "")
        if g.get("texto") != scene.text or not audio.is_file():
            return False
        if g.get("firma") != esperadas.get(scene.index):
            return False
        preparadas.append((scene, g, audio))

    cursor = 0.0
    for scene, g, audio in preparadas:
        scene.audio_path = audio
        scene.duration = float(g["duration"])
        scene.start = cursor
        scene.words = [Word(t, float(s) + cursor, float(d)) for t, s, d in g.get("words", [])]
        scene._firma = g.get("firma", "")  # type: ignore[attr-defined]
        cursor += scene.duration
    return True


def asignaciones_de(project: Project, settings: Settings) -> dict:
    """Qué voz lleva cada escena: la elegida a mano, o la del proyecto."""
    from . import voces as voces_lib

    elegidas = voces_lib.cargar(project)
    return {s.index: voces_lib.voz_de(elegidas, s.index, settings) for s in project.scenes}


def firmas_de(project: Project, settings: Settings, asignaciones: dict) -> dict[int, str]:
    """La huella que debería tener cada escena con esas fuentes de voz."""
    return {
        scene.index: firma_fuente(
            scene.text,
            _fuente(asignaciones.get(scene.index, (settings.voice.name, settings.voice_fx))),
            settings,
        )
        for scene in project.scenes
    }


def narrate(
    project: Project,
    tts: TTSProvider,
    settings: Settings,
    on_progress=None,
    *,
    voice_filter: str | None = None,
    reuse: bool = False,
) -> bool:
    """Genera la voz de cada escena y fija la línea de tiempo del video.

    `voice_filter` es una cadena de filtros ffmpeg (ver voicefx.py) que da
    carácter a la voz: grave, cavernosa, fantasmal. Se aplica DESPUÉS de
    sintetizar, así que no mueve los tiempos de palabra que reporta el motor;
    solo cambia el timbre y, si lleva reverb, añade una cola al final que se
    mide y se respeta como cualquier otro audio.

    Con `reuse=True` se aprovecha la narración que ya esté en la carpeta del
    proyecto, si se narró lo mismo con la misma voz. Devuelve True si la reusó.
    """
    if reuse and reusar_narracion(project, settings, voice_filter):
        if on_progress:
            on_progress("Reusando la narración ya generada")
        return True

    cursor = 0.0

    for scene in project.scenes:
        if on_progress:
            on_progress(f"Narrando escena {scene.index + 1}/{len(project.scenes)}")

        raw = project.audio_dir / f"raw_{scene.index:03d}.mp3"
        _, words = tts.synthesize(scene.text, raw)

        # Efecto de voz opcional. El audio con efecto pasa a ser la fuente para
        # medir y rellenar; sin efecto se usa el crudo directamente.
        fuente = raw
        if voice_filter:
            fuente = project.audio_dir / f"fx_{scene.index:03d}.wav"
            ffmpeg.apply_filter(raw, fuente, voice_filter)

        # La duración la manda el archivo, no el motor de voz: edge-tts
        # reporta el fin de la última palabra, pero el audio suele traer algo
        # de silencio (o cola de reverb) final que también ocupa tiempo.
        real = ffmpeg.duration(fuente)
        target = _quantize(max(real, words[-1].end if words else 0.0) + settings.scene_gap)

        final = project.audio_dir / f"escena_{scene.index:03d}.wav"
        ffmpeg.pad_to_exact(fuente, final, target)

        scene.audio_path = final
        scene.duration = target
        scene.start = cursor
        scene.words = [w.shifted(cursor) for w in words]
        cursor += target

    guardar_narracion(project, settings, voice_filter)
    return False


def narrar_o_reusar(
    project: Project,
    settings: Settings,
    *,
    voice_filter: str | None = None,
    reuse: bool = True,
    on_progress=None,
) -> bool:
    """Narra, o aprovecha la narración anterior del proyecto. True si la reusó.

    Es lo que usan los módulos de audio y montaje: cuando ya escuchaste la voz
    y solo estás cambiando las imágenes, el formato o la música, no tiene
    sentido volver a sintetizar (ni a esperar) lo mismo.

    Si el proyecto tiene voces elegidas escena por escena (pestaña Voces), se
    respetan: el video suena como lo dejaste ahí, y solo se generan las escenas
    que falten o hayan cambiado.
    """
    asignaciones = asignaciones_de(project, settings)
    fuentes = [_fuente(v) for v in asignaciones.values()]
    # "Personalizado" es todo lo que no sea una única voz generada para todo:
    # varias voces, un efecto distinto, o escenas con tu grabación o tu clon.
    personalizadas = (
        len({f.get("voz", "") for f in fuentes}) > 1
        or any(f.get("tipo") != "generada" for f in fuentes)
        or any(f.get("fx", "none") != settings.voice_fx for f in fuentes)
    )
    if personalizadas:
        firmas = firmas_de(project, settings, asignaciones)
        if reuse and restaurar_narracion(project, firmas):
            if on_progress:
                on_progress("Reusando las voces que elegiste por escena")
            return True
        narrar_escenas(
            project, settings, asignaciones, rehacer=not reuse, on_progress=on_progress
        )
        return False

    if reuse and reusar_narracion(project, settings, voice_filter):
        if on_progress:
            on_progress("Reusando la narración ya generada")
        return True

    tts = get_tts_provider(settings)
    tts.preflight()
    narrate(project, tts, settings, on_progress, voice_filter=voice_filter)
    return False


def _fingerprint(provider_name: str, extra: str, prompt: str) -> str:
    """Huella de cómo se generó una imagen: proveedor + variante (modelo, imagen
    de referencia) + prompt (que ya lleva el estilo). Si algo de esto cambia, la
    imagen guardada ya no corresponde."""
    return hashlib.sha256(f"{provider_name}|{extra}|{prompt}".encode()).hexdigest()[:16]


def illustrate(project: Project, provider: ImageProvider, on_progress=None) -> float:
    """Genera la imagen de cada escena. Devuelve el costo aproximado en USD.

    Reutiliza una imagen ya generada solo si su huella coincide: mismo
    proveedor, mismo estilo y mismo texto de escena. Así, repetir el montaje no
    re-paga a Gemini; pero cambiar de proveedor, de estilo o editar el guion
    regenera lo que haga falta, sin dejar imágenes viejas coladas en el video.
    """
    marca = project.image_dir / "_huellas.json"
    try:
        previas = json.loads(marca.read_text(encoding="utf-8")) if marca.exists() else {}
    except (json.JSONDecodeError, OSError):
        previas = {}

    huellas: dict[str, str] = {}
    generadas = 0
    for scene in project.scenes:
        path = project.image_dir / f"escena_{scene.index:03d}.jpg"
        clave = str(scene.index)
        fp = _fingerprint(provider.name, provider.fingerprint_extra, scene.image_prompt)

        if path.exists() and previas.get(clave) == fp:
            scene.image_path = path
            huellas[clave] = fp
            continue

        if on_progress:
            on_progress(f"Ilustrando escena {scene.index + 1}/{len(project.scenes)}")
        provider.generate(
            scene.image_prompt, path, index=scene.index, subject=scene.image_subject
        )
        generadas += 1
        scene.image_path = path
        huellas[clave] = fp

    marca.write_text(json.dumps(huellas), encoding="utf-8")
    # Solo se cobra lo que de verdad se generó en esta corrida.
    return provider.cost_per_image * generadas


def create(
    script_source: str | Path,
    name: str,
    settings: Settings,
    *,
    style: str | None = None,
    style_key: str | None = None,
    direct: bool = True,
    music: Path | None = None,
    portada: Path | None = None,
    portada_seconds: float = 2.0,
    reference: Path | None = None,
    references: list[Path] | None = None,
    voz_propia: Path | None = None,
    voz_clon: Path | None = None,
    clon_idioma: str = "es",
    output_dir: Path | None = None,
    on_progress=None,
) -> tuple[Project, float]:
    """Ejecuta el pipeline entero. Devuelve (proyecto, costo estimado USD).

    `style` es el sufijo de prompt (texto largo para la IA de imagen); `style_key`
    es la clave del preset ('terror', 'anime'…), que es lo que el efecto de voz
    'auto' necesita para decidir. Van separados a propósito.

    `portada` es una imagen que se antepone al video como fotograma de apertura
    (para elegirla de portada en TikTok) y se guarda además como `portada.jpg`
    1080x1920 para subir de miniatura en YouTube.

    `voz_propia` (tu narración ya grabada) y `voz_clon` (una muestra de tu voz)
    sustituyen a la voz generada, igual que en el montaje. Son excluyentes.
    """
    if voz_propia is not None and voz_clon is not None:
        raise ValueError("Elige una sola cosa: o usas tu narración grabada, o clonas tu voz.")
    if isinstance(script_source, Path):
        scenes, meta = load(script_source, settings.scene, style)
    else:
        scenes, meta = parse(script_source, settings.scene, style)

    # Las imágenes de referencia (coherencia de personaje) se fijan antes de
    # construir el proveedor, que las lee de los ajustes.
    refs = [r for r in (references or ([reference] if reference else [])) if r]
    if refs:
        settings.reference_images = [str(r) for r in refs]

    # Los proveedores se construyen y validan antes de generar nada: un
    # nombre mal escrito o una clave que falta debe fallar en el primer
    # segundo, no después de narrar el guion entero.
    tts = get_tts_provider(settings)
    images = get_image_provider(settings)
    tts.preflight()
    images.preflight()

    root = (output_dir or OUTPUT_DIR) / name
    project = Project(name=name, root=root, scenes=scenes)
    project.ensure_dirs()

    # Director de prompts: reescribe cada image_prompt en una descripción visual
    # rica y coherente con IA de texto. Solo para proveedores generativos; a
    # Pexels/placeholder no les sirve. Si falla, se queda el prompt simple.
    #
    # El modo referencia YA NO lo desactiva. Antes sí, porque la edición iba
    # siempre por kontext, cuya moderación rechaza las palabras de estilo. Los
    # modelos que aceptan varias referencias (flux-klein, gpt-image) tragan el
    # prompt dirigido entero, así que se pueden tener las dos cosas a la vez:
    # la referencia fija la cara del personaje y el director aporta la biblia
    # visual. Con kontext el proveedor sigue mandando solo el texto plano.
    if direct and images.name in director.GENERATIVE:
        director.enrich(
            project.scenes,
            style,
            project.image_dir / "_prompts.json",
            on_progress,
            brief=meta.as_brief(),
        )

    # La voz decide la duración de cada escena, venga de donde venga: tu
    # grabación (no se sintetiza nada), tu voz clonada (se reusa si ya se
    # clonó lo mismo, porque tarda minutos) o la voz generada de siempre.
    if voz_propia is not None:
        from . import vozpropia

        vozpropia.aplicar(project, voz_propia, settings, on_progress)
    elif voz_clon is not None:
        from . import clonvoz

        firma = clonvoz.marca(voz_clon, clon_idioma, 0.5, 0.5)
        if reusar_narracion(project, settings, firma):
            if on_progress:
                on_progress("Reusando tu voz clonada")
        else:
            clonvoz.generar(
                project, voz_clon, settings, idioma=clon_idioma, on_progress=on_progress
            )
            guardar_narracion(project, settings, firma)
    else:
        voice_filter = voicefx.resolve(settings.voice_fx, style_key)
        narrate(project, tts, settings, on_progress, voice_filter=voice_filter)
    cost = illustrate(project, images, on_progress)

    if on_progress:
        on_progress("Generando subtítulos")
    write_ass(project.all_words(), project.subtitle_path, settings.subtitle)

    compose.build_video(project, settings, music=music, on_progress=on_progress)

    if portada is not None:
        if on_progress:
            on_progress("Añadiendo portada")
        # Miniatura suelta para YouTube y, con ella, el fotograma de apertura.
        normal = project.root / "portada.jpg"
        compose.normalize_cover(portada, normal)
        con_portada = project.output_path.with_name(project.output_path.stem + "_cov.mp4")
        compose.add_cover(project.output_path, normal, con_portada, seconds=portada_seconds)
        con_portada.replace(project.output_path)

    return project, cost
