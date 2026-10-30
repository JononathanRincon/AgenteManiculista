"""Canal de notificaciones push de Calendar: registro, renovación y modo degradado (§B9.4).

Si no hay un canal vigente (URL no pública, Google rechaza el registro o el canal expiró), el
sistema queda en `modo_degradado`: refresca el espejo cada 5 minutos (EARS-CAC-04).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import uuid
from collections.abc import Callable
from urllib.parse import urlsplit

from sqlalchemy import Engine

from app.cache import CacheDisponibilidad
from app.modelos import CalendarioEstado, unidad_de_trabajo
from app.puertos import CanalesCalendario, ErrorCalendario, Reloj

log = logging.getLogger("canal")

TTL_POR_DEFECTO_S = 7 * 24 * 3600
MARGEN_POR_DEFECTO_S = 24 * 3600
INTERVALO_DEGRADADO_S = 300
_SUFIJOS_PRIVADOS = (".localhost", ".local", ".internal", ".test", ".lan", ".home.arpa")


def url_publica_https(url: str | None) -> bool:
    """True solo para una URL HTTPS con nombre o IP públicos (Google no entrega a otra cosa)."""
    if not url:
        return False
    try:
        partes = urlsplit(url)
        host = (partes.hostname or "").lower()
    except ValueError:
        return False
    if partes.scheme != "https" or not host or host == "localhost" or partes.username:
        return False
    if not partes.path.rstrip("/").endswith(
        "/webhook/calendar"
    ):  # un error de ruta perdería avisos
        return False
    if host.endswith(_SUFIJOS_PRIVADOS):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # un nombre sin punto es interno; solo dígitos y puntos (127.1) es una IP disfrazada
        return "." in host and not host.replace(".", "").isdigit()
    return ip.is_global


class GestorCanal:
    def __init__(
        self,
        engine: Engine,
        canales: CanalesCalendario,
        reloj: Reloj,
        calendario_id: str,
        *,
        url: str | None,
        token: str | None,
        ttl_s: int = TTL_POR_DEFECTO_S,
        margen_s: int = MARGEN_POR_DEFECTO_S,
        nuevo_id: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        self._engine = engine
        self._canales = canales
        self._reloj = reloj
        self._calendario_id = calendario_id
        self._url = url
        self._token = token
        self._ttl_s = ttl_s
        self._margen_s = margen_s
        self._nuevo_id = nuevo_id

    def asegurar(self) -> bool:
        """Deja un canal vigente (creando o renovando) y devuelve si el push está disponible."""
        ahora = self._reloj.ahora()
        with unidad_de_trabajo(self._engine, escritura=False) as s:
            estado = s.get(CalendarioEstado, self._calendario_id)
            canal_id = estado.canal_id if estado else None
            resource_id = estado.canal_resource_id if estado else None
            expira = estado.canal_expira_en if estado else None
        vigente = canal_id is not None and expira is not None and expira > ahora
        if not (self._token and url_publica_https(self._url)):
            self._marcar(degradado=True, motivo="url_no_publica_o_sin_token")
            return False
        if vigente and expira is not None and (expira - ahora).total_seconds() > self._margen_s:
            self._marcar(degradado=False)
            return True
        try:
            nuevo = self._canales.registrar_canal(
                self._calendario_id,
                canal_id=self._nuevo_id(),
                url=self._url or "",
                token=self._token,
                ttl_s=self._ttl_s,
            )
        except ErrorCalendario as exc:
            log.warning("canal_no_registrado", extra={"error": type(exc).__name__})
            self._marcar(degradado=not vigente, motivo="registro_fallido")
            return vigente  # si el anterior aún vale, se reintenta en el próximo ciclo
        with unidad_de_trabajo(self._engine) as s:
            fila = s.get(CalendarioEstado, self._calendario_id)
            if fila is None:
                fila = CalendarioEstado(calendario_id=self._calendario_id)
                s.add(fila)
            fila.canal_id = nuevo.id
            fila.canal_resource_id = nuevo.resource_id
            fila.canal_expira_en = nuevo.expira_en
            estaba_degradado = fila.modo_degradado is not False
            fila.modo_degradado = False
        if estaba_degradado:
            log.info("modo_degradado_terminado")
        if canal_id and resource_id:
            self._detener_anterior(canal_id, resource_id)
        log.info("canal_renovado", extra={"expira_en": nuevo.expira_en.isoformat()})
        return True

    def _detener_anterior(self, canal_id: str, resource_id: str) -> None:
        try:
            self._canales.detener_canal(canal_id, resource_id)
        except ErrorCalendario as exc:  # caduca solo; el token sigue siendo válido mientras tanto
            log.warning("canal_anterior_sin_detener", extra={"error": type(exc).__name__})

    def _marcar(self, *, degradado: bool, motivo: str = "") -> None:
        with unidad_de_trabajo(self._engine) as s:
            fila = s.get(CalendarioEstado, self._calendario_id)
            if fila is None:
                fila = CalendarioEstado(calendario_id=self._calendario_id)
                s.add(fila)
            cambio = fila.modo_degradado != degradado
            fila.modo_degradado = degradado
        if degradado and cambio:
            log.warning("modo_degradado", extra={"motivo": motivo})


async def mantener(
    gestor: GestorCanal,
    cache: CacheDisponibilidad,
    calendario_id: str,
    parar: asyncio.Event,
    *,
    intervalo_s: float = INTERVALO_DEGRADADO_S,
) -> None:
    """Cada ciclo: asegura el canal y, sin push, refresca el espejo a mano (EARS-CAC-03/04)."""
    while not parar.is_set():
        try:
            con_push = await asyncio.to_thread(gestor.asegurar)
            if not con_push:
                await asyncio.to_thread(cache.resincronizar, calendario_id)
        except Exception as exc:  # un ciclo fallido no debe matar la tarea
            log.error("mantenimiento_calendario_fallido", extra={"error": type(exc).__name__})
        try:
            await asyncio.wait_for(parar.wait(), timeout=intervalo_s)
        except TimeoutError:
            continue
