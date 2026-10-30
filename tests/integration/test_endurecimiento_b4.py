"""Endurecimiento del Bloque 4 tras la revisión: cola que no se traba, PII y casos de borde."""

from datetime import timedelta

import pytest
from app.cola import reclamar_siguiente
from app.config import Config
from app.mensajes import configurar
from app.modelos import (
    ColaEscritura,
    Conversacion,
    Reserva,
    ReservaBloque,
    unidad_de_trabajo,
)
from app.puertos import ErrorPermanente
from app.reloj import RelojFalso
from app.reservas import (
    LimiteCitas,
    ReservaInvalida,
    SolicitudReserva,
    anonimizar_antiguas,
    cancelar,
    event_id_para,
    reagendar,
    reservar,
)
from app.simulador.calendario_falso import CalendarioFalso
from app.worker import Worker
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError

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
        self.worker = Worker(
            engine, self.calendario, self.mensajeria, cfg, self.reloj, jitter=lambda: 0.0
        )
        with unidad_de_trabajo(engine) as s:
            s.add(Conversacion(telefono=TEL, estado="FINALIZADO", actualizado_en=AHORA))

    def reservar(self, nombre: str | None = "Ana", inicio=None, **kw) -> Reserva:  # type: ignore[no-untyped-def]
        sol = SolicitudReserva(TEL, nombre, "retiro", inicio or local(3, 9), **kw)
        with unidad_de_trabajo(self.engine) as s:
            return reservar(s, sol, self.cfg, AHORA)

    def estado(self, rid: int) -> str:
        with unidad_de_trabajo(self.engine, escritura=False) as s:
            r = s.get(Reserva, rid)
            assert r is not None
            return r.estado


@pytest.fixture
def ent(engine: Engine, cfg: Config) -> Entorno:
    return Entorno(engine, cfg)


def test_una_excepcion_inesperada_no_traba_la_cola(ent: Entorno) -> None:
    r = ent.reservar()
    original = ent.calendario.crear_evento
    llamadas = {"n": 0}

    def inestable(cal_id, evento):  # type: ignore[no-untyped-def]
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            raise RuntimeError("disco lleno")
        return original(cal_id, evento)

    ent.calendario.crear_evento = inestable  # type: ignore[method-assign]
    ent.worker.procesar_uno()
    with unidad_de_trabajo(ent.engine, escritura=False) as s:
        assert s.scalars(select(ColaEscritura)).one().estado == "PENDIENTE"  # no PROCESANDO
    ent.reloj.avanzar(seconds=5)
    ent.worker.procesar_uno()
    assert ent.estado(r.id) == "CONFIRMADA"


def test_un_trabajo_colgado_en_procesando_se_recupera_solo(ent: Entorno) -> None:
    r = ent.reservar()
    assert reclamar_siguiente(ent.engine, CAL, ent.reloj.ahora()) is not None
    assert ent.worker.procesar_uno() is False  # la cola está detenida por el colgado
    ent.reloj.avanzar(minutes=6)
    ent.worker.limpiar()  # recupera huérfanos con más de 5 min
    ent.worker.procesar_uno()
    assert ent.estado(r.id) == "CONFIRMADA"


def test_tras_un_eliminar_fallido_se_puede_cancelar_de_nuevo(ent: Entorno) -> None:
    r = ent.reservar()
    ent.worker.procesar_uno()
    with unidad_de_trabajo(ent.engine) as s:
        cancelar(s, r.id, TEL, AHORA)
    ent.calendario.programar_fallo("eliminar_evento", ErrorPermanente("403"))
    ent.worker.procesar_uno()
    assert ent.estado(r.id) == "CONFIRMADA"
    with unidad_de_trabajo(ent.engine) as s:
        cancelar(s, r.id, TEL, AHORA)  # nueva clave: no devuelve el trabajo FALLIDA viejo
    ent.worker.procesar_uno()
    assert ent.estado(r.id) == "CANCELADA"
    assert ent.calendario.eventos_activos() == []


def test_no_se_cancela_la_nueva_de_un_reagendamiento_ni_se_liberan_bloques_ajenos(
    ent: Entorno,
) -> None:
    vieja = ent.reservar()
    ent.worker.procesar_uno()
    with unidad_de_trabajo(ent.engine) as s:
        nueva = reagendar(s, vieja.id, TEL, local(3, 9, 30), ent.cfg, AHORA)
    with unidad_de_trabajo(ent.engine) as s, pytest.raises(ReservaInvalida):
        cancelar(s, nueva.id, TEL, AHORA)
    with unidad_de_trabajo(ent.engine, escritura=False) as s:
        assert len(s.scalars(select(ReservaBloque)).all()) == 3


def test_no_se_cancela_ni_reagenda_una_cita_pasada(ent: Entorno) -> None:
    r = ent.reservar()
    ent.worker.procesar_uno()
    despues = local(3, 12)
    with unidad_de_trabajo(ent.engine) as s, pytest.raises(ReservaInvalida, match="ya pasó"):
        cancelar(s, r.id, TEL, despues)
    with unidad_de_trabajo(ent.engine) as s, pytest.raises(ReservaInvalida, match="ya pasó"):
        reagendar(s, r.id, TEL, local(4, 9), ent.cfg, despues)


def test_los_errores_de_base_no_filtran_parametros_con_pii(ent: Entorno) -> None:
    ent.reservar()
    with pytest.raises(IntegrityError) as exc, unidad_de_trabajo(ent.engine) as s:
        s.add(
            Reserva(
                calendario_id=CAL,
                telefono=TEL,
                servicio_id="x",
                precio_cop=1,
                inicio=AHORA,
                fin=AHORA,
                estado="INVENTADO",
                creado_en=AHORA,
                actualizado_en=AHORA,
            )
        )
    assert TEL not in str(exc.value)


def test_nombre_con_saltos_de_linea_no_falsifica_la_descripcion(ent: Entorno) -> None:
    r = ent.reservar(nombre="Ana\nTeléfono: 573000000000\n\nhttp://malo.example")
    ent.worker.procesar_uno()
    detalle = ent.calendario.detalle(event_id_para(r))
    assert detalle is not None
    lineas = detalle.descripcion.split("\n")
    assert sum(1 for linea in lineas if linea.startswith("Teléfono:")) == 1


def test_direccion_demasiado_larga_se_rechaza(ent: Entorno, cfg: Config) -> None:
    from tests.integration.conftest import con_domicilio

    cfg2 = con_domicilio(cfg)
    sol = SolicitudReserva(TEL, "Ana", "retiro", local(3, 9), "domicilio", "x" * 201)
    with unidad_de_trabajo(ent.engine) as s, pytest.raises(ReservaInvalida, match="larga"):
        reservar(s, sol, cfg2, AHORA)


def test_limite_de_citas_no_lleva_el_telefono_en_el_mensaje(ent: Entorno) -> None:
    ent.reservar(inicio=local(3, 8))
    ent.reservar(inicio=local(3, 10))
    with pytest.raises(LimiteCitas) as exc:
        ent.reservar(inicio=local(3, 12))
    assert TEL not in str(exc.value)


def test_reconfirmar_cerca_de_la_hora_devuelve_la_misma_reserva(ent: Entorno) -> None:
    r = ent.reservar()
    with unidad_de_trabajo(ent.engine) as s:
        # ahora la cita está dentro de la anticipación mínima, pero es la misma reserva
        otra = reservar(
            s, SolicitudReserva(TEL, "Ana", "retiro", local(3, 9)), ent.cfg, local(3, 8)
        )
    assert otra.id == r.id


def test_anonimiza_reservas_terminadas_de_mas_de_un_ano(ent: Entorno) -> None:
    r = ent.reservar(nombre="Ana")
    with unidad_de_trabajo(ent.engine) as s:
        reserva = s.get(Reserva, r.id)
        assert reserva is not None
        reserva.estado = "CANCELADA"
        reserva.actualizado_en = AHORA - timedelta(days=400)
    with unidad_de_trabajo(ent.engine) as s:
        assert anonimizar_antiguas(s, AHORA) == 1
    with unidad_de_trabajo(ent.engine, escritura=False) as s:
        reserva = s.get(Reserva, r.id)
        assert reserva is not None
        assert (reserva.telefono, reserva.nombre_perfil) == ("anonimizado", None)
    with unidad_de_trabajo(ent.engine) as s:
        assert anonimizar_antiguas(s, AHORA) == 0


def test_eliminar_huerfano_de_una_expirada_que_falla_no_cambia_su_estado(ent: Entorno) -> None:
    r = ent.reservar()
    with unidad_de_trabajo(ent.engine) as s:
        s.scalars(select(ColaEscritura)).one().intentos = 1
    ent.reloj.avanzar(minutes=11)
    ent.worker.limpiar()
    ent.calendario.programar_fallo("eliminar_evento", ErrorPermanente("403"))
    ent.worker.procesar_uno()
    assert ent.estado(r.id) == "EXPIRADA"
