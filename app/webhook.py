"""Webhook del sidecar de WhatsApp: firma HMAC, filtros de chat y canalización del mensaje.

El sidecar (Baileys) envía a `POST /webhook/whatsapp` mensajes entrantes, mensajes propios de la
dueña y cambios de estado de la conexión. Se verifica la firma sobre el cuerpo crudo ANTES de
interpretar nada, se responde 200 de inmediato y el procesamiento ocurre en segundo plano.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from app.acumulador import Acumulador
from app.agente import Agente
from app.config import Config
from app.control_humano import ControlHumano
from app.conversacion import Entrante, Saliente
from app.limite_tasa import LimiteTasa
from app.logs import nuevo_correlation_id
from app.menus_numericos import componer, guardar_menu, resolver_entrada
from app.modelos import MensajeProcesado, unidad_de_trabajo
from app.puertos import Reloj

log = logging.getLogger("webhook")

SUFIJO_INDIVIDUAL = "@s.whatsapp.net"


LIMITE_CUERPO = 16 * 1024
TOLERANCIA_MARCA_S = 300


def verificar_firma(
    cuerpo: bytes, cabecera: str | None, secreto: str, marca: str | None, ahora_s: float
) -> bool:
    """HMAC-SHA256 de `marca.cuerpo` en tiempo constante; rechaza marcas viejas (EARS-WA-02).

    La marca de tiempo impide repetir un evento firmado capturado (`propio`, `estado`).
    """
    if not secreto or not cabecera or not marca or not cabecera.startswith("sha256="):
        return False
    try:
        if abs(ahora_s - float(marca)) > TOLERANCIA_MARCA_S:
            return False
    except ValueError:
        return False
    esperado = hmac.new(secreto.encode(), f"{marca}.".encode() + cuerpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, cabecera.removeprefix("sha256=").lower())


def telefono_de_jid(jid: str) -> str | None:
    """'573001112233:12@s.whatsapp.net' → '573001112233'; None si no es un chat individual."""
    if not jid.endswith(SUFIJO_INDIVIDUAL):  # grupos, difusión, canales y @lid se ignoran
        return None
    numero = jid.removesuffix(SUFIJO_INDIVIDUAL).split(":", 1)[0]
    return numero if numero.isdigit() and 6 <= len(numero) <= 20 else None


# --- esquema de los eventos del sidecar ---
class _Evento(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EventoMensaje(_Evento):
    tipo: Literal["mensaje"]
    id: str = Field(min_length=1, max_length=128)
    jid: str = Field(max_length=128)
    nombre: str | None = Field(default=None, max_length=128)
    texto: str | None = Field(default=None, max_length=4096)
    tipo_mensaje: Literal["texto", "audio", "imagen", "video", "documento", "otro"] = "texto"
    ts: float | None = None


class EventoPropio(_Evento):
    tipo: Literal["propio"]
    id: str = Field(min_length=1, max_length=128)
    jid: str = Field(max_length=128)


class EventoEstado(_Evento):
    tipo: Literal["estado"]
    conexion: Literal["open", "connecting", "close", "loggedOut", "reemplazada", "prohibida"]


Evento = Annotated[EventoMensaje | EventoPropio | EventoEstado, Field(discriminator="tipo")]
_ADAPTADOR: TypeAdapter[Evento] = TypeAdapter(Evento)


class Emisor(Protocol):
    """Envío humanizado hacia WhatsApp (lo implementa `MensajeriaBaileys`)."""

    def responder(
        self, telefono: str, salidas: list[Saliente], id_entrante: str | None
    ) -> None: ...

    def fue_emitido(self, id_mensaje: str) -> bool: ...


class Pasarela:
    """Canaliza lo que llega del sidecar: filtros, deduplicación, pausa, menús y respuesta."""

    def __init__(
        self,
        engine: Engine,
        config: Config,
        agente: Agente,
        control: ControlHumano,
        emisor: Emisor,
        reloj: Reloj,
        ventana_s: float | None = None,
    ) -> None:
        self._engine = engine
        self._config = config
        self._agente = agente
        self._control = control
        self._emisor = emisor
        self._reloj = reloj
        ventana = config.negocio.anti_ban.debounce_segundos if ventana_s is None else ventana_s
        self._acumulador = Acumulador(self._responder, ventana)
        self._tareas: set[asyncio.Task[None]] = set()
        self._limite_entrante = LimiteTasa(maximo=60, ventana_s=60)
        self.estado_conexion: str = "desconocido"
        self.estado_desde: datetime | None = None

    # --- punto de entrada de cada evento ---
    async def manejar(self, evento: EventoMensaje | EventoPropio | EventoEstado) -> str:
        """Procesa un evento; devuelve cómo se resolvió (útil para pruebas y registros)."""
        nuevo_correlation_id()
        if isinstance(evento, EventoEstado):
            return self._estado(evento)
        if isinstance(evento, EventoPropio):
            return self._propio(evento)
        return await self._mensaje(evento)

    def lanzar(self, evento: EventoMensaje | EventoPropio | EventoEstado) -> None:
        """Procesa en segundo plano sin bloquear la respuesta 200 al sidecar."""
        tarea = asyncio.create_task(self._seguro(evento))
        self._tareas.add(tarea)
        tarea.add_done_callback(self._tareas.discard)

    async def esperar_tareas(self) -> None:
        """Espera lo que esté en curso (apagado ordenado y pruebas)."""
        while self._tareas:
            await asyncio.gather(*list(self._tareas), return_exceptions=True)
        await self._acumulador.cerrar()

    async def _seguro(self, evento: EventoMensaje | EventoPropio | EventoEstado) -> None:
        try:
            await self.manejar(evento)
        except Exception:  # noqa: BLE001 - nada debe llegar de vuelta al sidecar ni romper la tarea
            log.exception("webhook_error_inesperado")

    # --- mensajes entrantes ---
    async def _mensaje(self, evento: EventoMensaje) -> str:
        telefono = telefono_de_jid(evento.jid)
        if telefono is None:
            return "ignorado_chat"
        ahora = self._reloj.ahora()
        if not self._limite_entrante.permitir(telefono):  # ráfagas: se descartan sin procesar
            return "limitado"
        if not await asyncio.to_thread(self._registrar, evento.id, ahora):
            return "duplicado"
        if await asyncio.to_thread(self._control.esta_pausado, telefono, ahora):
            return "pausado"
        texto = evento.texto if evento.tipo_mensaje == "texto" else None
        id_interactivo = (
            await asyncio.to_thread(self._mapear_numero, telefono, texto, ahora) if texto else None
        )
        marca = datetime.fromtimestamp(evento.ts, UTC) if evento.ts else ahora
        entrante = Entrante(
            telefono=telefono,
            nombre_perfil=evento.nombre,
            wa_message_id=evento.id,
            texto=None if id_interactivo else texto,
            id_interactivo=id_interactivo,
            ts=marca,
        )
        await self._acumulador.recibir(entrante)
        return "aceptado"

    def _registrar(self, id_mensaje: str, ahora: datetime) -> bool:
        """Deduplicación por id de WhatsApp (EARS-WA-04); prefijo distinto al del agente."""
        try:
            with unidad_de_trabajo(self._engine) as s:
                s.add(MensajeProcesado(wa_message_id=f"wh:{id_mensaje}", recibido_en=ahora))
        except IntegrityError:
            return False
        return True

    def _mapear_numero(self, telefono: str, texto: str, ahora: datetime) -> str | None:
        vigencia = self._config.negocio.inactividad_reinicio_minutos
        with unidad_de_trabajo(self._engine, escritura=False) as s:
            return resolver_entrada(s, telefono, texto, ahora, vigencia)

    async def _responder(self, entrante: Entrante) -> None:
        await asyncio.to_thread(self._procesar_y_enviar, entrante)

    def _procesar_y_enviar(self, entrante: Entrante) -> None:
        salidas = self._agente.procesar(entrante)
        mensaje = componer(salidas)
        if mensaje is None:
            return
        ahora = self._reloj.ahora()
        if self._control.esta_pausado(
            entrante.telefono, ahora
        ):  # la dueña intervino mientras tanto
            log.info("envio_descartado", extra={"motivo": "pausa_humana"})
            return
        if not self._control.autorizar_envio(entrante.telefono, ahora):
            log.warning("envio_descartado", extra={"motivo": "rate_limit_o_sin_conversacion"})
            return
        self._emisor.responder(entrante.telefono, salidas, entrante.wa_message_id)
        with unidad_de_trabajo(self._engine) as s:  # solo se recuerda el menú que sí se envió
            guardar_menu(s, entrante.telefono, mensaje.opciones, ahora)

    # --- eventos propios y de estado ---
    def _propio(self, evento: EventoPropio) -> str:
        telefono = telefono_de_jid(evento.jid)
        if telefono is None:
            return "ignorado_chat"
        if self._emisor.fue_emitido(evento.id):
            return "emitido_por_el_bot"
        pendiente = self._acumulador.descartar(
            telefono
        )  # lo que esperaba respuesta ya no se responde
        if self._control.pausar(telefono, self._reloj.ahora(), crear=pendiente):
            log.info("pausa_por_intervencion_humana", extra={"telefono": telefono})
            return "pausado_por_humano"
        return "sin_conversacion"

    def _estado(self, evento: EventoEstado) -> str:
        self.estado_conexion = evento.conexion
        self.estado_desde = self._reloj.ahora()
        if evento.conexion == "loggedOut":
            log.critical("whatsapp_sesion_cerrada", extra={"accion": "volver a vincular con el QR"})
        else:
            log.info("whatsapp_conexion", extra={"conexion": evento.conexion})
        return f"estado_{evento.conexion}"


async def _leer_acotado(request: Request) -> bytes:
    """Lee el cuerpo sin pasar de LIMITE_CUERPO (413 si lo supera), antes de verificar la firma."""
    declarado = request.headers.get("content-length")
    if declarado is not None and declarado.isdigit() and int(declarado) > LIMITE_CUERPO:
        raise HTTPException(status_code=413, detail="cuerpo demasiado grande")
    cuerpo = bytearray()
    async for trozo in request.stream():
        cuerpo.extend(trozo)
        if len(cuerpo) > LIMITE_CUERPO:
            raise HTTPException(status_code=413, detail="cuerpo demasiado grande")
    return bytes(cuerpo)


def crear_router_whatsapp() -> APIRouter:
    router = APIRouter()

    @router.post("/webhook/whatsapp")
    async def recibir(request: Request) -> dict[str, bool]:
        cuerpo = await _leer_acotado(request)  # crudo y con tope: sin firma no se acepta volumen
        secreto = request.app.state.webhook_secreto
        firma_valida = verificar_firma(
            cuerpo,
            request.headers.get("x-firma"),
            secreto,
            request.headers.get("x-marca"),
            datetime.now(UTC).timestamp(),
        )
        if not firma_valida:
            raise HTTPException(status_code=403, detail="firma inválida")
        try:
            evento = _ADAPTADOR.validate_json(cuerpo)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="evento inválido") from exc
        pasarela: Pasarela = request.app.state.pasarela
        pasarela.lanzar(evento)
        return {"ok": True}

    return router
