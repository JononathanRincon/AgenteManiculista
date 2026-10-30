"""Consulta de disponibilidad para ofrecer horarios: Calendar (caché) más reservas locales.

Solo sirve para OFRECER; la decisión de escribir se toma en `reservas.reservar` y en el worker.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import Engine, select

from app.cache import CacheDisponibilidad
from app.config import Config, Modalidad, Servicio
from app.disponibilidad import horarios_ofrecibles
from app.modelos import ReservaBloque, unidad_de_trabajo


def bloques_reservados(engine: Engine, config: Config, fecha: date) -> set[datetime]:
    """Bloques retenidos por reservas locales (PENDIENTE, CONFIRMADA, CANCELANDO) ese día."""
    zona = config.negocio.zona
    inicio = datetime.combine(fecha, time.min, tzinfo=zona).astimezone(UTC)
    fin = datetime.combine(fecha + timedelta(days=1), time.min, tzinfo=zona).astimezone(UTC)
    with unidad_de_trabajo(engine, escritura=False) as s:
        consulta = select(ReservaBloque.bloque_inicio).where(
            ReservaBloque.calendario_id == config.negocio.calendario_id,
            ReservaBloque.bloque_inicio >= inicio,
            ReservaBloque.bloque_inicio < fin,
        )
        return set(s.scalars(consulta))


def horas_libres(
    engine: Engine,
    cache: CacheDisponibilidad,
    config: Config,
    servicio: Servicio,
    modalidad: Modalidad,
    fecha: date,
    ahora: datetime,
    limite: int = 9,
) -> list[datetime]:
    """Horas de inicio libres ese día (Calendar ∪ reservas locales)."""
    ocupados = cache.ocupados_del_dia(config.negocio.calendario_id, fecha)
    ocupados |= bloques_reservados(engine, config, fecha)
    return horarios_ofrecibles(
        fecha, servicio, modalidad, ocupados, ahora, config.negocio, limite=limite
    )


def dias_con_cupo(
    engine: Engine,
    cache: CacheDisponibilidad,
    config: Config,
    servicio: Servicio,
    modalidad: Modalidad,
    ahora: datetime,
    limite: int = 10,
) -> list[date]:
    """Próximos días con al menos un horario libre (máximo `limite`)."""
    hoy = ahora.astimezone(config.negocio.zona).date()
    dias: list[date] = []
    for desplazamiento in range(config.negocio.ventana_maxima_dias + 1):
        fecha = hoy + timedelta(days=desplazamiento)
        if horas_libres(engine, cache, config, servicio, modalidad, fecha, ahora, limite=1):
            dias.append(fecha)
            if len(dias) == limite:
                break
    return dias
