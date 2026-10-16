"""Del nicho al guion: escribe el guion listo para producir.

Cierra el círculo. El buscador de nichos dice qué está funcionando; esto toma
uno de esos videos como **referencia de fórmula** —no de contenido— y escribe
un guion nuevo sobre el tema que le pidas, ya en el formato que entienden los
demás módulos:

    estilo: ...        <- la cabecera que dirige las imágenes
    epoca: ...
    ---
    Narración de la escena.
    [imagen: qué se ve]
    ---
    ...

Se copia la **estructura** (cómo engancha, cómo encadena, dónde pone el giro),
nunca el texto ni el tema exacto: YouTube desmonetiza el contenido reutilizado,
y un guion calcado se nota.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .config import PROJECT_ROOT
from .estudio import ia

#: Ritmo real medido de la narración de la app, en palabras por minuto.
PALABRAS_POR_MINUTO = 170
#: Cuánto texto lleva una escena. Es el mismo rango que usa el partidor de
#: guiones, así que lo que escriba la IA cae en una escena por bloque.
PALABRAS_POR_ESCENA = 28

GUIONES = PROJECT_ROOT / "guiones"


class GuionistaError(RuntimeError):
    """No se pudo escribir el guion."""


@dataclass
class Guion:
    tema: str
    titulo: str
    gancho: str
    texto: str          # el guion completo, listo para pegar
    escenas: int
    palabras: int
    ruta: Path | None = None

    @property
    def duracion_estimada(self) -> int:
        return round(self.palabras / PALABRAS_POR_MINUTO * 60)


SISTEMA = (
    "Eres un guionista de YouTube para canales faceless (sin cámara: voz en off "
    "sobre imágenes). Escribes guiones que se entienden al oírlos, no al "
    "leerlos.\n\n"
    "Forma exacta del JSON:\n"
    "{\n"
    ' "titulo": "título del video, máximo 100 caracteres",\n'
    ' "gancho": "la primera frase, la que decide si se quedan",\n'
    ' "cabecera": {"estilo": "look visual para toda la serie de imágenes", '
    '"epoca": "época y lugar, si aplica", "protagonistas": "a quién seguimos, '
    'si se repite alguien", "paleta": "colores dominantes", "evitar": "qué no '
    'debe aparecer nunca"},\n'
    ' "escenas": [{"narracion": "lo que se dice en voz alta", "imagen": "qué '
    'se ve, en una frase descriptiva"}]\n'
    "}\n\n"
    "Reglas del guion:\n"
    "- TODO en español neutro, para escuchar. Frases cortas. Nada de listas, "
    "encabezados, markdown ni acotaciones entre paréntesis: cada palabra que "
    "escribas se va a leer en voz alta tal cual.\n"
    "- Nada de 'hola a todos', 'bienvenidos a mi canal' ni presentaciones: la "
    "primera frase tiene que dar una razón para quedarse.\n"
    "- Cada escena: entre 20 y 35 palabras de narración. Una idea por escena.\n"
    "- La imagen describe lo que se VE en ese momento, concreto y visual, sin "
    "texto ni letreros. Si un personaje se repite, descríbelo igual siempre.\n"
    "- Estructura: gancho, promesa de lo que se va a contar, desarrollo por "
    "bloques que suben la apuesta, un giro o dato que no se espera, y un cierre "
    "que invita a comentar. Sin relleno: si una escena no aporta, fuera.\n"
    "- Si das cifras o estudios, que sean verificables y del dominio público; "
    "ante la duda, habla en términos generales. No inventes fuentes, "
    "instituciones ni porcentajes.\n"
    "Devuelve SOLO el objeto JSON."
)


def _slug(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    texto = re.sub(r"[^\w\s-]", "", texto).strip().lower()
    return re.sub(r"[\s_-]+", "-", texto)[:60] or "guion"


def _limpiar(narracion: str) -> str:
    """Quita lo que nunca debería leerse en voz alta.

    Aunque el prompt lo prohíbe, cada tantas generaciones aparece un "ESCENA 3:"
    o un asterisco de markdown. Narrarlo suena a error de la app, así que se
    recorta aquí en vez de confiar.
    """
    narracion = re.sub(r"^\s*(escena|parte|bloque)\s*\d+\s*[:.-]\s*", "", narracion, flags=re.I)
    narracion = re.sub(r"[*_#`]+", "", narracion)
    narracion = re.sub(r"^\s*\[[^\]]*\]\s*", "", narracion)
    return " ".join(narracion.split())


def desde_informe(informe: dict) -> dict:
    """Saca del análisis de un canal o video TODO lo que sirve para escribir.

    El análisis ya trae el gancho con su técnica, la estructura parte por
    parte, las técnicas de retención, la fórmula del título y el tono. Pasar
    solo el título, como se hacía desde el buscador de nichos, era tirar el 90%
    de lo que costó averiguar.
    """
    a = informe.get("analisis") or {}
    if not a:
        return {}

    origen = informe.get("canal") or informe.get("video") or {}
    gancho = a.get("gancho") or {}
    titulo = a.get("titulo") or {}
    ganchos = a.get("ganchos") or []          # los canales traen varios
    formulas = a.get("formulas_titulo") or []  # y varias fórmulas de título

    estructura: list[str] = []
    for parte in a.get("estructura") or []:   # análisis de un video
        if isinstance(parte, dict):
            estructura.append(
                f"{parte.get('parte', '')} — {parte.get('funcion', '')}".strip(" —")
            )
    estructura += [str(p) for p in (a.get("estructura_tipica") or [])]  # de un canal

    return {
        "titulo": origen.get("titulo", ""),
        "canal": origen.get("nombre") or origen.get("canal", ""),
        "nicho": a.get("nicho", ""),
        "audiencia": a.get("audiencia", ""),
        "tono": a.get("tono", ""),
        "promesa": a.get("promesa_central") or a.get("propuesta", ""),
        "formula_titulo": titulo.get("formula") or (
            formulas[0].get("formula") if formulas and isinstance(formulas[0], dict) else ""
        ),
        "disparadores": titulo.get("disparadores") or [],
        "gancho": gancho.get("tecnica") or (
            ganchos[0].get("patron") if ganchos and isinstance(ganchos[0], dict) else ""
        ),
        "estructura": estructura[:9],
        "retencion": [str(r) for r in (a.get("retencion") or [])][:6],
        "porque": a.get("que_separa_outliers") or "; ".join(a.get("por_que_funciono") or [])[:400],
        "estilo_visual": a.get("estilo_visual") or {},
        "ideas": a.get("ideas") or [],
    }


def _referencia(datos: dict | None) -> str:
    """El brief de la fórmula que se quiere imitar, si hay referencia."""
    if not datos:
        return ""
    partes = ["FÓRMULA DE REFERENCIA (imita la ESTRUCTURA, nunca el tema ni el texto):"]
    simples = [
        ("Título que funcionó", "titulo"),
        ("Canal", "canal"),
        ("Nicho", "nicho"),
        ("A quién le habla", "audiencia"),
        ("Tono de la narración", "tono"),
        ("Promesa que hace al espectador", "promesa"),
        ("Fórmula de título", "formula_titulo"),
        ("Tipo de gancho que usa", "gancho"),
        ("Por qué funciona", "porque"),
        ("Por qué funcionó", "por_que"),
    ]
    for etiqueta, clave in simples:
        valor = str(datos.get(clave, "")).strip()
        if valor:
            partes.append(f"- {etiqueta}: {valor}")

    if datos.get("disparadores"):
        partes.append("- Disparadores del título: " + ", ".join(datos["disparadores"][:6]))
    if datos.get("estructura"):
        partes.append("- Estructura que sigue, en orden:")
        partes += [f"    {i + 1}. {p}" for i, p in enumerate(datos["estructura"])]
    if datos.get("retencion"):
        partes.append("- Técnicas con las que retiene: " + "; ".join(datos["retencion"]))

    ev = datos.get("estilo_visual") or {}
    if any(ev.get(k) for k in ("estilo", "paleta", "evitar")):
        partes.append(
            "- ESTILO VISUAL DEL CANAL (úsalo tal cual en la cabecera del guion): "
            f"estilo: {ev.get('estilo', '')} · paleta: {ev.get('paleta', '')} · "
            f"evitar: {ev.get('evitar', '')}"
        )
    partes.append(
        "Sigue esa estructura paso a paso con TU tema, y aplica esas técnicas de "
        "retención en los mismos sitios. No copies frases ni el tema original."
    )
    return "\n".join(partes)


def componer(paquete: dict) -> tuple[str, int, int]:
    """Convierte el JSON de la IA en el archivo de guion. (texto, escenas, palabras)"""
    cabecera = paquete.get("cabecera") or {}
    lineas = [
        f"{campo}: {cabecera[campo].strip()}"
        for campo in ("estilo", "epoca", "protagonistas", "paleta", "evitar")
        if str(cabecera.get(campo, "")).strip()
    ]

    bloques: list[str] = []
    palabras = 0
    for escena in paquete.get("escenas") or []:
        narracion = _limpiar(str(escena.get("narracion", "")))
        if not narracion:
            continue
        imagen = " ".join(str(escena.get("imagen", "")).split())
        trozo = narracion
        if imagen:
            trozo += f"\n[imagen: {imagen}]"
        bloques.append(trozo)
        palabras += len(narracion.split())

    if not bloques:
        raise GuionistaError("La IA no devolvió ninguna escena utilizable.")

    cuerpo = "\n\n---\n\n".join(bloques)
    texto = ("\n".join(lineas) + "\n\n---\n\n" + cuerpo) if lineas else cuerpo
    return texto + "\n", len(bloques), palabras


def escribir(
    tema: str,
    *,
    minutos: float = 3.0,
    referencia: dict | None = None,
    contexto: str = "",
    guardar: bool = True,
    on_progress=None,
) -> Guion:
    """Escribe un guion nuevo sobre `tema`, con la fórmula de `referencia`."""
    if not tema.strip():
        raise GuionistaError("Dime de qué va el video.")

    objetivo = max(int(minutos * PALABRAS_POR_MINUTO), 60)
    escenas = max(round(objetivo / PALABRAS_POR_ESCENA), 4)

    peticion = [
        f"TEMA: {tema.strip()}",
        f"DURACIÓN OBJETIVO: {minutos:g} minutos "
        f"(unas {objetivo} palabras, unas {escenas} escenas)",
    ]
    brief = _referencia(referencia)
    if brief:
        peticion.append("\n" + brief)
    if contexto.strip():
        peticion.append("\nSOBRE EL CANAL (manda sobre el tono):\n" + contexto.strip())

    if on_progress:
        on_progress(f"Escribiendo el guion (~{minutos:g} min)")
    # Margen de tokens generoso: un guion de 10 minutos son ~1700 palabras más
    # las descripciones de imagen, y quedarse corto parte el guion a la mitad.
    paquete = ia.pedir_json(
        SISTEMA, "\n".join(peticion), max_tokens=min(24000, 3000 + escenas * 160)
    )

    texto, n_escenas, palabras = componer(paquete)
    guion = Guion(
        tema=tema.strip(),
        titulo=str(paquete.get("titulo", "")).strip(),
        gancho=str(paquete.get("gancho", "")).strip(),
        texto=texto,
        escenas=n_escenas,
        palabras=palabras,
    )

    if guardar:
        GUIONES.mkdir(parents=True, exist_ok=True)
        destino = GUIONES / f"{_slug(guion.titulo or tema)}.txt"
        destino.write_text(texto, encoding="utf-8")
        destino.with_suffix(".json").write_text(
            json.dumps(
                {
                    "tema": guion.tema,
                    "titulo": guion.titulo,
                    "gancho": guion.gancho,
                    "escenas": guion.escenas,
                    "palabras": guion.palabras,
                    "duracion_estimada": guion.duracion_estimada,
                    "referencia": referencia or {},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        guion.ruta = destino
    return guion
