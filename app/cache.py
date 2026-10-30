"""Caché de disponibilidad sobre el espejo de eventos (design.md §B3).

Solo sirve para OFRECER horarios; ninguna escritura se decide con estos datos (EARS-CAC-06).
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Engine, delete, select

from app.config import Negocio
from app.metricas import CACHE_HIT, CACHE_MISS, incrementar
from app.modelos import (
    CacheDisponibilidadFila,
    CalendarioEstado,
    EventoEspejo,
    unidad_de_trabajo,
)
from app.puertos import Calendario, ErrorSyncTokenExpirado, Reloj


def bloques_de_evento(inicio: datetime, fin: datetime, negocio: Negocio) -> list[datetime]:
    """Bloques de la cuadrícula tocados por un evento, incluido el margen posterior."""
    zona = negocio.zona
    paso = timedelta(minutes=negocio.bloque_minutos)
    local = inicio.astimezone(zona)
    piso = local.replace(
        minute=(local.minute // negocio.bloque_minutos) * negocio.bloque_minutos,
        second=0,
        microsecond=0,
    ).astimezone(UTC)
    limite = fin + timedelta(minutes=negocio.margen_minutos)
    bloques: list[datetime] = []
    actual = piso
    while actual < limite:
        bloques.append(actual)
        actual += paso
    return bloques


class CacheDisponibilidad:
    def __init__(
        self, engine: Engine, calendario: Calendario, negocio: Negocio, reloj: Reloj
    ) -> None:
        self._engine = engine
        self._calendario = calendario
        self._negocio = negocio
        self._reloj = reloj
        self._espejo_valido_hasta: dict[str, datetime] = {}
        self._candado_espejo = threading.Lock()  # dos resincronizaciones no pisan el sync token

    def ocupados_del_dia(self, calendario_id: str, fecha: date) -> set[datetime]:
        """Bloques ocupados por Calendar ese día (hora de Bogotá), desde caché si está vigente."""
        ahora = self._reloj.ahora()
        clave = fecha.isoformat()
        with unidad_de_trabajo(self._engine) as s:
            fila = s.get(CacheDisponibilidadFila, (calendario_id, clave))
            if fila is not None and fila.valido_hasta > ahora:
                incrementar(s, CACHE_HIT)
                return _leer_bloques(fila.bloques_ocupados)
            incrementar(s, CACHE_MISS)
        self._refrescar_espejo(calendario_id, ahora)
        bloques = self._reconstruir(calendario_id, fecha)
        with unidad_de_trabajo(self._engine) as s:
            s.merge(
                CacheDisponibilidadFila(
                    calendario_id=calendario_id,
                    fecha=clave,
                    bloques_ocupados=json.dumps(sorted(b.isoformat() for b in bloques)),
                    generado_en=ahora,
                    valido_hasta=ahora + timedelta(seconds=self._negocio.cache_ttl_segundos),
                )
            )
        return bloques

    def reiniciar(self) -> None:
        """Olvida la frescura del espejo (reinicio total de la demo)."""
        self._espejo_valido_hasta.clear()

    def invalidar(self, calendario_id: str, fecha: date | None = None) -> None:
        """Descarta el caché de un día (o de todos) y marca el espejo como desactualizado."""
        with unidad_de_trabajo(self._engine) as s:
            consulta = delete(CacheDisponibilidadFila).where(
                CacheDisponibilidadFila.calendario_id == calendario_id
            )
            if fecha is not None:
                consulta = consulta.where(CacheDisponibilidadFila.fecha == fecha.isoformat())
            s.execute(consulta)
        self._espejo_valido_hasta.pop(calendario_id, None)

    def resincronizar(self, calendario_id: str) -> None:
        """Invalida el caché y trae del calendario lo que cambió (push o refresco periódico)."""
        self.invalidar(calendario_id)
        self._refrescar_espejo(calendario_id, self._reloj.ahora())

    # --- espejo ---
    def _refrescar_espejo(self, calendario_id: str, ahora: datetime) -> None:
        with self._candado_espejo:
            self._refrescar_espejo_bloqueado(calendario_id, ahora)

    def _refrescar_espejo_bloqueado(self, calendario_id: str, ahora: datetime) -> None:
        valido = self._espejo_valido_hasta.get(calendario_id)
        if valido is not None and valido > ahora:
            return
        desde = ahora - timedelta(days=1)
        hasta = ahora + timedelta(days=self._negocio.ventana_maxima_dias + 2)
        with unidad_de_trabajo(self._engine, escritura=False) as s:
            estado = s.get(CalendarioEstado, calendario_id)
            token = estado.sync_token if estado is not None else None
        try:
            resultado = self._calendario.listar_eventos(
                calendario_id, sync_token=token, desde=desde, hasta=hasta
            )
        except ErrorSyncTokenExpirado:  # 410: se descarta el espejo y se lista todo (EC-20)
            resultado = self._calendario.listar_eventos(
                calendario_id, sync_token=None, desde=desde, hasta=hasta
            )
        with unidad_de_trabajo(self._engine) as s:
            if resultado.resincronizacion_completa:
                s.execute(delete(EventoEspejo).where(EventoEspejo.calendario_id == calendario_id))
            for e in resultado.eventos:
                if e.cancelado:
                    s.execute(
                        delete(EventoEspejo).where(
                            EventoEspejo.calendario_id == calendario_id,
                            EventoEspejo.event_id == e.event_id,
                        )
                    )
                else:
                    s.merge(
                        EventoEspejo(
                            calendario_id=calendario_id,
                            event_id=e.event_id,
                            inicio=e.inicio,
                            fin=e.fin,
                            estado="confirmed",
                        )
                    )
            fila = s.get(CalendarioEstado, calendario_id)
            if fila is None:
                fila = CalendarioEstado(calendario_id=calendario_id)
                s.add(fila)
            fila.sync_token = resultado.nuevo_sync_token
        ttl = timedelta(seconds=self._negocio.cache_ttl_segundos)
        self._espejo_valido_hasta[calendario_id] = ahora + ttl

    def _reconstruir(self, calendario_id: str, fecha: date) -> set[datetime]:
        zona = self._negocio.zona
        with unidad_de_trabajo(self._engine, escritura=False) as s:
            eventos = s.scalars(
                select(EventoEspejo).where(EventoEspejo.calendario_id == calendario_id)
            ).all()
            bloques: set[datetime] = set()
            for e in eventos:
                for b in bloques_de_evento(e.inicio, e.fin, self._negocio):
                    if b.astimezone(zona).date() == fecha:
                        bloques.add(b)
        return bloques


def _leer_bloques(crudo: str) -> set[datetime]:
    return {datetime.fromisoformat(b) for b in json.loads(crudo)}
