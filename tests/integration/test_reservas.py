"""reservar: transacción única, bloques únicos e idempotencia (EARS-RES-01..03, EARS-COL-02)."""

import json
from datetime import datetime, timedelta
from typing import Any

import pytest
from app.config import Config
from app.modelos import ColaEscritura, Reserva, ReservaBloque, unidad_de_trabajo
from app.reservas import (
    HorarioOcupado,
    LimiteCitas,
    ReservaInvalida,
    SolicitudReserva,
    event_id_para,
    reservar,
)
from sqlalchemy import Engine, select

from tests.integration.conftest import AHORA, local

TEL = "573001112233"


def _sol(
    inicio: datetime | None = None,
    servicio: str = "manicure_tradicional",
    tel: str = TEL,
    **kw: Any,
) -> SolicitudReserva:
    return SolicitudReserva(
        telefono=tel,
        nombre_perfil="Ana",
        servicio_id=servicio,
        inicio=inicio or local(3, 9),
        **kw,
    )


def _reservar(engine: Engine, cfg: Config, solicitud: SolicitudReserva) -> Reserva:
    with unidad_de_trabajo(engine) as s:
        return reservar(s, solicitud, cfg, AHORA)


def test_crea_reserva_bloques_y_trabajo_en_una_transaccion(engine: Engine, cfg: Config) -> None:
    r = _reservar(engine, cfg, _sol())
    assert r.estado == "PENDIENTE"
    assert r.fin == r.inicio + timedelta(minutes=45)
    assert r.expira_en == AHORA + timedelta(minutes=10)
    assert r.precio_cop == 30000
    assert r.recargo_cop == 0
    with unidad_de_trabajo(engine, escritura=False) as s:
        bloques = s.scalars(select(ReservaBloque.bloque_inicio)).all()
        trabajo = s.scalars(select(ColaEscritura)).one()
    assert sorted(bloques) == [local(3, 9), local(3, 9, 30)]
    assert trabajo.tipo_operacion == "CREAR"
    assert json.loads(trabajo.payload) == {"reserva_id": r.id}
    assert trabajo.reserva_id == r.id


def test_bloque_tomado_deshace_toda_la_transaccion(engine: Engine, cfg: Config) -> None:
    _reservar(engine, cfg, _sol())
    with pytest.raises(HorarioOcupado):
        _reservar(engine, cfg, _sol(tel="573009998877", inicio=local(3, 9, 30)))
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert len(s.scalars(select(Reserva)).all()) == 1
        assert len(s.scalars(select(ReservaBloque)).all()) == 2
        assert len(s.scalars(select(ColaEscritura)).all()) == 1


def test_reconfirmar_devuelve_la_misma_reserva(engine: Engine, cfg: Config) -> None:
    a = _reservar(engine, cfg, _sol())
    b = _reservar(engine, cfg, _sol())
    assert a.id == b.id
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert len(s.scalars(select(Reserva)).all()) == 1
        assert len(s.scalars(select(ColaEscritura)).all()) == 1


def test_limite_de_citas_futuras(engine: Engine, cfg: Config) -> None:
    _reservar(engine, cfg, _sol(inicio=local(3, 8)))
    _reservar(engine, cfg, _sol(inicio=local(3, 10)))
    with pytest.raises(LimiteCitas):
        _reservar(engine, cfg, _sol(inicio=local(3, 12)))
    # otra clienta no se ve afectada
    _reservar(engine, cfg, _sol(inicio=local(3, 12), tel="573005550000"))


@pytest.mark.parametrize(
    "cambio",
    [
        {"servicio": "no_existe"},
        {"inicio": local(3, 9, 15)},  # fuera de la cuadrícula
        {"inicio": local(8, 9)},  # domingo
        {"inicio": local(2, 8)},  # menos de 2 h de anticipación
        {"inicio": local(3, 17, 45)},  # la duración no termina antes del cierre
        {"modalidad": "domicilio", "direccion": "Calle 1"},  # domicilio deshabilitado
    ],
)
def test_solicitudes_invalidas(engine: Engine, cfg: Config, cambio: dict[str, Any]) -> None:
    with pytest.raises(ReservaInvalida):
        _reservar(engine, cfg, _sol(**cambio))


def test_domicilio_suma_desplazamiento_y_recargo(engine: Engine, cfg: Config) -> None:
    dom = cfg.negocio.domicilio.model_copy(update={"habilitado": True, "recargo_cop": 8000})
    cfg2 = cfg.model_copy(update={"negocio": cfg.negocio.model_copy(update={"domicilio": dom})})
    r = _reservar(engine, cfg2, _sol(modalidad="domicilio", direccion="Calle 1 # 2-3"))
    assert r.modalidad == "domicilio"
    assert r.recargo_cop == 8000
    assert r.direccion == "Calle 1 # 2-3"
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert len(s.scalars(select(ReservaBloque)).all()) == 3  # 45+15+30 min


def test_domicilio_sin_direccion_o_servicio_no_permitido(engine: Engine, cfg: Config) -> None:
    dom = cfg.negocio.domicilio.model_copy(update={"habilitado": True, "recargo_cop": 0})
    cfg2 = cfg.model_copy(update={"negocio": cfg.negocio.model_copy(update={"domicilio": dom})})
    with pytest.raises(ReservaInvalida):
        _reservar(engine, cfg2, _sol(modalidad="domicilio"))
    with pytest.raises(ReservaInvalida):
        _reservar(engine, cfg2, _sol(servicio="acrilicas", modalidad="domicilio", direccion="x"))


def test_clave_de_idempotencia_unica_y_event_id_hex(engine: Engine, cfg: Config) -> None:
    r = _reservar(engine, cfg, _sol())
    ev = event_id_para(r)
    assert len(ev) == 64
    assert set(ev) <= set("0123456789abcdef")
    assert ev == event_id_para(r)
