"""Límite de tasa por clave (IP o teléfono) con ventana deslizante en memoria (design.md §4)."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable


class LimiteTasa:
    def __init__(
        self, maximo: int, ventana_s: float, reloj: Callable[[], float] = time.monotonic
    ) -> None:
        self._maximo = maximo
        self._ventana_s = ventana_s
        self._reloj = reloj
        self._eventos: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def permitir(self, clave: str) -> bool:
        """Registra un intento; False si la clave superó el máximo dentro de la ventana."""
        ahora = self._reloj()
        with self._lock:
            cola = self._eventos.setdefault(clave, deque())
            while cola and ahora - cola[0] >= self._ventana_s:
                cola.popleft()
            if len(cola) >= self._maximo:
                return False
            cola.append(ahora)
            if len(self._eventos) > 10_000:  # acota la memoria ante muchas claves distintas
                self._limpiar(ahora)
            return True

    def _limpiar(self, ahora: float) -> None:
        vencidas = [
            clave
            for clave, cola in self._eventos.items()
            if not cola or ahora - cola[-1] >= self._ventana_s
        ]
        for clave in vencidas:
            del self._eventos[clave]
