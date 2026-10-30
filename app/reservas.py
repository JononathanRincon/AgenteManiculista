"""Reservas transaccionales con bloques únicos (design.md §B4, §2.3).

Todas las funciones reciben una sesión de escritura (`unidad_de_trabajo`): la transacción
BEGIN IMMEDIATE empieza con la primera consulta y se deshace completa ante cualquier excepción.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.cola import clave_idempotencia, encolar
from app.config import Config, Modalidad
from app.disponibilidad import bloques_ocupados, horarios_ofrecibles
from app.modelos import ColaEscritura, Reserva, ReservaBloque

ESTADOS_ACTIVOS = ("PENDIENTE", "CONFIRMADA")
ESTADOS_QUE_RETIENEN = ("PENDIENTE", "CONFIRMADA", "CANCELANDO")


class ErrorReserva(Exception):
    """Base de los errores de reserva."""


class HorarioOcupado(ErrorReserva):  # noqa: N818 - nombre fijado por el diseño
    """Algún bloque del horario pedido ya está tomado."""


class LimiteCitas(ErrorReserva):  # noqa: N818 - nombre fijado por el diseño
    """La clienta ya tiene el máximo de citas futuras."""


class ReservaInvalida(ErrorReserva):
    """La solicitud no cumple las reglas (servicio, modalidad, horario)."""


class ReservaNoEncontrada(ErrorReserva):
    """No existe una reserva de esa clienta con ese id."""


@dataclass(frozen=True)
class SolicitudReserva:
    telefono: str
    nombre_perfil: str | None
    servicio_id: str
    inicio: datetime
    modalidad: Modalidad = "local"
    direccion: str | None = None


def event_id_para(reserva: Reserva) -> str:
    """Id de evento de Calendar derivado de la reserva (hex de sha256; alfabeto 0-9a-f).

    Incluye `creado_en` para no chocar con ids de eventos viejos si la base se reinicia.
    """
    crudo = f"{reserva.calendario_id}|{reserva.id}|{reserva.creado_en.isoformat()}"
    return hashlib.sha256(crudo.encode()).hexdigest()


def _validar(solicitud: SolicitudReserva, config: Config, ahora: datetime) -> None:
    negocio = config.negocio
    try:
        servicio = config.servicio(solicitud.servicio_id)
    except KeyError as exc:
        raise ReservaInvalida(f"servicio desconocido: {solicitud.servicio_id}") from exc
    if solicitud.modalidad == "domicilio":
        if not negocio.domicilio.habilitado or not servicio.domicilio_permitido:
            raise ReservaInvalida("domicilio no disponible para este servicio")
        if not solicitud.direccion:
            raise ReservaInvalida("el domicilio requiere dirección")
    fecha = solicitud.inicio.astimezone(negocio.zona).date()
    permitidos = horarios_ofrecibles(
        fecha, servicio, solicitud.modalidad, set(), ahora, negocio, limite=1000
    )
    if solicitud.inicio not in permitidos:
        raise ReservaInvalida("horario fuera de la cuadrícula, del horario laboral o de la ventana")


def _existente(sesion: Session, solicitud: SolicitudReserva) -> Reserva | None:
    return sesion.scalar(
        select(Reserva).where(
            Reserva.telefono == solicitud.telefono,
            Reserva.inicio == solicitud.inicio,
            Reserva.servicio_id == solicitud.servicio_id,
            Reserva.estado.in_(ESTADOS_ACTIVOS),
        )
    )


def contar_futuras(sesion: Session, telefono: str, ahora: datetime) -> int:
    """Citas futuras activas (PENDIENTE o CONFIRMADA) de una clienta."""
    consulta = select(func.count()).select_from(Reserva)
    consulta = consulta.where(
        Reserva.telefono == telefono, Reserva.estado.in_(ESTADOS_ACTIVOS), Reserva.inicio >= ahora
    )
    return int(sesion.scalar(consulta) or 0)


def reservar(
    sesion: Session, solicitud: SolicitudReserva, config: Config, ahora: datetime
) -> Reserva:
    """Reserva + bloques + trabajo CREAR en una sola transacción (EARS-RES-01..03)."""
    _validar(solicitud, config, ahora)
    existente = _existente(sesion, solicitud)
    if existente is not None:  # reconfirmación de la misma clienta: devuelve la misma reserva
        return existente
    negocio = config.negocio
    if contar_futuras(sesion, solicitud.telefono, ahora) >= negocio.max_citas_futuras_por_clienta:
        raise LimiteCitas(solicitud.telefono)
    servicio = config.servicio(solicitud.servicio_id)
    domicilio = solicitud.modalidad == "domicilio"
    reserva = Reserva(
        calendario_id=negocio.calendario_id,
        telefono=solicitud.telefono,
        nombre_perfil=solicitud.nombre_perfil,
        servicio_id=servicio.id,
        modalidad=solicitud.modalidad,
        direccion=solicitud.direccion if domicilio else None,
        precio_cop=servicio.precio_cop,
        recargo_cop=(negocio.domicilio.recargo_cop or 0) if domicilio else 0,
        inicio=solicitud.inicio,
        fin=solicitud.inicio + timedelta(minutes=servicio.duracion_min),
        estado="PENDIENTE",
        expira_en=ahora + timedelta(minutes=negocio.reserva_pendiente_expira_minutos),
        creado_en=ahora,
        actualizado_en=ahora,
    )
    sesion.add(reserva)
    sesion.flush()
    bloques = bloques_ocupados(solicitud.inicio, servicio, solicitud.modalidad, negocio)
    try:
        cal = negocio.calendario_id
        for b in bloques:
            sesion.add(ReservaBloque(calendario_id=cal, bloque_inicio=b, reserva_id=reserva.id))
        sesion.flush()
    except IntegrityError as exc:
        raise HorarioOcupado(solicitud.inicio.isoformat()) from exc
    encolar(
        sesion,
        calendario_id=negocio.calendario_id,
        tipo="CREAR",
        payload={"reserva_id": reserva.id},
        clave=clave_idempotencia(solicitud.telefono, solicitud.inicio, "CREAR", reserva.id),
        reserva_id=reserva.id,
        ahora=ahora,
    )
    return reserva


def cargar(sesion: Session, reserva_id: int) -> Reserva:
    """Carga una reserva por id; ReservaNoEncontrada si no existe."""
    reserva = sesion.get(Reserva, reserva_id)
    if reserva is None:
        raise ReservaNoEncontrada(str(reserva_id))
    return reserva


def confirmar(sesion: Session, reserva: Reserva, event_id: str, ahora: datetime) -> None:
    """PENDIENTE -> CONFIRMADA con el id del evento creado."""
    reserva.estado = "CONFIRMADA"
    reserva.event_id = event_id
    reserva.expira_en = None
    reserva.actualizado_en = ahora


def liberar(sesion: Session, reserva: Reserva, estado_final: str, ahora: datetime) -> None:
    """Pasa a un estado final (CANCELADA, EXPIRADA, FALLIDA) y libera todos sus bloques."""
    sesion.execute(delete(ReservaBloque).where(ReservaBloque.reserva_id == reserva.id))
    reserva.estado = estado_final
    reserva.expira_en = None
    reserva.actualizado_en = ahora


def completar_reagendar(
    sesion: Session, nueva: Reserva, vieja: Reserva, event_id: str, ahora: datetime
) -> None:
    """Reagendar exitoso: la vieja se cancela y libera lo que le quedaba; la nueva se confirma."""
    liberar(sesion, vieja, "CANCELADA", ahora)
    confirmar(sesion, nueva, event_id, ahora)


def revertir_reagendar(
    sesion: Session, nueva: Reserva, vieja: Reserva, config: Config, ahora: datetime
) -> None:
    """Reagendar fallido: los bloques que eran de la vieja vuelven a ella y la nueva se libera."""
    negocio = config.negocio
    servicio = config.servicio(vieja.servicio_id)
    originales = set(bloques_ocupados(vieja.inicio, servicio, vieja.modalidad, negocio))  # type: ignore[arg-type]
    sesion.execute(
        update(ReservaBloque)
        .where(
            ReservaBloque.reserva_id == nueva.id,
            ReservaBloque.calendario_id == nueva.calendario_id,
            ReservaBloque.bloque_inicio.in_(originales),
        )
        .values(reserva_id=vieja.id)
    )
    liberar(sesion, nueva, "FALLIDA", ahora)


def expirar_pendientes(sesion: Session, config: Config, ahora: datetime) -> list[Reserva]:
    """PENDIENTE vencidas -> EXPIRADA, libera bloques y cancela su trabajo (EARS-RES-06)."""
    vencidas = sesion.scalars(
        select(Reserva).where(Reserva.estado == "PENDIENTE", Reserva.expira_en < ahora)
    ).all()
    expiradas: list[Reserva] = []
    for reserva in vencidas:
        trabajo = sesion.scalar(
            select(ColaEscritura).where(
                ColaEscritura.reserva_id == reserva.id,
                ColaEscritura.tipo_operacion.in_(("CREAR", "REAGENDAR")),
                ColaEscritura.estado.in_(("PENDIENTE", "PROCESANDO")),
            )
        )
        if trabajo is not None and trabajo.estado == "PROCESANDO":
            continue  # el worker lo está ejecutando: que termine o falle
        if trabajo is not None:
            trabajo.estado = "COMPLETADA"
            trabajo.resultado = "expirada"
            trabajo.actualizado_en = ahora
            if trabajo.intentos > 0:  # pudo haber quedado un evento huérfano de un intento previo
                encolar(
                    sesion,
                    calendario_id=reserva.calendario_id,
                    tipo="ELIMINAR",
                    payload={"reserva_id": reserva.id},
                    clave=clave_idempotencia(
                        reserva.telefono, reserva.inicio, "ELIMINAR", reserva.id
                    ),
                    reserva_id=reserva.id,
                    ahora=ahora,
                )
        if reserva.reemplaza_a is not None:
            revertir_reagendar(sesion, reserva, cargar(sesion, reserva.reemplaza_a), config, ahora)
            reserva.estado = "EXPIRADA"
        else:
            liberar(sesion, reserva, "EXPIRADA", ahora)
        expiradas.append(reserva)
    return expiradas
