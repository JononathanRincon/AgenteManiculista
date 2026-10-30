"""Expiración de reservas PENDIENTE y limpieza (EARS-RES-06, EC-08)."""

import asyncio
from datetime import timedelta

import pytest
from app.cola import reclamar_siguiente
from app.config import Config
from app.mensajes import configurar
from app.modelos import (
    ColaEscritura,
    Conversacion,
    MensajeProcesado,
    Reserva,
    unidad_de_trabajo,
)
from app.reloj import RelojFalso
from app.reservas import SolicitudReserva, reservar
from app.simulador.calendario_falso import CalendarioFalso
from app.worker import Worker
from sqlalchemy import Engine, select

from tests.integration.conftest import AHORA, MensajeriaRegistro, local

TEL = "573001112233"
CAL = "primary"


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

    def reservar(self, tel: str = TEL) -> Reserva:
        with unidad_de_trabajo(self.engine) as s:
            return reservar(s, SolicitudReserva(tel, "Ana", "retiro", local(3, 9)), self.cfg, AHORA)

    def estado(self, reserva_id: int) -> str:
        with unidad_de_trabajo(self.engine, escritura=False) as s:
            r = s.get(Reserva, reserva_id)
            assert r is not None
            return r.estado


@pytest.fixture
def ent(engine: Engine, cfg: Config) -> Entorno:
    return Entorno(engine, cfg)


def test_no_expira_antes_de_los_10_minutos(ent: Entorno) -> None:
    r = ent.reservar()
    ent.reloj.avanzar(minutes=9)
    assert ent.worker.limpiar() == 0
    assert ent.estado(r.id) == "PENDIENTE"


def test_expira_libera_horario_avisa_y_cancela_el_trabajo(ent: Entorno) -> None:
    r = ent.reservar()
    ent.reloj.avanzar(minutes=11)
    assert ent.worker.limpiar() == 1
    assert ent.estado(r.id) == "EXPIRADA"
    with unidad_de_trabajo(ent.engine, escritura=False) as s:
        trabajo = s.scalars(select(ColaEscritura)).one()
    assert (trabajo.estado, trabajo.resultado) == ("COMPLETADA", "expirada")
    assert "venció" in ent.mensajeria.enviados[0][1]
    # el horario queda libre para otra clienta y el worker ya no encuentra trabajo
    ent.reservar(tel="573009998877")
    assert ent.worker.procesar_uno() is True  # procesa el CREAR de la nueva clienta
    assert ent.calendario.llamadas["crear_evento"] == 1


def test_no_expira_una_reserva_en_proceso(ent: Entorno) -> None:
    r = ent.reservar()
    ent.reloj.avanzar(minutes=11)
    assert reclamar_siguiente(ent.engine, CAL, ent.reloj.ahora()) is not None  # PROCESANDO
    assert ent.worker.limpiar() == 0
    assert ent.estado(r.id) == "PENDIENTE"


def test_no_expira_una_confirmada(ent: Entorno) -> None:
    r = ent.reservar()
    ent.worker.procesar_uno()
    ent.reloj.avanzar(minutes=30)
    assert ent.worker.limpiar() == 0
    assert ent.estado(r.id) == "CONFIRMADA"


def test_expirar_con_intentos_previos_encola_la_eliminacion_del_huerfano(ent: Entorno) -> None:
    r = ent.reservar()
    with unidad_de_trabajo(ent.engine) as s:
        s.scalars(select(ColaEscritura)).one().intentos = 2  # hubo intentos que fallaron
    ent.reloj.avanzar(minutes=11)
    ent.worker.limpiar()
    with unidad_de_trabajo(ent.engine, escritura=False) as s:
        trabajos = s.scalars(select(ColaEscritura).order_by(ColaEscritura.id))
        tipos = [t.tipo_operacion for t in trabajos]
    assert tipos == ["CREAR", "ELIMINAR"]
    assert ent.estado(r.id) == "EXPIRADA"


def test_limpia_mensajes_procesados_de_mas_de_7_dias(ent: Entorno) -> None:
    with unidad_de_trabajo(ent.engine) as s:
        s.add(MensajeProcesado(wa_message_id="viejo", recibido_en=AHORA - timedelta(days=8)))
        s.add(MensajeProcesado(wa_message_id="reciente", recibido_en=AHORA - timedelta(days=2)))
    ent.worker.limpiar()
    with unidad_de_trabajo(ent.engine, escritura=False) as s:
        assert set(s.scalars(select(MensajeProcesado.wa_message_id))) == {"reciente"}


async def test_tarea_periodica_de_limpieza(ent: Entorno) -> None:
    r = ent.reservar()
    ent.reloj.avanzar(minutes=11)
    parar = asyncio.Event()
    tarea = asyncio.create_task(ent.worker.correr_limpieza(parar, cada_s=0.01))
    for _ in range(200):
        if ent.estado(r.id) == "EXPIRADA":
            break
        await asyncio.sleep(0.01)
    parar.set()
    await asyncio.wait_for(tarea, timeout=5)
    assert ent.estado(r.id) == "EXPIRADA"


async def test_la_limpieza_sobrevive_a_errores(ent: Entorno) -> None:
    intentos = {"n": 0}
    original = ent.worker.limpiar

    def explota() -> int:
        intentos["n"] += 1
        if intentos["n"] == 1:
            raise RuntimeError("inesperado")
        return original()

    ent.worker.limpiar = explota  # type: ignore[method-assign]
    parar = asyncio.Event()
    tarea = asyncio.create_task(ent.worker.correr_limpieza(parar, cada_s=0.01))
    for _ in range(100):
        if intentos["n"] >= 2:
            break
        await asyncio.sleep(0.01)
    parar.set()
    await asyncio.wait_for(tarea, timeout=5)
    assert intentos["n"] >= 2
