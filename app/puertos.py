"""Puertos del dominio (arquitectura hexagonal): Calendario, Mensajeria y Reloj.

ProveedorLLM se agrega en el Bloque 7.

El dominio solo depende de este módulo; los adaptadores (Google, WhatsApp, Gemini, simulador)
implementan estos contratos. Todos los puertos son síncronos: el código asíncrono los invoca con
`asyncio.to_thread`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


# --- Errores de calendario -------------------------------------------------------------------
class ErrorCalendario(Exception):
    """Base de los errores del puerto Calendario."""


class ErrorSyncTokenExpirado(ErrorCalendario):
    """HTTP 410: el sync token venció; hay que resincronizar por completo (EARS-CAC-05)."""


class ErrorTransitorio(ErrorCalendario):
    """5xx o red: se reintenta con backoff."""


class CuotaExcedida(ErrorCalendario):  # noqa: N818 - nombre fijado por el diseño
    """403/429 con usageLimits: se reintenta y se pausa la cola (EARS-COL-04)."""


class ErrorPermanente(ErrorCalendario):
    """4xx de datos: no se reintenta."""


# --- Tipos de calendario ---------------------------------------------------------------------
@dataclass(frozen=True)
class Intervalo:
    inicio: datetime
    fin: datetime


@dataclass(frozen=True)
class EventoNuevo:
    id: str
    titulo: str
    descripcion: str
    inicio: datetime
    fin: datetime
    telefono: str
    reserva_id: int
    ubicacion: str | None = None


@dataclass(frozen=True)
class EventoCita:
    event_id: str
    inicio: datetime
    fin: datetime
    reserva_id: int | None


@dataclass(frozen=True)
class EventoCalendario:
    """Evento tal como lo devuelve un listado (espejo local)."""

    event_id: str
    inicio: datetime
    fin: datetime
    cancelado: bool


@dataclass(frozen=True)
class ResultadoListado:
    eventos: list[EventoCalendario]
    nuevo_sync_token: str | None
    resincronizacion_completa: bool = False


class Calendario(Protocol):
    def listar_eventos(
        self, calendario_id: str, *, sync_token: str | None, desde: datetime, hasta: datetime
    ) -> ResultadoListado:
        """Eventos cambiados desde `sync_token` (o listado completo del rango si es None)."""
        ...

    def ocupacion_en_vivo(
        self,
        calendario_id: str,
        desde: datetime,
        hasta: datetime,
        *,
        excluir_event_id: str | None = None,
    ) -> list[Intervalo]:
        """Intervalos ocupados leídos directamente de Calendar (nunca de caché)."""
        ...

    def crear_evento(self, calendario_id: str, evento: EventoNuevo) -> str:
        """Crea el evento con id determinista; un duplicado (409) cuenta como éxito."""
        ...

    def eliminar_evento(self, calendario_id: str, event_id: str) -> None:
        """Elimina el evento; si ya no existe (404/410) cuenta como éxito."""
        ...

    def buscar_por_telefono(
        self, calendario_id: str, telefono: str, desde: datetime
    ) -> list[EventoCita]:
        """Citas futuras de una clienta por `extendedProperties.private.telefono`."""
        ...


@dataclass(frozen=True)
class CanalCalendar:
    """Canal de notificaciones push (`events.watch`) registrado en Google."""

    id: str
    resource_id: str
    expira_en: datetime


class CanalesCalendario(Protocol):
    def registrar_canal(
        self, calendario_id: str, *, canal_id: str, url: str, token: str, ttl_s: int
    ) -> CanalCalendar:
        """Pide a Google que avise cambios del calendario a `url`."""
        ...

    def detener_canal(self, canal_id: str, resource_id: str) -> None:
        """Detiene un canal; si ya expiró (404) cuenta como éxito."""
        ...


# --- Mensajería --------------------------------------------------------------------------------
@dataclass(frozen=True)
class Opcion:
    id: str
    titulo: str
    descripcion: str = ""


class Mensajeria(Protocol):
    def enviar_texto(self, telefono: str, texto: str) -> None: ...

    def enviar_botones(self, telefono: str, cuerpo: str, opciones: list[Opcion]) -> None:
        """Hasta 3 botones."""
        ...

    def enviar_lista(self, telefono: str, cuerpo: str, boton: str, filas: list[Opcion]) -> None:
        """Hasta 10 filas."""
        ...


# --- Reloj --------------------------------------------------------------------------------------
class Reloj(Protocol):
    def ahora(self) -> datetime:
        """Instante actual con zona UTC."""
        ...
