"""Ramas de borde del worker y de reservas: obsolescencia, compensaciones y caché."""

import pytest
from app.cache import CacheDisponibilidad
from app.cola import Trabajo
from app.config import Config
from app.mensajes import configurar
from app.modelos import ColaEscritura, Conversacion, Reserva, unidad_de_trabajo
from app.puertos import ErrorPermanente
from app.reloj import RelojFalso
from app.reservas import ReservaNoEncontrada, SolicitudReserva, cargar, expirar_pendientes, reservar
from app.simulador.calendario_falso import CalendarioFalso
from app.worker import Worker
from sqlalchemy import Engine, select

from tests.integration.conftest import AHORA, MensajeriaRegistro, local

TEL = "573001112233"


@pytest.fixture(autouse=True)
def _catalogo() -> None:
    configurar(None)


class Entorno:
    def __init__(self, engine: Engine, cfg: Config, *, con_cache: bool = False) -> None:
        self.engine, self.cfg = engine, cfg
        self.calendario = CalendarioFalso()
        self.mensajeria = MensajeriaRegistro()
        self.reloj = RelojFalso(AHORA)
        self.cache = (
            CacheDisponibilidad(engine, self.calendario, cfg.negocio, self.reloj)
            if con_cache
            else None
        )
        self.worker = Worker(
            engine, self.calendario, self.mensajeria, cfg, self.reloj, cache=self.cache
        )
        with unidad_de_trabajo(engine) as s:
            s.add(Conversacion(telefono=TEL, estado="FINALIZADO", actualizado_en=AHORA))

    def reservar(self, inicio=None) -> Reserva:  # type: ignore[no-untyped-def]
        solicitud = SolicitudReserva(TEL, "Ana", "retiro", inicio or local(3, 9))
        with unidad_de_trabajo(self.engine) as s:
            return reservar(s, solicitud, self.cfg, AHORA)

    def poner_estado(self, reserva_id: int, estado: str) -> None:
        with unidad_de_trabajo(self.engine) as s:
            s.get(Reserva, reserva_id).estado = estado  # type: ignore[union-attr]


def test_cargar_reserva_inexistente(engine: Engine) -> None:
    with unidad_de_trabajo(engine) as s, pytest.raises(ReservaNoEncontrada):
        cargar(s, 999)


def test_expirar_una_pendiente_sin_trabajo_en_cola(engine: Engine, cfg: Config) -> None:
    ent = Entorno(engine, cfg)
    r = ent.reservar()
    with unidad_de_trabajo(engine) as s:
        for t in s.scalars(select(ColaEscritura)):
            s.delete(t)
    ent.reloj.avanzar(minutes=11)
    with unidad_de_trabajo(engine) as s:
        expiradas = expirar_pendientes(s, cfg, ent.reloj.ahora())
    assert [e.id for e in expiradas] == [r.id]


def test_eliminar_huerfano_de_una_reserva_expirada_no_cambia_su_estado(
    engine: Engine, cfg: Config
) -> None:
    ent = Entorno(engine, cfg)
    r = ent.reservar()
    with unidad_de_trabajo(engine) as s:
        s.scalars(select(ColaEscritura)).one().intentos = 1
    ent.reloj.avanzar(minutes=11)
    ent.worker.limpiar()  # encola ELIMINAR del posible evento huérfano
    assert ent.worker.procesar_uno() is True
    assert ent.calendario.llamadas["eliminar_evento"] == 1
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert s.get(Reserva, r.id).estado == "EXPIRADA"  # type: ignore[union-attr]


def test_reagendar_obsoleto_se_completa_sin_tocar_calendar(engine: Engine, cfg: Config) -> None:
    from app.reservas import reagendar

    ent = Entorno(engine, cfg)
    vieja = ent.reservar()
    ent.worker.procesar_uno()
    with unidad_de_trabajo(engine) as s:
        nueva = reagendar(s, vieja.id, TEL, local(3, 14), cfg, AHORA)
    ent.poner_estado(nueva.id, "EXPIRADA")
    antes = ent.calendario.llamadas["crear_evento"]
    ent.worker.procesar_uno()
    assert ent.calendario.llamadas["crear_evento"] == antes


def test_eliminar_con_error_permanente_devuelve_la_cita_a_confirmada(
    engine: Engine, cfg: Config
) -> None:
    from app.reservas import cancelar

    ent = Entorno(engine, cfg)
    r = ent.reservar()
    ent.worker.procesar_uno()
    with unidad_de_trabajo(engine) as s:
        cancelar(s, r.id, TEL, AHORA)
    ent.calendario.programar_fallo("eliminar_evento", ErrorPermanente("403"))
    ent.worker.procesar_uno()
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert s.get(Reserva, r.id).estado == "CONFIRMADA"  # type: ignore[union-attr]
        ultimo = s.scalars(select(ColaEscritura).order_by(ColaEscritura.id.desc())).first()
        assert ultimo is not None
        assert ultimo.estado == "FALLIDA"


def test_fallo_definitivo_de_crear_sobre_una_reserva_ya_resuelta_no_hace_nada(
    engine: Engine, cfg: Config
) -> None:
    ent = Entorno(engine, cfg)
    r = ent.reservar()
    ent.worker.procesar_uno()  # CONFIRMADA
    trabajo = Trabajo(999, cfg.negocio.calendario_id, "CREAR", {"reserva_id": r.id}, 1, r.id)
    with unidad_de_trabajo(engine) as s:
        from app.cola import encolar

        encolar(
            s,
            calendario_id=cfg.negocio.calendario_id,
            tipo="CREAR",
            payload={},
            clave="zz",
            reserva_id=None,
            ahora=AHORA,
        )
    ent.worker._fallo_definitivo(trabajo, "prueba")  # noqa: SLF001
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert s.get(Reserva, r.id).estado == "CONFIRMADA"  # type: ignore[union-attr]
    assert len(ent.mensajeria.enviados) == 1  # solo la confirmación original


def test_revertir_ignora_una_nueva_que_ya_no_esta_pendiente(engine: Engine, cfg: Config) -> None:
    from app.reservas import reagendar

    ent = Entorno(engine, cfg)
    vieja = ent.reservar()
    ent.worker.procesar_uno()
    with unidad_de_trabajo(engine) as s:
        nueva = reagendar(s, vieja.id, TEL, local(3, 14), cfg, AHORA)
    ent.poner_estado(nueva.id, "CONFIRMADA")
    trabajo = Trabajo(
        999,
        cfg.negocio.calendario_id,
        "REAGENDAR",
        {"reserva_nueva_id": nueva.id, "reserva_vieja_id": vieja.id},
        1,
        nueva.id,
    )
    ent.worker._revertir(trabajo)  # noqa: SLF001
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert s.get(Reserva, nueva.id).estado == "CONFIRMADA"  # type: ignore[union-attr]


def test_si_tambien_falla_la_compensacion_se_registra_y_se_revierte(
    engine: Engine, cfg: Config
) -> None:
    from app.reservas import reagendar

    ent = Entorno(engine, cfg)
    vieja = ent.reservar()
    ent.worker.procesar_uno()
    with unidad_de_trabajo(engine) as s:
        nueva = reagendar(s, vieja.id, TEL, local(3, 14), cfg, AHORA)
    ent.calendario.programar_fallo("eliminar_evento", ErrorPermanente("403"), veces=2)
    ent.worker.procesar_uno()
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert s.get(Reserva, nueva.id).estado == "FALLIDA"  # type: ignore[union-attr]
        assert s.get(Reserva, vieja.id).estado == "CONFIRMADA"  # type: ignore[union-attr]


def test_el_worker_invalida_el_cache_del_dia(engine: Engine, cfg: Config) -> None:
    ent = Entorno(engine, cfg, con_cache=True)
    assert ent.cache is not None
    dia = local(3, 9).astimezone(cfg.negocio.zona).date()
    ent.cache.ocupados_del_dia(cfg.negocio.calendario_id, dia)
    assert ent.calendario.llamadas["listar_eventos"] == 1
    ent.reservar()
    ent.worker.procesar_uno()
    ocupados = ent.cache.ocupados_del_dia(cfg.negocio.calendario_id, dia)
    assert local(3, 9) in ocupados  # el evento recién creado ya se ve: el caché se invalidó
    assert ent.calendario.llamadas["listar_eventos"] == 2
