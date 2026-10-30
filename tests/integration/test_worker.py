"""Worker: verificación en vivo, creación idempotente y aviso (EARS-COL-05/06, EARS-CAL-01)."""

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from app.cola import encolar, fallidos, pausada_hasta
from app.config import Config
from app.mensajes import configurar
from app.modelos import ColaEscritura, Conversacion, Reserva, ReservaBloque, unidad_de_trabajo
from app.puertos import CuotaExcedida, ErrorPermanente, ErrorTransitorio
from app.reloj import RelojFalso
from app.reservas import SolicitudReserva, event_id_para, reservar
from app.simulador.calendario_falso import CalendarioFalso
from app.worker import MAX_INTENTOS, Worker, _jitter_aleatorio, espera_backoff
from sqlalchemy import Engine, select

from tests.integration.conftest import AHORA, MensajeriaRegistro, con_domicilio, local

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
            engine, self.calendario, self.mensajeria, cfg, self.reloj, jitter=lambda: 0.5
        )

    def conversacion(self, ultimo: Any = None, tel: str = TEL) -> None:
        with unidad_de_trabajo(self.engine) as s:
            s.add(Conversacion(telefono=tel, estado="FINALIZADO", actualizado_en=ultimo or AHORA))

    def reservar(self, inicio: Any = None, **kw: Any) -> Reserva:
        solicitud = SolicitudReserva(
            kw.pop("tel", TEL), "Ana", kw.pop("servicio", "retiro"), inicio or local(3, 9), **kw
        )
        with unidad_de_trabajo(self.engine) as s:
            return reservar(s, solicitud, self.cfg, AHORA)

    def reserva(self, reserva_id: int) -> Reserva:
        with unidad_de_trabajo(self.engine, escritura=False) as s:
            r = s.get(Reserva, reserva_id)
            assert r is not None
            return r

    def bloques(self) -> int:
        with unidad_de_trabajo(self.engine, escritura=False) as s:
            return len(s.scalars(select(ReservaBloque)).all())

    def trabajo(self) -> ColaEscritura:
        with unidad_de_trabajo(self.engine, escritura=False) as s:
            return s.scalars(select(ColaEscritura).order_by(ColaEscritura.id.desc())).first()  # type: ignore[return-value]


@pytest.fixture
def ent(engine: Engine, cfg: Config) -> Entorno:
    return Entorno(engine, cfg)


def test_cola_vacia_no_hace_nada(ent: Entorno) -> None:
    assert ent.worker.procesar_uno() is False


def test_crear_confirma_la_reserva_y_crea_el_evento(ent: Entorno) -> None:
    ent.conversacion()
    r = ent.reservar()
    assert ent.worker.procesar_uno() is True
    confirmada = ent.reserva(r.id)
    assert confirmada.estado == "CONFIRMADA"
    assert confirmada.event_id == event_id_para(r)
    assert confirmada.expira_en is None
    detalle = ent.calendario.detalle(event_id_para(r))
    assert detalle is not None
    assert detalle.titulo == "Retiro · Ana"
    assert TEL in detalle.descripcion
    assert detalle.telefono == TEL
    assert detalle.reserva_id == r.id
    assert detalle.ubicacion is None
    assert ent.trabajo().estado == "COMPLETADA"
    assert len(ent.mensajeria.enviados) == 1
    assert "Cita confirmada" in ent.mensajeria.enviados[0][1]
    assert "martes 3 de noviembre" in ent.mensajeria.enviados[0][1]
    assert "9:00 a. m." in ent.mensajeria.enviados[0][1]


def test_evento_manual_en_el_hueco_libera_la_reserva(ent: Entorno) -> None:
    ent.conversacion()
    r = ent.reservar()
    ent.calendario.agregar_evento_manual(local(3, 9), local(3, 10), "Cita de la dueña")
    ent.worker.procesar_uno()
    assert ent.reserva(r.id).estado == "FALLIDA"
    assert ent.bloques() == 0
    assert ent.trabajo().estado == "FALLIDA"
    assert ent.calendario.llamadas["crear_evento"] == 0
    assert "ya no está libre" in ent.mensajeria.enviados[0][1]
    with unidad_de_trabajo(ent.engine, escritura=False) as s:
        assert fallidos(s) == 1


def test_reintento_tras_caida_no_duplica_el_evento(ent: Entorno) -> None:
    ent.conversacion()
    r = ent.reservar()
    # el evento ya se había insertado antes de la caída (no se guardó event_id)
    primero = ent.worker
    ent.calendario.crear_evento(CAL, primero._evento_nuevo(ent.reserva(r.id)))  # noqa: SLF001
    ent.worker.procesar_uno()
    assert ent.reserva(r.id).estado == "CONFIRMADA"
    assert len(ent.calendario.eventos_activos()) == 1
    assert ent.calendario.conflictos_409 == 1


def test_fuera_de_la_ventana_de_24h_solo_se_registra(ent: Entorno) -> None:
    ent.conversacion(ultimo=AHORA - timedelta(hours=25))
    r = ent.reservar()
    ent.worker.procesar_uno()
    assert ent.reserva(r.id).estado == "CONFIRMADA"
    assert ent.mensajeria.enviados == []


def test_sin_conversacion_no_envia(ent: Entorno) -> None:
    r = ent.reservar()
    ent.worker.procesar_uno()
    assert ent.reserva(r.id).estado == "CONFIRMADA"
    assert ent.mensajeria.enviados == []


def test_un_fallo_de_envio_no_deshace_la_cita(ent: Entorno) -> None:
    ent.conversacion()
    ent.mensajeria.falla = True
    r = ent.reservar()
    ent.worker.procesar_uno()
    assert ent.reserva(r.id).estado == "CONFIRMADA"


def test_domicilio_lleva_direccion_en_location_y_descripcion(ent: Entorno) -> None:
    ent.cfg = con_domicilio(ent.cfg)
    ent.worker = Worker(
        ent.engine, ent.calendario, ent.mensajeria, ent.cfg, ent.reloj, jitter=lambda: 0.0
    )
    ent.conversacion()
    r = ent.reservar(modalidad="domicilio", direccion="Cra 7 # 8-9")
    ent.worker.procesar_uno()
    detalle = ent.calendario.detalle(event_id_para(r))
    assert detalle is not None
    assert detalle.ubicacion == "Cra 7 # 8-9"
    assert "Cra 7 # 8-9" in detalle.descripcion
    assert "8000" in detalle.descripcion
    assert "a domicilio en Cra 7 # 8-9" in ent.mensajeria.enviados[0][1]


def test_trabajo_obsoleto_se_completa_sin_crear(ent: Entorno) -> None:
    r = ent.reservar()
    with unidad_de_trabajo(ent.engine) as s:
        s.get(Reserva, r.id).estado = "EXPIRADA"  # type: ignore[union-attr]
    ent.worker.procesar_uno()
    assert ent.calendario.llamadas["crear_evento"] == 0
    assert ent.trabajo().estado == "COMPLETADA"


def test_trabajo_de_actualizar_no_tiene_efecto(ent: Entorno) -> None:
    with unidad_de_trabajo(ent.engine) as s:
        encolar(
            s,
            calendario_id=CAL,
            tipo="ACTUALIZAR",
            payload={},
            clave="x",
            reserva_id=None,
            ahora=AHORA,
        )
    ent.worker.procesar_uno()
    assert ent.trabajo().estado == "COMPLETADA"


async def test_el_bucle_procesa_y_se_detiene(ent: Entorno) -> None:
    ent.conversacion()
    r = ent.reservar()
    parar = asyncio.Event()
    tarea = asyncio.create_task(ent.worker.correr(parar, intervalo_s=0.01))
    for _ in range(200):
        if ent.reserva(r.id).estado == "CONFIRMADA":
            break
        await asyncio.sleep(0.01)
    parar.set()
    await asyncio.wait_for(tarea, timeout=5)
    assert ent.reserva(r.id).estado == "CONFIRMADA"


async def test_el_bucle_sobrevive_a_errores_inesperados(ent: Entorno) -> None:
    llamadas = {"n": 0}
    original = ent.worker.procesar_uno

    def explota() -> bool:
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            raise RuntimeError("inesperado")
        return original()

    ent.worker.procesar_uno = explota  # type: ignore[method-assign]
    parar = asyncio.Event()
    tarea = asyncio.create_task(ent.worker.correr(parar, intervalo_s=0.01))
    for _ in range(100):
        if llamadas["n"] >= 2:
            break
        await asyncio.sleep(0.01)
    parar.set()
    await asyncio.wait_for(tarea, timeout=5)
    assert llamadas["n"] >= 2


# --- TASK-18: backoff con jitter, límite de intentos y pausa por cuota ---
def test_espera_backoff_crece_y_tiene_tope() -> None:
    assert [espera_backoff(n, 0.0) for n in (1, 2, 3, 6, 7, 10)] == [2, 4, 8, 64, 64, 64]
    assert espera_backoff(1, 0.5) == 2.5


def test_jitter_por_defecto_esta_entre_0_y_1(ent: Entorno) -> None:
    valores = [_jitter_aleatorio() for _ in range(50)]
    assert all(0.0 <= v <= 1.0 for v in valores)


def test_error_transitorio_reintenta_con_backoff(ent: Entorno) -> None:
    ent.conversacion()
    r = ent.reservar()
    ent.calendario.programar_fallo("crear_evento", ErrorTransitorio("503"))
    ent.worker.procesar_uno()
    trabajo = ent.trabajo()
    assert trabajo.estado == "PENDIENTE"
    assert trabajo.intentos == 1
    assert trabajo.proxima_ejecucion == AHORA + timedelta(seconds=3)
    assert ent.reserva(r.id).estado == "PENDIENTE"
    assert ent.worker.procesar_uno() is False  # todavía no es la hora
    ent.reloj.avanzar(seconds=3)
    assert ent.worker.procesar_uno() is True
    assert ent.reserva(r.id).estado == "CONFIRMADA"
    assert len(ent.calendario.eventos_activos()) == 1


def test_cuota_agotada_reintenta_pausa_la_cola_y_termina_con_un_solo_evento(ent: Entorno) -> None:
    ent.conversacion()
    r = ent.reservar()
    ent.calendario.programar_fallo("crear_evento", CuotaExcedida("429 usageLimits"))
    ent.worker.procesar_uno()
    pausa = pausada_hasta(ent.engine, CAL)
    assert pausa == AHORA + timedelta(seconds=3)
    assert ent.worker.procesar_uno() is False  # pausada
    ent.reloj.avanzar(seconds=3)
    assert ent.worker.procesar_uno() is True
    assert ent.reserva(r.id).estado == "CONFIRMADA"
    assert len(ent.calendario.eventos_activos()) == 1
    assert ent.calendario.llamadas["crear_evento"] == 2


def test_agotados_los_intentos_el_trabajo_falla_y_libera(ent: Entorno) -> None:
    ent.conversacion()
    r = ent.reservar()
    ent.calendario.programar_fallo("crear_evento", ErrorTransitorio("503"), veces=MAX_INTENTOS)
    for _ in range(MAX_INTENTOS):
        ent.reloj.avanzar(seconds=100)
        assert ent.worker.procesar_uno() is True
    assert ent.trabajo().estado == "FALLIDA"
    assert ent.trabajo().intentos == MAX_INTENTOS
    assert ent.reserva(r.id).estado == "FALLIDA"
    assert ent.bloques() == 0
    assert "ya no está libre" in ent.mensajeria.enviados[-1][1]
    assert ent.calendario.llamadas["crear_evento"] == MAX_INTENTOS


def test_error_permanente_no_reintenta(ent: Entorno) -> None:
    ent.conversacion()
    r = ent.reservar()
    ent.calendario.programar_fallo("crear_evento", ErrorPermanente("400"))
    ent.worker.procesar_uno()
    assert ent.trabajo().estado == "FALLIDA"
    assert ent.trabajo().intentos == 1
    assert ent.reserva(r.id).estado == "FALLIDA"
