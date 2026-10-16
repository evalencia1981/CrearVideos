"""Interfaz común de los proveedores de imagen a video.

Un proveedor sabe pedir UN clip a partir de una imagen y un prompt, y decir
cuánto costaría antes de pedirlo. Todo lo demás (presupuesto, caché,
concurrencia, métricas) lo hace el laboratorio, igual para todos.

Para sumar otro proveedor (Kling, Runway, Hailuo, fal…): una clase que
herede de VideoProvider, registrada en proveedores/__init__.py, y sus modelos
en config.MODELOS.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Modelo


class ErrorProveedor(RuntimeError):
    """Fallo de generación con un motivo legible (filtro, cuota, tiempo…)."""


@dataclass
class Generacion:
    """Lo que devuelve un proveedor tras generar."""

    mp4: Path
    #: Costo que informa la propia API (p. ej. tokens de Omni). None si no lo da.
    costo_real_usd: float | None = None
    costo_real_fuente: str = ""
    #: Lo que la API diga y valga la pena guardar (tokens, ids, avisos).
    extra: dict = field(default_factory=dict)


class VideoProvider(ABC):
    nombre: str = ""

    def __init__(self, modelo: Modelo):
        self.modelo = modelo

    @property
    def modelo_id(self) -> str:
        return self.modelo.id

    def duracion_valida(self, duracion: float) -> int:
        """La duración permitida más cercana a la pedida (empate: la menor)."""
        return min(self.modelo.duraciones, key=lambda d: (abs(d - duracion), d))

    def validar(self, duracion: int, resolucion: str) -> None:
        if resolucion not in self.modelo.resoluciones:
            raise ErrorProveedor(
                f"{self.modelo.clave} no admite {resolucion}; "
                f"sí: {', '.join(self.modelo.resoluciones)}"
            )
        if resolucion in self.modelo.solo_8s and duracion != max(self.modelo.duraciones):
            raise ErrorProveedor(f"{self.modelo.clave} solo da {resolucion} con 8 s")

    def estimar_costo(self, duracion: int, resolucion: str = "720p") -> float:
        """USD estimados para un clip, con el precio publicado por segundo."""
        return round(self.modelo.resoluciones[resolucion] * duracion, 4)

    @abstractmethod
    def generar(
        self, imagen: Path, prompt: str, duracion: int, aspecto: str,
        resolucion: str, destino: Path,
    ) -> Generacion:
        """Genera el clip con `imagen` como primer fotograma y lo guarda en `destino`."""
