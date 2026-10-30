"""Cálculo puro de bloques y horarios ofrecibles (design.md §B2). Sin I/O."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from app.config import DIAS, Modalidad, Negocio, Servicio


def minutos_ocupados(servicio: Servicio, modalidad: Modalidad, negocio: Negocio) -> int:
    """Duración + margen (+ desplazamiento si es domicilio), en minutos."""
    extra = negocio.domicilio.minutos_desplazamiento if modalidad == "domicilio" else 0
    return servicio.duracion_min + negocio.margen_minutos + extra


def bloques_ocupados(
    inicio: datetime, servicio: Servicio, modalidad: Modalidad, negocio: Negocio
) -> list[datetime]:
    """Bloques de la cuadrícula que ocupa una cita que empieza en `inicio`."""
    bloque = negocio.bloque_minutos
    cantidad = -(-minutos_ocupados(servicio, modalidad, negocio) // bloque)  # techo
    return [inicio + timedelta(minutes=bloque * i) for i in range(cantidad)]


def _primer_candidato(abre: time, bloque: int) -> int:
    """Primer minuto del día, múltiplo de la cuadrícula, que no sea anterior a la apertura."""
    minutos = abre.hour * 60 + abre.minute
    return -(-minutos // bloque) * bloque


def horarios_ofrecibles(
    fecha: date,
    servicio: Servicio,
    modalidad: Modalidad,
    ocupados: set[datetime],
    ahora: datetime,
    negocio: Negocio,
    limite: int = 10,
) -> list[datetime]:
    """Horas de inicio (UTC) libres para `fecha`, ordenadas y como máximo `limite`."""
    zona = negocio.zona
    hoy = ahora.astimezone(zona).date()
    if fecha < hoy or fecha > hoy + timedelta(days=negocio.ventana_maxima_dias):
        return []
    horario = negocio.horario.get(DIAS[fecha.weekday()])
    if horario is None:
        return []
    minimo = ahora + timedelta(hours=negocio.anticipacion_minima_horas)
    cierre = horario.cierra.hour * 60 + horario.cierra.minute
    ofrecidos: list[datetime] = []
    minuto = _primer_candidato(horario.abre, negocio.bloque_minutos)
    while minuto + servicio.duracion_min <= cierre and len(ofrecidos) < limite:
        local = datetime.combine(fecha, time(minuto // 60, minuto % 60), tzinfo=zona)
        inicio = local.astimezone(UTC)
        minuto += negocio.bloque_minutos
        if inicio < minimo:
            continue
        if any(b in ocupados for b in bloques_ocupados(inicio, servicio, modalidad, negocio)):
            continue
        ofrecidos.append(inicio)
    return ofrecidos
