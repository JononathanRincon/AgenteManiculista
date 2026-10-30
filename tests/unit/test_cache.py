"""Caché de disponibilidad: TTL, métricas e invalidación (EARS-CAC-01, NFR-REN-02)."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from app.cache import CacheDisponibilidad, bloques_de_evento
from app.config import Config, cargar_config
from app.metricas import CACHE_HIT, CACHE_MISS, leer, tasa_acierto_cache
from app.modelos import crear_engine, crear_tablas, unidad_de_trabajo
from app.reloj import RelojFalso
from app.simulador.calendario_falso import CalendarioFalso
from sqlalchemy import Engine

RAIZ = Path(__file__).resolve().parents[2]
BOGOTA = ZoneInfo("America/Bogota")
AHORA = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
CAL = "cal"
MARTES = date(2026, 11, 3)


@pytest.fixture
def cfg() -> Config:
    return cargar_config(RAIZ / "config")


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = crear_engine(f"sqlite:///{tmp_path / 'c.db'}")
    crear_tablas(eng)
    yield eng
    eng.dispose()


def _local(h: int, mi: int = 0, dia: int = 3) -> datetime:
    return datetime(2026, 11, dia, h, mi, tzinfo=BOGOTA).astimezone(UTC)


def _montar(engine: Engine, cfg: Config) -> tuple[CacheDisponibilidad, CalendarioFalso, RelojFalso]:
    cal, reloj = CalendarioFalso(), RelojFalso(AHORA)
    return CacheDisponibilidad(engine, cal, cfg.negocio, reloj), cal, reloj


def test_diez_consultas_del_mismo_dia_hacen_una_llamada(engine: Engine, cfg: Config) -> None:
    cache, cal, _ = _montar(engine, cfg)
    for _ in range(10):
        cache.ocupados_del_dia(CAL, MARTES)
    assert cal.llamadas["listar_eventos"] == 1
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert leer(s, CACHE_MISS) == 1
        assert leer(s, CACHE_HIT) == 9
        assert tasa_acierto_cache(s) == pytest.approx(0.9)


def test_otro_dia_reutiliza_el_espejo_fresco(engine: Engine, cfg: Config) -> None:
    cache, cal, _ = _montar(engine, cfg)
    cache.ocupados_del_dia(CAL, MARTES)
    cache.ocupados_del_dia(CAL, MARTES + timedelta(days=1))
    assert cal.llamadas["listar_eventos"] == 1


def test_expira_con_el_ttl(engine: Engine, cfg: Config) -> None:
    cache, cal, reloj = _montar(engine, cfg)
    cache.ocupados_del_dia(CAL, MARTES)
    reloj.avanzar(seconds=61)
    cache.ocupados_del_dia(CAL, MARTES)
    assert cal.llamadas["listar_eventos"] == 2


def test_invalidar_un_dia_fuerza_refresco(engine: Engine, cfg: Config) -> None:
    cache, cal, _ = _montar(engine, cfg)
    cache.ocupados_del_dia(CAL, MARTES)
    cal.agregar_evento_manual(_local(9), _local(10))
    cache.invalidar(CAL, MARTES)
    ocupados = cache.ocupados_del_dia(CAL, MARTES)
    assert _local(9) in ocupados
    assert cal.llamadas["listar_eventos"] == 2


def test_invalidar_todo(engine: Engine, cfg: Config) -> None:
    cache, cal, _ = _montar(engine, cfg)
    cache.ocupados_del_dia(CAL, MARTES)
    cache.ocupados_del_dia(CAL, MARTES + timedelta(days=1))
    cache.invalidar(CAL)
    cache.ocupados_del_dia(CAL, MARTES)
    cache.ocupados_del_dia(CAL, MARTES + timedelta(days=1))
    assert cal.llamadas["listar_eventos"] == 2


def test_evento_ocupa_bloques_mas_margen(engine: Engine, cfg: Config) -> None:
    cache, cal, _ = _montar(engine, cfg)
    cal.agregar_evento_manual(_local(9), _local(10))  # 9:00-10:00 + 15 min de margen
    ocupados = cache.ocupados_del_dia(CAL, MARTES)
    assert ocupados == {_local(9), _local(9, 30), _local(10)}
    assert cache.ocupados_del_dia(CAL, MARTES + timedelta(days=1)) == set()


def test_bloques_de_evento_no_alineado_arranca_en_el_piso(cfg: Config) -> None:
    bloques = bloques_de_evento(_local(9, 10), _local(9, 40), cfg.negocio)
    assert bloques == [_local(9), _local(9, 30)]


def test_tasa_sin_consultas_es_cero(engine: Engine) -> None:
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert tasa_acierto_cache(s) == 0.0
