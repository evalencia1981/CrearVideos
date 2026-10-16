"""Importa guiones exportados por otras herramientas.

Por ahora, el resumen de **StoryFlow Pro**: bloques `ESCENA #0001 [TIPO]` con
narración, texto en pantalla y el prompt de imagen ya escrito.

    ESCENA #0001 [PERSONAJE]
    NARRACIÓN: Si crees que lo peor que te puede pasar...
    TEXTO PANTALLA: (Ninguno)
    PROMPT: ACTION: Wilson stands in a sunlit kitchen...
    ------------------------------------------

Se convierte al formato propio (escenas separadas por `---`, con
`[imagen: ...]`), así que el mismo archivo sirve para generar el video entero o
solo el audio, sin tocarlo a mano. El PROMPT del export manda sobre lo que el
director deduciría: ya viene escrito plano por plano.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Cabecera de bloque: ESCENA #0007 [CONTEXTO]. El tipo es opcional.
_ESCENA_RE = re.compile(r"^\s*ESCENA\s*#?\s*(\d+)\s*(?:\[([^\]]*)\])?\s*$", re.MULTILINE)
#: Los campos de cada bloque, con y sin tilde (los exports varían).
_CAMPOS = {
    "narracion": ("NARRACIÓN", "NARRACION", "NARRACIÓN DE VOZ"),
    "pantalla": ("TEXTO PANTALLA", "TEXTO EN PANTALLA", "TEXTO"),
    "prompt": ("PROMPT", "IMAGEN", "PROMPT DE IMAGEN"),
}
#: Valores que significan "este campo va vacío".
_VACIOS = {"(ninguno)", "ninguno", "(none)", "none", "-", "n/a", "—"}


@dataclass
class Bloque:
    """Una escena tal como venía en el export."""

    numero: int
    tipo: str = ""
    narracion: str = ""
    pantalla: str = ""
    prompt: str = ""
    extra: dict[str, str] = field(default_factory=dict)


def es_storyflow(texto: str) -> bool:
    """¿Este texto es un export de StoryFlow Pro y no un guion normal?

    Se piden dos señales: la cabecera de escena y el campo de narración. Un
    guion normal que por casualidad mencione "ESCENA" en una frase no pasa.
    """
    if not _ESCENA_RE.search(texto):
        return False
    return bool(re.search(r"^\s*NARRACI[ÓO]N\s*:", texto, re.MULTILINE))


def _limpiar(valor: str) -> str:
    valor = " ".join(valor.split())
    return "" if valor.lower() in _VACIOS else valor


def _campos_del_bloque(cuerpo: str) -> dict[str, str]:
    """Separa los campos del bloque.

    Cada campo va desde su etiqueta hasta la siguiente etiqueta conocida, así
    que un valor de varias líneas (pasa con TEXTO PANTALLA) se conserva entero.
    """
    etiquetas = [alias for alias_s in _CAMPOS.values() for alias in alias_s]
    patron = re.compile(
        rf"^\s*({'|'.join(map(re.escape, etiquetas))})\s*:\s*(.*?)"
        rf"(?=^\s*(?:{'|'.join(map(re.escape, etiquetas))})\s*:|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    encontrados: dict[str, str] = {}
    for m in patron.finditer(cuerpo):
        etiqueta, valor = m.group(1).upper(), m.group(2)
        for campo, alias in _CAMPOS.items():
            if etiqueta in alias and campo not in encontrados:
                encontrados[campo] = _limpiar(valor)
    return encontrados


def parse_storyflow(texto: str) -> list[Bloque]:
    """Lee el export y devuelve sus escenas en orden."""
    marcas = list(_ESCENA_RE.finditer(texto))
    bloques: list[Bloque] = []
    for i, m in enumerate(marcas):
        fin = marcas[i + 1].start() if i + 1 < len(marcas) else len(texto)
        cuerpo = texto[m.end() : fin]
        # La línea de guiones que separa bloques no es parte de ningún campo.
        cuerpo = re.sub(r"^\s*-{5,}\s*$", "", cuerpo, flags=re.MULTILINE)
        campos = _campos_del_bloque(cuerpo)
        if not campos.get("narracion"):
            continue  # un bloque sin narración no se puede narrar ni montar
        bloques.append(
            Bloque(
                numero=int(m.group(1)),
                tipo=(m.group(2) or "").strip(),
                narracion=campos.get("narracion", ""),
                pantalla=campos.get("pantalla", ""),
                prompt=campos.get("prompt", ""),
            )
        )
    return bloques


def a_guion(bloques: list[Bloque]) -> str:
    """Convierte los bloques al formato de guion de CrearVideos."""
    partes = []
    for b in bloques:
        trozo = b.narracion
        if b.prompt:
            # El prompt puede traer corchetes; se quitan porque cierran el
            # marcador [imagen: ...] antes de tiempo y parten la descripción.
            trozo += f"\n[imagen: {b.prompt.replace('[', '(').replace(']', ')')}]"
        partes.append(trozo)
    return "\n\n---\n\n".join(partes) + "\n"


def convertir(texto: str) -> str:
    """Si el texto es un export conocido lo traduce; si no, lo devuelve igual."""
    if not es_storyflow(texto):
        return texto
    bloques = parse_storyflow(texto)
    return a_guion(bloques) if bloques else texto
