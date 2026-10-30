"""Modelos ORM, PRAGMAs y escritura serializada (NFR-INT-01, EARS-COL-01)."""

import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from app.modelos import (
    Conversacion,
    Metrica,
    Reserva,
    ReservaBloque,
    crear_engine,
    crear_tablas,
    unidad_de_trabajo,
)
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, OperationalError, StatementError

AHORA = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = crear_engine(f"sqlite:///{tmp_path / 'prueba.db'}", busy_timeout_ms=300)
    crear_tablas(eng)
    yield eng
    eng.dispose()


def _reserva(**extra: object) -> Reserva:
    datos: dict[str, object] = {
        "calendario_id": "cal",
        "telefono": "573001112233",
        "servicio_id": "retiro",
        "precio_cop": 15000,
        "inicio": AHORA,
        "fin": AHORA + timedelta(minutes=30),
        "estado": "PENDIENTE",
        "creado_en": AHORA,
        "actualizado_en": AHORA,
    }
    datos.update(extra)
    return Reserva(**datos)


def test_pragmas_activos(engine: Engine) -> None:
    with engine.connect() as c:
        assert c.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert c.execute(text("PRAGMA busy_timeout")).scalar() == 300
        assert c.execute(text("PRAGMA synchronous")).scalar() == 1  # NORMAL


def test_fecha_utc_ida_y_vuelta(engine: Engine) -> None:
    local = datetime(2026, 11, 3, 10, 0, tzinfo=timezone(timedelta(hours=-5)))
    with unidad_de_trabajo(engine) as s:
        s.add(_reserva(inicio=local))
    with unidad_de_trabajo(engine, escritura=False) as s:
        r = s.query(Reserva).one()
        assert r.inicio == AHORA
        assert r.inicio.tzinfo is not None
        crudo = s.execute(text("SELECT inicio FROM reservas")).scalar()
    assert crudo == "2026-11-03T15:00:00Z"


def test_fecha_sin_zona_se_rechaza(engine: Engine) -> None:
    ingenua = datetime(2026, 11, 3, 15, 0)  # noqa: DTZ001
    with pytest.raises(StatementError, match="zona"), unidad_de_trabajo(engine) as s:
        s.add(_reserva(inicio=ingenua))


def test_bloque_unico_por_calendario(engine: Engine) -> None:
    with unidad_de_trabajo(engine) as s:
        a, b = _reserva(), _reserva(telefono="573009998877")
        s.add_all([a, b])
        s.flush()
        s.add(ReservaBloque(calendario_id="cal", bloque_inicio=AHORA, reserva_id=a.id))
        segunda = b.id
    with pytest.raises(IntegrityError), unidad_de_trabajo(engine) as s:
        s.add(ReservaBloque(calendario_id="cal", bloque_inicio=AHORA, reserva_id=segunda))


def test_estado_invalido_rechazado(engine: Engine) -> None:
    with pytest.raises(IntegrityError), unidad_de_trabajo(engine) as s:
        s.add(_reserva(estado="INVENTADO"))


def test_rollback_ante_excepcion(engine: Engine) -> None:
    with pytest.raises(RuntimeError), unidad_de_trabajo(engine) as s:
        s.add(Metrica(nombre="x", valor=1))
        raise RuntimeError("falla")
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert s.query(Metrica).count() == 0


def test_conversacion_valores_por_defecto(engine: Engine) -> None:
    with unidad_de_trabajo(engine) as s:
        s.add(Conversacion(telefono="573001", estado="INICIO", actualizado_en=AHORA))
    with unidad_de_trabajo(engine, escritura=False) as s:
        c = s.get(Conversacion, "573001")
        assert c is not None
        assert c.contexto == "{}"
        assert c.aviso_datos_aceptado is False


def test_segunda_escritura_espera_mientras_otra_tiene_begin_immediate(engine: Engine) -> None:
    dentro = threading.Event()
    soltar = threading.Event()

    def retenedora() -> None:
        with unidad_de_trabajo(engine) as s:
            s.add(Metrica(nombre="a", valor=1))
            s.flush()  # la transacción (BEGIN IMMEDIATE) empieza con el primer SQL
            dentro.set()
            soltar.wait(5)

    hilo = threading.Thread(target=retenedora)
    hilo.start()
    assert dentro.wait(5)
    try:
        with pytest.raises(OperationalError, match="locked"), unidad_de_trabajo(engine) as s:
            s.add(Metrica(nombre="b", valor=1))
        with unidad_de_trabajo(engine, escritura=False) as s:  # una lectura sí avanza (WAL)
            assert s.query(Metrica).count() == 0
    finally:
        soltar.set()
        hilo.join()
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert s.query(Metrica).count() == 1
