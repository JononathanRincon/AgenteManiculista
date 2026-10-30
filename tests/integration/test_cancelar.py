"""Cancelar con estado CANCELANDO: retiene bloques hasta eliminar el evento (EARS-RES-08, EC-12)."""

import pytest
from app.config import Config
from app.mensajes import configurar
from app.modelos import ColaEscritura, Conversacion, Reserva, ReservaBloque, unidad_de_trabajo
from app.puertos import ErrorTransitorio
from app.reloj import RelojFalso
from app.reservas import (
    ReservaInvalida,
    ReservaNoEncontrada,
    SolicitudReserva,
    cancelar,
    event_id_para,
    reservar,
)
from app.simulador.calendario_falso import CalendarioFalso
from app.worker import Worker
from sqlalchemy import Engine, select

from tests.integration.conftest import AHORA, MensajeriaRegistro, local

TEL = "573001112233"


@pytest.fixture(autouse=True)
def _catalogo() -> None:
    configurar(None)


class Entorno:
    def __init__(self, engine: Engine, cfg: Config) -> None:
        self.engine, self.cfg = engine, cfg
        self.calendario = CalendarioFalso()
        self.mensajeria = MensajeriaRegistro()
        self.reloj = RelojFalso(AHORA)
        self.worker = Worker(engine, self.calendario, self.mensajeria, cfg, self.reloj)
        with unidad_de_trabajo(engine) as s:
            s.add(Conversacion(telefono=TEL, estado="FINALIZADO", actualizado_en=AHORA))

    def reservar(self) -> Reserva:
        solicitud = SolicitudReserva(TEL, "Ana", "retiro", local(3, 9))
        with unidad_de_trabajo(self.engine) as s:
            return reservar(s, solicitud, self.cfg, AHORA)

    def cancelar(self, reserva_id: int, tel: str = TEL) -> Reserva:
        with unidad_de_trabajo(self.engine) as s:
            return cancelar(s, reserva_id, tel, AHORA)

    def estado(self, reserva_id: int) -> str:
        with unidad_de_trabajo(self.engine, escritura=False) as s:
            r = s.get(Reserva, reserva_id)
            assert r is not None
            return r.estado

    def bloques(self) -> int:
        with unidad_de_trabajo(self.engine, escritura=False) as s:
            return len(s.scalars(select(ReservaBloque)).all())


@pytest.fixture
def ent(engine: Engine, cfg: Config) -> Entorno:
    return Entorno(engine, cfg)


def test_cancelar_confirmada_retiene_bloques_hasta_eliminar_el_evento(ent: Entorno) -> None:
    r = ent.reservar()
    ent.worker.procesar_uno()  # CREAR
    assert len(ent.calendario.eventos_activos()) == 1
    ent.cancelar(r.id)
    assert ent.estado(r.id) == "CANCELANDO"
    assert ent.bloques() == 2  # el horario sigue retenido
    ent.worker.procesar_uno()  # ELIMINAR
    assert ent.estado(r.id) == "CANCELADA"
    assert ent.bloques() == 0
    assert ent.calendario.eventos_activos() == []


def test_si_la_eliminacion_falla_los_bloques_siguen_retenidos(ent: Entorno) -> None:
    r = ent.reservar()
    ent.worker.procesar_uno()
    ent.cancelar(r.id)
    ent.calendario.programar_fallo("eliminar_evento", ErrorTransitorio("503"))
    ent.worker.procesar_uno()
    assert ent.estado(r.id) == "CANCELANDO"
    assert ent.bloques() == 2
    ent.reloj.avanzar(seconds=100)
    ent.worker.procesar_uno()
    assert ent.estado(r.id) == "CANCELADA"
    assert ent.bloques() == 0


def test_cancelar_con_el_crear_pendiente_se_encola_detras(ent: Entorno) -> None:
    r = ent.reservar()
    ent.cancelar(r.id)
    with unidad_de_trabajo(ent.engine, escritura=False) as s:
        trabajos = s.scalars(select(ColaEscritura).order_by(ColaEscritura.id))
        tipos = [t.tipo_operacion for t in trabajos]
    assert tipos == ["CREAR", "ELIMINAR"]
    ent.worker.procesar_uno()  # CREAR: crea el evento, pero no confirma ni avisa
    assert ent.estado(r.id) == "CANCELANDO"
    assert len(ent.calendario.eventos_activos()) == 1
    assert ent.mensajeria.enviados == []
    ent.worker.procesar_uno()  # ELIMINAR
    assert ent.estado(r.id) == "CANCELADA"
    assert ent.calendario.eventos_activos() == []
    assert ent.bloques() == 0


def test_eliminar_un_evento_que_no_existe_es_exito(ent: Entorno) -> None:
    r = ent.reservar()
    ent.cancelar(r.id)
    with unidad_de_trabajo(ent.engine) as s:
        crear = select(ColaEscritura).where(ColaEscritura.tipo_operacion == "CREAR")
        s.scalars(crear).one().estado = "COMPLETADA"
    ent.worker.procesar_uno()  # ELIMINAR de un evento que nunca se creó
    assert ent.estado(r.id) == "CANCELADA"
    assert ent.calendario.detalle(event_id_para(r)) is None


def test_cancelar_dos_veces_es_idempotente(ent: Entorno) -> None:
    r = ent.reservar()
    ent.worker.procesar_uno()
    ent.cancelar(r.id)
    ent.cancelar(r.id)
    with unidad_de_trabajo(ent.engine, escritura=False) as s:
        consulta = select(ColaEscritura).where(ColaEscritura.tipo_operacion == "ELIMINAR")
        assert len(s.scalars(consulta).all()) == 1
    ent.worker.procesar_uno()
    assert ent.cancelar(r.id).estado == "CANCELADA"


def test_otra_clienta_no_puede_cancelar(ent: Entorno) -> None:
    r = ent.reservar()
    with pytest.raises(ReservaNoEncontrada):
        ent.cancelar(r.id, tel="573009998877")
    assert ent.estado(r.id) == "PENDIENTE"


def test_no_se_cancela_una_reserva_expirada(ent: Entorno) -> None:
    r = ent.reservar()
    with unidad_de_trabajo(ent.engine) as s:
        s.get(Reserva, r.id).estado = "EXPIRADA"  # type: ignore[union-attr]
    with pytest.raises(ReservaInvalida):
        ent.cancelar(r.id)
