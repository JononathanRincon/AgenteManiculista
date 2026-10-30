"""Canal push de Calendar: registro, renovación y /webhook/calendar (EARS-CAC-02, 03)."""

from datetime import timedelta
from typing import Any

import pytest
from app.canal import GestorCanal, url_publica_https
from app.limite_tasa import LimiteTasa
from app.modelos import CalendarioEstado, unidad_de_trabajo
from app.puertos import CanalCalendar, ErrorPermanente, ErrorTransitorio
from app.reloj import RelojFalso
from app.webhook import crear_router_calendar
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from tests.integration.conftest import AHORA
from tests.integration.test_calendario_google import CAL, _calendario, _error

URL = "https://agenda.ejemplo.co/webhook/calendar"
TOKEN = "token-del-canal-de-prueba-0123456789"  # noqa: S105 - valor de prueba
SEMANA = 7 * 24 * 3600


# ------------------------------------------------------------------ adaptador de Google
def test_registrar_canal_envia_el_cuerpo_de_events_watch() -> None:
    expira_ms = int((AHORA + timedelta(days=7)).timestamp() * 1000)
    cal, http = _calendario(
        lambda *_: (200, {"id": "c1", "resourceId": "r1", "expiration": str(expira_ms)})
    )
    canal = cal.registrar_canal(CAL, canal_id="c1", url=URL, token=TOKEN, ttl_s=SEMANA)
    assert canal == CanalCalendar("c1", "r1", AHORA + timedelta(days=7))
    metodo, ruta, _, cuerpo = http.peticiones[0]
    assert (metodo, ruta) == (
        "POST",
        "/calendar/v3/calendars/agenda%40group.calendar.google.com/events/watch",
    )
    assert cuerpo == {
        "id": "c1",
        "type": "web_hook",
        "address": URL,
        "token": TOKEN,
        "params": {"ttl": str(SEMANA)},
    }


@pytest.mark.parametrize("respuesta", [{}, {"id": "c", "resourceId": "r", "expiration": "x"}])
def test_un_canal_incompleto_de_google_es_error_permanente(respuesta: dict[str, Any]) -> None:
    cal, _ = _calendario(lambda *_: (200, respuesta))
    with pytest.raises(ErrorPermanente):
        cal.registrar_canal(CAL, canal_id="c", url=URL, token=TOKEN, ttl_s=60)


def test_detener_canal_y_tolerar_que_ya_expiro() -> None:
    cal, http = _calendario(lambda *_: (204, None))
    cal.detener_canal("c1", "r1")
    assert http.peticiones[0][:2] == ("POST", "/calendar/v3/channels/stop")
    assert http.peticiones[0][3] == {"id": "c1", "resourceId": "r1"}
    vencido, _ = _calendario(lambda *_: _error(404, "notFound"))
    vencido.detener_canal("c1", "r1")


# -------------------------------------------------------------------------- URL pública
@pytest.mark.parametrize(
    ("url", "valida"),
    [
        (URL, True),
        ("https://8.8.8.8/x", True),
        (None, False),
        ("", False),
        ("http://agenda.ejemplo.co/x", False),
        ("https://localhost/x", False),
        ("https://mi.localhost/x", False),
        ("https://servidor.local/x", False),
        ("https://intranet/x", False),
        ("https://127.0.0.1/x", False),
        ("https://192.168.1.5/x", False),
        ("https://10.0.0.2/x", False),
        ("https:///sin-host", False),
        ("https://[::1/x", False),  # urlsplit lo rechaza
    ],
)
def test_url_publica_https(url: str | None, valida: bool) -> None:
    assert url_publica_https(url) is valida


# ---------------------------------------------------------------------- gestor del canal
class CanalesFalsos:
    def __init__(self) -> None:
        self.registrados: list[str] = []
        self.detenidos: list[tuple[str, str]] = []
        self.falla_registro: Exception | None = None
        self.falla_detener: Exception | None = None
        self.reloj: RelojFalso | None = None

    def registrar_canal(
        self, calendario_id: str, *, canal_id: str, url: str, token: str, ttl_s: int
    ) -> CanalCalendar:
        if self.falla_registro:
            raise self.falla_registro
        self.registrados.append(canal_id)
        assert self.reloj is not None
        return CanalCalendar(
            canal_id, f"res-{canal_id}", self.reloj.ahora() + timedelta(seconds=ttl_s)
        )

    def detener_canal(self, canal_id: str, resource_id: str) -> None:
        if self.falla_detener:
            raise self.falla_detener
        self.detenidos.append((canal_id, resource_id))


def _gestor(
    engine: Engine, url: str | None = URL, token: str | None = TOKEN
) -> tuple[GestorCanal, CanalesFalsos, RelojFalso]:
    reloj = RelojFalso(AHORA)
    canales = CanalesFalsos()
    canales.reloj = reloj
    ids = iter(f"canal{i}" for i in range(1, 50))
    gestor = GestorCanal(
        engine,
        canales,
        reloj,
        "cal",
        url=url,
        token=token,
        ttl_s=SEMANA,
        margen_s=86400,
        nuevo_id=lambda: next(ids),
    )
    return gestor, canales, reloj


def _estado(engine: Engine) -> CalendarioEstado:
    with unidad_de_trabajo(engine, escritura=False) as s:
        fila = s.get(CalendarioEstado, "cal")
        assert fila is not None
        s.expunge(fila)
        return fila


def test_primer_registro_guarda_el_canal_y_sale_del_modo_degradado(engine: Engine) -> None:
    gestor, canales, _ = _gestor(engine)
    assert gestor.asegurar() is True
    fila = _estado(engine)
    assert (fila.canal_id, fila.canal_resource_id) == ("canal1", "res-canal1")
    assert fila.canal_expira_en == AHORA + timedelta(seconds=SEMANA)
    assert fila.modo_degradado is False
    assert canales.detenidos == []


def test_un_canal_vigente_no_se_toca(engine: Engine) -> None:
    gestor, canales, reloj = _gestor(engine)
    gestor.asegurar()
    reloj.avanzar(days=5)  # quedan 2 días > margen de 1 día
    assert gestor.asegurar() is True
    assert canales.registrados == ["canal1"]


def test_el_canal_se_renueva_antes_de_caducar_y_se_detiene_el_anterior(engine: Engine) -> None:
    gestor, canales, reloj = _gestor(engine)
    gestor.asegurar()
    reloj.avanzar(days=6, hours=2)  # falta menos del margen, pero sigue vigente
    assert gestor.asegurar() is True
    assert canales.registrados == ["canal1", "canal2"]
    assert canales.detenidos == [("canal1", "res-canal1")]
    assert _estado(engine).canal_id == "canal2"


def test_si_falla_detener_el_anterior_la_renovacion_igual_cuenta(engine: Engine) -> None:
    gestor, canales, reloj = _gestor(engine)
    gestor.asegurar()
    reloj.avanzar(days=6, hours=2)
    canales.falla_detener = ErrorTransitorio("x")
    assert gestor.asegurar() is True
    assert _estado(engine).canal_id == "canal2"


def test_si_la_renovacion_falla_con_el_canal_vigente_se_conserva_el_push(engine: Engine) -> None:
    gestor, canales, reloj = _gestor(engine)
    gestor.asegurar()
    reloj.avanzar(days=6, hours=2)
    canales.falla_registro = ErrorTransitorio("x")
    assert gestor.asegurar() is True  # el viejo aún vale; se reintenta en el próximo ciclo
    fila = _estado(engine)
    assert fila.canal_id == "canal1"
    assert fila.modo_degradado is False


def test_si_el_canal_expiro_y_no_se_puede_renovar_queda_degradado(engine: Engine) -> None:
    gestor, canales, reloj = _gestor(engine)
    gestor.asegurar()
    reloj.avanzar(days=8)
    canales.falla_registro = ErrorPermanente("x")
    assert gestor.asegurar() is False
    assert _estado(engine).modo_degradado is True


@pytest.mark.parametrize(("url", "token"), [(None, TOKEN), ("http://x.co/w", TOKEN), (URL, None)])
def test_sin_url_publica_o_sin_token_no_se_registra_y_queda_degradado(
    engine: Engine, url: str | None, token: str | None
) -> None:
    gestor, canales, _ = _gestor(engine, url, token)
    assert gestor.asegurar() is False
    assert canales.registrados == []
    assert _estado(engine).modo_degradado is True
    assert gestor.asegurar() is False  # repetir no cambia nada


# ---------------------------------------------------------------------- /webhook/calendar
def _app() -> tuple[TestClient, list[int]]:
    avisos: list[int] = []
    app = FastAPI()
    app.state.calendar_channel_token = TOKEN
    app.state.limite_calendar = LimiteTasa(maximo=30, ventana_s=60)
    app.state.alinear_calendario = lambda: avisos.append(1)
    app.include_router(crear_router_calendar())
    return TestClient(app), avisos


def test_push_valido_invalida_y_resincroniza() -> None:
    c, avisos = _app()
    r = c.post(
        "/webhook/calendar",
        headers={"X-Goog-Channel-Token": TOKEN, "X-Goog-Resource-State": "exists"},
    )
    assert r.status_code == 200
    assert avisos == [1]


def test_el_saludo_inicial_sync_no_dispara_resincronizacion() -> None:
    c, avisos = _app()
    r = c.post(
        "/webhook/calendar",
        headers={"X-Goog-Channel-Token": TOKEN, "X-Goog-Resource-State": "sync"},
    )
    assert r.status_code == 200
    assert avisos == []


@pytest.mark.parametrize(
    "cabeceras", [{}, {"X-Goog-Channel-Token": ""}, {"X-Goog-Channel-Token": "otro"}]
)
def test_push_con_token_invalido_da_403_y_no_toca_nada(cabeceras: dict[str, str]) -> None:
    c, avisos = _app()
    assert c.post("/webhook/calendar", headers=cabeceras).status_code == 403
    assert avisos == []


def test_frena_los_intentos_de_adivinar_el_token() -> None:
    c, _ = _app()
    codigos = [
        c.post("/webhook/calendar", headers={"X-Goog-Channel-Token": f"x{i}"}).status_code
        for i in range(32)
    ]
    assert codigos[30:] == [403, 403]
    assert c.post("/webhook/calendar", headers={"X-Goog-Channel-Token": TOKEN}).status_code == 403


def test_al_recuperar_el_push_se_sale_del_modo_degradado(engine: Engine) -> None:
    sin_url, _, _ = _gestor(engine, url=None)
    assert sin_url.asegurar() is False
    con_url, _, _ = _gestor(engine)  # mismo estado en la base: ahora sí hay URL pública
    assert con_url.asegurar() is True
    assert _estado(engine).modo_degradado is False
