"""Espejo de eventos con syncToken y resincronización ante 410 (EARS-CAC-05, EC-20)."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from app.cache import CacheDisponibilidad
from app.config import Config, cargar_config
from app.modelos import (
    CalendarioEstado,
    EventoEspejo,
    crear_engine,
    crear_tablas,
    unidad_de_trabajo,
)
from app.puertos import ErrorTransitorio, ResultadoListado
from app.reloj import RelojFalso
from app.simulador.calendario_falso import CalendarioFalso
from sqlalchemy import Engine, select

RAIZ = Path(__file__).resolve().parents[2]
BOGOTA = ZoneInfo("America/Bogota")
AHORA = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
CAL = "cal"
MARTES = date(2026, 11, 3)


class CalendarioEspia(CalendarioFalso):
    def __init__(self) -> None:
        super().__init__()
        self.tokens_recibidos: list[str | None] = []

    def listar_eventos(
        self, calendario_id: str, *, sync_token: str | None, desde: datetime, hasta: datetime
    ) -> ResultadoListado:
        self.tokens_recibidos.append(sync_token)
        return super().listar_eventos(
            calendario_id, sync_token=sync_token, desde=desde, hasta=hasta
        )


def _local(h: int, mi: int = 0) -> datetime:
    return datetime(2026, 11, 3, h, mi, tzinfo=BOGOTA).astimezone(UTC)


@pytest.fixture
def cfg() -> Config:
    return cargar_config(RAIZ / "config")


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = crear_engine(f"sqlite:///{tmp_path / 'e.db'}")
    crear_tablas(eng)
    yield eng
    eng.dispose()


def _ids_espejo(engine: Engine) -> set[str]:
    with unidad_de_trabajo(engine, escritura=False) as s:
        return set(s.scalars(select(EventoEspejo.event_id)).all())


def _token(engine: Engine) -> str | None:
    with unidad_de_trabajo(engine, escritura=False) as s:
        fila = s.get(CalendarioEstado, CAL)
        return fila.sync_token if fila else None


def test_primer_refresco_es_completo_y_guarda_el_token(engine: Engine, cfg: Config) -> None:
    cal = CalendarioEspia()
    cal.agregar_evento_manual(_local(9), _local(10))
    cache = CacheDisponibilidad(engine, cal, cfg.negocio, RelojFalso(AHORA))
    cache.ocupados_del_dia(CAL, MARTES)
    assert cal.tokens_recibidos == [None]
    assert _token(engine) is not None
    assert len(_ids_espejo(engine)) == 1


def test_refrescos_siguientes_son_incrementales(engine: Engine, cfg: Config) -> None:
    cal = CalendarioEspia()
    manual = cal.agregar_evento_manual(_local(9), _local(10))
    cache = CacheDisponibilidad(engine, cal, cfg.negocio, RelojFalso(AHORA))
    cache.ocupados_del_dia(CAL, MARTES)
    primer_token = _token(engine)

    nuevo = cal.agregar_evento_manual(_local(14), _local(15))
    cal.eliminar_evento(CAL, manual)
    cache.invalidar(CAL)
    ocupados = cache.ocupados_del_dia(CAL, MARTES)

    assert cal.tokens_recibidos == [None, primer_token]
    assert _ids_espejo(engine) == {nuevo}
    assert _local(14) in ocupados
    assert _local(9) not in ocupados
    assert _token(engine) != primer_token


def test_410_borra_el_espejo_y_hace_listado_completo_sin_error(engine: Engine, cfg: Config) -> None:
    cal = CalendarioEspia()
    cal.agregar_evento_manual(_local(9), _local(10))
    cache = CacheDisponibilidad(engine, cal, cfg.negocio, RelojFalso(AHORA))
    cache.ocupados_del_dia(CAL, MARTES)
    viejo = _token(engine)

    cal.agregar_evento_manual(_local(11), _local(12))
    cal.vencer_sync_tokens()
    cache.invalidar(CAL)
    ocupados = cache.ocupados_del_dia(CAL, MARTES)

    assert cal.tokens_recibidos == [None, viejo, None]  # intento con token, 410, listado completo
    assert len(_ids_espejo(engine)) == 2
    assert _local(11) in ocupados
    assert _token(engine) != viejo


def test_errores_distintos_de_410_se_propagan_sin_romper_el_espejo(
    engine: Engine, cfg: Config
) -> None:
    cal = CalendarioEspia()
    cal.agregar_evento_manual(_local(9), _local(10))
    cache = CacheDisponibilidad(engine, cal, cfg.negocio, RelojFalso(AHORA))
    cache.ocupados_del_dia(CAL, MARTES)
    cache.invalidar(CAL)
    cal.programar_fallo("listar_eventos", ErrorTransitorio("503"))
    with pytest.raises(ErrorTransitorio):
        cache.ocupados_del_dia(CAL, MARTES)
    assert len(_ids_espejo(engine)) == 1
    assert _local(9) in cache.ocupados_del_dia(CAL, MARTES + timedelta(days=0))
