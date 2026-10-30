"""Modo degradado: sin push el espejo se refresca solo y queda registrado (EARS-CAC-04)."""

import asyncio
import logging
from datetime import timedelta

import pytest
from app.cache import CacheDisponibilidad
from app.canal import GestorCanal, mantener
from app.config import Config
from app.puertos import CanalCalendar, ErrorTransitorio
from app.reloj import RelojFalso
from app.salud import construir_salud
from app.simulador.calendario_falso import CalendarioFalso
from sqlalchemy import Engine

from tests.integration.conftest import AHORA, local


class CanalesOk:
    def __init__(self, reloj: RelojFalso) -> None:
        self.reloj = reloj

    def registrar_canal(
        self, calendario_id: str, *, canal_id: str, url: str, token: str, ttl_s: int
    ) -> CanalCalendar:
        return CanalCalendar(canal_id, "r", self.reloj.ahora() + timedelta(seconds=ttl_s))

    def detener_canal(self, canal_id: str, resource_id: str) -> None:
        return None


def _montaje(
    engine: Engine, cfg: Config, url: str | None
) -> tuple[GestorCanal, CacheDisponibilidad, CalendarioFalso, RelojFalso]:
    reloj = RelojFalso(AHORA)
    calendario = CalendarioFalso()
    cache = CacheDisponibilidad(engine, calendario, cfg.negocio, reloj)
    gestor = GestorCanal(
        engine,
        CanalesOk(reloj),
        reloj,
        cfg.negocio.calendario_id,
        url=url,
        token="token-del-canal-de-prueba-0123456789",  # noqa: S106 - valor de prueba
    )
    return gestor, cache, calendario, reloj


async def _ciclos(gestor: GestorCanal, cache: CacheDisponibilidad, cal_id: str, n: int) -> None:
    parar = asyncio.Event()
    tarea = asyncio.create_task(mantener(gestor, cache, cal_id, parar, intervalo_s=0.01))
    await asyncio.sleep(0.01 * n + 0.1)
    parar.set()
    await tarea


async def test_sin_url_publica_refresca_el_espejo_y_registra_modo_degradado(
    engine: Engine, cfg: Config, caplog: pytest.LogCaptureFixture
) -> None:
    gestor, cache, calendario, _ = _montaje(engine, cfg, url=None)
    caplog.set_level(logging.WARNING, logger="canal")
    await _ciclos(gestor, cache, cfg.negocio.calendario_id, 4)
    assert calendario.llamadas["listar_eventos"] >= 2  # refresca en cada ciclo
    registros = [r for r in caplog.records if r.getMessage() == "modo_degradado"]
    assert len(registros) == 1  # se registra al entrar, no en cada ciclo
    assert construir_salud(engine, cfg)["modo_degradado"] is True


async def test_con_canal_vigente_no_refresca_a_mano(engine: Engine, cfg: Config) -> None:
    gestor, cache, calendario, _ = _montaje(engine, cfg, url="https://agenda.ejemplo.co/w")
    await _ciclos(gestor, cache, cfg.negocio.calendario_id, 3)
    assert calendario.llamadas["listar_eventos"] == 0
    salud = construir_salud(engine, cfg)
    assert salud["modo_degradado"] is False
    assert salud["canal_expira_en"] is not None


async def test_un_ciclo_fallido_no_mata_la_tarea(
    engine: Engine, cfg: Config, caplog: pytest.LogCaptureFixture
) -> None:
    gestor, cache, calendario, _ = _montaje(engine, cfg, url=None)
    calendario.programar_fallo("listar_eventos", ErrorTransitorio("x"), veces=1)
    caplog.set_level(logging.ERROR, logger="canal")
    await _ciclos(gestor, cache, cfg.negocio.calendario_id, 4)
    assert any(r.getMessage() == "mantenimiento_calendario_fallido" for r in caplog.records)
    assert calendario.llamadas["listar_eventos"] >= 2  # siguió intentando


def test_resincronizar_trae_lo_que_agrego_la_duena_a_mano(engine: Engine, cfg: Config) -> None:
    _, cache, calendario, _ = _montaje(engine, cfg, url=None)
    cal_id = cfg.negocio.calendario_id
    dia = local(3, 9).astimezone(cfg.negocio.zona).date()
    assert cache.ocupados_del_dia(cal_id, dia) == set()  # queda en caché
    calendario.agregar_evento_manual(local(3, 9), local(3, 10))
    assert cache.ocupados_del_dia(cal_id, dia) == set()  # la caché aún vale
    cache.resincronizar(cal_id)
    assert cache.ocupados_del_dia(cal_id, dia) != set()
