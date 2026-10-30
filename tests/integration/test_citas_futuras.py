"""Citas futuras por teléfono y aislamiento entre clientas (EARS-RES-09, EARS-CON-07, EC-19)."""

from datetime import timedelta

from app.config import Config
from app.modelos import Reserva, unidad_de_trabajo
from app.reservas import SolicitudReserva, cancelar, citas_futuras, reagendar, reservar
from sqlalchemy import Engine

from tests.integration.conftest import AHORA, local

A, B = "573001112233", "573009998877"


def _reservar(engine: Engine, cfg: Config, tel: str, dia: int, hora: int) -> Reserva:
    with unidad_de_trabajo(engine) as s:
        return reservar(s, SolicitudReserva(tel, "X", "retiro", local(dia, hora)), cfg, AHORA)


def _futuras(engine: Engine, tel: str, ahora=AHORA) -> list[int]:  # type: ignore[no-untyped-def]
    with unidad_de_trabajo(engine, escritura=False) as s:
        return [r.id for r in citas_futuras(s, tel, ahora)]


def test_lista_solo_las_citas_de_esa_clienta_ordenadas(engine: Engine, cfg: Config) -> None:
    tarde = _reservar(engine, cfg, A, 4, 14)
    temprano = _reservar(engine, cfg, A, 3, 9)
    ajena = _reservar(engine, cfg, B, 3, 11)
    assert _futuras(engine, A) == [temprano.id, tarde.id]
    assert _futuras(engine, B) == [ajena.id]
    assert _futuras(engine, "573000000000") == []


def test_excluye_pasadas_y_canceladas(engine: Engine, cfg: Config) -> None:
    cita = _reservar(engine, cfg, A, 3, 9)
    assert _futuras(engine, A, ahora=local(3, 10)) == []  # ya pasó
    with unidad_de_trabajo(engine) as s:
        cancelar(s, cita.id, A, AHORA)
    assert _futuras(engine, A) == []


def test_la_nueva_de_un_reagendamiento_en_curso_no_se_cuenta_doble(
    engine: Engine, cfg: Config
) -> None:
    vieja = _reservar(engine, cfg, A, 3, 9)
    with unidad_de_trabajo(engine) as s:
        s.get(Reserva, vieja.id).estado = "CONFIRMADA"  # type: ignore[union-attr]
    with unidad_de_trabajo(engine) as s:
        reagendar(s, vieja.id, A, local(3, 14), cfg, AHORA)
    assert _futuras(engine, A) == [vieja.id]


def test_con_el_maximo_de_citas_aun_se_puede_reagendar(engine: Engine, cfg: Config) -> None:
    primera = _reservar(engine, cfg, A, 3, 9)
    _reservar(engine, cfg, A, 3, 11)
    with unidad_de_trabajo(engine) as s:
        s.get(Reserva, primera.id).estado = "CONFIRMADA"  # type: ignore[union-attr]
    with unidad_de_trabajo(engine) as s:
        nueva = reagendar(s, primera.id, A, local(3, 14), cfg, AHORA + timedelta(minutes=1))
    assert nueva.reemplaza_a == primera.id
