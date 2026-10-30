"""Rutas de la demo, SSE y reglas de arranque (EARS-SIM-01..05)."""

import asyncio
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from app.config import Config, Settings
from app.limite_tasa import LimiteTasa
from app.main import MasDeUnProceso, crear_app, verificar_un_solo_proceso
from app.mensajes import configurar
from app.reloj import RelojFalso
from app.simulador.rutas import flujo_eventos
from fastapi.testclient import TestClient

from tests.integration.conftest import AHORA

TEL = "573001112233"


def _ajustes(tmp_path: Path, *, simulador: bool, **extra: Any) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        simulador=simulador,
        database_url=f"sqlite:///{tmp_path / 'demo.db'}",
        **extra,
    )


@pytest.fixture
def cliente(tmp_path: Path, cfg: Config) -> Iterator[TestClient]:
    configurar(None)
    app = crear_app(_ajustes(tmp_path, simulador=True), cfg, RelojFalso(AHORA))
    with TestClient(app) as c:
        yield c


def test_rutas_no_existen_sin_simulador(tmp_path: Path, cfg: Config) -> None:
    app = crear_app(_ajustes(tmp_path, simulador=False), cfg)
    with TestClient(app) as c:
        assert c.get("/health").json() == {"estado": "ok"}
        assert c.get("/demo").status_code == 404
        assert c.get("/demo/api/calendario").status_code == 404
        cuerpo = {"telefono_demo": TEL, "texto": "hola"}
        assert c.post("/demo/api/mensaje", json=cuerpo).status_code == 404
        assert c.post("/demo/api/reiniciar").status_code == 404
        assert c.get("/openapi.json").status_code == 404


def test_rutas_existen_con_simulador(cliente: TestClient) -> None:
    pagina = cliente.get("/demo")
    assert pagina.status_code == 200
    assert "text/html" in pagina.headers["content-type"]
    assert cliente.get("/demo/api/calendario").json() == {"eventos": []}


def test_rutas_cabeceras_de_seguridad_solo_en_demo(cliente: TestClient) -> None:
    cabeceras = cliente.get("/demo").headers
    assert "default-src 'self'" in cabeceras["content-security-policy"]
    assert cabeceras["x-content-type-options"] == "nosniff"
    assert "content-security-policy" not in cliente.get("/health").headers


def test_rutas_mensaje_y_chat(cliente: TestClient) -> None:
    r = cliente.post("/demo/api/mensaje", json={"telefono_demo": TEL, "texto": "hola"})
    assert r.status_code == 200
    respuestas = r.json()["respuestas"]
    assert respuestas[0]["tipo"] == "botones"
    assert [o["id"] for o in respuestas[0]["opciones"]] == ["aviso:si", "aviso:no"]
    chat = cliente.get(f"/demo/api/chat/{TEL}").json()["mensajes"]
    assert [m["de"] for m in chat] == ["clienta", "bot"]
    assert cliente.get("/demo/api/chat/abc").status_code == 422


@pytest.mark.parametrize(
    "cuerpo",
    [
        {"telefono_demo": "abc", "texto": "hola"},
        {"telefono_demo": TEL, "texto": "x" * 501},
        {"telefono_demo": TEL, "extra": 1},
        {"texto": "hola"},
    ],
)
def test_rutas_validan_la_entrada(cliente: TestClient, cuerpo: dict[str, object]) -> None:
    assert cliente.post("/demo/api/mensaje", json=cuerpo).status_code == 422


def test_rutas_reiniciar_devuelve_204_y_limpia(cliente: TestClient) -> None:
    cliente.post("/demo/api/mensaje", json={"telefono_demo": TEL, "texto": "hola"})
    assert cliente.post("/demo/api/reiniciar").status_code == 204
    assert cliente.get(f"/demo/api/chat/{TEL}").json() == {"mensajes": []}


def test_rutas_limite_de_tasa(cliente: TestClient) -> None:
    cliente.app.state.limite_demo = LimiteTasa(maximo=3, ventana_s=60)  # type: ignore[attr-defined]
    codigos = [cliente.get("/demo/api/calendario").status_code for _ in range(5)]
    assert codigos == [200, 200, 200, 429, 429]


def test_rutas_un_solo_proceso(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cfg: Config
) -> None:
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    with pytest.raises(MasDeUnProceso):
        verificar_un_solo_proceso()
    app = crear_app(_ajustes(tmp_path, simulador=False), cfg)
    with pytest.raises(MasDeUnProceso), TestClient(app):
        pass
    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    verificar_un_solo_proceso()


async def test_rutas_flujo_de_eventos_emite_cambio_y_latido(cliente: TestClient) -> None:
    demo = cliente.app.state.demo  # type: ignore[attr-defined]
    flujo = flujo_eventos(demo, latido_s=0.05)
    assert (await anext(flujo)).startswith("retry")
    assert (await anext(flujo)).startswith(": latido")
    hilo = threading.Timer(0.01, demo._notificar)  # noqa: SLF001
    hilo.start()
    mensaje = await asyncio.wait_for(anext(flujo), timeout=2)
    while mensaje.startswith(": latido"):
        mensaje = await asyncio.wait_for(anext(flujo), timeout=2)
    assert mensaje.startswith("event: cambio")
    await flujo.aclose()
    assert demo._oyentes == []  # noqa: SLF001 - el flujo se desuscribe al cerrarse
