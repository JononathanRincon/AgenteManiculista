"""Webhook del sidecar: firma HMAC, filtros de chat, dedup, pausa humana y debounce."""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from app.agente import Agente
from app.cache import CacheDisponibilidad
from app.config import AntiBan, Config, Settings
from app.control_humano import ControlHumano
from app.conversacion import Saliente
from app.main import crear_app
from app.mensajes import configurar
from app.modelos import Conversacion, EnvioReciente, unidad_de_trabajo
from app.reloj import RelojFalso
from app.simulador.calendario_falso import CalendarioFalso
from app.webhook import (
    EventoEstado,
    EventoMensaje,
    EventoPropio,
    Pasarela,
    telefono_de_jid,
    verificar_firma,
)
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, func, select

from tests.integration.conftest import AHORA

SECRETO = "secreto-de-prueba"
TEL = "573001112233"
JID = f"{TEL}@s.whatsapp.net"


class EmisorFalso:
    def __init__(self, demora_s: float = 0.0) -> None:
        self.respuestas: list[tuple[str, list[Saliente], str | None]] = []
        self.emitidos: set[str] = set()
        self.demora_s = demora_s

    def responder(self, telefono: str, salidas: list[Saliente], id_entrante: str | None) -> None:
        if self.demora_s:
            time.sleep(self.demora_s)
        self.respuestas.append((telefono, salidas, id_entrante))

    def fue_emitido(self, id_mensaje: str) -> bool:
        return id_mensaje in self.emitidos


def _config(cfg: Config, **anti: Any) -> Config:
    anti.setdefault("debounce_segundos", 0)
    negocio = cfg.negocio.model_copy(update={"anti_ban": AntiBan(**anti)})
    return cfg.model_copy(update={"negocio": negocio})


def _pasarela(
    engine: Engine, cfg: Config, emisor: EmisorFalso, reloj: RelojFalso, **anti: Any
) -> Pasarela:
    config = _config(cfg, **anti)
    calendario = CalendarioFalso()
    cache = CacheDisponibilidad(engine, calendario, config.negocio, reloj)
    agente = Agente(engine, config, cache, reloj)
    return Pasarela(engine, config, agente, ControlHumano(engine, config.negocio), emisor, reloj)


def _mensaje(id_: str, texto: str | None = "hola", jid: str = JID, **extra: Any) -> EventoMensaje:
    return EventoMensaje(
        tipo="mensaje", id=id_, jid=jid, nombre="María Pérez", texto=texto, **extra
    )


@pytest.fixture(autouse=True)
def _catalogo() -> None:
    configurar(None)


# ------------------------------------------------------------------ firma
def _firma(cuerpo: bytes, secreto: str = SECRETO) -> str:
    return "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()


def test_verificar_firma() -> None:
    cuerpo = b'{"tipo": "estado", "conexion": "open"}'
    assert verificar_firma(cuerpo, _firma(cuerpo), SECRETO) is True
    assert verificar_firma(cuerpo, _firma(cuerpo).upper().replace("SHA256=", "sha256="), SECRETO)
    assert verificar_firma(cuerpo, _firma(cuerpo, "otro"), SECRETO) is False
    assert verificar_firma(cuerpo + b" ", _firma(cuerpo), SECRETO) is False
    assert verificar_firma(cuerpo, None, SECRETO) is False
    assert verificar_firma(cuerpo, "", SECRETO) is False
    assert verificar_firma(cuerpo, _firma(cuerpo).removeprefix("sha256="), SECRETO) is False
    assert verificar_firma(cuerpo, _firma(cuerpo, ""), "") is False  # sin secreto nunca se acepta


@pytest.mark.parametrize(
    ("jid", "esperado"),
    [
        (JID, TEL),
        (f"{TEL}:12@s.whatsapp.net", TEL),
        ("120363025@g.us", None),  # grupo
        ("status@broadcast", None),  # estados / difusión
        ("120363@newsletter", None),  # canal
        ("999999999999@lid", None),
        ("abc@s.whatsapp.net", None),
        ("123@s.whatsapp.net", None),  # demasiado corto
        ("", None),
    ],
)
def test_solo_los_chats_individuales_tienen_telefono(jid: str, esperado: str | None) -> None:
    assert telefono_de_jid(jid) == esperado


@pytest.fixture
def cliente(tmp_path: Path, cfg: Config) -> Iterator[tuple[TestClient, EmisorFalso, Pasarela]]:
    emisor = EmisorFalso(demora_s=1.0)
    reloj = RelojFalso(AHORA)
    holder: dict[str, Pasarela] = {}

    def fabrica(engine: Engine) -> Pasarela:
        holder["p"] = _pasarela(engine, cfg, emisor, reloj)
        return holder["p"]

    ajustes = Settings(  # type: ignore[call-arg]
        _env_file=None,
        webhook_secret=SecretStr(SECRETO),
        database_url=f"sqlite:///{tmp_path / 'w.db'}",
    )
    app = crear_app(ajustes, _config(cfg), reloj, fabrica_pasarela=fabrica)
    with TestClient(app) as c:
        yield c, emisor, holder["p"]


def _post(c: TestClient, cuerpo: dict[str, Any], firma: str | None = "auto") -> Any:
    crudo = json.dumps(cuerpo).encode()
    cabeceras = {} if firma is None else {"X-Firma": _firma(crudo) if firma == "auto" else firma}
    return c.post("/webhook/whatsapp", content=crudo, headers=cabeceras)


def test_firma_invalida_o_ausente_da_403_y_no_procesa(
    cliente: tuple[TestClient, EmisorFalso, Pasarela],
) -> None:
    c, emisor, _ = cliente
    evento = {"tipo": "mensaje", "id": "A1", "jid": JID, "texto": "hola"}
    assert _post(c, evento, firma=None).status_code == 403
    assert _post(c, evento, firma="sha256=" + "0" * 64).status_code == 403
    assert _post(c, evento, firma="basura").status_code == 403
    assert emisor.respuestas == []


def test_payload_invalido_con_firma_valida_da_422(
    cliente: tuple[TestClient, EmisorFalso, Pasarela],
) -> None:
    c, _, _ = cliente
    assert _post(c, {"tipo": "mensaje", "id": "", "jid": JID}).status_code == 422
    assert _post(c, {"tipo": "desconocido"}).status_code == 422
    assert _post(c, {"tipo": "mensaje", "id": "x", "jid": JID, "campo_extra": 1}).status_code == 422
    assert (
        c.post(
            "/webhook/whatsapp", content=b"no es json", headers={"X-Firma": _firma(b"no es json")}
        ).status_code
        == 422
    )


def test_con_procesamiento_lento_el_200_llega_en_menos_de_un_segundo(
    cliente: tuple[TestClient, EmisorFalso, Pasarela],
) -> None:
    c, emisor, _ = cliente  # el emisor tarda 1 s en responder
    inicio = time.perf_counter()
    r = _post(c, {"tipo": "mensaje", "id": "L1", "jid": JID, "texto": "hola"})
    transcurrido = time.perf_counter() - inicio
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert transcurrido < 0.8
    for _ in range(100):
        if emisor.respuestas:
            break
        time.sleep(0.05)
    assert len(emisor.respuestas) == 1  # la respuesta salió después, en segundo plano


# ------------------------------------------------------------------ canalización directa
@pytest.fixture
def banco(engine: Engine, cfg: Config) -> tuple[Pasarela, EmisorFalso, RelojFalso, Engine]:
    emisor, reloj = EmisorFalso(), RelojFalso(AHORA)
    return _pasarela(engine, cfg, emisor, reloj), emisor, reloj, engine


@pytest.mark.parametrize(
    "jid", ["120363025@g.us", "status@broadcast", "120363@newsletter", "999999999999@lid"]
)
async def test_grupos_difusion_y_canales_se_ignoran_por_completo(
    banco: tuple[Pasarela, EmisorFalso, RelojFalso, Engine], jid: str
) -> None:
    pasarela, emisor, _, engine = banco
    assert await pasarela.manejar(_mensaje("G1", jid=jid)) == "ignorado_chat"
    assert emisor.respuestas == []
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert s.scalar(select(func.count()).select_from(Conversacion)) == 0  # ni se registra


async def test_el_mismo_mensaje_dos_veces_produce_un_solo_efecto(
    banco: tuple[Pasarela, EmisorFalso, RelojFalso, Engine],
) -> None:
    pasarela, emisor, _, _ = banco
    assert await pasarela.manejar(_mensaje("D1")) == "aceptado"
    assert await pasarela.manejar(_mensaje("D1")) == "duplicado"
    assert len(emisor.respuestas) == 1


async def test_primer_mensaje_responde_con_el_aviso_y_el_numero_lo_acepta(
    banco: tuple[Pasarela, EmisorFalso, RelojFalso, Engine],
) -> None:
    pasarela, emisor, _, _ = banco
    await pasarela.manejar(_mensaje("N1", "hola"))
    telefono, salidas, id_entrante = emisor.respuestas[0]
    assert (telefono, id_entrante) == (TEL, "N1")
    assert "Ley 1581" in salidas[0].cuerpo
    await pasarela.manejar(_mensaje("N2", "1"))  # "1" = Acepto
    assert "Hola María" in emisor.respuestas[1][1][0].cuerpo
    await pasarela.manejar(_mensaje("N3", "99"))  # número fuera de rango: texto libre
    assert "No te entendí" in emisor.respuestas[2][1][0].cuerpo


async def test_un_mensaje_no_textual_recibe_el_menu(
    banco: tuple[Pasarela, EmisorFalso, RelojFalso, Engine],
) -> None:
    pasarela, emisor, _, _ = banco
    await pasarela.manejar(_mensaje("T1", "hola"))
    await pasarela.manejar(_mensaje("T2", "1"))
    await pasarela.manejar(_mensaje("T3", None, tipo_mensaje="audio"))
    assert "solo entiendo mensajes de texto" in emisor.respuestas[2][1][0].cuerpo


async def test_la_marca_de_tiempo_del_sidecar_se_respeta(
    banco: tuple[Pasarela, EmisorFalso, RelojFalso, Engine],
) -> None:
    pasarela, emisor, _, _ = banco
    assert await pasarela.manejar(_mensaje("TS1", ts=AHORA.timestamp())) == "aceptado"
    assert len(emisor.respuestas) == 1


async def test_si_la_dueña_responde_a_mano_el_bot_se_pausa_dos_horas(
    banco: tuple[Pasarela, EmisorFalso, RelojFalso, Engine],
) -> None:
    pasarela, emisor, reloj, _ = banco
    await pasarela.manejar(_mensaje("H1", "hola"))
    assert (
        await pasarela.manejar(EventoPropio(tipo="propio", id="OWNER-1", jid=JID))
        == "pausado_por_humano"
    )
    assert await pasarela.manejar(_mensaje("H2", "1")) == "pausado"
    assert len(emisor.respuestas) == 1  # no le responde mientras la dueña atiende
    reloj.avanzar(minutes=119)
    assert await pasarela.manejar(_mensaje("H3", "1")) == "pausado"
    reloj.avanzar(minutes=2)
    assert await pasarela.manejar(_mensaje("H4", "1")) == "aceptado"
    assert len(emisor.respuestas) == 2


async def test_lo_que_emitio_el_propio_bot_no_activa_la_pausa(
    banco: tuple[Pasarela, EmisorFalso, RelojFalso, Engine],
) -> None:
    pasarela, emisor, _, _ = banco
    await pasarela.manejar(_mensaje("E1", "hola"))
    emisor.emitidos.add("MIO-1")
    assert (
        await pasarela.manejar(EventoPropio(tipo="propio", id="MIO-1", jid=JID))
        == "emitido_por_el_bot"
    )
    assert await pasarela.manejar(_mensaje("E2", "1")) == "aceptado"


async def test_los_mensajes_propios_de_chats_ajenos_al_bot_se_ignoran(
    banco: tuple[Pasarela, EmisorFalso, RelojFalso, Engine],
) -> None:
    pasarela, _, _, engine = banco
    # la dueña chatea con alguien que nunca escribió al bot: no se guarda nada
    assert (
        await pasarela.manejar(
            EventoPropio(tipo="propio", id="P1", jid="573009990000@s.whatsapp.net")
        )
        == "sin_conversacion"
    )
    assert (
        await pasarela.manejar(EventoPropio(tipo="propio", id="P2", jid="120363@g.us"))
        == "ignorado_chat"
    )
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert s.scalar(select(func.count()).select_from(Conversacion)) == 0


async def test_tope_de_respuestas_por_telefono(engine: Engine, cfg: Config) -> None:
    emisor, reloj = EmisorFalso(), RelojFalso(AHORA)
    pasarela = _pasarela(
        engine, cfg, emisor, reloj, max_mensajes_por_telefono=2, max_mensajes_globales=10
    )
    for n in range(4):
        await pasarela.manejar(_mensaje(f"R{n}", "hola"))
    assert len(emisor.respuestas) == 2
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert s.scalar(select(func.count()).select_from(EnvioReciente)) == 2


async def test_tres_mensajes_seguidos_reciben_una_sola_respuesta(
    engine: Engine, cfg: Config
) -> None:
    emisor, reloj = EmisorFalso(), RelojFalso(AHORA)
    pasarela = _pasarela(engine, cfg, emisor, reloj, debounce_segundos=0.1)
    for n, texto in enumerate(("hola", "tienes cita", "para mañana")):
        await pasarela.manejar(_mensaje(f"B{n}", texto))
    assert emisor.respuestas == []  # sigue esperando a que termine de escribir
    await asyncio.sleep(0.4)
    await pasarela.esperar_tareas()
    assert len(emisor.respuestas) == 1


async def test_estado_de_la_conexion_y_alerta_critica_al_cerrarse_la_sesion(
    banco: tuple[Pasarela, EmisorFalso, RelojFalso, Engine], caplog: pytest.LogCaptureFixture
) -> None:
    pasarela, _, _, _ = banco
    assert pasarela.estado_conexion == "desconocido"
    await pasarela.manejar(EventoEstado(tipo="estado", conexion="open"))
    assert pasarela.estado_conexion == "open"
    with caplog.at_level(logging.INFO, logger="webhook"):
        await pasarela.manejar(EventoEstado(tipo="estado", conexion="loggedOut"))
    assert pasarela.estado_conexion == "loggedOut"
    assert pasarela.estado_desde == AHORA
    assert any(r.levelno == logging.CRITICAL for r in caplog.records)


async def test_lanzar_registra_los_errores_sin_propagarlos(
    banco: tuple[Pasarela, EmisorFalso, RelojFalso, Engine], caplog: pytest.LogCaptureFixture
) -> None:
    pasarela, emisor, _, _ = banco

    def explota(*_: object) -> None:
        raise RuntimeError("falla del envío")

    emisor.responder = explota  # type: ignore[method-assign,assignment]
    with caplog.at_level(logging.ERROR):
        pasarela.lanzar(_mensaje("X1"))  # el fallo ocurre dentro del procesamiento del mensaje
        await pasarela.esperar_tareas()
    assert any("acumulador_error_inesperado" in r.getMessage() for r in caplog.records)
    caplog.clear()
    pasarela._registrar = explota  # type: ignore[method-assign,assignment]  # noqa: SLF001
    with caplog.at_level(logging.ERROR):
        pasarela.lanzar(_mensaje("X2"))  # y este, antes de llegar al acumulador
        await pasarela.esperar_tareas()
    assert any("webhook_error_inesperado" in r.getMessage() for r in caplog.records)


async def test_nunca_se_escribe_a_quien_no_escribio_primero(
    banco: tuple[Pasarela, EmisorFalso, RelojFalso, Engine],
) -> None:
    pasarela, emisor, _, _ = banco
    await pasarela.manejar(EventoPropio(tipo="propio", id="Y1", jid="573007776655@s.whatsapp.net"))
    await pasarela.manejar(EventoEstado(tipo="estado", conexion="open"))
    assert emisor.respuestas == []  # el bot solo habla cuando le escriben
