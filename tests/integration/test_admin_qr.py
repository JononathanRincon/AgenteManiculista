"""QR de vinculación protegido por token y estado de WhatsApp en /health (EARS-WA-12, 14)."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from app.config import Config, Settings
from app.main import crear_app
from app.mensajes import configurar
from app.reloj import RelojFalso
from app.webhook import EventoEstado
from app.whatsapp import ClienteSidecar
from fastapi.testclient import TestClient
from pydantic import SecretStr

from tests.integration.conftest import AHORA

ADMIN = "token-administrador-de-prueba-0123456789"  # noqa: S105 - valor de prueba
TOKEN_SIDECAR = "token-del-sidecar-de-prueba-0123456789"  # noqa: S105 - valor de prueba
QR = "▄▄▄▄ QR DE PRUEBA ▄▄▄▄"


def _app(tmp_path: Path, cfg: Config, **extra: Any) -> TestClient:
    configurar(None)
    ajustes = Settings(  # type: ignore[call-arg]
        _env_file=None,
        admin_token=SecretStr(ADMIN),
        database_url=f"sqlite:///{tmp_path / 'a.db'}",
        **extra,
    )
    return TestClient(crear_app(ajustes, cfg, RelojFalso(AHORA)))


def _con_sidecar(app: TestClient, respuesta: httpx.Response | Exception) -> None:
    def manejar(_: httpx.Request) -> httpx.Response:
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta

    http = httpx.Client(base_url="http://sidecar:3000", transport=httpx.MockTransport(manejar))
    app.app.state.sidecar = ClienteSidecar("http://sidecar:3000", "t", http)  # type: ignore[attr-defined]


@pytest.fixture
def cliente(tmp_path: Path, cfg: Config) -> Iterator[TestClient]:
    with _app(
        tmp_path, cfg, sidecar_url="http://sidecar:3000", sidecar_token=SecretStr(TOKEN_SIDECAR)
    ) as c:
        yield c


def test_sin_el_token_correcto_no_se_entrega_el_qr(cliente: TestClient) -> None:
    _con_sidecar(cliente, httpx.Response(200, text=QR))
    assert cliente.get("/admin/whatsapp/qr").status_code == 403
    assert cliente.get("/admin/whatsapp/qr", headers={"X-Admin-Token": "otro"}).status_code == 403
    assert cliente.get("/admin/whatsapp/qr", headers={"X-Admin-Token": ""}).status_code == 403


def test_con_el_token_correcto_entrega_el_qr_sin_cache(cliente: TestClient) -> None:
    _con_sidecar(cliente, httpx.Response(200, text=QR))
    r = cliente.get("/admin/whatsapp/qr", headers={"X-Admin-Token": ADMIN})
    assert r.status_code == 200
    assert r.text == QR
    assert "text/plain" in r.headers["content-type"]
    assert r.headers["cache-control"] == "no-store"


def test_si_no_hay_qr_pendiente_responde_404(cliente: TestClient) -> None:
    _con_sidecar(cliente, httpx.Response(404, json={"error": "sin_qr"}))
    assert cliente.get("/admin/whatsapp/qr", headers={"X-Admin-Token": ADMIN}).status_code == 404


@pytest.mark.parametrize(
    "falla", [httpx.ConnectError("sin red"), httpx.Response(500), httpx.Response(401)]
)
def test_si_el_sidecar_falla_responde_503(
    cliente: TestClient, falla: httpx.Response | Exception
) -> None:
    _con_sidecar(cliente, falla)
    r = cliente.get("/admin/whatsapp/qr", headers={"X-Admin-Token": ADMIN})
    assert r.status_code == 503


def test_sin_sidecar_configurado_responde_503(tmp_path: Path, cfg: Config) -> None:
    with _app(tmp_path, cfg) as c:
        assert c.get("/admin/whatsapp/qr", headers={"X-Admin-Token": ADMIN}).status_code == 503


def test_frena_los_intentos_de_adivinar_el_token(cliente: TestClient) -> None:
    codigos = [
        cliente.get("/admin/whatsapp/qr", headers={"X-Admin-Token": f"x{i}"}).status_code
        for i in range(32)
    ]
    assert codigos[:30] == [403] * 30
    assert codigos[30:] == [429, 429]


def test_la_ruta_no_existe_sin_token_administrativo(tmp_path: Path, cfg: Config) -> None:
    configurar(None)
    ajustes = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'n.db'}")  # type: ignore[call-arg]
    with TestClient(crear_app(ajustes, cfg)) as c:
        assert c.get("/admin/whatsapp/qr", headers={"X-Admin-Token": ADMIN}).status_code == 404


def test_health_muestra_el_estado_de_whatsapp(tmp_path: Path, cfg: Config) -> None:
    from app.webhook import Pasarela

    capturada: dict[str, Pasarela] = {}

    def fabrica(engine: Any) -> Pasarela:
        capturada["p"] = Pasarela.__new__(Pasarela)
        capturada["p"].estado_conexion = "desconocido"
        capturada["p"].estado_desde = None

        async def esperar() -> None:
            return None

        capturada["p"].esperar_tareas = esperar  # type: ignore[method-assign]
        return capturada["p"]

    configurar(None)
    ajustes = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'h.db'}")  # type: ignore[call-arg]
    with TestClient(crear_app(ajustes, cfg, RelojFalso(AHORA), fabrica_pasarela=fabrica)) as c:
        assert c.get("/health").json()["whatsapp_estado"] == "desconocido"
        capturada["p"].estado_conexion = "loggedOut"
        capturada["p"].estado_desde = AHORA
        datos = c.get("/health").json()
        assert datos["whatsapp_estado"] == "loggedOut"  # la dueña debe volver a vincular
        assert datos["whatsapp_desde"] == AHORA.isoformat()


def test_health_sin_whatsapp_configurado(tmp_path: Path, cfg: Config) -> None:
    configurar(None)
    ajustes = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'z.db'}")  # type: ignore[call-arg]
    with TestClient(crear_app(ajustes, cfg)) as c:
        assert c.get("/health").json()["whatsapp_estado"] == "no_configurado"
    assert EventoEstado is not None
