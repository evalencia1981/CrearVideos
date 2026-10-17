"""Interfaz de línea de comandos."""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config import OUTPUT_DIR, load_settings
from .ffmpeg import FFmpegError
from .pipeline import create
from .script import load_script

app = typer.Typer(
    add_completion=False,
    help="Convierte un guion en un short vertical con narración y subtítulos.",
    no_args_is_help=True,
)
console = Console()


def _slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[\s_-]+", "-", text) or "video"


@app.command("crear")
def crear(
    guion: Path = typer.Argument(..., exists=True, dir_okay=False, help="Archivo .txt con el guion"),
    nombre: str = typer.Option(None, "--nombre", "-n", help="Nombre del proyecto (por defecto, el del archivo)"),
    voz: str = typer.Option(None, "--voz", "-v", help="Voz de edge-tts, p. ej. es-MX-DaliaNeural"),
    imagenes: str = typer.Option(None, "--imagenes", "-i", help="pollinations | pexels | placeholder | gemini | openai"),
    modelo: str = typer.Option(None, "--modelo", help="Modelo de Pollinations: flux-klein | gpt-image-1.5 | flux | MarcosFRG/phoenix-1.0 (ver: modelos)"),
    gratis: bool = typer.Option(False, "--gratis", help="Modo prueba: ignora el token de Pollinations y usa 'sana' gratis (no gasta pollen)"),
    estilo: str = typer.Option(None, "--estilo", "-e", help="Estilo visual: cinematografico, anime, terror… (ver: estilos)"),
    voz_fx: str = typer.Option("auto", "--voz-fx", help="Efecto de voz: auto | none | terror | fantasma | demonio | profunda | eco | radio (ver: efectos)"),
    sin_director: bool = typer.Option(False, "--sin-director", help="No usar IA para enriquecer los prompts de imagen"),
    portada: Path = typer.Option(None, "--portada", exists=True, dir_okay=False, help="Imagen de portada (se antepone al video y se guarda portada.jpg para YouTube)"),
    referencia: list[Path] = typer.Option(None, "--referencia", exists=True, dir_okay=False, help="Foto del personaje: las escenas se generan a partir de ella. Repetible (--referencia a.jpg --referencia b.jpg). Requiere pollinations con token."),
    musica: str = typer.Option("auto", "--musica", "-m", help="auto | no | ruta a una pista (ver: musica)"),
    volumen: float = typer.Option(None, "--volumen", help="Volumen de la música, 0 a 1 (por defecto 0.30)"),
    velocidad: float = typer.Option(1.0, "--velocidad", help="Velocidad del video final: 1.25 más rápido, 0.9 más lento"),
    sin_ducking: bool = typer.Option(False, "--sin-ducking", help="Música a volumen fijo, sin bajarla bajo la voz"),
    voz_propia: Path = typer.Option(None, "--voz-propia", exists=True, help="TU voz ya grabada: un MP3/WAV con toda la narración, o una carpeta con un audio por escena"),
    voz_clon: Path = typer.Option(None, "--voz-clon", exists=True, help="Clonar tu voz: muestra de 15-30 s y genera la narración imitándola"),
    formato: str = typer.Option("vertical", "--formato", "-f", help="vertical (9:16) | horizontal (16:9) | cuadrado"),
    salida: Path = typer.Option(None, "--salida", "-s", help="Carpeta donde dejar el proyecto"),
):
    """Genera el video a partir de un guion."""
    from . import music as musica_lib, styles, voicefx
    from .clonvoz import ClonError
    from .vozpropia import VozPropiaError

    if voz_propia and voz_clon:
        console.print("[red]--voz-propia y --voz-clon no van juntos:[/red] elige una de las dos.")
        raise typer.Exit(1)

    settings = load_settings()
    if voz:
        settings.voice.name = voz
    if imagenes:
        settings.image_provider = imagenes
    if modelo:
        settings.image_model = modelo

    # Pista de calidad para Pollinations: sin token cae a 'sana' (básico).
    import os

    settings.pollinations_free = gratis
    tiene_token = bool(os.getenv("POLLINATIONS_TOKEN")) and not gratis
    img_label = settings.image_provider
    if settings.image_provider == "pollinations":
        if gratis:
            img_label = "pollinations · sana (modo gratis, no gasta pollen)"
        elif referencia:
            from .providers.images import pollinations as _poll

            _m = modelo or os.getenv("POLLINATIONS_MODEL") or _poll.DEFAULT_MODEL
            _edit = _m if _poll.max_references(_m) else _poll.EDIT_MODEL
            img_label = f"pollinations · {_edit} ({len(referencia)} referencia(s))"
        elif tiene_token:
            img_label = f"pollinations · {os.getenv('POLLINATIONS_MODEL', 'flux')}"
        else:
            img_label = "pollinations · sana (sin token, básico)"

    # La referencia (img2img) solo funciona en pollinations con token.
    if referencia:
        if gratis:
            console.print("[red]--referencia y --gratis no van juntos:[/red] la edición con referencia va por el gateway y gasta pollen.")
            raise typer.Exit(1)
        if settings.image_provider != "pollinations":
            console.print("[red]--referencia solo funciona con --imagenes pollinations.[/red]")
            raise typer.Exit(1)
        if not tiene_token:
            console.print(
                "[red]--referencia necesita POLLINATIONS_TOKEN en .env[/red] "
                "(la edición con referencia va por el gateway, que pide token)."
            )
            raise typer.Exit(1)
    if sin_ducking:
        settings.music.duck = False
    if volumen is not None:
        settings.music.volume = max(0.0, min(1.0, volumen))

    if estilo and not styles.is_known(estilo):
        opciones = ", ".join(s["key"] for s in styles.options())
        console.print(f"[red]Estilo desconocido:[/red] '{estilo}'.\nOpciones: {opciones}")
        raise typer.Exit(1)
    style_suffix = styles.resolve(estilo)

    if not voicefx.is_known(voz_fx):
        opciones = ", ".join(o["value"] for o in voicefx.options())
        console.print(f"[red]Efecto de voz desconocido:[/red] '{voz_fx}'.\nOpciones: {opciones}")
        raise typer.Exit(1)
    settings.voice_fx = voz_fx

    name = _slug(nombre or guion.stem)

    # Se resuelve antes de generar nada: una pista mal escrita debe fallar en el
    # primer segundo, igual que una clave que falta.
    try:
        pista = musica_lib.elegir(musica, style=estilo, seed=name)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if pista is not None:
        etiqueta = pista.name
    elif musica.strip().lower() != "auto":
        etiqueta = "sin música"
    elif musica_lib.library():
        # Hay pistas, pero ninguna sirve para este estilo. Decirlo: si no, el
        # video sale mudo y parece que el ducking no funciona.
        ambiente = musica_lib.MOODS.get((estilo or "").lower(), "general")
        etiqueta = (
            f"[yellow]ninguna — no hay pistas en {ambiente}/ ni en general/[/yellow]"
        )
    else:
        etiqueta = "[yellow]ninguna — biblioteca vacía (ver: musica)[/yellow]"

    console.print(
        Panel(
            f"[bold]{name}[/bold]\n"
            f"voz       [cyan]{voz_propia.name + ' (tuya, grabada)' if voz_propia else (('clon de ' + voz_clon.name) if voz_clon else settings.voice.name)}[/cyan]\n"
            f"imágenes  [cyan]{img_label}[/cyan]\n"
            f"formato   [cyan]{formato}[/cyan]\n"
            f"estilo    [cyan]{estilo or 'auto'}[/cyan]\n"
            f"voz fx    [cyan]{voicefx.label(voz_fx, estilo)}[/cyan]\n"
            f"portada   [cyan]{portada.name if portada else 'no'}[/cyan]\n"
            f"referencia[cyan] {', '.join(r.name for r in referencia) if referencia else 'no'}[/cyan]\n"
            f"director  [cyan]{'no' if sin_director else 'sí'}[/cyan]\n"
            f"música    [cyan]{etiqueta}[/cyan]"
            + (f"  [dim](vol {settings.music.volume:.0%})[/dim]" if pista else ""),
            title="CrearVideos",
            border_style="cyan",
        )
    )

    try:
        with console.status("[cyan]Preparando…") as status:
            project, cost = create(
                guion,
                name,
                settings,
                style=style_suffix,
                style_key=estilo,
                direct=not sin_director,
                music=pista,
                portada=portada,
                references=referencia,
                voz_propia=voz_propia,
                voz_clon=voz_clon,
                formato=formato,
                output_dir=salida,
                on_progress=lambda m: status.update(f"[cyan]{m}…"),
            )
    except (FFmpegError, ClonError, VozPropiaError, RuntimeError, ValueError) as e:
        console.print(f"\n[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if abs(velocidad - 1.0) > 1e-3:
        from . import compose

        try:
            with console.status(f"[cyan]Ajustando velocidad a {velocidad}×…"):
                tmp = project.output_path.with_name(project.output_path.stem + "_vel.mp4")
                compose.retime(project.output_path, tmp, velocidad)
                tmp.replace(project.output_path)
        except (FFmpegError, ValueError) as e:
            console.print(f"\n[red]No se pudo cambiar la velocidad:[/red] {e}")
            raise typer.Exit(1)

    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_row("Video", f"[green]{project.output_path}[/green]")
    table.add_row("Duración", f"{project.total_duration:.1f}s")
    table.add_row("Escenas", str(len(project.scenes)))
    table.add_row("Palabras", str(len(project.all_words())))
    table.add_row("Costo aprox.", "gratis" if cost == 0 else f"${cost:.3f} USD")
    console.print()
    console.print(table)


@app.command("audio")
def audio_cmd(
    guion: Path = typer.Argument(..., exists=True, dir_okay=False, help="Archivo .txt con el guion (o un export de StoryFlow Pro)"),
    nombre: str = typer.Option(None, "--nombre", "-n", help="Nombre del proyecto (por defecto, el del archivo)"),
    voz: str = typer.Option(None, "--voz", "-v", help="Voz de edge-tts, p. ej. es-MX-DaliaNeural"),
    voz_fx: str = typer.Option("none", "--voz-fx", help="Efecto de voz: none | terror | profunda | eco | radio… (ver: efectos)"),
    velocidad: float = typer.Option(1.0, "--velocidad", help="1.1 más rápido, 0.9 más lento. No cambia el tono"),
    renarrar: bool = typer.Option(False, "--renarrar", help="Volver a generar la voz aunque el proyecto ya tenga una igual"),
    voz_clon: Path = typer.Option(None, "--voz-clon", exists=True, dir_okay=False, help="Clonar tu voz: muestra de 15-30 s y narra imitándola"),
    salida: Path = typer.Option(None, "--salida", "-s", help="Carpeta donde dejar el proyecto"),
):
    """Genera SOLO el audio de la narración, sin imágenes ni montaje."""
    from . import narracion, voicefx
    from .clonvoz import ClonError

    settings = load_settings()
    if voz:
        settings.voice.name = voz
    if not voicefx.is_known(voz_fx):
        opciones = ", ".join(o["value"] for o in voicefx.options())
        console.print(f"[red]Efecto de voz desconocido:[/red] '{voz_fx}'.\nOpciones: {opciones}")
        raise typer.Exit(1)
    settings.voice_fx = voz_fx

    name = _slug(nombre or guion.stem)
    console.print(
        Panel(
            f"[bold]{name}[/bold]\n"
            f"voz       [cyan]{settings.voice.name}[/cyan]\n"
            f"voz fx    [cyan]{voicefx.label(voz_fx, None)}[/cyan]\n"
            f"velocidad [cyan]{velocidad}×[/cyan]",
            title="CrearVideos · solo audio",
            border_style="cyan",
        )
    )

    try:
        with console.status("[cyan]Preparando…") as status:
            r = narracion.create_audio(
                guion,
                name,
                settings,
                speed=velocidad,
                reusar=not renarrar,
                voz_clon=voz_clon,
                output_dir=salida,
                on_progress=lambda m: status.update(f"[cyan]{m}…"),
            )
    except (FFmpegError, ClonError, RuntimeError, ValueError) as e:
        console.print(f"\n[red]Error:[/red] {e}")
        raise typer.Exit(1)

    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_row("Audio", f"[green]{r.mp3}[/green]")
    table.add_row("Duración", f"{r.duracion / 60:.1f} min ({r.duracion:.1f}s)")
    table.add_row("Escenas", str(r.escenas))
    table.add_row("Palabras", str(r.palabras))
    table.add_row("Voz", "reusada de la anterior" if r.reusada else "generada ahora")
    table.add_row("Por escena", f"{r.project.audio_dir}")
    table.add_row("Marcas", f"{r.escenas_srt.name}, {r.subtitulos_srt.name}, {r.datos.name}")
    console.print()
    console.print(table)


@app.command("montar")
def montar_cmd(
    guion: Path = typer.Argument(..., exists=True, dir_okay=False, help="Archivo .txt con el guion (o un export de StoryFlow Pro)"),
    medios: Path = typer.Argument(..., exists=True, file_okay=False, help="Carpeta con tus imágenes y videos"),
    nombre: str = typer.Option(None, "--nombre", "-n", help="Nombre del proyecto (por defecto, el del archivo)"),
    formato: str = typer.Option("vertical", "--formato", "-f", help="vertical (9:16) | horizontal (16:9) | cuadrado"),
    voz: str = typer.Option(None, "--voz", "-v", help="Voz de edge-tts, p. ej. es-MX-DaliaNeural"),
    voz_propia: Path = typer.Option(None, "--voz-propia", exists=True, help="TU voz ya grabada: un MP3/WAV con toda la narración, o una carpeta con un audio por escena"),
    voz_clon: Path = typer.Option(None, "--voz-clon", exists=True, dir_okay=False, help="Clonar tu voz: muestra de 15-30 s y genera la narración imitándola"),
    voz_fx: str = typer.Option("none", "--voz-fx", help="Efecto de voz (ver: efectos)"),
    musica: str = typer.Option("no", "--musica", "-m", help="auto | no | ruta a una pista (ver: musica)"),
    volumen: float = typer.Option(None, "--volumen", help="Volumen de la música, 0 a 1"),
    sin_subtitulos: bool = typer.Option(False, "--sin-subtitulos", help="No quemar los subtítulos en el video"),
    renarrar: bool = typer.Option(False, "--renarrar", help="Volver a generar la voz aunque el proyecto ya tenga una igual"),
    solo_ver: bool = typer.Option(False, "--solo-ver", help="Solo muestra qué archivo va en cada escena y termina"),
    salida: Path = typer.Option(None, "--salida", "-s", help="Carpeta donde dejar el proyecto"),
):
    """Monta el video con TUS imágenes y videos, poniendo voz y subtítulos."""
    from . import montaje, music as musica_lib, voicefx
    from .clonvoz import ClonError
    from .medios import MediosError
    from .vozpropia import VozPropiaError

    if voz_propia and voz_clon:
        console.print("[red]--voz-propia y --voz-clon no van juntos:[/red] o usas tu audio tal cual, o clonas la voz.")
        raise typer.Exit(1)

    settings = load_settings()
    if voz:
        settings.voice.name = voz
    if volumen is not None:
        settings.music.volume = max(0.0, min(1.0, volumen))
    if not voicefx.is_known(voz_fx):
        console.print(f"[red]Efecto de voz desconocido:[/red] '{voz_fx}'")
        raise typer.Exit(1)
    settings.voice_fx = voz_fx

    name = _slug(nombre or guion.stem)

    try:
        previo = montaje.previsualizar(guion, medios, settings)
    except (MediosError, ValueError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    tabla = Table(title="Escena → archivo")
    tabla.add_column("#", justify="right", style="cyan", no_wrap=True)
    tabla.add_column("Archivo")
    tabla.add_column("Tipo", style="dim")
    tabla.add_column("Narración", style="dim", max_width=48)
    for fila in previo["filas"]:
        tabla.add_row(str(fila["escena"]), fila["archivo"], fila["tipo"], fila["texto"])
    console.print(tabla)
    console.print(
        f"[dim]{previo['escenas']} escenas · {previo['archivos']} archivos · "
        f"emparejado por {previo['metodo']}[/dim]"
    )
    for aviso in previo["avisos"]:
        console.print(f"[yellow]{aviso}[/yellow]")
    if solo_ver:
        return

    try:
        pista = musica_lib.elegir(musica, style=None, seed=name)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    console.print(
        Panel(
            f"[bold]{name}[/bold]\n"
            f"formato    [cyan]{formato}[/cyan]\n"
            f"voz        [cyan]{voz_propia.name if voz_propia else (('clon de ' + voz_clon.name) if voz_clon else settings.voice.name)}[/cyan]\n"
            f"subtítulos [cyan]{'no' if sin_subtitulos else 'sí'}[/cyan]\n"
            f"música     [cyan]{pista.name if pista else 'sin música'}[/cyan]",
            title="CrearVideos · montaje con tus medios",
            border_style="cyan",
        )
    )

    try:
        with console.status("[cyan]Preparando…") as status:
            r = montaje.montar(
                guion,
                name,
                settings,
                medios,
                formato=formato,
                music=pista,
                subtitulos=not sin_subtitulos,
                reusar_voz=not renarrar,
                voz_propia=voz_propia,
                voz_clon=voz_clon,
                output_dir=salida,
                on_progress=lambda m: status.update(f"[cyan]{m}…"),
            )
    except (FFmpegError, MediosError, VozPropiaError, ClonError, RuntimeError, ValueError) as e:
        console.print(f"\n[red]Error:[/red] {e}")
        raise typer.Exit(1)

    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_row("Video", f"[green]{r.video}[/green]")
    table.add_row("Formato", f"{r.formato} ({r.tamaño[0]}x{r.tamaño[1]})")
    table.add_row("Duración", f"{r.duracion / 60:.1f} min ({r.duracion:.1f}s)")
    table.add_row("Escenas", str(len(r.project.scenes)))
    table.add_row("Voz", r.origen_voz)
    table.add_row("Parte", str(r.project.root / "montaje.json"))
    console.print()
    console.print(table)


@app.command("ensamblar")
def ensamblar_cmd(
    medios: Path = typer.Argument(..., exists=True, file_okay=False, help="Carpeta con los segmentos de video"),
    nombre: str = typer.Option(None, "--nombre", "-n", help="Nombre del proyecto (por defecto, el de la carpeta)"),
    formato: str = typer.Option("original", "--formato", "-f", help="original | vertical | horizontal | cuadrado"),
    transicion: str = typer.Option("ninguna", "--transicion", "-t", help="ninguna | fundido | negro | disolver | izquierda… (ver: transiciones)"),
    duracion: float = typer.Option(0.5, "--duracion", help="Segundos que dura cada transición"),
    musica: str = typer.Option("no", "--musica", "-m", help="auto | no | ruta a una pista"),
    volumen: float = typer.Option(0.12, "--volumen", help="Volumen de la música (los clips ya traen voz)"),
    solo_ver: bool = typer.Option(False, "--solo-ver", help="Solo lista los segmentos y termina"),
    salida: Path = typer.Option(None, "--salida", "-s", help="Carpeta donde dejar el proyecto"),
):
    """Une segmentos de video que YA traen su voz. Sin narración ni subtítulos."""
    from . import ensamblar as ens, music as musica_lib

    name = _slug(nombre or medios.name)
    try:
        segmentos = ens.revisar(medios)
    except (ens.EnsamblarError, FFmpegError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    tabla = Table(title=f"{len(segmentos)} segmentos")
    tabla.add_column("#", justify="right", style="cyan", no_wrap=True)
    tabla.add_column("Archivo")
    tabla.add_column("Duración", justify="right")
    tabla.add_column("Tamaño", style="dim")
    tabla.add_column("Audio")
    for i, s in enumerate(segmentos, 1):
        tabla.add_row(
            str(i), s.path.name, f"{s.duracion:.1f}s", f"{s.ancho}x{s.alto}",
            "sí" if s.tiene_audio else "[yellow]no[/yellow]",
        )
    console.print(tabla)
    total = sum(s.duracion for s in segmentos)
    console.print(f"[dim]suma de los clips: {total:.1f}s ({total / 60:.1f} min)[/dim]")
    if solo_ver:
        return

    try:
        pista = musica_lib.elegir(musica, style=None, seed=name)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    try:
        with console.status("[cyan]Preparando…") as status:
            r = ens.ensamblar(
                medios,
                name,
                formato=formato,
                transicion=transicion,
                duracion_transicion=duracion,
                musica=pista,
                volumen_musica=volumen,
                output_dir=salida,
                on_progress=lambda m: status.update(f"[cyan]{m}…"),
            )
    except (ens.EnsamblarError, FFmpegError, ValueError) as e:
        console.print(f"\n[red]Error:[/red] {e}")
        raise typer.Exit(1)

    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_row("Video", f"[green]{r.salida}[/green]")
    table.add_row("Formato", f"{r.formato} ({r.tamaño[0]}x{r.tamaño[1]})")
    table.add_row("Duración", f"{r.duracion / 60:.1f} min ({r.duracion:.1f}s)")
    table.add_row("Segmentos", str(len(r.segmentos)))
    if r.transicion != "ninguna":
        table.add_row(
            "Transición",
            f"{r.transicion} · el cruce recorta {r.suma_clips - r.duracion:.1f}s del total",
        )
    console.print()
    console.print(table)


@app.command("transiciones")
def transiciones_cmd():
    """Lista las transiciones disponibles para 'ensamblar'."""
    from .ensamblar import TRANSICIONES

    table = Table(title="Transiciones")
    table.add_column("Clave", style="cyan", no_wrap=True)
    table.add_column("Qué hace")
    for clave, (_, desc) in TRANSICIONES.items():
        table.add_row(clave, desc)
    console.print(table)


@app.command("voces-escena")
def voces_escena_cmd(
    guion: Path = typer.Argument(..., exists=True, dir_okay=False, help="Guion (.txt o export de StoryFlow)"),
    nombre: str = typer.Option(None, "--nombre", "-n", help="Nombre del proyecto"),
    asignar: str = typer.Option(None, "--asignar", help="Poner voz a escenas: '3=es-ES-ElviraNeural' o '3,5,8=es-CO-SalomeNeural'"),
    base: str = typer.Option(None, "--base", help="Voz por defecto para las escenas sin voz propia"),
    grabada: str = typer.Option(None, "--grabada", help="Usar TU grabación en escenas: '2' o '2,4' (con --audios), o 'todas'"),
    audios: Path = typer.Option(None, "--audios", exists=True, help="Carpeta con tus audios (o un archivo suelto)"),
    clonar: str = typer.Option(None, "--clonar", help="Clonar tu voz en escenas: '5' o '5,9', o 'todas' (con --muestra)"),
    muestra: Path = typer.Option(None, "--muestra", exists=True, help="Muestra de 15-30 s de tu voz para clonar"),
    generar: bool = typer.Option(False, "--generar", help="Generar el audio de las escenas que falten"),
    rehacer: str = typer.Option(None, "--rehacer", help="Regenerar escenas concretas: '3' o '3,7'"),
    unir: bool = typer.Option(False, "--unir", help="Al terminar, unir todo en narracion.mp3"),
):
    """Da una voz distinta a cada escena y genera solo lo que cambie."""
    from . import voces as voces_lib

    settings = load_settings()
    name = _slug(nombre or guion.stem)
    try:
        project, _ = voces_lib.preparar(guion, name, settings)
    except voces_lib.VocesError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if base:
        voces_lib.asignar_todas(project, base)

    def _escenas(texto: str) -> list[int] | None:
        """'todas' o una lista de números de escena (que empiezan en 1)."""
        if texto.strip().lower() in ("todas", "todo", "all"):
            return None
        return [int(p.strip()) - 1 for p in texto.split(",")]

    try:
        if grabada:
            if not audios:
                console.print("[red]--grabada necesita --audios[/red] con la carpeta o el archivo.")
                raise typer.Exit(1)
            indices = _escenas(grabada)
            if indices is None:
                voces_lib.asignar_todas(project, tipo=voces_lib.PROPIA, carpeta=str(audios))
            else:
                for i in indices:
                    ruta = str(audios) if audios.is_file() else voces_lib._audio_de_carpeta(str(audios), i)
                    voces_lib.asignar(project, i, tipo=voces_lib.PROPIA, archivo=ruta)
        if clonar:
            if not muestra:
                console.print("[red]--clonar necesita --muestra[/red] con tu grabación de referencia.")
                raise typer.Exit(1)
            indices = _escenas(clonar)
            if indices is None:
                voces_lib.asignar_todas(project, tipo=voces_lib.CLONADA, muestra=str(muestra))
            else:
                for i in indices:
                    voces_lib.asignar(project, i, tipo=voces_lib.CLONADA, muestra=str(muestra))
    except ValueError:
        console.print("[red]Formato:[/red] números de escena separados por comas, o 'todas'.")
        raise typer.Exit(1)

    if asignar:
        try:
            indices, voz = asignar.split("=", 1)
            for parte in indices.split(","):
                voces_lib.asignar(project, int(parte.strip()) - 1, voz.strip())
        except (ValueError, IndexError):
            console.print("[red]Formato:[/red] --asignar '3=es-ES-ElviraNeural' o '3,5=es-CO-SalomeNeural'")
            raise typer.Exit(1)

    solo = None
    if rehacer:
        try:
            solo = [int(p.strip()) - 1 for p in rehacer.split(",")]
        except ValueError:
            console.print("[red]Formato:[/red] --rehacer '3' o --rehacer '3,7'")
            raise typer.Exit(1)

    if generar or solo:
        try:
            with console.status("[cyan]Generando…") as status:
                voces_lib.generar(
                    project, settings, solo=solo, rehacer=bool(solo),
                    on_progress=lambda m: status.update(f"[cyan]{m}…"),
                )
        except (FFmpegError, RuntimeError) as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)

    escenas = voces_lib.estado(project, settings)
    tabla = Table(title=f"Voces de {name}")
    tabla.add_column("#", justify="right", style="cyan", no_wrap=True)
    tabla.add_column("Voz", no_wrap=True)
    tabla.add_column("Efecto", style="dim", no_wrap=True)
    tabla.add_column("Estado")
    tabla.add_column("Dura", justify="right")
    tabla.add_column("Texto", style="dim", max_width=42)
    for e in escenas:
        if e.tipo == voces_lib.PROPIA:
            origen = "[cyan]tu grabación[/cyan] " + (Path(e.archivo).name if e.archivo else "[red](falta)[/red]")
        elif e.tipo == voces_lib.CLONADA:
            origen = "[magenta]tu voz clonada[/magenta]"
        else:
            origen = e.voz
        estado_txt = "[green]lista[/green]" if e.generada else "[yellow]pendiente[/yellow]"
        if e.aviso:
            estado_txt = f"[red]{e.aviso}[/red]"
        tabla.add_row(
            str(e.index + 1), origen, e.fx, estado_txt,
            f"{e.duracion:.1f}s" if e.generada else "—",
            e.texto[:80],
        )
    console.print(tabla)

    r = voces_lib.resumen(escenas)
    detalles = [f"{len(r['voces'])} voz(ces) generadas"]
    if r["propias"]:
        detalles.append(f"{r['propias']} con tu grabación")
    if r["clonadas"]:
        detalles.append(f"{r['clonadas']} clonadas")
    console.print(
        f"[dim]{r['generadas']}/{r['total']} listas · {r['duracion']}s · "
        f"{' · '.join(detalles)}[/dim]"
    )
    if r["pendientes"]:
        console.print("[yellow]Genera lo que falta con:[/yellow] --generar")

    if unir:
        try:
            mp3 = voces_lib.exportar_mp3(project, on_progress=lambda m: console.print(f"[cyan]{m}…[/cyan]"))
            console.print(f"[green]Narración completa:[/green] {mp3}")
        except (voces_lib.VocesError, FFmpegError) as e:
            console.print(f"[red]Error:[/red] {e}")


@app.command("escenas")
def escenas(
    guion: Path = typer.Argument(..., exists=True, dir_okay=False),
):
    """Muestra cómo se partiría el guion, sin generar ni gastar nada."""
    settings = load_settings()
    scenes = load_script(guion, settings.scene)

    for s in scenes:
        console.print(f"\n[bold cyan]Escena {s.index + 1}[/bold cyan]  [dim]{len(s.text)} caracteres[/dim]")
        console.print(f"  [white]{s.text}[/white]")
        console.print(f"  [dim]imagen:[/dim] [yellow]{s.image_prompt}[/yellow]")

    words = sum(len(s.text.split()) for s in scenes)
    console.print(
        f"\n[bold]{len(scenes)} escenas[/bold] · ~{words} palabras · "
        f"~{words / 2.6:.0f}s estimados"
    )


@app.command("modelos")
def modelos_cmd():
    """Lista los modelos de imagen de Pollinations y lo que cuesta cada uno."""
    from .providers.images import pollinations

    table = Table(title="Modelos de imagen (Pollinations con token)")
    table.add_column("Clave", style="cyan", no_wrap=True)
    table.add_column("Modelo")
    table.add_column("Pollen/img", justify="right")
    table.add_column("Short de 7", justify="right")
    for m in pollinations.model_options():
        marca = " [green](por defecto)[/green]" if m["key"] == pollinations.DEFAULT_MODEL else ""
        table.add_row(m["key"], m["label"] + marca, f"{m['pollen']}", f"{m['pollen'] * 7:.3f}")
    console.print(table)
    console.print(
        "\n[dim]Sin token, el endpoint anónimo ignora el modelo, devuelve 576x1024 "
        "y le quema una marca de agua.[/dim]"
    )


@app.command("estilos")
def estilos_cmd():
    """Lista los estilos visuales disponibles para --estilo."""
    from . import styles

    table = Table(title="Estilos visuales")
    table.add_column("Clave", style="cyan", no_wrap=True)
    table.add_column("Nombre")
    for s in styles.options():
        table.add_row(s["key"], s["label"])
    console.print(table)


@app.command("velocidad")
def velocidad_cmd(
    nombre: str = typer.Argument(..., help="Nombre del proyecto ya generado"),
    factor: float = typer.Argument(..., help="1.25 = 25% más rápido; 0.9 = 10% más lento"),
):
    """Reexporta un video ya hecho a otra velocidad, sin regenerarlo.

    Acelera o ralentiza imagen y audio por igual, manteniendo el tono de la voz.
    Deja el nuevo archivo aparte, conservando el original.
    """
    from . import compose

    src = OUTPUT_DIR / _slug(nombre) / f"{_slug(nombre)}.mp4"
    if not src.is_file():
        console.print(f"[red]No existe el video:[/red] {src}")
        raise typer.Exit(1)

    etiqueta = f"{factor}".replace(".", "_").rstrip("0").rstrip("_")
    dst = src.with_name(f"{src.stem}_x{etiqueta}.mp4")
    try:
        with console.status(f"[cyan]Reexportando a {factor}×…"):
            compose.retime(src, dst, factor)
    except (FFmpegError, ValueError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    console.print(f"[green]Listo.[/green] {dst}")


@app.command("efectos")
def efectos_cmd():
    """Lista los efectos de voz disponibles para --voz-fx."""
    from . import voicefx

    table = Table(title="Efectos de voz")
    table.add_column("Clave", style="cyan", no_wrap=True)
    table.add_column("Efecto")
    for o in voicefx.options():
        table.add_row(o["value"], o["label"])
    console.print(table)
    console.print(
        "\n[dim]'auto' aplica terror en estilos de terror/suspenso, radio en scifi, "
        "y nada en el resto.[/dim]"
    )


@app.command("musica")
def musica_cmd():
    """Muestra las pistas de fondo disponibles y dónde dejar las tuyas."""
    from . import music as musica_lib

    musica_lib.ensure_dirs()
    biblioteca = musica_lib.library()

    if not biblioteca:
        console.print(
            Panel(
                f"La biblioteca está vacía. Deja tus pistas en:\n"
                f"[cyan]{musica_lib.MUSIC_DIR}[/cyan]\n\n"
                f"Una carpeta por ambiente — [dim]{', '.join(musica_lib.FOLDERS)}[/dim] —\n"
                f"y [cyan]--musica auto[/cyan] elige según el estilo del video.\n"
                f"Lo que va en [cyan]general/[/cyan] sirve para cualquiera.\n\n"
                f"[yellow]Usa solo música que puedas usar.[/yellow] Fuentes gratuitas con\n"
                f"licencia clara: pixabay.com/music, freemusicarchive.org,\n"
                f"incompetech.com. No se descarga nada solo: subir música ajena\n"
                f"a TikTok o YouTube es lo que tumba un canal.",
                title="Música de fondo",
                border_style="cyan",
            )
        )
        return

    table = Table(title="Música de fondo")
    table.add_column("Ambiente", style="cyan", no_wrap=True)
    table.add_column("Pista")
    table.add_column("Estilos que la usan", style="dim")

    for carpeta, pistas in biblioteca.items():
        usos = [k for k, v in musica_lib.MOODS.items() if v == carpeta]
        etiqueta = ", ".join(usos) if usos else "cualquiera"
        for i, p in enumerate(pistas):
            table.add_row(carpeta if i == 0 else "", p.stem, etiqueta if i == 0 else "")
    console.print(table)
    console.print(f"\n[dim]{musica_lib.MUSIC_DIR}[/dim]")


@app.command("voces")
def voces(
    idioma: str = typer.Option("es", "--idioma", "-i", help="Prefijo de idioma, p. ej. es, en, pt"),
):
    """Lista las voces disponibles de edge-tts."""
    from .providers.tts.edge import list_voices

    table = Table(title=f"Voces disponibles ({idioma})")
    table.add_column("Voz", style="cyan", no_wrap=True)
    table.add_column("Género")
    table.add_column("Región", style="dim")

    for v in list_voices(f"{idioma}-"):
        table.add_row(v["ShortName"], v["Gender"], v["Locale"])
    console.print(table)


@app.command("nichos")
def nichos_cmd(
    consulta: str = typer.Argument(..., help="Tema o nicho a explorar, p. ej. 'historia antigua documental'"),
    limite: int = typer.Option(12, "--limite", "-l", help="Cuántos videos traer"),
    orden: str = typer.Option("virales", "--orden", "-o", help="virales | vistas | despegue | dinero | audiencia"),
    tipo: str = typer.Option("todos", "--tipo", help="todos | largo | short"),
    faceless: bool = typer.Option(False, "--faceless", help="Solo los que se pueden hacer sin cámara"),
    sin_ia: bool = typer.Option(False, "--sin-ia", help="Sin clasificar el nicho (más rápido)"),
):
    """Busca qué está funcionando en un nicho: virales, audiencia y dinero."""
    from .estudio import nichos
    from .estudio.youtube import YouTubeError

    try:
        with console.status("[cyan]Buscando…") as status:
            r = nichos.buscar(
                consulta, limite=limite, orden=orden, tipo=tipo,
                solo_faceless=faceless, clasificar=not sin_ia,
                on_progress=lambda m: status.update(f"[cyan]{m}…"),
            )
    except (YouTubeError, ValueError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if not r:
        console.print("[yellow]Sin resultados.[/yellow] Prueba otras palabras.")
        raise typer.Exit(0)

    tabla = Table(title=f"{consulta} · por {nichos.ORDENES[orden][0]}")
    tabla.add_column("Vistas/día", justify="right", style="cyan", no_wrap=True)
    tabla.add_column("Vistas", justify="right", no_wrap=True)
    tabla.add_column("Subs", justify="right", style="dim", no_wrap=True)
    tabla.add_column("×subs", justify="right", no_wrap=True)
    tabla.add_column("USD est.", justify="right", no_wrap=True)
    tabla.add_column("FL", no_wrap=True)
    tabla.add_column("Nicho", max_width=20)
    tabla.add_column("Título", max_width=40)
    for h in r:
        ing = h.ingresos or {}
        tabla.add_row(
            f"{h.vistas_dia:,}".replace(",", ".") if h.vistas_dia else "—",
            f"{h.vistas:,}".replace(",", "."),
            f"{h.suscriptores:,}".replace(",", ".") if h.suscriptores else "—",
            f"{h.vistas_por_suscriptor:g}" if h.vistas_por_suscriptor else "—",
            f"${ing['usd'][0]}-{ing['usd'][1]}" if ing else "—",
            "[green]sí[/green]" if h.faceless == "sí" else h.faceless or "—",
            h.nicho, h.titulo,
        )
    console.print(tabla)

    res = nichos.resumen(r)
    if res.get("nichos"):
        console.print("[dim]nichos: " + ", ".join(f"{n} ({c})" for n, c in res["nichos"]) + "[/dim]")
    console.print(
        f"[dim]{res['faceless']}/{res['total']} se pueden hacer sin cámara · "
        f"mediana {res['vistas_dia_mediana'] or '—'} vistas/día[/dim]"
    )
    destino = nichos.guardar(consulta, r)
    console.print(f"[dim]guardado en {destino}[/dim]")
    console.print(
        "\n[cyan]Para escribir un guion con la fórmula de uno:[/cyan]  "
        "python -m crearvideos guion \"tu tema\" --como <id del video>"
    )


@app.command("estetica")
def estetica_cmd(
    canal: str = typer.Argument(..., help="Canal o video YA analizado (el nombre de la carpeta en estudio/)"),
    extra: str = typer.Option("", "--extra", help="Qué quieres tú: 'protagonista mujer joven', 'estilo cómic'…"),
    piezas: str = typer.Option("", "--piezas", help="Solo algunas: personaje,escena,miniatura,avatar,portada"),
):
    """Crea el tablero visual de un canal: personaje, escena, miniatura, avatar."""
    from .estudio import estetica

    lista = [p.strip() for p in piezas.split(",") if p.strip()] or None
    try:
        with console.status("[cyan]Diseñando…") as status:
            t = estetica.crear(
                canal, extra=extra, piezas=lista,
                on_progress=lambda m: status.update(f"[cyan]{m}…"),
            )
    except (estetica.EsteticaError, RuntimeError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    console.print(Panel(t.concepto or "—", title="Concepto visual", border_style="cyan"))
    if t.paleta:
        console.print("Paleta: " + "  ".join(f"[on {c}]  [/on {c}] {c}" for c in t.paleta))

    tabla = Table(show_header=True, box=None, padding=(0, 2, 0, 0))
    tabla.add_column("Pieza", style="cyan")
    tabla.add_column("Archivo")
    tabla.add_column("Tamaño", style="dim")
    for p in t.piezas:
        tabla.add_row(p["titulo"], p["archivo"], p["tamaño"])
    console.print()
    console.print(tabla)
    console.print(f"\n[green]Tablero en[/green] {t.carpeta}")
    ficha = t.carpeta / "personaje.txt"
    if ficha.is_file():
        console.print(f"[dim]Para fijar el personaje en tus guiones, pega la línea de {ficha.name}[/dim]")
    for a in t.avisos:
        console.print(f"[yellow]{a}[/yellow]")


@app.command("catalogo")
def catalogo_cmd(
    por_nicho: int = typer.Option(6, "--muestras", help="Cuántos videos se miran por nicho"),
    top: int = typer.Option(0, "--top", help="Enseñar solo los N primeros"),
):
    """Lista los nichos que funcionan sin cámara, medidos en YouTube ahora."""
    from .estudio import nichos

    with console.status("[cyan]Midiendo los nichos…") as status:
        filas = nichos.explorar(por_nicho, on_progress=lambda m: status.update(f"[cyan]{m}…"))
    if top:
        filas = filas[:top]

    tabla = Table(title="Nichos para canales sin cámara")
    tabla.add_column("Nicho", no_wrap=True)
    tabla.add_column("RPM $", justify="right", style="dim", no_wrap=True)
    tabla.add_column("Vistas del típico", justify="right", no_wrap=True)
    tabla.add_column("Del mejor", justify="right", style="dim", no_wrap=True)
    tabla.add_column("USD por video", justify="right", style="cyan", no_wrap=True)
    tabla.add_column("A tener en cuenta", max_width=46)
    for f in filas:
        ing = f["ingresos"]
        r = f["rpm_rango"]
        tabla.add_row(
            f["nombre"],
            f"{r[0]:g}-{r[1]:g}",
            f"{f['vistas_mediana']:,}".replace(",", ".") if f["vistas_mediana"] else "—",
            f"{f['vistas_max']:,}".replace(",", ".") if f["vistas_max"] else "—",
            f"${ing['usd'][0]}-{ing['usd'][1]}" if ing else "—",
            f["nota"],
        )
    console.print(tabla)
    console.print(
        "\n[dim]«Vistas del típico» es la mediana de los primeros resultados: mide lo que "
        "consigue quien YA está arriba en ese nicho, no lo que sacarás la primera semana. "
        "Los dólares son estimación por RPM.[/dim]"
    )
    console.print(
        "\n[cyan]Para entrar en uno:[/cyan]  python -m crearvideos nichos \"<la búsqueda del nicho>\""
    )


@app.command("guion")
def guion_cmd(
    tema: str = typer.Argument(..., help="De qué va tu video"),
    minutos: float = typer.Option(3.0, "--minutos", "-m", help="Duración objetivo"),
    como: str = typer.Option(None, "--como", help="Imitar la fórmula de un video (id o URL de YouTube)"),
    contexto: str = typer.Option("", "--contexto", help="Tono y público de tu canal"),
):
    """Escribe un guion listo para producir, opcionalmente con una fórmula de referencia."""
    from . import guionista
    from .estudio import nichos, youtube
    from .estudio.ia import IAError

    referencia = None
    if como:
        try:
            with console.status("[cyan]Leyendo el video de referencia…"):
                v, _ = youtube.video(como)
            referencia = {"titulo": v.titulo, "canal": v.canal, "nicho": ", ".join(v.categorias)}
        except youtube.YouTubeError as e:
            console.print(f"[yellow]No se pudo leer la referencia:[/yellow] {e}")

    try:
        with console.status("[cyan]Escribiendo…") as status:
            g = guionista.escribir(
                tema, minutos=minutos, referencia=referencia, contexto=contexto,
                on_progress=lambda m: status.update(f"[cyan]{m}…"),
            )
    except (guionista.GuionistaError, IAError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    console.print(Panel(g.titulo or tema, title="Título", border_style="cyan"))
    console.print(f"[dim]gancho:[/dim] {g.gancho}\n")
    console.print(g.texto[:1200] + ("\n[dim]…[/dim]" if len(g.texto) > 1200 else ""))

    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_row("Guion", f"[green]{g.ruta}[/green]")
    table.add_row("Escenas", str(g.escenas))
    table.add_row("Palabras", str(g.palabras))
    table.add_row("Duración est.", f"{g.duracion_estimada // 60}:{g.duracion_estimada % 60:02d}")
    console.print()
    console.print(table)


@app.command("publicar")
def publicar_cmd(
    proyecto: str = typer.Argument(..., help="Nombre del proyecto ya generado (o ruta a un .txt con --guion)"),
    guion: bool = typer.Option(False, "--guion", help="El argumento es un archivo de guion, no un proyecto (sin capítulos)"),
    contexto: str = typer.Option("", "--contexto", help="Tono o datos del canal, p. ej. el ADN que sacó 'analizar'"),
):
    """Prepara la publicación en YouTube: título, descripción, capítulos y SEO."""
    from . import publicar
    from .estudio.ia import IAError

    try:
        with console.status("[cyan]Preparando…") as status:
            origen = Path(proyecto).read_text(encoding="utf-8") if guion else proyecto
            p = publicar.generar(
                origen,
                contexto=contexto,
                es_guion=guion,
                on_progress=lambda m: status.update(f"[cyan]{m}…"),
            )
    except (publicar.PublicarError, IAError, OSError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    from rich.markdown import Markdown

    console.print(Markdown(publicar.markdown(p)))
    if p.get("carpeta"):
        console.print(f"\n[green]Guardado en[/green] {p['carpeta']}\\publicacion.md")
    if p.get("aviso"):
        console.print(f"[yellow]{p['aviso']}[/yellow]")


@app.command("youtube-conectar")
def youtube_conectar_cmd(
    desconectar: bool = typer.Option(False, "--desconectar", help="Borra el permiso guardado"),
):
    """Autoriza tu canal de YouTube (una sola vez, abre el navegador)."""
    from . import youtube_subir as yt

    if desconectar:
        console.print(f"[green]{yt.desconectar()}[/green]")
        return

    ok, motivo = yt.configurado()
    if not ok:
        console.print(Panel(motivo, title="Falta configurar", border_style="yellow"))
        raise typer.Exit(1)
    try:
        console.print("[cyan]Se abrirá el navegador para elegir la cuenta del canal…[/cyan]")
        console.print(f"[green]{yt.conectar()}[/green]")
    except yt.YouTubeError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@app.command("subir")
def subir_cmd(
    proyecto: str = typer.Argument(..., help="Proyecto con video y publicacion.json"),
    privacidad: str = typer.Option("privado", "--privacidad", "-p", help="privado | oculto | publico"),
    categoria: str = typer.Option("educacion", "--categoria", help="educacion | ciencia | gente | entretenimiento | tutorial | noticias"),
    titulo: str = typer.Option(None, "--titulo", help="Otro título en vez del recomendado"),
    programar: str = typer.Option(None, "--programar", help="Publicar solo en 'AAAA-MM-DD HH:MM' (tu hora)"),
    miniatura: Path = typer.Option(None, "--miniatura", exists=True, dir_okay=False, help="Imagen de miniatura"),
    si: bool = typer.Option(False, "--si", "-y", help="No preguntar antes de subir"),
):
    """Sube el video del proyecto a YouTube. Por defecto, PRIVADO."""
    from . import publicar as pub_lib, youtube_subir as yt

    try:
        raiz, pub = yt._proyecto(proyecto)
        archivo = yt._video(raiz)
    except yt.YouTubeError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if not yt.autorizado():
        console.print(
            "[yellow]Todavía no autorizaste tu canal.[/yellow] Ejecuta:  "
            "[cyan]python -m crearvideos youtube-conectar[/cyan]"
        )
        raise typer.Exit(1)

    nombre = titulo or pub.get("titulo_recomendado", "")
    capitulos = len(pub.get("capitulos") or [])
    cuando = programar or ("al subirse" if privacidad == "publico" else "—")
    console.print(
        Panel(
            f"[bold]{nombre}[/bold]\n"
            f"archivo     [cyan]{archivo.name}[/cyan] ({archivo.stat().st_size / 1024 / 1024:.0f} MB)\n"
            f"duración    [cyan]{pub_lib.mmss(pub.get('duracion') or 0)}[/cyan]\n"
            f"privacidad  [cyan]{privacidad}[/cyan]\n"
            f"se publica  [cyan]{cuando}[/cyan]\n"
            f"categoría   [cyan]{categoria}[/cyan]\n"
            f"etiquetas   [cyan]{len(pub.get('tags') or [])}[/cyan]\n"
            f"capítulos   [cyan]{capitulos}[/cyan]",
            title="Se va a subir a YouTube",
            border_style="cyan",
        )
    )
    # Subir es difícil de deshacer y sale de tu PC: se confirma siempre, salvo
    # que lo pidas explícitamente con --si.
    if not si and not typer.confirm("¿Subir ahora?", default=False):
        console.print("[yellow]Cancelado. No se subió nada.[/yellow]")
        raise typer.Exit(0)

    try:
        with console.status("[cyan]Subiendo…") as status:
            r = yt.subir(
                proyecto,
                privacidad=privacidad,
                categoria=categoria,
                titulo=titulo,
                programar=programar,
                miniatura=miniatura,
                on_progress=lambda m: status.update(f"[cyan]{m}"),
            )
    except yt.YouTubeError as e:
        console.print(f"\n[red]Error:[/red] {e}")
        raise typer.Exit(1)

    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_row("Video", f"[green]{r['url']}[/green]")
    table.add_row("Editar", r["estudio"])
    table.add_row("Privacidad", r["privacidad"])
    if r["programado"]:
        table.add_row("Se publica", r["programado"])
    console.print()
    console.print(table)
    if r["aviso"]:
        console.print(f"[yellow]{r['aviso']}[/yellow]")


@app.command("analizar")
def analizar_cmd(
    url: str = typer.Argument(..., help="URL de un video, o el @ / URL de un canal"),
    videos: int = typer.Option(30, "--videos", help="Canal: cuántos videos largos y shorts leer"),
    profundidad: int = typer.Option(3, "--profundidad", help="Canal: cuántos outliers leer a fondo"),
    sin_ia: bool = typer.Option(False, "--sin-ia", help="Solo datos medidos y transcripción, sin análisis de IA"),
):
    """Módulo estudio: analiza un video o canal de YouTube que funciona."""
    from .estudio import analizador, youtube

    try:
        tipo, _ = youtube.tipo_de_url(url)
        with console.status("[cyan]Preparando…") as status:
            progreso = lambda m: status.update(f"[cyan]{m}…")  # noqa: E731
            if tipo == "video":
                informe, carpeta = analizador.analizar_video(url, not sin_ia, progreso)
            else:
                informe, carpeta = analizador.analizar_canal(
                    url, videos, profundidad, not sin_ia, progreso
                )
    except youtube.YouTubeError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    from rich.markdown import Markdown

    console.print(Markdown((carpeta / "informe.md").read_text(encoding="utf-8")))
    console.print(f"\n[green]Guardado en[/green] {carpeta}")
    if informe.get("aviso"):
        console.print(f"[yellow]{informe['aviso']}[/yellow]")


@app.command("web")
def web(
    puerto: int = typer.Option(8000, "--puerto", "-p", help="Puerto donde escuchar"),
    sin_abrir: bool = typer.Option(False, "--sin-abrir", help="No abrir el navegador solo"),
    clave: str = typer.Option("", "--clave", help="Pide clave de acceso (obligatorio si la publicas por un túnel)"),
):
    """Abre la interfaz web local para crear videos desde el navegador."""
    from .web.server import serve

    url = f"http://127.0.0.1:{puerto}"
    console.print(
        Panel(
            f"Interfaz abierta en [bold cyan]{url}[/bold cyan]\n"
            f"[dim]Ctrl+C para cerrar.[/dim]",
            title="CrearVideos web",
            border_style="cyan",
        )
    )
    if clave:
        console.print("[dim]Con clave de acceso: el navegador la pedirá al entrar.[/dim]")
    try:
        serve(port=puerto, abrir=not sin_abrir, clave=clave)
    except OSError as e:
        console.print(
            f"\n[red]No se pudo abrir el puerto {puerto}:[/red] {e}\n"
            f"Prueba otro, p. ej. [cyan]python -m crearvideos web --puerto 8080[/cyan]"
        )
        raise typer.Exit(1)


@app.command("limpiar")
def limpiar(
    nombre: str = typer.Argument(..., help="Nombre del proyecto a borrar"),
):
    """Borra los archivos intermedios de un proyecto, conservando el MP4."""
    import shutil

    root = OUTPUT_DIR / _slug(nombre)
    if not root.exists():
        console.print(f"[red]No existe el proyecto:[/red] {root}")
        raise typer.Exit(1)

    for sub in ("audio", "clips", "images"):
        shutil.rmtree(root / sub, ignore_errors=True)
    for f in ("video_mudo.mp4", "clips.txt", "audio.txt", "narracion.wav"):
        (root / f).unlink(missing_ok=True)
    console.print(f"[green]Limpio.[/green] Se conservó {root / (_slug(nombre) + '.mp4')}")


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelado.[/yellow]")
        sys.exit(130)


if __name__ == "__main__":
    main()
