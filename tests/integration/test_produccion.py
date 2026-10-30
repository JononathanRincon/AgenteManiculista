"""Cableado de producción: el grafo completo arranca, atiende los webhooks y se apaga limpio."""

import time
from pathlib import Path

import pytest
from app.calendario import CalendarioGoogle
from app.config import Config, ConfiguracionInvalida, Settings
from app.main import crear_app
from app.mensajes import configurar
from app.produccion import Produccion, construir_produccion, exigir_ajustes
from app.reloj import RelojFalso
from app.simulador.calendario_falso import CalendarioFalso
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine

from tests.integration.conftest import AHORA
from tests.integration.test_calendario_google import _json_de_cuenta_de_servicio
from tests.integration.test_push import CanalesFalsos
from tests.integration.test_webhook_whatsapp import SECRETO, _cabeceras

CANAL = "token-del-canal-de-prueba-0123456789"  # noqa: S105 - valor de prueba
SIDECAR = "token-del-sidecar-de-prueba-0123456789"  # noqa: S105 - valor de prueba


class SidecarFalso:
    def leer(self, telefono: str, id_mensaje: str) -> None: ...

    def presencia(self, telefono: str, estado: str) -> None: ...

    def enviar(self, telefono: str, texto: str) -> str:
        return "id"


def _ajustes(tmp_path: Path, **extra: object) -> Settings:
    base: dict[str, object] = {
        "entorno": "produccion",
        "database_url": f"sqlite:///{tmp_path / 'p.db'}",
        "google_service_account_json": SecretStr("{}"),
        "sidecar_url": "http://sidecar:3000",
        "sidecar_token": SecretStr(SIDECAR),
        "webhook_secret": SecretStr(SECRETO),
        "calendar_webhook_url": "https://agenda.ejemplo.co/webhook/calendar",
        "calendar_channel_token": SecretStr(CANAL),
    }
    return Settings(_env_file=None, **(base | extra))  # type: ignore[arg-type, call-arg]


def _fabrica(calendario: CalendarioFalso, canales: CanalesFalsos):  # type: ignore[no-untyped-def]
    def construir(ajustes: Settings, config: Config, engine: Engine, reloj: object) -> Produccion:
        canales.reloj = reloj  # type: ignore[assignment]
        return construir_produccion(
            ajustes,
            config,
            engine,
            reloj,  # type: ignore[arg-type]
            calendario=calendario,
            canales=canales,
            sidecar=SidecarFalso(),
        )

    return construir


def test_exigir_ajustes_nombra_lo_que_falta(tmp_path: Path) -> None:
    vacios = Settings(_env_file=None, entorno="produccion", database_url="sqlite://")  # type: ignore[call-arg]
    with pytest.raises(ConfiguracionInvalida) as info:
        exigir_ajustes(vacios)
    for nombre in ("GOOGLE_SERVICE_ACCOUNT_JSON", "SIDECAR_URL", "SIDECAR_TOKEN", "WEBHOOK_SECRET"):
        assert nombre in str(info.value)
    exigir_ajustes(_ajustes(tmp_path))


def test_el_grafo_real_usa_calendar_de_google_tambien_para_los_canales(
    tmp_path: Path, cfg: Config, engine: Engine
) -> None:
    ajustes = _ajustes(
        tmp_path, google_service_account_json=SecretStr(_json_de_cuenta_de_servicio())
    )
    prod = construir_produccion(ajustes, cfg, engine, RelojFalso(AHORA))
    assert isinstance(prod.calendario, CalendarioGoogle)
    assert prod.calendario_id == cfg.negocio.calendario_id


def test_un_calendario_ajeno_exige_canales_explicitos(
    tmp_path: Path, cfg: Config, engine: Engine
) -> None:
    with pytest.raises(ConfiguracionInvalida):
        construir_produccion(
            _ajustes(tmp_path), cfg, engine, calendario=CalendarioFalso(), sidecar=SidecarFalso()
        )


def test_en_produccion_arranca_atiende_los_webhooks_y_se_apaga_limpio(
    tmp_path: Path, cfg: Config
) -> None:
    configurar(None)
    calendario, canales = CalendarioFalso(), CanalesFalsos()
    app = crear_app(
        _ajustes(tmp_path), cfg, RelojFalso(AHORA), fabrica_produccion=_fabrica(calendario, canales)
    )
    with TestClient(app) as c:
        cuerpo = b'{"tipo": "estado", "conexion": "open"}'
        r = c.post("/webhook/whatsapp", content=cuerpo, headers=_cabeceras(cuerpo))
        assert r.status_code == 200  # antes del cableado respondía 500 sin pasarela
        time.sleep(0.3)
        assert c.get("/health").json()["whatsapp_estado"] == "open"
        antes = calendario.llamadas["listar_eventos"]
        r = c.post(
            "/webhook/calendar",
            headers={"X-Goog-Channel-Token": CANAL, "X-Goog-Resource-State": "exists"},
        )
        assert r.status_code == 200
        assert calendario.llamadas["listar_eventos"] > antes  # resincronizó tras el push
        assert c.post("/webhook/calendar", headers={"X-Goog-Channel-Token": "x"}).status_code == 403
        assert canales.registrados  # el gestor registró el canal al arrancar
        assert c.get("/health").json()["modo_degradado"] is False


def test_en_produccion_sin_credenciales_no_arranca(tmp_path: Path, cfg: Config) -> None:
    configurar(None)
    ajustes = Settings(  # type: ignore[call-arg]
        _env_file=None, entorno="produccion", database_url=f"sqlite:///{tmp_path / 'x.db'}"
    )
    with pytest.raises(ConfiguracionInvalida), TestClient(crear_app(ajustes, cfg)):
        pass
