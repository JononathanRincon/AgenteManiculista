"""Adaptador de Google Calendar v3 con cuenta de servicio (design.md §B9.2).

Implementa el puerto `Calendario`. Verificado contra la documentación oficial de Calendar API v3
(guía de sincronización, `events.list/insert/delete/get`, errores de uso); lo que se desprende:

- Sincronización: la solicitud con `syncToken` no admite `timeMin`, `timeMax`, `q`, `orderBy`,
  `updatedMin`, `iCalUID` ni `privateExtendedProperty`, y exige `showDeleted` distinto de false.
  El `nextSyncToken` solo llega en la última página, y vence con HTTP 410.
- `insert` con `id` propio: un id repetido responde 409; Google reserva el id de un evento
  borrado, así que un 409 solo es éxito si el evento sigue vigente.
- Un evento borrado llega como `status: cancelled` y, en sincronización, solo con su `id`.
- Los eventos de día completo traen `start.date` y `end.date` (exclusivo), no `dateTime`.
"""

from __future__ import annotations

import http.client
import json
import re
import threading
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import google_auth_httplib2  # type: ignore[import-untyped]
import httplib2  # type: ignore[import-untyped]
from google.auth.exceptions import RefreshError, TransportError
from google.oauth2 import service_account
from googleapiclient.discovery import build  # type: ignore[import-untyped]
from googleapiclient.errors import HttpError  # type: ignore[import-untyped]

from app.config import ConfiguracionInvalida
from app.puertos import (
    CuotaExcedida,
    ErrorPermanente,
    ErrorSyncTokenExpirado,
    ErrorTransitorio,
    EventoCalendario,
    EventoCita,
    EventoNuevo,
    Intervalo,
    ResultadoListado,
)

SCOPE = "https://www.googleapis.com/auth/calendar.events"
TIMEOUT_S = 10.0
MAX_PAGINAS = 100
POR_PAGINA = 2500
SIN_HORARIO = datetime(1970, 1, 1, tzinfo=UTC)  # los eventos borrados llegan sin horario
_RAZONES_CUOTA = frozenset(
    {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded", "dailyLimitExceeded"}
)
_TELEFONO = re.compile(r"[0-9]{6,20}", re.ASCII)

type Servicio = Any
type FabricaServicio = Callable[[], Servicio]


class CalendarioGoogle:
    """Google Calendar v3. Cada hilo crea su propio cliente (httplib2 no es seguro entre hilos)."""

    def __init__(self, fabrica_servicio: FabricaServicio, zona: ZoneInfo) -> None:
        self._fabrica = fabrica_servicio
        self._zona = zona
        self._hilo = threading.local()

    @classmethod
    def desde_cuenta_de_servicio(
        cls, credencial: str, zona: ZoneInfo, *, timeout_s: float = TIMEOUT_S
    ) -> CalendarioGoogle:
        """`credencial` es el JSON de la cuenta de servicio o la ruta a ese archivo."""
        credenciales = _leer_credenciales(credencial)

        def fabrica() -> Servicio:
            http = google_auth_httplib2.AuthorizedHttp(
                credenciales, http=httplib2.Http(timeout=timeout_s)
            )
            return build("calendar", "v3", http=http, static_discovery=True, cache_discovery=False)

        return cls(fabrica, zona)

    # --- puerto Calendario -------------------------------------------------------------------
    def listar_eventos(
        self, calendario_id: str, *, sync_token: str | None, desde: datetime, hasta: datetime
    ) -> ResultadoListado:
        # La ventana se aplica aquí y no en la consulta: con timeMin/timeMax Google no entrega
        # un sync token utilizable.
        parametros: dict[str, Any] = {
            "calendarId": calendario_id,
            "singleEvents": True,
            "maxResults": POR_PAGINA,
        }
        if sync_token is not None:
            parametros |= {"syncToken": sync_token, "showDeleted": True}
        items, token = self._recorrer(parametros, sync=sync_token is not None)
        incremental = sync_token is not None
        eventos: list[EventoCalendario] = []
        for item in items:
            evento = self._a_evento(item, desde, hasta, incremental=incremental)
            if evento is not None:
                eventos.append(evento)
        return ResultadoListado(eventos, token, resincronizacion_completa=not incremental)

    def ocupacion_en_vivo(
        self,
        calendario_id: str,
        desde: datetime,
        hasta: datetime,
        *,
        excluir_event_id: str | None = None,
    ) -> list[Intervalo]:
        parametros: dict[str, Any] = {
            "calendarId": calendario_id,
            "timeMin": _rfc3339(desde),
            "timeMax": _rfc3339(hasta),
            "singleEvents": True,
            "maxResults": POR_PAGINA,
        }
        items, _ = self._recorrer(parametros)
        ocupado: list[Intervalo] = []
        for item in items:
            if item.get("status") == "cancelled" or item.get("id") == excluir_event_id:
                continue
            horario = self._horario(item)
            if horario is not None:  # cualquier evento bloquea, aunque figure como "libre"
                ocupado.append(Intervalo(*horario))
        return ocupado

    def crear_evento(self, calendario_id: str, evento: EventoNuevo) -> str:
        cuerpo: dict[str, Any] = {
            "id": evento.id,
            "summary": evento.titulo,
            "description": evento.descripcion,
            "start": {"dateTime": _rfc3339(evento.inicio)},
            "end": {"dateTime": _rfc3339(evento.fin)},
            "extendedProperties": {
                "private": {"telefono": evento.telefono, "reserva_id": str(evento.reserva_id)}
            },
        }
        if evento.ubicacion:
            cuerpo["location"] = evento.ubicacion
        peticion = self._servicio().events().insert(calendarId=calendario_id, body=cuerpo)
        if self._ejecutar(peticion, ignorar=(409,)) is None:
            self._verificar_vigente(calendario_id, evento.id)
        return evento.id

    def eliminar_evento(self, calendario_id: str, event_id: str) -> None:
        peticion = self._servicio().events().delete(calendarId=calendario_id, eventId=event_id)
        self._ejecutar(peticion, ignorar=(404, 410))  # ya no existe: el objetivo se cumplió

    def buscar_por_telefono(
        self, calendario_id: str, telefono: str, desde: datetime
    ) -> list[EventoCita]:
        if not _TELEFONO.fullmatch(telefono):  # el valor viaja dentro de "clave=valor"
            raise ErrorPermanente("teléfono inválido para la búsqueda")
        parametros: dict[str, Any] = {
            "calendarId": calendario_id,
            "privateExtendedProperty": f"telefono={telefono}",
            "timeMin": _rfc3339(desde),
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": 250,
        }
        items, _ = self._recorrer(parametros)
        citas: list[EventoCita] = []
        for item in items:
            horario = self._horario(item)
            if item.get("status") == "cancelled" or horario is None:
                continue
            privadas = item.get("extendedProperties", {}).get("private", {})
            citas.append(EventoCita(item["id"], *horario, _entero(privadas.get("reserva_id"))))
        return citas

    # --- internos ----------------------------------------------------------------------------
    def _servicio(self) -> Servicio:
        servicio = getattr(self._hilo, "servicio", None)
        if servicio is None:
            servicio = self._fabrica()
            self._hilo.servicio = servicio
        return servicio

    def _recorrer(
        self, parametros: dict[str, Any], *, sync: bool = False
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Todas las páginas de `events.list`; el sync token viene en la última."""
        items: list[dict[str, Any]] = []
        pagina: str | None = None
        for _ in range(MAX_PAGINAS):
            consulta = parametros | ({"pageToken": pagina} if pagina else {})
            respuesta = self._ejecutar(self._servicio().events().list(**consulta), sync=sync)
            items.extend(respuesta.get("items", []))
            pagina = respuesta.get("nextPageToken")
            if pagina is None:
                return items, respuesta.get("nextSyncToken")
        raise ErrorPermanente("el calendario tiene demasiadas páginas de eventos")

    def _ejecutar(self, peticion: Any, *, sync: bool = False, ignorar: Iterable[int] = ()) -> Any:
        """Ejecuta una petición y traduce los fallos al vocabulario del puerto.

        Sin reintentos propios (`num_retries=0`): reintentar es trabajo del worker (EARS-COL-03).
        Devuelve None si el estado HTTP está en `ignorar`.
        """
        try:
            return peticion.execute(num_retries=0)
        except HttpError as exc:
            if exc.resp.status in tuple(ignorar):
                return None
            raise _traducir(exc, sync=sync) from exc
        except RefreshError as exc:  # credenciales rechazadas: reintentar no arregla nada
            raise ErrorPermanente(
                "Google rechazó las credenciales de la cuenta de servicio"
            ) from exc
        except (OSError, httplib2.HttpLib2Error, http.client.HTTPException, TransportError) as exc:
            raise ErrorTransitorio(f"fallo de red con Calendar: {type(exc).__name__}") from exc

    def _verificar_vigente(self, calendario_id: str, event_id: str) -> None:
        """Tras un 409: el id existe, pero solo es éxito si el evento no fue eliminado."""
        peticion = self._servicio().events().get(calendarId=calendario_id, eventId=event_id)
        existente = self._ejecutar(peticion, ignorar=(404, 410))
        if existente is None or existente.get("status") == "cancelled":
            raise ErrorPermanente("el id del evento pertenece a un evento eliminado")

    def _a_evento(
        self, item: dict[str, Any], desde: datetime, hasta: datetime, *, incremental: bool
    ) -> EventoCalendario | None:
        if item.get("status") == "cancelled":  # en el listado completo el espejo ya está vacío
            return (
                EventoCalendario(item["id"], SIN_HORARIO, SIN_HORARIO, True)
                if incremental
                else None
            )
        horario = self._horario(item)
        if horario is None:
            return None
        inicio, fin = horario
        if inicio < hasta and fin > desde:
            return EventoCalendario(item["id"], inicio, fin, False)
        # fuera de la ventana: en incremental puede ser un evento que se movió y hay que sacarlo
        # del espejo; en el listado completo el espejo ya se vació.
        return EventoCalendario(item["id"], inicio, fin, True) if incremental else None

    def _horario(self, item: dict[str, Any]) -> tuple[datetime, datetime] | None:
        inicio = self._instante(item.get("start", {}))
        fin = self._instante(item.get("end", {}))
        if inicio is None or fin is None:
            return None
        return inicio, fin

    def _instante(self, marca: dict[str, Any]) -> datetime | None:
        if "dateTime" in marca:
            valor = datetime.fromisoformat(marca["dateTime"])
            if valor.tzinfo is None:
                valor = valor.replace(tzinfo=ZoneInfo(marca.get("timeZone", self._zona.key)))
            return valor.astimezone(UTC)
        if "date" in marca:  # día completo: medianoche local
            dia = date.fromisoformat(marca["date"])
            return datetime(dia.year, dia.month, dia.day, tzinfo=self._zona).astimezone(UTC)
        return None


def _rfc3339(valor: datetime) -> str:
    return valor.astimezone(UTC).isoformat()


def _entero(valor: Any) -> int | None:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _razones(exc: HttpError) -> set[str]:
    """Dominios y razones del cuerpo de error de Google (vacío si el cuerpo no es JSON)."""
    try:
        errores = json.loads(exc.content)["error"]["errors"]
        return {str(e.get(campo)) for e in errores for campo in ("domain", "reason")}
    except (ValueError, KeyError, TypeError, AttributeError):
        return set()


def _traducir(exc: HttpError, *, sync: bool) -> Exception:
    estado = exc.resp.status
    detalle = f"Calendar respondió {estado}"
    if estado == 410 and sync:
        return ErrorSyncTokenExpirado(detalle)
    if estado == 429:
        return CuotaExcedida(detalle)
    if estado == 403:
        razones = _razones(exc)
        if "usageLimits" in razones or razones & _RAZONES_CUOTA:
            return CuotaExcedida(detalle)
        return ErrorPermanente(detalle)  # sin permiso: el calendario no está compartido
    if estado >= 500 or estado == 408:
        return ErrorTransitorio(detalle)
    return ErrorPermanente(detalle)


def _leer_credenciales(credencial: str) -> service_account.Credentials:
    """Credenciales de la cuenta de servicio desde el JSON o desde un archivo (secret file)."""
    try:
        texto = (
            credencial
            if credencial.lstrip().startswith("{")
            else Path(credencial).read_text(encoding="utf-8")
        )
        credenciales: service_account.Credentials = (
            service_account.Credentials.from_service_account_info(  # type: ignore[no-untyped-call]
                json.loads(texto), scopes=[SCOPE]
            )
        )
        return credenciales
    except (OSError, ValueError) as exc:
        # sin el contenido: contiene la clave privada
        raise ConfiguracionInvalida(
            "GOOGLE_SERVICE_ACCOUNT_JSON no es un JSON de cuenta de servicio válido"
        ) from exc
