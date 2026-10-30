"""CalendarioFalso: contrato del puerto, fallos programables y 409 (EARS-COL-05, EARS-CAC-05)."""

from datetime import UTC, datetime, timedelta

import pytest
from app.puertos import (
    CuotaExcedida,
    ErrorSyncTokenExpirado,
    ErrorTransitorio,
    EventoNuevo,
)
from app.simulador.calendario_falso import CalendarioFalso

T0 = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
CAL = "cal"
RANGO = (T0 - timedelta(days=1), T0 + timedelta(days=2))


def _evento(id_: str = "abc1", inicio: datetime = T0, reserva_id: int = 1) -> EventoNuevo:
    return EventoNuevo(
        id=id_,
        titulo="Retiro · Ana",
        descripcion="Tel 573001",
        inicio=inicio,
        fin=inicio + timedelta(minutes=30),
        telefono="573001",
        reserva_id=reserva_id,
        ubicacion="Calle 1",
    )


def test_crear_y_listar_completo() -> None:
    cal = CalendarioFalso()
    assert cal.crear_evento(CAL, _evento()) == "abc1"
    res = cal.listar_eventos(CAL, sync_token=None, desde=RANGO[0], hasta=RANGO[1])
    assert [e.event_id for e in res.eventos] == ["abc1"]
    assert res.resincronizacion_completa is True
    assert res.nuevo_sync_token is not None


def test_id_duplicado_es_exito_y_no_duplica() -> None:
    cal = CalendarioFalso()
    cal.crear_evento(CAL, _evento())
    assert cal.crear_evento(CAL, _evento()) == "abc1"
    assert cal.conflictos_409 == 1
    assert len(cal.eventos_activos()) == 1


def test_listado_incremental_trae_solo_cambios_incluidos_cancelados() -> None:
    cal = CalendarioFalso()
    cal.crear_evento(CAL, _evento("a"))
    token = cal.listar_eventos(CAL, sync_token=None, desde=RANGO[0], hasta=RANGO[1])
    cal.crear_evento(CAL, _evento("b", T0 + timedelta(hours=2), 2))
    cal.eliminar_evento(CAL, "a")
    inc = cal.listar_eventos(CAL, sync_token=token.nuevo_sync_token, desde=RANGO[0], hasta=RANGO[1])
    assert inc.resincronizacion_completa is False
    por_id = {e.event_id: e.cancelado for e in inc.eventos}
    assert por_id == {"a": True, "b": False}


def test_sync_token_vencido_lanza_410() -> None:
    cal = CalendarioFalso()
    cal.crear_evento(CAL, _evento())
    token = cal.listar_eventos(CAL, sync_token=None, desde=RANGO[0], hasta=RANGO[1])
    cal.vencer_sync_tokens()
    with pytest.raises(ErrorSyncTokenExpirado):
        cal.listar_eventos(CAL, sync_token=token.nuevo_sync_token, desde=RANGO[0], hasta=RANGO[1])
    with pytest.raises(ErrorSyncTokenExpirado):
        basura = "basura"
        cal.listar_eventos(CAL, sync_token=basura, desde=RANGO[0], hasta=RANGO[1])


def test_fallos_programados_se_consumen_en_orden() -> None:
    cal = CalendarioFalso()
    cal.programar_fallo("crear_evento", CuotaExcedida("429"), veces=2)
    cal.programar_fallo("crear_evento", ErrorTransitorio("503"))
    with pytest.raises(CuotaExcedida):
        cal.crear_evento(CAL, _evento())
    with pytest.raises(CuotaExcedida):
        cal.crear_evento(CAL, _evento())
    with pytest.raises(ErrorTransitorio):
        cal.crear_evento(CAL, _evento())
    assert cal.crear_evento(CAL, _evento()) == "abc1"
    assert cal.llamadas["crear_evento"] == 4


def test_ocupacion_en_vivo_detecta_evento_manual_y_excluye() -> None:
    cal = CalendarioFalso()
    manual = cal.agregar_evento_manual(T0, T0 + timedelta(hours=1))
    ocupado = cal.ocupacion_en_vivo(CAL, T0, T0 + timedelta(minutes=30))
    assert len(ocupado) == 1
    assert cal.ocupacion_en_vivo(CAL, T0, T0 + timedelta(minutes=30), excluir_event_id=manual) == []
    assert cal.ocupacion_en_vivo(CAL, T0 + timedelta(hours=1), T0 + timedelta(hours=2)) == []


def test_eliminar_inexistente_es_exito_y_libera() -> None:
    cal = CalendarioFalso()
    cal.eliminar_evento(CAL, "no-existe")
    cal.crear_evento(CAL, _evento())
    cal.eliminar_evento(CAL, "abc1")
    cal.eliminar_evento(CAL, "abc1")
    assert cal.ocupacion_en_vivo(CAL, T0, T0 + timedelta(hours=1)) == []
    # tras eliminar se puede volver a crear con el mismo id
    cal.crear_evento(CAL, _evento())
    assert len(cal.eventos_activos()) == 1


def test_buscar_por_telefono_aisla_clientas() -> None:
    cal = CalendarioFalso()
    cal.crear_evento(CAL, _evento("a"))
    cal.crear_evento(CAL, EventoNuevo("b", "x", "y", T0, T0, "573999", 2))
    cal.agregar_evento_manual(T0, T0 + timedelta(hours=1))
    citas = cal.buscar_por_telefono(CAL, "573001", T0 - timedelta(days=1))
    assert [c.event_id for c in citas] == ["a"]
    assert cal.buscar_por_telefono(CAL, "573001", T0 + timedelta(days=1)) == []


def test_oyentes_reciben_cambios_y_reiniciar_limpia() -> None:
    cal = CalendarioFalso()
    avisos: list[int] = []
    cal.suscribir(lambda: avisos.append(1))
    cal.crear_evento(CAL, _evento())
    cal.eliminar_evento(CAL, "abc1")
    cal.agregar_evento_manual(T0, T0 + timedelta(hours=1))
    cal.reiniciar()
    assert len(avisos) == 4
    assert cal.eventos_activos() == []


def test_detalle_y_retardo() -> None:
    cal = CalendarioFalso(retardo_s=0.01)
    cal.crear_evento(CAL, _evento())
    detalle = cal.detalle("abc1")
    assert detalle is not None
    assert detalle.ubicacion == "Calle 1"
    assert cal.detalle("zzz") is None
