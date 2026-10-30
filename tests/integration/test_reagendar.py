"""Reagendar sin dejar a la clienta sin cita (EARS-RES-07, EC-13, US-04)."""

from datetime import datetime

import pytest
from app.config import Config
from app.mensajes import configurar
from app.modelos import Conversacion, Reserva, ReservaBloque, unidad_de_trabajo
from app.puertos import ErrorPermanente, ErrorTransitorio
from app.reloj import RelojFalso
from app.reservas import (
    HorarioOcupado,
    ReservaInvalida,
    ReservaNoEncontrada,
    SolicitudReserva,
    cancelar,
    event_id_para,
    reagendar,
    reservar,
)
from app.simulador.calendario_falso import CalendarioFalso
from app.worker import MAX_INTENTOS, Worker
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
        self.vieja = self.reservar(local(3, 9))
        self.worker.procesar_uno()  # la cita original queda CONFIRMADA con su evento
        self.mensajeria.enviados.clear()

    def reservar(self, inicio: datetime, tel: str = TEL) -> Reserva:
        with unidad_de_trabajo(self.engine) as s:
            return reservar(s, SolicitudReserva(tel, "Ana", "retiro", inicio), self.cfg, AHORA)

    def reagendar(self, inicio: datetime, tel: str = TEL) -> Reserva:
        with unidad_de_trabajo(self.engine) as s:
            return reagendar(s, self.vieja.id, tel, inicio, self.cfg, AHORA)

    def estado(self, reserva_id: int) -> str:
        with unidad_de_trabajo(self.engine, escritura=False) as s:
            r = s.get(Reserva, reserva_id)
            assert r is not None
            return r.estado

    def bloques(self) -> dict[datetime, int]:
        with unidad_de_trabajo(self.engine, escritura=False) as s:
            filas = s.scalars(select(ReservaBloque)).all()
            return {f.bloque_inicio: f.reserva_id for f in filas}


@pytest.fixture
def ent(engine: Engine, cfg: Config) -> Entorno:
    return Entorno(engine, cfg)


def test_reagendar_a_un_horario_que_se_solapa_con_la_propia_cita(ent: Entorno) -> None:
    nueva = ent.reagendar(local(3, 9, 30))  # 9:00-10:00 -> 9:30-10:30
    assert nueva.reemplaza_a == ent.vieja.id
    bloques = ent.bloques()
    assert bloques[local(3, 9)] == ent.vieja.id  # lo exclusivo de la vieja sigue retenido
    assert bloques[local(3, 9, 30)] == nueva.id  # bloque compartido transferido
    assert bloques[local(3, 10)] == nueva.id
    ent.worker.procesar_uno()
    assert ent.estado(nueva.id) == "CONFIRMADA"
    assert ent.estado(ent.vieja.id) == "CANCELADA"
    assert set(ent.bloques()) == {local(3, 9, 30), local(3, 10)}
    activos = ent.calendario.eventos_activos()
    assert [(e.event_id, e.inicio) for e in activos] == [(event_id_para(nueva), local(3, 9, 30))]
    assert "Reagendada" in ent.mensajeria.enviados[0][1]


def test_reagendar_a_horario_ocupado_no_cambia_nada(ent: Entorno) -> None:
    ent.reservar(local(3, 11), tel="573009998877")
    antes = ent.bloques()
    with pytest.raises(HorarioOcupado):
        ent.reagendar(local(3, 10, 30))  # choca con la reserva de las 11:00? 10:30-11:30
    assert ent.bloques() == antes
    assert ent.estado(ent.vieja.id) == "CONFIRMADA"


def test_falla_la_creacion_la_clienta_conserva_su_cita(ent: Entorno) -> None:
    nueva = ent.reagendar(local(3, 9, 30))
    ent.calendario.programar_fallo("crear_evento", ErrorPermanente("400"))
    ent.worker.procesar_uno()
    assert ent.estado(nueva.id) == "FALLIDA"
    assert ent.estado(ent.vieja.id) == "CONFIRMADA"
    assert ent.bloques() == {local(3, 9): ent.vieja.id, local(3, 9, 30): ent.vieja.id}
    assert [e.event_id for e in ent.calendario.eventos_activos()] == [event_id_para(ent.vieja)]
    assert "sigue igual" in ent.mensajeria.enviados[0][1]


def test_falla_la_eliminacion_se_borra_el_evento_nuevo_y_se_revierte(ent: Entorno) -> None:
    nueva = ent.reagendar(local(3, 9, 30))
    ent.calendario.programar_fallo("eliminar_evento", ErrorPermanente("403 forbidden"))
    ent.worker.procesar_uno()
    assert ent.estado(nueva.id) == "FALLIDA"
    assert ent.estado(ent.vieja.id) == "CONFIRMADA"
    assert [e.event_id for e in ent.calendario.eventos_activos()] == [event_id_para(ent.vieja)]


def test_error_transitorio_al_eliminar_se_reintenta_sin_duplicar(ent: Entorno) -> None:
    nueva = ent.reagendar(local(3, 9, 30))
    ent.calendario.programar_fallo("eliminar_evento", ErrorTransitorio("503"))
    ent.worker.procesar_uno()
    assert ent.estado(nueva.id) == "PENDIENTE"
    ent.reloj.avanzar(seconds=100)
    ent.worker.procesar_uno()
    assert ent.estado(nueva.id) == "CONFIRMADA"
    assert len(ent.calendario.eventos_activos()) == 1
    assert ent.calendario.conflictos_409 == 1  # el crear se repitió y el 409 cuenta como éxito


def test_intentos_agotados_revierten(ent: Entorno) -> None:
    nueva = ent.reagendar(local(3, 9, 30))
    ent.calendario.programar_fallo("crear_evento", ErrorTransitorio("503"), veces=MAX_INTENTOS)
    for _ in range(MAX_INTENTOS):
        ent.reloj.avanzar(seconds=100)
        ent.worker.procesar_uno()
    assert ent.estado(nueva.id) == "FALLIDA"
    assert ent.estado(ent.vieja.id) == "CONFIRMADA"


def test_evento_ajeno_en_el_nuevo_horario_revierte(ent: Entorno) -> None:
    nueva = ent.reagendar(local(3, 14))
    ent.calendario.agregar_evento_manual(local(3, 14), local(3, 15))
    ent.worker.procesar_uno()
    assert ent.estado(nueva.id) == "FALLIDA"
    assert ent.estado(ent.vieja.id) == "CONFIRMADA"
    assert ent.calendario.llamadas["crear_evento"] == 1  # solo el de la cita original


def test_el_propio_evento_nuevo_de_un_intento_previo_no_cuenta_como_ocupacion(
    ent: Entorno,
) -> None:
    nueva = ent.reagendar(local(3, 14))
    ent.calendario.programar_fallo("eliminar_evento", ErrorTransitorio("503"))
    ent.worker.procesar_uno()  # creó el nuevo, falló al eliminar el viejo
    ent.reloj.avanzar(seconds=100)
    ent.worker.procesar_uno()  # reintento: el evento nuevo ya existe y es nuestro
    assert ent.estado(nueva.id) == "CONFIRMADA"


def test_validaciones(ent: Entorno) -> None:
    with pytest.raises(ReservaNoEncontrada):
        ent.reagendar(local(3, 14), tel="573009998877")
    with pytest.raises(ReservaInvalida):
        ent.reagendar(local(3, 9))  # mismo horario
    with pytest.raises(ReservaInvalida):
        ent.reagendar(local(3, 9, 15))  # fuera de la cuadrícula
    pendiente = ent.reservar(local(4, 9))
    with unidad_de_trabajo(ent.engine) as s, pytest.raises(ReservaInvalida):
        reagendar(s, pendiente.id, TEL, local(4, 10), ent.cfg, AHORA)  # aún PENDIENTE


def test_reconfirmar_el_mismo_reagendamiento_devuelve_la_misma_reserva(ent: Entorno) -> None:
    a = ent.reagendar(local(3, 14))
    b = ent.reagendar(local(3, 14))
    assert a.id == b.id
    with pytest.raises(ReservaInvalida):
        ent.reagendar(local(3, 15))  # otro distinto mientras hay uno en curso


def test_no_se_cancela_la_cita_mientras_se_reagenda(ent: Entorno) -> None:
    ent.reagendar(local(3, 14))
    with unidad_de_trabajo(ent.engine) as s, pytest.raises(ReservaInvalida):
        cancelar(s, ent.vieja.id, TEL, AHORA)


def test_reagendar_pendiente_que_expira_revierte(ent: Entorno) -> None:
    nueva = ent.reagendar(local(3, 9, 30))
    ent.reloj.avanzar(minutes=11)
    assert ent.worker.limpiar() == 1
    assert ent.estado(nueva.id) == "EXPIRADA"
    assert ent.estado(ent.vieja.id) == "CONFIRMADA"
    assert ent.bloques() == {local(3, 9): ent.vieja.id, local(3, 9, 30): ent.vieja.id}
    assert "sigue igual" in ent.mensajeria.enviados[0][1]
