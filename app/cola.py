"""Cola de escritura en SQLite: FIFO estricta por calendario, idempotente (design.md §B4)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session

from app.modelos import CalendarioEstado, ColaEscritura, unidad_de_trabajo

TipoOperacion = Literal["CREAR", "ACTUALIZAR", "ELIMINAR", "REAGENDAR"]


@dataclass(frozen=True)
class Trabajo:
    id: int
    calendario_id: str
    tipo: str
    payload: dict[str, Any]
    intentos: int
    reserva_id: int | None


def clave_idempotencia(telefono: str, inicio: datetime, tipo: str, reserva_id: int) -> str:
    """sha256(telefono|inicio|tipo|reserva_id) (EARS-COL-02)."""
    crudo = f"{telefono}|{inicio.isoformat()}|{tipo}|{reserva_id}"
    return hashlib.sha256(crudo.encode()).hexdigest()


def encolar(
    sesion: Session,
    *,
    calendario_id: str,
    tipo: TipoOperacion,
    payload: dict[str, Any],
    clave: str,
    reserva_id: int | None,
    ahora: datetime,
) -> ColaEscritura:
    """Agrega un trabajo; si la clave ya existe devuelve el existente (idempotencia)."""
    consulta = select(ColaEscritura).where(ColaEscritura.clave_idempotencia == clave)
    existente = sesion.scalar(consulta)
    if existente is not None:
        return existente
    trabajo = ColaEscritura(
        calendario_id=calendario_id,
        tipo_operacion=tipo,
        payload=json.dumps(payload),
        proxima_ejecucion=ahora,
        clave_idempotencia=clave,
        reserva_id=reserva_id,
        creado_en=ahora,
        actualizado_en=ahora,
    )
    sesion.add(trabajo)
    sesion.flush()
    return trabajo


def reclamar_siguiente(engine: Engine, calendario_id: str, ahora: datetime) -> Trabajo | None:
    """Reclama el primer trabajo de la fila (FIFO estricta) si ya es su hora; atómico."""
    with unidad_de_trabajo(engine) as s:
        cabeza = s.scalar(
            select(ColaEscritura)
            .where(
                ColaEscritura.calendario_id == calendario_id,
                ColaEscritura.estado.in_(("PENDIENTE", "PROCESANDO")),
            )
            .order_by(ColaEscritura.id)
            .limit(1)
        )
        if cabeza is None or cabeza.estado != "PENDIENTE" or cabeza.proxima_ejecucion > ahora:
            return None
        cabeza.estado = "PROCESANDO"
        cabeza.intentos += 1
        cabeza.actualizado_en = ahora
        return Trabajo(
            id=cabeza.id,
            calendario_id=cabeza.calendario_id,
            tipo=cabeza.tipo_operacion,
            payload=json.loads(cabeza.payload),
            intentos=cabeza.intentos,
            reserva_id=cabeza.reserva_id,
        )


def _cerrar(
    engine: Engine, trabajo_id: int, estado: str, resultado: str | None, ahora: datetime
) -> None:
    with unidad_de_trabajo(engine) as s:
        s.execute(
            update(ColaEscritura)
            .where(ColaEscritura.id == trabajo_id)
            .values(estado=estado, resultado=resultado, actualizado_en=ahora)
        )


def completar(
    engine: Engine, trabajo_id: int, ahora: datetime, resultado: str | None = None
) -> None:
    """Marca el trabajo como COMPLETADA."""
    _cerrar(engine, trabajo_id, "COMPLETADA", resultado, ahora)


def fallar(engine: Engine, trabajo_id: int, ahora: datetime, motivo: str) -> None:
    """Marca el trabajo como FALLIDA (sin más reintentos)."""
    _cerrar(engine, trabajo_id, "FALLIDA", motivo, ahora)


def reintentar(
    engine: Engine, trabajo_id: int, proxima: datetime, ahora: datetime, motivo: str
) -> None:
    """Devuelve el trabajo a PENDIENTE para `proxima`."""
    with unidad_de_trabajo(engine) as s:
        s.execute(
            update(ColaEscritura)
            .where(ColaEscritura.id == trabajo_id)
            .values(
                estado="PENDIENTE",
                proxima_ejecucion=proxima,
                resultado=motivo,
                actualizado_en=ahora,
            )
        )


def recuperar_huerfanos(
    engine: Engine, ahora: datetime, antiguedad: timedelta | None = None
) -> int:
    """PROCESANDO colgados vuelven a PENDIENTE (al arrancar, o con más de `antiguedad`)."""
    condiciones = [ColaEscritura.estado == "PROCESANDO"]
    if antiguedad is not None:
        condiciones.append(ColaEscritura.actualizado_en < ahora - antiguedad)
    with unidad_de_trabajo(engine) as s:
        resultado = s.execute(
            update(ColaEscritura)
            .where(*condiciones)
            .values(estado="PENDIENTE", proxima_ejecucion=ahora, actualizado_en=ahora)
        )
        return int(resultado.rowcount)  # type: ignore[attr-defined]


def profundidad(sesion: Session, calendario_id: str | None = None) -> int:
    """Trabajos PENDIENTE o PROCESANDO."""
    consulta = select(func.count()).select_from(ColaEscritura)
    consulta = consulta.where(ColaEscritura.estado.in_(("PENDIENTE", "PROCESANDO")))
    if calendario_id is not None:
        consulta = consulta.where(ColaEscritura.calendario_id == calendario_id)
    return int(sesion.scalar(consulta) or 0)


def fallidos(sesion: Session) -> int:
    """Trabajos FALLIDA acumulados."""
    consulta = select(func.count()).select_from(ColaEscritura)
    return int(sesion.scalar(consulta.where(ColaEscritura.estado == "FALLIDA")) or 0)


def pausar(engine: Engine, calendario_id: str, hasta: datetime) -> None:
    """Pausa la cola de un calendario hasta `hasta` (cuota agotada)."""
    with unidad_de_trabajo(engine) as s:
        fila = s.get(CalendarioEstado, calendario_id)
        if fila is None:
            fila = CalendarioEstado(calendario_id=calendario_id)
            s.add(fila)
        fila.cola_pausada_hasta = hasta


def pausada_hasta(engine: Engine, calendario_id: str) -> datetime | None:
    """Instante hasta el cual la cola está pausada, si lo está."""
    with unidad_de_trabajo(engine, escritura=False) as s:
        fila = s.get(CalendarioEstado, calendario_id)
        return fila.cola_pausada_hasta if fila is not None else None
