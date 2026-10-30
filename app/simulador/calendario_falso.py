"""Calendario en memoria: doble de pruebas y calendario del simulador (design.md §B6.2)."""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from app.puertos import (
    ErrorCalendario,
    ErrorSyncTokenExpirado,
    EventoCalendario,
    EventoCita,
    EventoNuevo,
    Intervalo,
    ResultadoListado,
)


@dataclass
class _Evento:
    event_id: str
    titulo: str
    descripcion: str
    inicio: datetime
    fin: datetime
    ubicacion: str | None
    telefono: str | None
    reserva_id: int | None
    cancelado: bool
    version: int


class CalendarioFalso:
    """Implementa el puerto Calendario con fallos programables y contadores de llamadas."""

    def __init__(self, *, retardo_s: float = 0.0) -> None:
        self._eventos: dict[str, _Evento] = {}
        self._version = 0
        self._tokens_vencidos_antes_de = 0
        self._fallos: dict[str, list[ErrorCalendario]] = defaultdict(list)
        self._lock = threading.RLock()
        self._oyentes: list[Callable[[], None]] = []
        self.retardo_s = retardo_s
        self.llamadas: dict[str, int] = defaultdict(int)
        self.conflictos_409 = 0

    # --- control para pruebas y simulador ---
    def programar_fallo(self, metodo: str, error: ErrorCalendario, veces: int = 1) -> None:
        """Hace que las próximas `veces` llamadas a `metodo` lancen `error`."""
        with self._lock:
            self._fallos[metodo].extend([error] * veces)

    def vencer_sync_tokens(self) -> None:
        """Todo sync token emitido hasta ahora responderá 410."""
        with self._lock:
            self._tokens_vencidos_antes_de = self._version + 1

    def agregar_evento_manual(
        self, inicio: datetime, fin: datetime, titulo: str = "Evento de la dueña"
    ) -> str:
        """Simula que la dueña agenda a mano."""
        with self._lock:
            event_id = f"manual{len(self._eventos) + 1:04d}"
            self._guardar(_Evento(event_id, titulo, "", inicio, fin, None, None, None, False, 0))
        self._avisar()
        return event_id

    def suscribir(self, oyente: Callable[[], None]) -> None:
        """Registra un oyente que se invoca cuando cambia el calendario."""
        self._oyentes.append(oyente)

    def eventos_activos(self) -> list[EventoCalendario]:
        """Eventos no cancelados, ordenados por inicio."""
        with self._lock:
            activos = [e for e in self._eventos.values() if not e.cancelado]
        return [
            EventoCalendario(e.event_id, e.inicio, e.fin, False)
            for e in sorted(activos, key=lambda e: e.inicio)
        ]

    def detalle(self, event_id: str) -> _Evento | None:
        """Devuelve el evento interno (titulo, descripción, ubicación) o None."""
        with self._lock:
            return self._eventos.get(event_id)

    def reiniciar(self) -> None:
        """Borra todos los eventos."""
        with self._lock:
            self._eventos.clear()
            self._version += 1
        self._avisar()

    # --- puerto Calendario ---
    def listar_eventos(
        self, calendario_id: str, *, sync_token: str | None, desde: datetime, hasta: datetime
    ) -> ResultadoListado:
        self._registrar("listar_eventos")
        with self._lock:
            if sync_token is None:
                eventos = [
                    e
                    for e in self._eventos.values()
                    if not e.cancelado and e.inicio < hasta and e.fin > desde
                ]
                completo = True
            else:
                base = self._leer_token(sync_token)
                eventos = [e for e in self._eventos.values() if e.version > base]
                completo = False
            token = f"t{self._version}"
        resultado = [EventoCalendario(e.event_id, e.inicio, e.fin, e.cancelado) for e in eventos]
        return ResultadoListado(resultado, token, resincronizacion_completa=completo)

    def ocupacion_en_vivo(
        self,
        calendario_id: str,
        desde: datetime,
        hasta: datetime,
        *,
        excluir_event_id: str | None = None,
    ) -> list[Intervalo]:
        self._registrar("ocupacion_en_vivo")
        with self._lock:
            return [
                Intervalo(e.inicio, e.fin)
                for e in self._eventos.values()
                if not e.cancelado
                and e.event_id != excluir_event_id
                and e.inicio < hasta
                and e.fin > desde
            ]

    def crear_evento(self, calendario_id: str, evento: EventoNuevo) -> str:
        self._registrar("crear_evento")
        if self.retardo_s:
            time.sleep(self.retardo_s)
        with self._lock:
            existente = self._eventos.get(evento.id)
            if existente is not None and not existente.cancelado:
                self.conflictos_409 += 1  # 409: ya existe, se trata como éxito
                return evento.id
            self._guardar(
                _Evento(
                    evento.id,
                    evento.titulo,
                    evento.descripcion,
                    evento.inicio,
                    evento.fin,
                    evento.ubicacion,
                    evento.telefono,
                    evento.reserva_id,
                    False,
                    0,
                )
            )
        self._avisar()
        return evento.id

    def eliminar_evento(self, calendario_id: str, event_id: str) -> None:
        self._registrar("eliminar_evento")
        with self._lock:
            existente = self._eventos.get(event_id)
            if existente is not None and not existente.cancelado:
                existente.cancelado = True
                self._version += 1
                existente.version = self._version
        self._avisar()

    def buscar_por_telefono(
        self, calendario_id: str, telefono: str, desde: datetime
    ) -> list[EventoCita]:
        self._registrar("buscar_por_telefono")
        with self._lock:
            return [
                EventoCita(e.event_id, e.inicio, e.fin, e.reserva_id)
                for e in self._eventos.values()
                if not e.cancelado and e.telefono == telefono and e.inicio >= desde
            ]

    # --- internos ---
    def _registrar(self, metodo: str) -> None:
        with self._lock:
            self.llamadas[metodo] += 1
            if self._fallos[metodo]:
                raise self._fallos[metodo].pop(0)

    def _guardar(self, evento: _Evento) -> None:
        self._version += 1
        evento.version = self._version
        self._eventos[evento.event_id] = evento

    def _leer_token(self, token: str) -> int:
        try:
            version = int(token.removeprefix("t"))
        except ValueError as exc:
            raise ErrorSyncTokenExpirado(token) from exc
        if version < self._tokens_vencidos_antes_de:
            raise ErrorSyncTokenExpirado(token)
        return version

    def _avisar(self) -> None:
        for oyente in list(self._oyentes):
            oyente()
