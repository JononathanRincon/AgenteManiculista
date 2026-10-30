"""Pausa por intervención humana, rate limit y nunca en frío (EARS-WA-15, 17, 18, EC-22)."""

from datetime import timedelta

import pytest
from app.config import AntiBan, Config
from app.control_humano import ControlHumano
from app.modelos import Conversacion, unidad_de_trabajo
from sqlalchemy import Engine

from tests.integration.conftest import AHORA

TEL = "573001112233"


def _control(engine: Engine, cfg: Config, **anti: int) -> ControlHumano:
    negocio = cfg.negocio.model_copy(update={"anti_ban": AntiBan(**anti)}) if anti else cfg.negocio
    return ControlHumano(engine, negocio)


def _escribio(engine: Engine, telefono: str = TEL) -> None:
    with unidad_de_trabajo(engine) as s:
        s.add(Conversacion(telefono=telefono, estado="MENU", actualizado_en=AHORA))


def test_la_pausa_dura_120_minutos_y_luego_expira(engine: Engine, cfg: Config) -> None:
    _escribio(engine)
    control = _control(engine, cfg)
    assert control.esta_pausado(TEL, AHORA) is False
    assert control.pausar(TEL, AHORA) is True
    assert control.esta_pausado(TEL, AHORA + timedelta(minutes=119)) is True
    assert control.esta_pausado(TEL, AHORA + timedelta(minutes=120)) is False  # justo al vencer
    assert control.esta_pausado(TEL, AHORA + timedelta(hours=3)) is False


def test_pausar_de_nuevo_extiende_la_pausa_y_reanudar_la_quita(engine: Engine, cfg: Config) -> None:
    _escribio(engine)
    control = _control(engine, cfg)
    control.pausar(TEL, AHORA)
    control.pausar(TEL, AHORA + timedelta(minutes=100))
    assert control.esta_pausado(TEL, AHORA + timedelta(minutes=200)) is True
    control.reanudar(TEL)
    assert control.esta_pausado(TEL, AHORA + timedelta(minutes=1)) is False
    control.reanudar("573000000000")  # inexistente: no falla


def test_no_se_crean_filas_para_chats_personales_de_la_duena(engine: Engine, cfg: Config) -> None:
    control = _control(engine, cfg)
    assert control.pausar("573009998877", AHORA) is False  # esa persona nunca escribió al bot
    assert control.esta_pausado("573009998877", AHORA) is False
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert s.get(Conversacion, "573009998877") is None


def test_la_pausa_de_una_clienta_no_afecta_a_otra(engine: Engine, cfg: Config) -> None:
    _escribio(engine, "573001111111")
    _escribio(engine, "573002222222")
    control = _control(engine, cfg)
    control.pausar("573001111111", AHORA)
    assert control.esta_pausado("573002222222", AHORA) is False


def test_tope_de_15_mensajes_por_telefono_en_10_minutos(engine: Engine, cfg: Config) -> None:
    _escribio(engine)
    control = _control(engine, cfg)
    resultados = [control.autorizar_envio(TEL, AHORA + timedelta(seconds=i)) for i in range(17)]
    assert resultados == [True] * 15 + [False] * 2
    # a los 10 minutos del primer envío la ventana se libera
    assert control.autorizar_envio(TEL, AHORA + timedelta(minutes=10, seconds=1)) is True


def test_la_ventana_es_deslizante(engine: Engine, cfg: Config) -> None:
    _escribio(engine)
    control = _control(engine, cfg, max_mensajes_por_telefono=2, max_mensajes_globales=10)
    assert control.autorizar_envio(TEL, AHORA) is True
    assert control.autorizar_envio(TEL, AHORA + timedelta(minutes=6)) is True
    assert control.autorizar_envio(TEL, AHORA + timedelta(minutes=7)) is False
    salio_el_primero = AHORA + timedelta(minutes=10, seconds=1)
    assert control.autorizar_envio(TEL, salio_el_primero) is True


def test_tope_global_entre_todas_las_clientas(engine: Engine, cfg: Config) -> None:
    for n in range(4):
        _escribio(engine, f"57300000000{n}")
    control = _control(engine, cfg, max_mensajes_por_telefono=2, max_mensajes_globales=3)
    asignados = [control.autorizar_envio(f"57300000000{n}", AHORA) for n in (0, 0, 1, 2, 3)]
    assert asignados == [True, True, True, False, False]


def test_nunca_se_escribe_en_frio(engine: Engine, cfg: Config) -> None:
    control = _control(engine, cfg)
    assert control.autorizar_envio("573005550000", AHORA) is False  # nunca escribió
    _escribio(engine, "573005550000")
    assert control.autorizar_envio("573005550000", AHORA) is True


@pytest.mark.parametrize("minutos", [0, 5, 9])
def test_limpiar_conserva_lo_que_sigue_en_la_ventana(
    engine: Engine, cfg: Config, minutos: int
) -> None:
    _escribio(engine)
    control = _control(engine, cfg)
    control.autorizar_envio(TEL, AHORA)
    borrados = control.limpiar(AHORA + timedelta(minutes=minutos))
    assert borrados == 0


def test_limpiar_borra_lo_que_ya_salio_de_la_ventana(engine: Engine, cfg: Config) -> None:
    _escribio(engine)
    control = _control(engine, cfg)
    control.autorizar_envio(TEL, AHORA)
    assert control.limpiar(AHORA + timedelta(minutes=10)) == 1


def test_pausar_con_crear_registra_a_una_clienta_que_aun_no_tenia_conversacion(
    engine: Engine, cfg: Config
) -> None:
    control = _control(engine, cfg)
    assert control.pausar("573004443322", AHORA) is False
    assert control.pausar("573004443322", AHORA, crear=True) is True
    assert control.esta_pausado("573004443322", AHORA + timedelta(minutes=5)) is True
