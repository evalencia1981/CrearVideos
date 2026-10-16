"""Contrato común de los proveedores de imagen."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class ImageProvider(ABC):
    name: str = "base"
    #: Costo aproximado por imagen en USD, para el resumen de gasto.
    cost_per_image: float = 0.0

    @property
    def fingerprint_extra(self) -> str:
        """Parte extra de la huella de caché, más allá del nombre del proveedor.

        Sirve para que un cambio que no está en el prompt (el modelo, una imagen
        de referencia) también regenere las imágenes en vez de reutilizarlas.
        Por defecto nada; los proveedores que lo necesiten lo sobrescriben.
        """
        return ""

    @abstractmethod
    def generate(
        self, prompt: str, out_path: Path, *, index: int = 0, subject: str = ""
    ) -> Path:
        """Genera una imagen vertical en `out_path` y devuelve su ruta.

        `prompt` trae el estilo global concatenado; `subject` es solo la
        descripción de esta escena. Los generadores de IA usan el primero,
        los buscadores de stock el segundo.
        """

    def preflight(self) -> None:
        """Comprueba que el proveedor puede trabajar. Lanza si no.

        Se llama antes de generar nada. Sin esto, un .env sin la clave se
        descubre después de haber narrado el guion entero: medio minuto de
        trabajo tirado para acabar en un error que se sabía desde el principio.
        """
