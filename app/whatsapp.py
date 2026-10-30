"""Adaptador de WhatsApp sobre el sidecar de Baileys: cliente HTTP y puerto `Mensajeria`.

El sidecar solo ofrece tres primitivas (leer, presencia, enviar). La humanización, los menús
numerados y el rate limit viven en Python (ver `humanizacion`, `menus_numericos`, `control_humano`).
"""

from __future__ import annotations

import logging
import random
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

import httpx

from app.control_humano import ControlHumano
from app.conversacion import Saliente
from app.humanizacion import Dormir, SecuenciaEnvio
from app.menus_numericos import componer, degradar
from app.puertos import Opcion, Reloj

log = logging.getLogger("whatsapp")

SUFIJO_JID = "@s.whatsapp.net"
TIMEOUT_S = 10.0
INTENTOS = 3
MAX_EMITIDOS = 500
MAX_CANDADOS = 1000
CODIGOS_REINTENTABLES = {429, 500, 502, 503, 504}


class ErrorSidecar(Exception):
    """El sidecar no pudo atender la petición (tras los reintentos o por un error de datos)."""


class ClienteSidecar:
    """Cliente HTTP del sidecar con reintentos y backoff exponencial (EARS-WA-05)."""

    def __init__(
        self,
        url_base: str,
        token: str,
        cliente: httpx.Client | None = None,
        dormir: Dormir = time.sleep,
        azar: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._http = cliente or httpx.Client(base_url=url_base, timeout=TIMEOUT_S)
        self._cabeceras = {"Authorization": f"Bearer {token}"}
        self._dormir = dormir
        self._azar = azar

    def leer(self, telefono: str, id_mensaje: str) -> None:
        self._enviar("/leer", {"jid": _jid(telefono), "id": id_mensaje})

    def presencia(self, telefono: str, estado: str) -> None:
        self._enviar("/presencia", {"jid": _jid(telefono), "estado": estado})

    def enviar(self, telefono: str, texto: str) -> str:
        """Envía un texto; la `clave` única evita duplicados si hay que reintentar."""
        cuerpo = {"jid": _jid(telefono), "texto": texto, "clave": uuid.uuid4().hex}
        respuesta = self._enviar("/enviar", cuerpo)
        identificador = respuesta.get("id")
        if not isinstance(identificador, str) or not identificador:
            raise ErrorSidecar("respuesta de /enviar sin id")
        return identificador

    def qr(self) -> str | None:
        """QR de vinculación en texto (ASCII), o None si no hay uno pendiente."""
        try:
            r = self._http.get("/qr", headers=self._cabeceras)
        except httpx.TransportError as exc:
            raise ErrorSidecar(f"/qr inalcanzable ({type(exc).__name__})") from exc
        if r.status_code == 404:
            return None
        if r.status_code >= 300:
            raise ErrorSidecar(f"/qr respondió {r.status_code}")
        return r.text

    def estado(self) -> dict[str, Any]:
        """Estado de la sesión de WhatsApp según el sidecar (para /health)."""
        return self._enviar("/estado", None, metodo="GET")

    def _enviar(
        self, ruta: str, cuerpo: dict[str, Any] | None, metodo: str = "POST"
    ) -> dict[str, Any]:
        ultimo: str = "sin intentos"
        for intento in range(1, INTENTOS + 1):
            try:
                r = self._http.request(metodo, ruta, json=cuerpo, headers=self._cabeceras)
            except httpx.TransportError as exc:  # timeout o red
                ultimo = type(exc).__name__
            else:
                if r.status_code < 300:
                    return _json(r)
                if r.status_code not in CODIGOS_REINTENTABLES:
                    raise ErrorSidecar(f"{ruta} respondió {r.status_code}")
                ultimo = f"HTTP {r.status_code}"
            if intento < INTENTOS:
                self._dormir(2 ** (intento - 1) + self._azar(0.0, 0.5))
        log.error("sidecar_sin_respuesta", extra={"ruta": ruta, "motivo": ultimo})
        raise ErrorSidecar(f"{ruta} falló tras {INTENTOS} intentos ({ultimo})")


def _json(respuesta: httpx.Response) -> dict[str, Any]:
    try:
        datos = respuesta.json()
    except ValueError as exc:
        raise ErrorSidecar("respuesta que no es JSON") from exc
    if not isinstance(datos, dict):
        raise ErrorSidecar("respuesta con forma inesperada")
    return datos


def _jid(telefono: str) -> str:
    return f"{telefono}{SUFIJO_JID}"


class MensajeriaBaileys:
    """Implementa el puerto `Mensajeria` y el `Emisor` de la pasarela."""

    def __init__(
        self,
        secuencia: SecuenciaEnvio,
        control: ControlHumano,
        reloj: Reloj,
    ) -> None:
        self._secuencia = secuencia
        self._control = control
        self._reloj = reloj
        self._emitidos: OrderedDict[str, None] = OrderedDict()
        self._candados: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

    # --- puerto Mensajeria (avisos del worker, fuera de una conversación en curso) ---
    def enviar_texto(self, telefono: str, texto: str) -> None:
        ahora = self._reloj.ahora()
        if self._control.esta_pausado(telefono, ahora):  # la dueña está atendiendo a mano
            log.info("envio_descartado", extra={"motivo": "pausa_humana"})
            return
        if not self._control.autorizar_envio(telefono, ahora):
            log.warning("envio_descartado", extra={"motivo": "rate_limit_o_sin_conversacion"})
            return
        self._enviar(telefono, texto, None)

    def enviar_botones(self, telefono: str, cuerpo: str, opciones: list[Opcion]) -> None:
        self.enviar_texto(telefono, degradar(Saliente("botones", cuerpo, opciones)).cuerpo)

    def enviar_lista(self, telefono: str, cuerpo: str, boton: str, filas: list[Opcion]) -> None:
        self.enviar_texto(telefono, degradar(Saliente("lista", cuerpo, filas, boton)).cuerpo)

    # --- respuesta a un mensaje entrante (la pasarela ya autorizó el envío) ---
    def responder(self, telefono: str, salidas: list[Saliente], id_entrante: str | None) -> None:
        mensaje = componer(salidas)
        if mensaje is not None:
            self._enviar(telefono, mensaje.cuerpo, id_entrante)

    def fue_emitido(self, id_mensaje: str) -> bool:
        with self._lock:
            return id_mensaje in self._emitidos

    def _enviar(self, telefono: str, texto: str, id_entrante: str | None) -> None:
        with self._lock:
            if len(self._candados) > MAX_CANDADOS:  # poda los candados que nadie está usando
                self._candados = {t: c for t, c in self._candados.items() if c.locked()}
            candado = self._candados.setdefault(telefono, threading.Lock())
        with candado:  # un mensaje a la vez por clienta: se conserva el orden y la presencia
            identificador = self._secuencia.enviar_respuesta(telefono, texto, id_entrante)
        with self._lock:
            self._emitidos[identificador] = None
            while len(self._emitidos) > MAX_EMITIDOS:
                self._emitidos.popitem(last=False)
