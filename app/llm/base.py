"""Puerto del proveedor de lenguaje: interpreta texto, nunca ejecuta (ADR-001)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from app.intencion import Intencion


@dataclass(frozen=True)
class ServicioResumen:
    """Lo único del catálogo que puede ver el LLM: id y nombre."""

    id: str
    nombre: str


class ProveedorLLM(Protocol):
    def interpretar(
        self, texto: str, catalogo: list[ServicioResumen], hoy: date, estado: str
    ) -> Intencion | None:
        """Interpreta el texto; devuelve None ante cualquier fallo (nunca lanza)."""
        ...
