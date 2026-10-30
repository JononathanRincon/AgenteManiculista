"""`/health` expone las métricas operativas sin datos personales (EARS-OBS-01)."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from app.config import Config, Settings
from app.main import crear_app
from app.mensajes import configurar
from app.reloj import RelojFalso
from fastapi.testclient import TestClient

from tests.integration.conftest import AHORA

CLAVES = {
    "estado",
    "cola_profundidad",
    "cache_tasa_acierto",
    "mensajes_sin_llm_pct",
    "trabajos_fallidos",
    "modo_degradado",
    "canal_expira_en",
    "cola_pausada_hasta",
}


@pytest.fixture
def cliente(tmp_path: Path, cfg: Config) -> Iterator[TestClient]:
    configurar(None)
    ajustes = Settings(  # type: ignore[call-arg]
        _env_file=None, simulador=True, database_url=f"sqlite:///{tmp_path / 'h.db'}"
    )
    with TestClient(crear_app(ajustes, cfg, RelojFalso(AHORA))) as c:
        yield c


def test_health_en_reposo(cliente: TestClient) -> None:
    r = cliente.get("/health")
    assert r.status_code == 200
    datos = r.json()
    assert set(datos) == CLAVES
    assert datos["estado"] == "ok"
    assert (datos["cola_profundidad"], datos["trabajos_fallidos"]) == (0, 0)
    assert datos["cache_tasa_acierto"] == 0.0
    assert datos["mensajes_sin_llm_pct"] == 0.0
    assert datos["modo_degradado"] is True
    assert datos["canal_expira_en"] is None
    assert datos["cola_pausada_hasta"] is None


def test_health_refleja_la_actividad_y_no_contiene_datos_personales(cliente: TestClient) -> None:
    telefono = "573004445566"
    for boton in ("aviso:si", "menu:agendar", "srv:retiro"):
        cliente.post("/demo/api/mensaje", json={"telefono_demo": telefono, "boton_id": boton})
    dias = cliente.post(
        "/demo/api/mensaje", json={"telefono_demo": telefono, "boton_id": "srv:retiro"}
    ).json()
    cuerpo = cliente.get("/health").text
    datos = cliente.get("/health").json()
    assert datos["mensajes_sin_llm_pct"] == 100.0  # ningún mensaje necesitó el LLM
    assert telefono not in cuerpo
    assert "María" not in cuerpo and "Calle" not in cuerpo
    assert dias is not None


def test_health_cuenta_la_cola_y_los_fallidos(cliente: TestClient) -> None:
    demo = cliente.app.state.demo  # type: ignore[attr-defined]
    cliente.post("/demo/api/carrera", json={})
    assert cliente.get("/health").json()["cola_profundidad"] == 1
    demo.worker.procesar_uno()
    datos = cliente.get("/health").json()
    assert datos["cola_profundidad"] == 0
    assert datos["cache_tasa_acierto"] > 0


def test_health_incluye_el_id_de_correlacion(cliente: TestClient) -> None:
    primero = cliente.get("/health").headers["x-correlation-id"]
    segundo = cliente.get("/health").headers["x-correlation-id"]
    assert primero != segundo


def test_health_antes_de_iniciar_responde_503(tmp_path: Path, cfg: Config) -> None:
    ajustes = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'x.db'}")  # type: ignore[call-arg]
    cliente = TestClient(crear_app(ajustes, cfg))  # sin lifespan: el motor aún no existe
    r = cliente.get("/health")
    assert r.status_code == 503
    assert r.json() == {"estado": "iniciando"}
