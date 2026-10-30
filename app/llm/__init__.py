"""Proveedores de lenguaje intercambiables por entorno."""

from __future__ import annotations

import importlib

from app.config import Settings
from app.llm.base import ProveedorLLM
from app.llm.ninguno import Ninguno


def crear_proveedor(settings: Settings) -> ProveedorLLM:
    """Fábrica según `PROVEEDOR_LLM`. El adaptador de Gemini se importa solo si se usa."""
    if settings.proveedor_llm == "gemini":
        modulo = importlib.import_module("app.llm.gemini")
        proveedor: ProveedorLLM = modulo.ProveedorGemini.desde_settings(settings)
        return proveedor
    return Ninguno()
