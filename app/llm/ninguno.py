"""Proveedor nulo: con `PROVEEDOR_LLM=none` todo funciona con botones y palabras clave."""

from __future__ import annotations

from datetime import date

from app.intencion import Intencion
from app.llm.base import ServicioResumen


class Ninguno:
    def interpretar(
        self, texto: str, catalogo: list[ServicioResumen], hoy: date, estado: str
    ) -> Intencion | None:
        return None
