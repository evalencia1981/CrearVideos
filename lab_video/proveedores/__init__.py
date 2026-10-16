"""Registro de proveedores: nombre (config.Modelo.proveedor) -> clase."""

from __future__ import annotations

from ..config import MODELOS
from .base import ErrorProveedor, Generacion, VideoProvider
from .gemini import GeminiOmni, GeminiVeo

PROVEEDORES: dict[str, type[VideoProvider]] = {
    GeminiVeo.nombre: GeminiVeo,
    GeminiOmni.nombre: GeminiOmni,
}


def proveedor_para(clave_modelo: str) -> VideoProvider:
    modelo = MODELOS[clave_modelo]
    return PROVEEDORES[modelo.proveedor](modelo)


__all__ = ["ErrorProveedor", "Generacion", "VideoProvider", "PROVEEDORES", "proveedor_para"]
