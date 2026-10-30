"""Humanización de los envíos: leído, "escribiendo…", pausa y envío único (EARS-WA-06..09).

Meta detecta bots por respuestas instantáneas y flujos mecánicos. Toda la política vive aquí, en
Python, con `Dormir` y `azar` inyectables: las pruebas y el simulador no esperan de verdad.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Protocol

from app.config import AntiBan

Dormir = Callable[[float], None]
Azar = Callable[[float, float], float]


def sin_espera(segundos: float) -> None:
    """`Dormir` nulo para pruebas y simulador (retardo 0)."""


def tiempo_lectura(cfg: AntiBan, azar: Azar = random.uniform) -> float:
    """Pausa aleatoria antes de marcar leído: 1,5 a 3 s por defecto (EARS-WA-06)."""
    return azar(cfg.lectura_min_ms, cfg.lectura_max_ms) / 1000


def tiempo_escritura(texto: str, cfg: AntiBan, azar: Azar = random.uniform) -> float:
    """~45 ms por carácter, entre 2,5 y 6 s, más un jitter de −400 a +600 ms (EARS-WA-08)."""
    proporcional = len(texto) * cfg.escritura_ms_por_caracter
    base = min(max(proporcional, cfg.escritura_min_ms), cfg.escritura_max_ms)
    return max(0.0, (base + azar(cfg.jitter_min_ms, cfg.jitter_max_ms)) / 1000)


class Sidecar(Protocol):
    """Primitivas que expone el sidecar de Baileys; no contiene lógica de negocio."""

    def leer(self, telefono: str, id_mensaje: str) -> None: ...

    def presencia(self, telefono: str, estado: str) -> None: ...

    def enviar(self, telefono: str, texto: str) -> str: ...


class SecuenciaEnvio:
    def __init__(
        self,
        sidecar: Sidecar,
        cfg: AntiBan,
        dormir: Dormir = sin_espera,
        azar: Azar = random.uniform,
    ) -> None:
        self._sidecar = sidecar
        self._cfg = cfg
        self._dormir = dormir
        self._azar = azar

    def enviar_respuesta(self, telefono: str, texto: str, id_entrante: str | None = None) -> str:
        """leer (si hay mensaje entrante) → composing → espera → paused → un único envío."""
        if id_entrante is not None:
            self._dormir(tiempo_lectura(self._cfg, self._azar))
            self._sidecar.leer(telefono, id_entrante)
        self._sidecar.presencia(telefono, "composing")
        try:
            self._dormir(tiempo_escritura(texto, self._cfg, self._azar))
        finally:
            self._sidecar.presencia(telefono, "paused")
        return self._sidecar.enviar(telefono, texto)
