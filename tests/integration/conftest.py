"""Fixtures compartidos de integración."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from app.config import Config, cargar_config
from app.modelos import crear_engine, crear_tablas
from sqlalchemy import Engine

RAIZ = Path(__file__).resolve().parents[2]
BOGOTA = ZoneInfo("America/Bogota")
# lunes 2 de noviembre de 2026, 07:00 hora de Bogotá
AHORA = datetime(2026, 11, 2, 7, 0, tzinfo=BOGOTA).astimezone(UTC)


def local(dia: int, h: int, mi: int = 0) -> datetime:
    """Instante UTC de una hora local de Bogotá en noviembre de 2026."""
    return datetime(2026, 11, dia, h, mi, tzinfo=BOGOTA).astimezone(UTC)


@pytest.fixture
def cfg() -> Config:
    return cargar_config(RAIZ / "config")


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = crear_engine(f"sqlite:///{tmp_path / 'i.db'}", busy_timeout_ms=10000)
    crear_tablas(eng)
    yield eng
    eng.dispose()
