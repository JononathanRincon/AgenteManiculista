"""Worker único por calendario: verificación en vivo, escritura en Calendar y reintentos.

Es la única identidad que escribe en Calendar (RBAC §3). Ninguna decisión se toma con datos
cacheados: antes de crear un evento se consulta Calendar en vivo (EARS-COL-05).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import random
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import Engine, delete

from app import cola
from app.cache import CacheDisponibilidad
from app.config import Config
from app.disponibilidad import minutos_ocupados
from app.formato import fecha, hora
from app.mensajes import texto
from app.modelos import (
    Conversacion,
    LlamadaLLM,
    MensajeProcesado,
    Reserva,
    unidad_de_trabajo,
)
from app.puertos import (
    Calendario,
    CuotaExcedida,
    ErrorCalendario,
    ErrorPermanente,
    EventoNuevo,
    Intervalo,
    Mensajeria,
    Reloj,
)
from app.reservas import (
    anonimizar_antiguas,
    cargar,
    completar_reagendar,
    confirmar,
    event_id_para,
    expirar_pendientes,
    liberar,
    revertir_reagendar,
)

log = logging.getLogger("worker")

MAX_INTENTOS = 5
TOPE_BACKOFF_S = 64
JITTER_MAX_S = 1.0
VENTANA_MENSAJERIA = timedelta(hours=24)
RETENCION_MENSAJES = timedelta(days=7)
ANTIGUEDAD_HUERFANO = timedelta(minutes=5)


def _jitter_aleatorio() -> float:
    return random.uniform(0.0, JITTER_MAX_S)  # noqa: S311 - jitter, no criptografía


def espera_backoff(intentos: int, jitter: float) -> float:
    """min(64, 2**intentos) s + jitter (EARS-COL-03). `intentos` cuenta los ya realizados."""
    return min(TOPE_BACKOFF_S, 1 << intentos) + jitter


class _Ocupado(Exception):  # noqa: N818 - señal interna, no un error público
    """La verificación en vivo detectó ocupación ajena."""


class Worker:
    def __init__(
        self,
        engine: Engine,
        calendario: Calendario,
        mensajeria: Mensajeria,
        config: Config,
        reloj: Reloj,
        cache: CacheDisponibilidad | None = None,
        jitter: Callable[[], float] | None = None,
    ) -> None:
        self._engine = engine
        self._calendario = calendario
        self._mensajeria = mensajeria
        self._config = config
        self._reloj = reloj
        self._cache = cache
        self._jitter: Callable[[], float] = jitter or _jitter_aleatorio
        self._cal_id = config.negocio.calendario_id

    # --- bucle ---
    async def correr(self, parar: asyncio.Event, intervalo_s: float = 0.5) -> None:
        """Bucle del worker: procesa trabajos hasta que `parar` se active."""
        cola.recuperar_huerfanos(self._engine, self._reloj.ahora())
        while not parar.is_set():
            try:
                hubo = await asyncio.to_thread(self.procesar_uno)
            except Exception:  # noqa: BLE001 - el bucle nunca debe morir
                log.exception("worker_error_inesperado")
                hubo = False
            if not hubo:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(parar.wait(), timeout=intervalo_s)

    def procesar_uno(self) -> bool:
        """Reclama y ejecuta un trabajo. Devuelve True si había uno por procesar."""
        ahora = self._reloj.ahora()
        pausa = cola.pausada_hasta(self._engine, self._cal_id)
        if pausa is not None and pausa > ahora:
            return False
        trabajo = cola.reclamar_siguiente(self._engine, self._cal_id, ahora)
        if trabajo is None:
            return False
        try:
            self._ejecutar(trabajo)
        except _Ocupado:
            self._fallo_definitivo(trabajo, "ocupado en vivo")
        except ErrorPermanente as exc:
            self._fallo_definitivo(trabajo, f"permanente: {type(exc).__name__}")
        except ErrorCalendario as exc:
            self._reintentar_o_fallar(trabajo, exc)
        except Exception as exc:  # noqa: BLE001 - nunca dejar la fila trabada en PROCESANDO
            nombre = type(exc).__name__
            log.error("trabajo_error_inesperado", extra={"trabajo": trabajo.id, "error": nombre})
            self._reintentar_o_fallar(trabajo, exc)
        return True

    # --- limpieza periódica ---
    def limpiar(self) -> int:
        """Expira reservas PENDIENTE vencidas, avisa y borra mensajes procesados viejos."""
        ahora = self._reloj.ahora()
        cola.recuperar_huerfanos(self._engine, ahora, antiguedad=ANTIGUEDAD_HUERFANO)
        with unidad_de_trabajo(self._engine) as s:
            expiradas = expirar_pendientes(s, self._config, ahora)
            anonimizar_antiguas(s, ahora)
            s.execute(delete(LlamadaLLM).where(LlamadaLLM.en < ahora - timedelta(days=2)))
            s.execute(
                delete(MensajeProcesado).where(
                    MensajeProcesado.recibido_en < ahora - RETENCION_MENSAJES
                )
            )
        for reserva in expiradas:
            self._invalidar(reserva)
            self._avisar(reserva, "reagendar_fallo" if reserva.reemplaza_a else "cita_expirada")
        return len(expiradas)

    async def correr_limpieza(self, parar: asyncio.Event, cada_s: float = 60.0) -> None:
        """Tarea periódica de limpieza (cada 60 s)."""
        while not parar.is_set():
            try:
                await asyncio.to_thread(self.limpiar)
            except Exception:  # noqa: BLE001 - la tarea nunca debe morir
                log.exception("limpieza_error_inesperado")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(parar.wait(), timeout=cada_s)

    # --- ejecución por tipo ---
    def _ejecutar(self, trabajo: cola.Trabajo) -> None:
        if trabajo.tipo == "CREAR":
            self._crear(trabajo)
        elif trabajo.tipo == "ELIMINAR":
            self._eliminar(trabajo)
        elif trabajo.tipo == "REAGENDAR":
            self._reagendar(trabajo)
        else:  # ACTUALIZAR no tiene casos de uso en el MVP
            cola.completar(self._engine, trabajo.id, self._reloj.ahora(), "sin efecto")

    def _rango_en_vivo(self, reserva: Reserva) -> tuple[datetime, datetime]:
        negocio = self._config.negocio
        servicio = self._config.servicio(reserva.servicio_id)
        minutos = minutos_ocupados(servicio, reserva.modalidad, negocio)  # type: ignore[arg-type]
        desde = reserva.inicio - timedelta(minutes=negocio.margen_minutos)
        return desde, reserva.inicio + timedelta(minutes=minutos)

    def _ocupacion_ajena(self, reserva: Reserva, excluir: str | None) -> list[Intervalo]:
        desde, hasta = self._rango_en_vivo(reserva)
        return self._calendario.ocupacion_en_vivo(
            self._cal_id, desde, hasta, excluir_event_id=excluir
        )

    def _evento_nuevo(self, reserva: Reserva) -> EventoNuevo:
        servicio = self._config.servicio(reserva.servicio_id)
        nombre = reserva.nombre_perfil or texto("nombre_desconocido")
        domicilio = reserva.modalidad == "domicilio"
        extra = (
            texto(
                "evento_extra_domicilio",
                direccion=reserva.direccion or "",
                recargo=reserva.recargo_cop,
            )
            if domicilio
            else ""
        )
        modalidad = texto("modalidad_domicilio" if domicilio else "modalidad_local")
        return EventoNuevo(
            id=event_id_para(reserva),
            titulo=texto("evento_titulo", servicio=servicio.nombre, nombre=nombre),
            descripcion=texto(
                "evento_descripcion",
                nombre=nombre,
                telefono=reserva.telefono,
                modalidad=modalidad,
                extra=extra,
            ),
            inicio=reserva.inicio,
            fin=reserva.fin,
            telefono=reserva.telefono,
            reserva_id=reserva.id,
            ubicacion=reserva.direccion if domicilio else None,
        )

    def _crear(self, trabajo: cola.Trabajo) -> None:
        ahora = self._reloj.ahora()
        with unidad_de_trabajo(self._engine, escritura=False) as s:
            reserva = cargar(s, int(trabajo.payload["reserva_id"]))
        if reserva.estado not in ("PENDIENTE", "CANCELANDO"):
            cola.completar(self._engine, trabajo.id, ahora, f"obsoleto: {reserva.estado}")
            return
        if self._ocupacion_ajena(reserva, event_id_para(reserva)):
            raise _Ocupado
        event_id = self._calendario.crear_evento(self._cal_id, self._evento_nuevo(reserva))
        with unidad_de_trabajo(self._engine) as s:
            actual = cargar(s, reserva.id)
            if actual.estado == "PENDIENTE":
                confirmar(s, actual, event_id, ahora)
            else:  # CANCELANDO: el ELIMINAR que sigue en la cola borrará este evento
                actual.event_id = event_id
                actual.actualizado_en = ahora
            estado = actual.estado
        cola.completar(self._engine, trabajo.id, ahora, "creado")
        self._invalidar(reserva)
        if estado == "CONFIRMADA":
            clave = (
                "cita_confirmada_domicilio"
                if reserva.modalidad == "domicilio"
                else "cita_confirmada"
            )
            self._avisar(reserva, clave)

    def _eliminar(self, trabajo: cola.Trabajo) -> None:
        ahora = self._reloj.ahora()
        with unidad_de_trabajo(self._engine, escritura=False) as s:
            reserva = cargar(s, int(trabajo.payload["reserva_id"]))
        self._calendario.eliminar_evento(self._cal_id, event_id_para(reserva))
        with unidad_de_trabajo(self._engine) as s:
            actual = cargar(s, reserva.id)
            if actual.estado == "CANCELANDO":
                liberar(s, actual, "CANCELADA", ahora)
        cola.completar(self._engine, trabajo.id, ahora, "eliminado")
        self._invalidar(reserva)

    def _reagendar(self, trabajo: cola.Trabajo) -> None:
        ahora = self._reloj.ahora()
        with unidad_de_trabajo(self._engine, escritura=False) as s:
            nueva = cargar(s, int(trabajo.payload["reserva_nueva_id"]))
            vieja = cargar(s, int(trabajo.payload["reserva_vieja_id"]))
        if nueva.estado != "PENDIENTE":
            cola.completar(self._engine, trabajo.id, ahora, f"obsoleto: {nueva.estado}")
            return
        id_vieja, id_nueva = event_id_para(vieja), event_id_para(nueva)
        # ajeno = presente aunque excluyamos cualquiera de nuestros dos eventos
        sin_vieja = self._ocupacion_ajena(nueva, id_vieja)
        sin_nueva = self._ocupacion_ajena(nueva, id_nueva)
        if [i for i in sin_vieja if i in sin_nueva]:
            raise _Ocupado
        self._calendario.crear_evento(self._cal_id, self._evento_nuevo(nueva))
        self._calendario.eliminar_evento(self._cal_id, id_vieja)
        with unidad_de_trabajo(self._engine) as s:
            completar_reagendar(s, cargar(s, nueva.id), cargar(s, vieja.id), id_nueva, ahora)
        cola.completar(self._engine, trabajo.id, ahora, "reagendado")
        self._invalidar(vieja)
        self._invalidar(nueva)
        self._avisar(nueva, "reagendar_ok")

    # --- errores ---
    def _reintentar_o_fallar(self, trabajo: cola.Trabajo, error: Exception) -> None:
        ahora = self._reloj.ahora()
        if trabajo.intentos >= MAX_INTENTOS:
            self._fallo_definitivo(
                trabajo, f"agotó {MAX_INTENTOS} intentos: {type(error).__name__}"
            )
            return
        espera = espera_backoff(trabajo.intentos, self._jitter())
        proxima = ahora + timedelta(seconds=math.ceil(espera))  # las fechas se guardan al segundo
        if isinstance(error, CuotaExcedida):
            cola.pausar(self._engine, self._cal_id, proxima)
            log.warning("cola_pausada", extra={"hasta": proxima.isoformat()})
        cola.reintentar(self._engine, trabajo.id, proxima, ahora, type(error).__name__)

    def _fallo_definitivo(self, trabajo: cola.Trabajo, motivo: str) -> None:
        """Compensa según el tipo de trabajo y lo marca FALLIDA (EARS-COL-06, EARS-RES-05)."""
        ahora = self._reloj.ahora()
        log.error("trabajo_fallido", extra={"trabajo": trabajo.id, "tipo": trabajo.tipo})
        cola.fallar(self._engine, trabajo.id, ahora, motivo)
        if trabajo.tipo == "CREAR":
            reserva = self._liberar_si(trabajo, "FALLIDA", ("PENDIENTE", "CANCELANDO"))
            if reserva is not None:
                self._encolar_limpieza_de_huerfano(trabajo, reserva)
                self._avisar(reserva, "cita_fallida")
        elif trabajo.tipo == "REAGENDAR":
            self._revertir(trabajo)
        else:  # ELIMINAR (ACTUALIZAR no tiene casos de uso y nunca falla)
            self._cancelacion_fallida(trabajo)

    def _liberar_si(
        self, trabajo: cola.Trabajo, final: str, estados: tuple[str, ...]
    ) -> Reserva | None:
        ahora = self._reloj.ahora()
        with unidad_de_trabajo(self._engine) as s:
            reserva = cargar(s, int(trabajo.payload["reserva_id"]))
            if reserva.estado not in estados:
                return None
            liberar(s, reserva, final, ahora)
        self._invalidar(reserva)
        return reserva

    def _encolar_limpieza_de_huerfano(self, trabajo: cola.Trabajo, reserva: Reserva) -> None:
        """Tras varios intentos pudo quedar un evento creado: se encola su eliminación."""
        if trabajo.intentos <= 1:
            return
        with unidad_de_trabajo(self._engine) as s:
            cola.encolar(
                s,
                calendario_id=self._cal_id,
                tipo="ELIMINAR",
                payload={"reserva_id": reserva.id},
                clave=cola.clave_idempotencia(
                    reserva.telefono, reserva.inicio, "ELIMINAR-HUERFANO", reserva.id
                ),
                reserva_id=reserva.id,
                ahora=self._reloj.ahora(),
            )

    def _cancelacion_fallida(self, trabajo: cola.Trabajo) -> None:
        """Si no se pudo borrar el evento, la cita sigue viva: vuelve a CONFIRMADA."""
        with unidad_de_trabajo(self._engine) as s:
            reserva = cargar(s, int(trabajo.payload["reserva_id"]))
            if reserva.estado == "CANCELANDO":
                reserva.estado = "CONFIRMADA"
                reserva.actualizado_en = self._reloj.ahora()
        log.error("cancelacion_fallida", extra={"reserva": reserva.id})

    def _revertir(self, trabajo: cola.Trabajo) -> None:
        ahora = self._reloj.ahora()
        with unidad_de_trabajo(self._engine) as s:
            nueva = cargar(s, int(trabajo.payload["reserva_nueva_id"]))
            vieja = cargar(s, int(trabajo.payload["reserva_vieja_id"]))
            if nueva.estado != "PENDIENTE":
                return
            revertir_reagendar(s, nueva, vieja, self._config, ahora)
        try:  # compensación del evento nuevo si alcanzó a crearse
            self._calendario.eliminar_evento(self._cal_id, event_id_para(nueva))
        except ErrorCalendario:
            log.warning("compensacion_fallida", extra={"reserva": nueva.id})
        self._invalidar(nueva)
        self._avisar(nueva, "reagendar_fallo")

    # --- utilidades ---
    def _invalidar(self, reserva: Reserva) -> None:
        if self._cache is not None:
            dia = reserva.inicio.astimezone(self._config.negocio.zona).date()
            self._cache.invalidar(self._cal_id, dia)

    def _avisar(self, reserva: Reserva, clave: str) -> None:
        """Envía un mensaje solo dentro de la ventana de 24 h de WhatsApp (EC-11)."""
        ahora = self._reloj.ahora()
        with unidad_de_trabajo(self._engine, escritura=False) as s:
            conversacion = s.get(Conversacion, reserva.telefono)
            ultimo = conversacion.actualizado_en if conversacion is not None else None
        if ultimo is None or ahora - ultimo > VENTANA_MENSAJERIA:
            log.info("aviso_fuera_de_ventana", extra={"reserva": reserva.id, "clave": clave})
            return
        negocio = self._config.negocio
        servicio = self._config.servicio(reserva.servicio_id)
        cuerpo = texto(
            clave,
            servicio=servicio.nombre,
            fecha=fecha(reserva.inicio, negocio),
            hora=hora(reserva.inicio, negocio),
            direccion=reserva.direccion or "",
        )
        try:
            self._mensajeria.enviar_texto(reserva.telefono, cuerpo)
        except Exception:  # noqa: BLE001 - un fallo de envío nunca debe deshacer la cita
            log.exception("aviso_no_enviado", extra={"reserva": reserva.id})
