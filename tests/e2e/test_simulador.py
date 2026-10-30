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


def test_ui_sirve_los_archivos_estaticos(cliente: TestClient) -> None:
    for ruta, tipo in (
        ("/demo/static/app.js", "javascript"),
        ("/demo/static/estilos.css", "css"),
    ):
        r = cliente.get(ruta)
        assert r.status_code == 200
        assert tipo in r.headers["content-type"]
        assert "default-src 'self'" in r.headers["content-security-policy"]
    assert cliente.get("/demo/static/../../config/negocio.yaml").status_code in (404, 400)
    assert cliente.get("/demo/static/no_existe.js").status_code == 404


def test_ui_cumple_la_politica_de_contenido_y_la_accesibilidad(cliente: TestClient) -> None:
    html = cliente.get("/demo").text
    assert 'lang="es"' in html
    assert 'name="viewport"' in html
    assert "<script src=" in html and "<script>" not in html  # CSP: sin scripts en línea
    assert " style=" not in html and "<style" not in html  # CSP: sin estilos en línea
    assert 'role="log"' in html
    assert 'aria-live="polite"' in html
    for texto in ("Dos confirmaciones simultáneas", "Reiniciar", "datos de ejemplo"):
        assert texto in html


def test_ui_los_ids_que_usa_el_javascript_existen_en_el_html(cliente: TestClient) -> None:
    import re

    html = cliente.get("/demo").text
    js = cliente.get("/demo/static/app.js").text
    usados = set(re.findall(r'el\("([a-z-]+)"\)', js))
    assert usados
    for identificador in usados:
        assert f'id="{identificador}"' in html, identificador


def test_ui_objetivos_tactiles_de_44_px_y_diseno_vertical(cliente: TestClient) -> None:
    css = cliente.get("/demo/static/estilos.css").text
    assert css.count("min-height: 44px") >= 3
    assert "max-aspect-ratio: 1/1" in css
    assert "prefers-reduced-motion" in css
    assert "prefers-color-scheme: dark" in css


def test_carrera_gana_exactamente_una_y_el_calendario_queda_con_una_cita(
    cliente: TestClient,
) -> None:
    r = cliente.post("/demo/api/carrera", json={})
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["ganadora"]["nombre"] != cuerpo["rechazada"]["nombre"]
    assert "se acaba de ocupar" in cuerpo["rechazada"]["mensaje"]
    demo = cliente.app.state.demo  # type: ignore[attr-defined]
    demo.worker.procesar_uno()
    eventos = cliente.get("/demo/api/calendario").json()["eventos"]
    assert [e["estado"] for e in eventos] == ["confirmada"]


def test_carrera_repetida_nunca_produce_dos_ganadoras(cliente: TestClient) -> None:
    demo = cliente.app.state.demo  # type: ignore[attr-defined]
    for _ in range(12):
        r = cliente.post("/demo/api/carrera", json={"servicio_id": "retiro"})
        assert r.status_code == 200
        while demo.worker.procesar_uno():
            pass
    eventos = cliente.get("/demo/api/calendario").json()["eventos"]
    inicios = [e["inicio"] for e in eventos]
    assert len(inicios) == 12
    assert len(set(inicios)) == 12  # ningún horario repetido


def test_carrera_valida_servicio_y_horario(cliente: TestClient) -> None:
    assert cliente.post("/demo/api/carrera", json={"servicio_id": "inventado"}).status_code == 404
    ocupado = cliente.post("/demo/api/carrera", json={"inicio": "2030-01-01T10:00:00-05:00"})
    assert ocupado.status_code == 409
    assert ocupado.json()["detail"]["codigo"] == "HORARIO_NO_DISPONIBLE"
    assert cliente.post("/demo/api/carrera", json={"inicio": "no-es-fecha"}).status_code == 422
    assert cliente.post("/demo/api/carrera", json={"extra": 1}).status_code == 422


def test_carrera_con_horario_explicito(cliente: TestClient) -> None:
    demo = cliente.app.state.demo  # type: ignore[attr-defined]
    primera = cliente.post("/demo/api/carrera", json={}).json()
    assert primera["ganadora"]
    while demo.worker.procesar_uno():
        pass
    inicio = cliente.get("/demo/api/calendario").json()["eventos"][0]["inicio"]
    repetida = cliente.post("/demo/api/carrera", json={"inicio": inicio})
    assert repetida.status_code == 409  # el horario ya quedó tomado por la primera prueba


def test_errores_internos_de_la_demo_se_traducen_a_http(cliente: TestClient) -> None:
    from app.simulador.demo import CarreraSinGanadora, TelefonoInvalido

    demo = cliente.app.state.demo  # type: ignore[attr-defined]

    def sin_ganadora(*_: object) -> dict[str, dict[str, str]]:
        raise CarreraSinGanadora("[]")

    def telefono_malo(*_: object, **__: object) -> list[object]:
        raise TelefonoInvalido("x")

    demo.carrera = sin_ganadora
    assert cliente.post("/demo/api/carrera", json={}).status_code == 500
    demo.mensaje = telefono_malo
    cuerpo = {"telefono_demo": TEL, "texto": "hola"}
    assert cliente.post("/demo/api/mensaje", json=cuerpo).status_code == 422


def test_demo_ramas_de_la_carrera_y_del_mensaje(cliente: TestClient) -> None:
    from app.simulador.demo import CarreraSinGanadora, HorarioNoDisponible

    demo = cliente.app.state.demo  # type: ignore[attr-defined]
    demo.mensaje(TEL)  # sin texto ni botón: no registra nada de la clienta
    assert [m.de for m in demo.transporte.historial(TEL)] == ["bot"]

    original = demo.agente.procesar

    def ignora_confirmaciones(entrante):  # type: ignore[no-untyped-def]
        return [] if entrante.id_interactivo == "conf:si" else original(entrante)

    demo.agente.procesar = ignora_confirmaciones
    with pytest.raises(CarreraSinGanadora):
        demo.carrera()

    def segunda_sin_la_hora(entrante):  # type: ignore[no-untyped-def]
        salidas = original(entrante)
        if entrante.telefono.endswith("2") and (entrante.id_interactivo or "").startswith("dia:"):
            for s in salidas:
                s.opciones[:] = [o for o in s.opciones if not o.id.startswith("hora:")]
        return salidas

    demo.agente.procesar = segunda_sin_la_hora
    with pytest.raises(HorarioNoDisponible):
        demo.carrera()


def test_demo_sin_horarios_y_con_horario_explicito_valido(tmp_path: Path, cfg: Config) -> None:

    from app.simulador.demo import HorarioNoDisponible

    cerrado = cfg.negocio.model_copy(update={"horario": {d: None for d in cfg.negocio.horario}})
    app = crear_app(
        _ajustes(tmp_path, simulador=True),
        cfg.model_copy(update={"negocio": cerrado}),
        RelojFalso(AHORA),
    )
    with TestClient(app) as c:
        assert c.post("/demo/api/carrera", json={}).status_code == 409
        with pytest.raises(HorarioNoDisponible):
            c.app.state.demo.carrera()  # type: ignore[attr-defined]


def test_demo_carrera_con_un_horario_explicito_que_si_esta_libre(cliente: TestClient) -> None:
    r = cliente.post("/demo/api/carrera", json={"inicio": "2026-11-02T09:30:00-05:00"})
    assert r.status_code == 200
    demo = cliente.app.state.demo  # type: ignore[attr-defined]
    while demo.worker.procesar_uno():
        pass
    inicios = [e["inicio"] for e in cliente.get("/demo/api/calendario").json()["eventos"]]
    assert inicios == ["2026-11-02T09:30:00-05:00"]


async def test_la_ruta_de_eventos_devuelve_un_flujo_sse(cliente: TestClient) -> None:
    from app.simulador.rutas import crear_router

    ruta = next(r for r in crear_router().routes if getattr(r, "path", "") == "/demo/api/eventos")
    respuesta = await ruta.endpoint(cliente.app.state.demo)  # type: ignore[attr-defined]
    assert respuesta.media_type == "text/event-stream"
    assert respuesta.headers["cache-control"] == "no-cache"
    await respuesta.body_iterator.aclose()


async def test_varios_avisos_seguidos_se_funden_en_uno(cliente: TestClient) -> None:
    demo = cliente.app.state.demo  # type: ignore[attr-defined]
    flujo = flujo_eventos(demo, latido_s=5)
    await anext(flujo)  # retry
    siguiente = asyncio.ensure_future(anext(flujo))
    await asyncio.sleep(0.01)
    demo._notificar()  # noqa: SLF001
    demo._notificar()  # noqa: SLF001
    assert (await asyncio.wait_for(siguiente, timeout=2)).startswith("event: cambio")
    await flujo.aclose()
