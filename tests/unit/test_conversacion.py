"""Máquina de estados: transiciones, persistencia e inactividad (EARS-CON-01/02)."""

from datetime import timedelta

import pytest
from app.config import Config
from app.conversacion import (
    TRANSICIONES,
    Contexto,
    Estado,
    TransicionInvalida,
    cargar_sesion,
    guardar_sesion,
)
from app.modelos import unidad_de_trabajo
from sqlalchemy import Engine

from tests.integration.conftest import AHORA

TEL = "573001112233"
PARES = [(a, b) for a in Estado for b in Estado]


@pytest.mark.parametrize(("origen", "destino"), PARES)
def test_transiciones_solo_las_permitidas(origen: Estado, destino: Estado) -> None:
    ctx = Contexto(estado=origen)
    if destino in TRANSICIONES[origen]:
        ctx.ir(destino)
        assert ctx.estado == destino
    else:
        with pytest.raises(TransicionInvalida):
            ctx.ir(destino)
        assert ctx.estado == origen


def test_confirmando_solo_se_alcanza_desde_eligiendo_hora() -> None:
    origenes = [e for e in Estado if Estado.CONFIRMANDO in TRANSICIONES[e]]
    assert origenes == [Estado.ELIGIENDO_HORA]


def test_el_diagrama_del_sdd_esta_incluido() -> None:
    esperadas = {
        (Estado.INICIO, Estado.MENU),
        (Estado.INICIO, Estado.ELIGIENDO_HORA),
        (Estado.MENU, Estado.ELIGIENDO_SERVICIO),
        (Estado.MENU, Estado.GESTIONANDO_CITA),
        (Estado.ELIGIENDO_SERVICIO, Estado.ELIGIENDO_MODALIDAD),
        (Estado.ELIGIENDO_SERVICIO, Estado.ELIGIENDO_DIA),
        (Estado.ELIGIENDO_MODALIDAD, Estado.ELIGIENDO_DIA),
        (Estado.ELIGIENDO_DIA, Estado.ELIGIENDO_HORA),
        (Estado.ELIGIENDO_HORA, Estado.CONFIRMANDO),
        (Estado.CONFIRMANDO, Estado.FINALIZADO),
        (Estado.CONFIRMANDO, Estado.ELIGIENDO_HORA),
        (Estado.GESTIONANDO_CITA, Estado.FINALIZADO),
        (Estado.FINALIZADO, Estado.INICIO),
    }
    assert all(destino in TRANSICIONES[origen] for origen, destino in esperadas)


def test_contexto_ida_y_vuelta_por_json() -> None:
    ctx = Contexto(estado=Estado.ELIGIENDO_HORA, servicio_id="retiro", horas_ofrecidas=["a", "b"])
    copia = Contexto.desde(ctx.estado, ctx.a_json())
    assert copia == ctx


def test_limpiar_seleccion() -> None:
    ctx = Contexto(servicio_id="retiro", fecha="2026-11-03", reagendando=4, hora="x")
    ctx.limpiar_seleccion()
    assert ctx == Contexto()


def test_conversacion_nueva_empieza_en_inicio_sin_aviso(engine: Engine, cfg: Config) -> None:
    with unidad_de_trabajo(engine) as s:
        sesion = cargar_sesion(s, TEL, AHORA, 30)
        guardar_sesion(s, TEL, sesion, AHORA)
    assert sesion.contexto.estado == Estado.INICIO
    assert sesion.aviso_aceptado is False


def test_persiste_estado_contexto_y_aviso(engine: Engine) -> None:
    with unidad_de_trabajo(engine) as s:
        sesion = cargar_sesion(s, TEL, AHORA, 30)
        sesion.contexto.estado = Estado.ELIGIENDO_DIA
        sesion.contexto.servicio_id = "retiro"
        sesion.aviso_aceptado = True
        guardar_sesion(s, TEL, sesion, AHORA)
    with unidad_de_trabajo(engine) as s:
        otra = cargar_sesion(s, TEL, AHORA + timedelta(minutes=5), 30)
    assert (otra.contexto.estado, otra.contexto.servicio_id) == (Estado.ELIGIENDO_DIA, "retiro")
    assert otra.aviso_aceptado is True


def test_inactividad_de_30_minutos_reinicia_a_inicio_y_conserva_el_aviso(engine: Engine) -> None:
    with unidad_de_trabajo(engine) as s:
        sesion = cargar_sesion(s, TEL, AHORA, 30)
        sesion.contexto.estado = Estado.ELIGIENDO_HORA
        sesion.contexto.servicio_id = "retiro"
        sesion.aviso_aceptado = True
        guardar_sesion(s, TEL, sesion, AHORA)
    with unidad_de_trabajo(engine) as s:
        justo = cargar_sesion(s, TEL, AHORA + timedelta(minutes=30), 30)
        tarde = cargar_sesion(s, TEL, AHORA + timedelta(minutes=31), 30)
    assert justo.contexto.estado == Estado.ELIGIENDO_HORA  # 30 min exactos: aún activa
    assert tarde.contexto == Contexto()
    assert tarde.aviso_aceptado is True
