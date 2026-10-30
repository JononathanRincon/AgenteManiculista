"""Relojes: el del sistema y uno controlable para pruebas y simulador (design.md §1.3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class RelojSistema:
    def ahora(self) -> datetime:
        """Hora actual en UTC."""
        return datetime.now(UTC)


class RelojFalso:
    """Reloj que solo avanza cuando se le ordena; los tests nunca duermen de verdad."""

    def __init__(self, inicio: datetime) -> None:
        self._ahora = inicio

    def ahora(self) -> datetime:
        return self._ahora

    def avanzar(self, **kwargs: float) -> None:
        """Avanza el reloj (por ejemplo, `avanzar(seconds=61)`)."""
        self._ahora += timedelta(**kwargs)

    def fijar(self, instante: datetime) -> None:
        """Fija el reloj en un instante."""
        self._ahora = instante
