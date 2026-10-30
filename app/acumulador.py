"""Acumulador por teléfono (debounce): varios mensajes seguidos se responden una sola vez.

Las clientas suelen mandar tres mensajes en pocos segundos ("Hola", "¿tienes cita?", "para mañana").
Contestar a cada uno delata a un bot; se espera la ventana configurada, se reinicia con cada mensaje
nuevo y se procesa el conjunto como un solo texto (EARS-WA-16).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace

from app.conversacion import Entrante

log = logging.getLogger("acumulador")

Procesar = Callable[[Entrante], Awaitable[None]]
Dormir = Callable[[float], Awaitable[None]]


@dataclass
class _Cubeta:
    mensajes: list[Entrante] = field(default_factory=list)
    primero_en: float = 0.0
    tarea: asyncio.Task[None] | None = None
    esperando: bool = False


class Acumulador:
    def __init__(
        self,
        procesar: Procesar,
        ventana_s: float,
        dormir: Dormir = asyncio.sleep,
        espera_maxima_s: float = 20.0,
        max_mensajes: int = 20,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self._procesar = procesar
        self._ventana_s = ventana_s
        self._dormir = dormir
        self._espera_maxima_s = espera_maxima_s
        self._max_mensajes = max_mensajes
        self._reloj = reloj
        self._cubetas: dict[str, _Cubeta] = {}
        self._candados: dict[str, asyncio.Lock] = {}

    async def recibir(self, entrante: Entrante) -> None:
        """Acumula el mensaje; los toques de botón y los mensajes no textuales no se funden."""
        es_texto = entrante.texto is not None and entrante.id_interactivo is None
        if self._ventana_s <= 0 or not es_texto:
            await self._vaciar(entrante.telefono)  # primero lo que ya estaba esperando
            await self._despachar(entrante)
            return
        cubeta = self._cubetas.setdefault(entrante.telefono, _Cubeta())
        if not cubeta.mensajes:
            cubeta.primero_en = self._reloj()
        cubeta.mensajes.append(entrante)
        if cubeta.tarea is not None and cubeta.esperando:
            cubeta.tarea.cancel()  # reinicia la ventana; nunca se cancela un envío en marcha
        cubeta.esperando = True
        demasiado = (
            len(cubeta.mensajes) >= self._max_mensajes
            or self._reloj() - cubeta.primero_en >= self._espera_maxima_s
        )
        if demasiado:  # quien escribe sin parar no deja el lote abierto para siempre
            cubeta.esperando = False
            await self._vaciar(entrante.telefono)
            return
        cubeta.tarea = asyncio.create_task(self._temporizador(entrante.telefono))

    async def cerrar(self) -> None:
        """Al apagar, procesa de inmediato lo que quedaba esperando."""
        for telefono in list(self._cubetas):
            cubeta = self._cubetas[telefono]
            if cubeta.tarea is not None and cubeta.esperando:
                cubeta.tarea.cancel()
                cubeta.esperando = False
                with contextlib.suppress(asyncio.CancelledError):
                    await cubeta.tarea
            await self._vaciar(telefono)

    def descartar(self, telefono: str) -> bool:
        """Olvida lo que esperaba respuesta (la dueña ya intervino); True si había algo."""
        cubeta = self._cubetas.get(telefono)
        if cubeta is None or not cubeta.mensajes:
            return False
        cubeta.mensajes.clear()
        if cubeta.tarea is not None and cubeta.esperando:
            cubeta.tarea.cancel()
            cubeta.esperando = False
        return True

    def pendientes(self) -> int:
        """Cantidad de teléfonos con mensajes esperando su ventana."""
        return sum(1 for c in self._cubetas.values() if c.mensajes)

    async def _temporizador(self, telefono: str) -> None:
        await self._dormir(self._ventana_s)
        self._cubetas[telefono].esperando = False  # desde aquí ya no se puede cancelar
        await self._vaciar(telefono)

    async def _vaciar(self, telefono: str) -> None:
        cubeta = self._cubetas.get(telefono)
        if cubeta is None or not cubeta.mensajes:
            return
        mensajes, cubeta.mensajes = cubeta.mensajes, []
        if not cubeta.esperando:
            self._cubetas.pop(telefono, None)  # poda: no quedan cubetas vacías acumulándose
        textos = [m.texto.strip() for m in mensajes if m.texto and m.texto.strip()]
        if not textos:
            return
        ultimo = mensajes[-1]
        await self._despachar(replace(ultimo, texto="\n".join(textos)))

    async def _despachar(self, entrante: Entrante) -> None:
        candado = self._candados.setdefault(entrante.telefono, asyncio.Lock())
        async with candado:  # un teléfono a la vez: se conserva el orden
            try:
                await self._procesar(entrante)
            except Exception:  # noqa: BLE001 - un fallo no debe matar el acumulador
                log.exception("acumulador_error_inesperado")
