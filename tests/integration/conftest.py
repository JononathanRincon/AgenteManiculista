"""Fixtures compartidos de integración."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from app.config import Config, cargar_config
from app.modelos import crear_engine, crear_tablas
from app.puertos import Opcion
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


class MensajeriaRegistro:
    """Mensajería de prueba: guarda lo enviado."""

    def __init__(self) -> None:
        self.enviados: list[tuple[str, str]] = []
        self.falla = False

    def enviar_texto(self, telefono: str, texto: str) -> None:
        if self.falla:
            raise RuntimeError("envío caído")
        self.enviados.append((telefono, texto))

    def enviar_botones(self, telefono: str, cuerpo: str, opciones: list[Opcion]) -> None:
        self.enviados.append((telefono, cuerpo))

    def enviar_lista(self, telefono: str, cuerpo: str, boton: str, filas: list[Opcion]) -> None:
        self.enviados.append((telefono, cuerpo))


def con_domicilio(cfg: Config, recargo: int = 8000) -> Config:
    """Copia de la configuración con el domicilio habilitado."""
    dom = cfg.negocio.domicilio.model_copy(update={"habilitado": True, "recargo_cop": recargo})
    return cfg.model_copy(update={"negocio": cfg.negocio.model_copy(update={"domicilio": dom})})
