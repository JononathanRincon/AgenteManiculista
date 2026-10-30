"""Reservas transaccionales con bloques únicos (design.md §B4, §2.3).

Todas las funciones reciben una sesión de escritura (`unidad_de_trabajo`): la transacción
BEGIN IMMEDIATE empieza con la primera consulta y se deshace completa ante cualquier excepción.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, delete, func, or_, select, update
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
        if not _limpio(solicitud.direccion, MAX_DIRECCION):
            raise ReservaInvalida("el domicilio requiere dirección")
        if len(solicitud.direccion or "") > MAX_DIRECCION:
            raise ReservaInvalida("la dirección es demasiado larga")
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
    consulta = select(func.count()).select_from(Reserva).where(*_filtro_futuras(telefono, ahora))
    return int(sesion.scalar(consulta) or 0)


def _filtro_futuras(telefono: str, ahora: datetime) -> tuple[ColumnElement[bool], ...]:
    """Citas activas futuras; la nueva de un reagendamiento en curso no cuenta doble."""
    return (
        Reserva.telefono == telefono,
        Reserva.estado.in_(ESTADOS_ACTIVOS),
        Reserva.inicio >= ahora,
        or_(Reserva.estado == "CONFIRMADA", Reserva.reemplaza_a.is_(None)),
    )


MAX_NOMBRE = 64
MAX_DIRECCION = 200
RETENCION_DIAS = 365


def _limpio(texto: str | None, maximo: int) -> str | None:
    """Colapsa saltos de línea y espacios (evita falsificar líneas del evento) y recorta."""
    if texto is None:
        return None
    return " ".join(texto.split())[:maximo] or None


def _construir(solicitud: SolicitudReserva, config: Config, ahora: datetime) -> Reserva:
    negocio = config.negocio
    servicio = config.servicio(solicitud.servicio_id)
    domicilio = solicitud.modalidad == "domicilio"
    return Reserva(
        calendario_id=negocio.calendario_id,
        telefono=solicitud.telefono,
        nombre_perfil=_limpio(solicitud.nombre_perfil, MAX_NOMBRE),
        servicio_id=servicio.id,
        modalidad=solicitud.modalidad,
        direccion=_limpio(solicitud.direccion, MAX_DIRECCION) if domicilio else None,
        precio_cop=servicio.precio_cop,
        recargo_cop=(negocio.domicilio.recargo_cop or 0) if domicilio else 0,
        inicio=solicitud.inicio,
        fin=solicitud.inicio + timedelta(minutes=servicio.duracion_min),
        estado="PENDIENTE",
        expira_en=ahora + timedelta(minutes=negocio.reserva_pendiente_expira_minutos),
        creado_en=ahora,
        actualizado_en=ahora,
    )


def reservar(
    sesion: Session, solicitud: SolicitudReserva, config: Config, ahora: datetime
) -> Reserva:
    """Reserva + bloques + trabajo CREAR en una sola transacción (EARS-RES-01..03)."""
    existente = _existente(sesion, solicitud)
    if existente is not None:  # reconfirmación de la misma clienta: devuelve la misma reserva
        return existente
    _validar(solicitud, config, ahora)
    negocio = config.negocio
    if contar_futuras(sesion, solicitud.telefono, ahora) >= negocio.max_citas_futuras_por_clienta:
        raise LimiteCitas
    reserva = _construir(solicitud, config, ahora)
    sesion.add(reserva)
    sesion.flush()
    servicio = config.servicio(solicitud.servicio_id)
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


def _de_la_clienta(sesion: Session, reserva_id: int, telefono: str) -> Reserva:
    """Carga la reserva solo si pertenece a ese teléfono (aislamiento entre clientas)."""
    reserva = cargar(sesion, reserva_id)
    if reserva.telefono != telefono:
        raise ReservaNoEncontrada(str(reserva_id))
    return reserva


def _reagendar_en_curso(sesion: Session, reserva_id: int) -> bool:
    consulta = select(func.count()).select_from(Reserva)
    consulta = consulta.where(Reserva.reemplaza_a == reserva_id, Reserva.estado == "PENDIENTE")
    return bool(sesion.scalar(consulta))


def cancelar(sesion: Session, reserva_id: int, telefono: str, ahora: datetime) -> Reserva:
    """PENDIENTE/CONFIRMADA -> CANCELANDO + trabajo ELIMINAR; retiene bloques (EARS-RES-08)."""
    reserva = _de_la_clienta(sesion, reserva_id, telefono)
    if reserva.estado in ("CANCELANDO", "CANCELADA"):
        return reserva
    if reserva.estado not in ESTADOS_ACTIVOS:
        raise ReservaInvalida(f"no se puede cancelar una reserva {reserva.estado}")
    if reserva.inicio < ahora:
        raise ReservaInvalida("la cita ya pasó")
    nueva_de_reagendar = reserva.reemplaza_a is not None and reserva.estado == "PENDIENTE"
    if _reagendar_en_curso(sesion, reserva.id) or nueva_de_reagendar:
        raise ReservaInvalida("hay un reagendamiento en curso")
    previos = sesion.scalar(
        select(func.count())
        .select_from(ColaEscritura)
        .where(ColaEscritura.reserva_id == reserva.id, ColaEscritura.tipo_operacion == "ELIMINAR")
    )
    tipo_clave = f"ELIMINAR{previos}" if previos else "ELIMINAR"  # tras un fallo se puede repetir
    reserva.estado = "CANCELANDO"
    reserva.expira_en = None
    reserva.actualizado_en = ahora
    # el trabajo va al final de la fila: si el CREAR sigue en cola, se ejecuta antes (EC-12)
    encolar(
        sesion,
        calendario_id=reserva.calendario_id,
        tipo="ELIMINAR",
        payload={"reserva_id": reserva.id, "event_id": reserva.event_id},
        clave=clave_idempotencia(reserva.telefono, reserva.inicio, tipo_clave, reserva.id),
        reserva_id=reserva.id,
        ahora=ahora,
    )
    return reserva


def reagendar(
    sesion: Session,
    reserva_id: int,
    telefono: str,
    nuevo_inicio: datetime,
    config: Config,
    ahora: datetime,
) -> Reserva:
    """Crea la reserva nueva (toma los bloques propios) y encola REAGENDAR (EARS-RES-07)."""
    vieja = _de_la_clienta(sesion, reserva_id, telefono)
    if vieja.estado != "CONFIRMADA":
        raise ReservaInvalida("solo se reagenda una cita confirmada")
    if vieja.inicio < ahora:
        raise ReservaInvalida("la cita ya pasó")
    en_curso = sesion.scalar(
        select(Reserva).where(Reserva.reemplaza_a == vieja.id, Reserva.estado == "PENDIENTE")
    )
    if en_curso is not None:
        if en_curso.inicio == nuevo_inicio:
            return en_curso  # reconfirmación: misma reserva
        raise ReservaInvalida("hay un reagendamiento en curso")
    if nuevo_inicio == vieja.inicio:
        raise ReservaInvalida("el nuevo horario es el mismo de la cita")
    solicitud = SolicitudReserva(
        telefono=telefono,
        nombre_perfil=vieja.nombre_perfil,
        servicio_id=vieja.servicio_id,
        inicio=nuevo_inicio,
        modalidad=vieja.modalidad,  # type: ignore[arg-type]
        direccion=vieja.direccion,
    )
    _validar(solicitud, config, ahora)
    nueva = _construir(solicitud, config, ahora)
    nueva.reemplaza_a = vieja.id
    sesion.add(nueva)
    sesion.flush()
    negocio = config.negocio
    servicio = config.servicio(vieja.servicio_id)
    for bloque in bloques_ocupados(nuevo_inicio, servicio, solicitud.modalidad, negocio):
        fila = sesion.get(ReservaBloque, (negocio.calendario_id, bloque))
        if fila is None:
            sesion.add(
                ReservaBloque(
                    calendario_id=negocio.calendario_id, bloque_inicio=bloque, reserva_id=nueva.id
                )
            )
        elif fila.reserva_id == vieja.id:  # bloque propio: se transfiere (EC-13)
            fila.reserva_id = nueva.id
        else:
            raise HorarioOcupado(nuevo_inicio.isoformat())
    sesion.flush()
    encolar(
        sesion,
        calendario_id=negocio.calendario_id,
        tipo="REAGENDAR",
        payload={"reserva_nueva_id": nueva.id, "reserva_vieja_id": vieja.id},
        clave=clave_idempotencia(telefono, nuevo_inicio, "REAGENDAR", nueva.id),
        reserva_id=nueva.id,
        ahora=ahora,
    )
    return nueva


def citas_futuras(sesion: Session, telefono: str, ahora: datetime) -> list[Reserva]:
    """Citas futuras activas de ESA clienta, ordenadas por fecha (EARS-RES-09)."""
    consulta = select(Reserva).where(*_filtro_futuras(telefono, ahora)).order_by(Reserva.inicio)
    return list(sesion.scalars(consulta))


def anonimizar_antiguas(sesion: Session, ahora: datetime) -> int:
    """Borra los datos personales de reservas terminadas hace más de 12 meses (Ley 1581)."""
    limite = ahora - timedelta(days=RETENCION_DIAS)
    resultado = sesion.execute(
        update(Reserva)
        .where(
            Reserva.estado.in_(("CANCELADA", "EXPIRADA", "FALLIDA")),
            Reserva.actualizado_en < limite,
            Reserva.telefono != "anonimizado",
        )
        .values(telefono="anonimizado", nombre_perfil=None, direccion=None)
    )
    return int(resultado.rowcount)  # type: ignore[attr-defined]
