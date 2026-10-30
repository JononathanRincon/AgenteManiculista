"""Adaptador CalendarioGoogle con un HTTP simulado bajo el cliente real de Google (design.md §B9.2).

Se simula la capa de transporte (`httplib2`), no el adaptador: así se prueban de verdad la
construcción de las URL, los parámetros y el análisis de errores del cliente oficial.
"""

import json
import socket
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import httplib2  # type: ignore[import-untyped]
import pytest
from app.calendario import CalendarioGoogle
from app.config import ConfiguracionInvalida
from app.puertos import (
    CuotaExcedida,
    ErrorPermanente,
    ErrorSyncTokenExpirado,
    ErrorTransitorio,
    EventoNuevo,
)
from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.discovery import build  # type: ignore[import-untyped]

BOGOTA = ZoneInfo("America/Bogota")
CAL = "agenda@group.calendar.google.com"
DESDE = datetime(2026, 11, 2, 0, 0, tzinfo=BOGOTA).astimezone(UTC)
HASTA = datetime(2026, 11, 30, 0, 0, tzinfo=BOGOTA).astimezone(UTC)
Respuesta = tuple[int, Any]
Manejador = Callable[[str, str, dict[str, list[str]], Any], Respuesta]


class HttpFalso:
    """Reemplaza a httplib2.Http: registra cada petición y responde con un manejador."""

    def __init__(self, manejador: Manejador) -> None:
        self.manejador = manejador
        self.peticiones: list[tuple[str, str, dict[str, list[str]], Any]] = []

    def request(self, uri: str, method: str = "GET", body: Any = None, **_: Any) -> Any:
        partes = urlsplit(uri)
        consulta = parse_qs(partes.query)
        cuerpo = json.loads(body) if body else None
        self.peticiones.append((method, partes.path, consulta, cuerpo))
        estado, datos = self.manejador(method, partes.path, consulta, cuerpo)
        contenido = b"" if datos is None else json.dumps(datos).encode()
        return httplib2.Response({"status": str(estado), "content-type": "application/json"}), (
            contenido
        )


def _calendario(manejador: Manejador) -> tuple[CalendarioGoogle, HttpFalso]:
    http = HttpFalso(manejador)

    def fabrica() -> Any:
        return build("calendar", "v3", http=http, static_discovery=True, cache_discovery=False)

    return CalendarioGoogle(fabrica, BOGOTA), http


def _error(estado: int, razon: str = "", dominio: str = "global") -> Respuesta:
    errores = [{"domain": dominio, "reason": razon}] if razon else []
    return estado, {"error": {"code": estado, "message": "x", "errors": errores}}


def _evento(
    id_: str,
    inicio: datetime,
    fin: datetime,
    *,
    estado: str = "confirmed",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": id_,
        "status": estado,
        "start": {"dateTime": inicio.isoformat()},
        "end": {"dateTime": fin.isoformat()},
        **extra,
    }


def _local(dia: int, h: int, mi: int = 0) -> datetime:
    return datetime(2026, 11, dia, h, mi, tzinfo=BOGOTA).astimezone(UTC)


def _nuevo(ubicacion: str | None = None) -> EventoNuevo:
    return EventoNuevo(
        id="a1b2c3d4e5f6",
        titulo="Semipermanente · Ana",
        descripcion="Tel. 573001112233",
        inicio=_local(3, 10),
        fin=_local(3, 11, 30),
        telefono="573001112233",
        reserva_id=7,
        ubicacion=ubicacion,
    )


# ------------------------------------------------------------------------- crear_evento
def test_crear_evento_envia_propiedades_privadas_y_id_determinista() -> None:
    cal, http = _calendario(lambda *_: (200, {"id": "a1b2c3d4e5f6"}))
    assert cal.crear_evento(CAL, _nuevo()) == "a1b2c3d4e5f6"
    metodo, ruta, _, cuerpo = http.peticiones[0]
    assert metodo == "POST"
    assert ruta == "/calendar/v3/calendars/agenda%40group.calendar.google.com/events"
    assert cuerpo["id"] == "a1b2c3d4e5f6"
    assert cuerpo["summary"] == "Semipermanente · Ana"
    assert cuerpo["description"] == "Tel. 573001112233"
    assert cuerpo["extendedProperties"] == {
        "private": {"telefono": "573001112233", "reserva_id": "7"}
    }
    assert datetime.fromisoformat(cuerpo["start"]["dateTime"]) == _local(3, 10)
    assert datetime.fromisoformat(cuerpo["end"]["dateTime"]) == _local(3, 11, 30)
    assert "location" not in cuerpo  # EARS-CAL-02: solo con domicilio


def test_crear_evento_a_domicilio_incluye_la_direccion_en_location() -> None:
    cal, http = _calendario(lambda *_: (200, {"id": "a1b2c3d4e5f6"}))
    cal.crear_evento(CAL, _nuevo(ubicacion="Calle 10 # 5-20"))
    assert http.peticiones[0][3]["location"] == "Calle 10 # 5-20"


def test_un_409_con_el_evento_vigente_cuenta_como_exito() -> None:
    def manejar(metodo: str, _ruta: str, *_: Any) -> Respuesta:
        if metodo == "POST":
            return _error(409, "duplicate")
        return 200, {"id": "a1b2c3d4e5f6", "status": "confirmed"}

    cal, http = _calendario(manejar)
    assert cal.crear_evento(CAL, _nuevo()) == "a1b2c3d4e5f6"
    assert [p[0] for p in http.peticiones] == ["POST", "GET"]


@pytest.mark.parametrize("lectura", [(200, {"id": "x", "status": "cancelled"}), _error(404)])
def test_un_409_por_un_id_de_un_evento_eliminado_no_se_da_por_exito(lectura: Respuesta) -> None:
    # Google reserva para siempre el id de un evento borrado: darlo por creado dejaría
    # una reserva CONFIRMADA sin evento en el calendario.
    def manejar(metodo: str, *_: Any) -> Respuesta:
        return _error(409, "duplicate") if metodo == "POST" else lectura

    cal, _ = _calendario(manejar)
    with pytest.raises(ErrorPermanente):
        cal.crear_evento(CAL, _nuevo())


# ------------------------------------------------------------------------ eliminar_evento
def test_eliminar_evento() -> None:
    cal, http = _calendario(lambda *_: (204, None))
    cal.eliminar_evento(CAL, "ev1")
    metodo, ruta, _, _ = http.peticiones[0]
    assert (metodo, ruta) == (
        "DELETE",
        "/calendar/v3/calendars/agenda%40group.calendar.google.com/events/ev1",
    )


@pytest.mark.parametrize("estado", [404, 410])
def test_eliminar_un_evento_que_ya_no_existe_cuenta_como_exito(estado: int) -> None:
    cal, _ = _calendario(lambda *_: _error(estado, "notFound"))
    cal.eliminar_evento(CAL, "ev1")


def test_eliminar_propaga_los_demas_errores() -> None:
    cal, _ = _calendario(lambda *_: _error(503))
    with pytest.raises(ErrorTransitorio):
        cal.eliminar_evento(CAL, "ev1")


# --------------------------------------------------------------------- mapeo de errores
@pytest.mark.parametrize(
    "respuesta",
    [
        _error(403, "rateLimitExceeded", "usageLimits"),
        _error(403, "userRateLimitExceeded", "usageLimits"),
        _error(403, "quotaExceeded", "usageLimits"),
        _error(403, "dailyLimitExceeded", "usageLimits"),
        _error(429, "rateLimitExceeded", "usageLimits"),
        _error(429),
    ],
)
def test_403_y_429_de_cuota_son_cuota_excedida(respuesta: Respuesta) -> None:
    cal, _ = _calendario(lambda *_: respuesta)
    with pytest.raises(CuotaExcedida):
        cal.crear_evento(CAL, _nuevo())


@pytest.mark.parametrize("estado", [500, 502, 503, 504])
def test_5xx_es_transitorio(estado: int) -> None:
    cal, _ = _calendario(lambda *_: _error(estado, "backendError"))
    with pytest.raises(ErrorTransitorio):
        cal.crear_evento(CAL, _nuevo())


@pytest.mark.parametrize(
    "respuesta",
    [
        _error(400, "badRequest"),
        _error(401, "authError"),
        _error(403, "forbidden"),  # el calendario no está compartido con la cuenta de servicio
        _error(404, "notFound"),
        _error(410, "deleted"),
        (302, None),  # estado inesperado
    ],
)
def test_4xx_de_datos_o_permisos_es_permanente(respuesta: Respuesta) -> None:
    cal, _ = _calendario(lambda *_: respuesta)
    with pytest.raises(ErrorPermanente):
        cal.crear_evento(CAL, _nuevo())


def test_un_cuerpo_de_error_ilegible_no_rompe_el_mapeo() -> None:
    http = HttpFalso(lambda *_: (200, None))

    def request(*_: Any, **__: Any) -> Any:
        return httplib2.Response({"status": "403"}), b"<html>no es json</html>"

    http.request = request  # type: ignore[method-assign]
    cal = CalendarioGoogle(
        lambda: build("calendar", "v3", http=http, static_discovery=True, cache_discovery=False),
        BOGOTA,
    )
    with pytest.raises(ErrorPermanente):
        cal.crear_evento(CAL, _nuevo())


@pytest.mark.parametrize(
    "falla",
    [
        TimeoutError("sin respuesta"),
        socket.gaierror("sin resolución"),
        ConnectionResetError("reinicio"),
        httplib2.ServerNotFoundError("sin DNS"),
        TransportError("no se pudo renovar el token"),  # type: ignore[no-untyped-call]
    ],
)
def test_los_fallos_de_red_son_transitorios(falla: Exception) -> None:
    def manejar(*_: Any) -> Respuesta:
        raise falla

    cal, _ = _calendario(manejar)
    with pytest.raises(ErrorTransitorio):
        cal.crear_evento(CAL, _nuevo())


def test_credenciales_rechazadas_son_permanentes() -> None:
    def manejar(*_: Any) -> Respuesta:
        raise RefreshError("invalid_grant")  # type: ignore[no-untyped-call]

    cal, _ = _calendario(manejar)
    with pytest.raises(ErrorPermanente):
        cal.crear_evento(CAL, _nuevo())


# --------------------------------------------------------------------- listar_eventos
def _pagina(*eventos: dict[str, Any], **extra: Any) -> Respuesta:
    return 200, {"items": list(eventos), **extra}


def test_listado_completo_no_usa_ventana_ni_sync_token_y_filtra_el_rango() -> None:
    dentro = _evento("dentro", _local(3, 9), _local(3, 10))
    fuera = _evento("fuera", _local(1, 9), _local(1, 10))  # antes de la ventana
    cancelado = _evento("cancelado", _local(4, 9), _local(4, 10), estado="cancelled")
    cal, http = _calendario(
        lambda *_: _pagina(dentro, fuera, cancelado, nextSyncToken="TOKEN-1"),
    )
    r = cal.listar_eventos(CAL, sync_token=None, desde=DESDE, hasta=HASTA)
    assert [e.event_id for e in r.eventos] == ["dentro"]
    assert r.nuevo_sync_token == "TOKEN-1"  # noqa: S105 - valor de prueba
    assert r.resincronizacion_completa is True
    consulta = http.peticiones[0][2]
    # con timeMin/timeMax Google no entrega un sync token utilizable
    assert "timeMin" not in consulta
    assert "timeMax" not in consulta
    assert "syncToken" not in consulta
    assert consulta["singleEvents"] == ["true"]


def test_listado_incremental_pide_los_borrados_y_no_mezcla_parametros_prohibidos() -> None:
    borrado = {"id": "ev9", "status": "cancelled"}  # Google entrega solo id y estado
    movido_fuera = _evento("ev8", _local(1, 9), _local(1, 10))
    cal, http = _calendario(
        lambda *_: _pagina(
            _evento("ev7", _local(3, 9), _local(3, 10)), borrado, movido_fuera, nextSyncToken="T2"
        )
    )
    r = cal.listar_eventos(CAL, sync_token="T1", desde=DESDE, hasta=HASTA)  # noqa: S106 - valor de prueba
    assert r.resincronizacion_completa is False
    assert r.nuevo_sync_token == "T2"  # noqa: S105 - valor de prueba
    por_id = {e.event_id: e for e in r.eventos}
    assert por_id["ev7"].cancelado is False
    assert por_id["ev9"].cancelado is True
    # un evento que se movió fuera de la ventana debe salir del espejo
    assert por_id["ev8"].cancelado is True
    consulta = http.peticiones[0][2]
    assert consulta["syncToken"] == ["T1"]
    assert consulta["showDeleted"] == ["true"]
    for prohibido in ("timeMin", "timeMax", "q", "orderBy", "updatedMin", "iCalUID"):
        assert prohibido not in consulta
    assert "privateExtendedProperty" not in consulta


def test_el_listado_recorre_las_paginas_hasta_el_sync_token() -> None:
    def manejar(_m: str, _r: str, consulta: dict[str, list[str]], _c: Any) -> Respuesta:
        if "pageToken" not in consulta:
            return _pagina(_evento("a", _local(3, 9), _local(3, 10)), nextPageToken="P2")
        return _pagina(_evento("b", _local(3, 11), _local(3, 12)), nextSyncToken="FIN")

    cal, http = _calendario(manejar)
    r = cal.listar_eventos(CAL, sync_token=None, desde=DESDE, hasta=HASTA)
    assert [e.event_id for e in r.eventos] == ["a", "b"]
    assert r.nuevo_sync_token == "FIN"  # noqa: S105 - valor de prueba
    assert http.peticiones[1][2]["pageToken"] == ["P2"]


def test_un_410_con_sync_token_pide_resincronizar() -> None:
    cal, _ = _calendario(lambda *_: _error(410, "fullSyncRequired"))
    with pytest.raises(ErrorSyncTokenExpirado):
        cal.listar_eventos(CAL, sync_token="VIEJO", desde=DESDE, hasta=HASTA)  # noqa: S106 - valor de prueba


def test_un_410_sin_sync_token_no_es_de_sincronizacion() -> None:
    cal, _ = _calendario(lambda *_: _error(410, "deleted"))
    with pytest.raises(ErrorPermanente):
        cal.listar_eventos(CAL, sync_token=None, desde=DESDE, hasta=HASTA)


def test_eventos_de_todo_el_dia_ocupan_el_dia_en_hora_local() -> None:
    festivo = {
        "id": "festivo",
        "status": "confirmed",
        "start": {"date": "2026-11-16"},
        "end": {"date": "2026-11-17"},  # el fin de un evento de día completo es exclusivo
    }
    cal, _ = _calendario(lambda *_: _pagina(festivo, nextSyncToken="T"))
    r = cal.listar_eventos(CAL, sync_token=None, desde=DESDE, hasta=HASTA)
    assert (r.eventos[0].inicio, r.eventos[0].fin) == (_local(16, 0), _local(17, 0))


def test_eventos_sin_horario_se_ignoran() -> None:
    raro = {"id": "raro", "status": "confirmed", "start": {}, "end": {}}
    cal, _ = _calendario(lambda *_: _pagina(raro, nextSyncToken="T"))
    assert cal.listar_eventos(CAL, sync_token=None, desde=DESDE, hasta=HASTA).eventos == []


# --------------------------------------------------------------------- ocupacion_en_vivo
def test_ocupacion_en_vivo_lee_el_rango_y_excluye_el_evento_indicado() -> None:
    cal, http = _calendario(
        lambda *_: _pagina(
            _evento("propio", _local(3, 9), _local(3, 10)),
            _evento("otro", _local(3, 11), _local(3, 12)),
            _evento("borrado", _local(3, 13), _local(3, 14), estado="cancelled"),
        )
    )
    ocupado = cal.ocupacion_en_vivo(CAL, _local(3, 0), _local(4, 0), excluir_event_id="propio")
    assert [(i.inicio, i.fin) for i in ocupado] == [(_local(3, 11), _local(3, 12))]
    consulta = http.peticiones[0][2]
    assert datetime.fromisoformat(consulta["timeMin"][0]) == _local(3, 0)
    assert datetime.fromisoformat(consulta["timeMax"][0]) == _local(4, 0)
    assert consulta["singleEvents"] == ["true"]
    assert "syncToken" not in consulta


def test_ocupacion_en_vivo_sin_exclusion_incluye_todo_y_pagina() -> None:
    def manejar(_m: str, _r: str, consulta: dict[str, list[str]], _c: Any) -> Respuesta:
        if "pageToken" not in consulta:
            return _pagina(_evento("a", _local(3, 9), _local(3, 10)), nextPageToken="P2")
        return _pagina(_evento("b", _local(3, 11), _local(3, 12)))

    cal, _ = _calendario(manejar)
    assert len(cal.ocupacion_en_vivo(CAL, _local(3, 0), _local(4, 0))) == 2


def test_un_evento_de_todo_el_dia_bloquea_la_ocupacion_en_vivo() -> None:
    vacaciones = {
        "id": "vac",
        "status": "confirmed",
        "start": {"date": "2026-11-03"},
        "end": {"date": "2026-11-04"},
    }
    cal, _ = _calendario(lambda *_: _pagina(vacaciones))
    ocupado = cal.ocupacion_en_vivo(CAL, _local(3, 8), _local(3, 18))
    assert [(i.inicio, i.fin) for i in ocupado] == [(_local(3, 0), _local(4, 0))]


def test_ocupacion_en_vivo_propaga_los_errores() -> None:
    cal, _ = _calendario(lambda *_: _error(429))
    with pytest.raises(CuotaExcedida):
        cal.ocupacion_en_vivo(CAL, _local(3, 0), _local(4, 0))


# ------------------------------------------------------------------- buscar_por_telefono
def test_buscar_por_telefono_filtra_por_propiedad_privada() -> None:
    cal, http = _calendario(
        lambda *_: _pagina(
            _evento(
                "e1",
                _local(5, 10),
                _local(5, 11),
                extendedProperties={"private": {"telefono": "573001112233", "reserva_id": "12"}},
            ),
            _evento(  # sin reserva_id (la dueña lo creó a mano con la propiedad): se tolera
                "e2",
                _local(6, 10),
                _local(6, 11),
                extendedProperties={"private": {"telefono": "573001112233", "reserva_id": "x"}},
            ),
            _evento("e3", _local(7, 10), _local(7, 11), estado="cancelled"),
        )
    )
    citas = cal.buscar_por_telefono(CAL, "573001112233", _local(3, 0))
    assert [(c.event_id, c.reserva_id) for c in citas] == [("e1", 12), ("e2", None)]
    consulta = http.peticiones[0][2]
    assert consulta["privateExtendedProperty"] == ["telefono=573001112233"]
    assert datetime.fromisoformat(consulta["timeMin"][0]) == _local(3, 0)
    assert consulta["singleEvents"] == ["true"]


def test_buscar_por_telefono_rechaza_valores_que_no_son_un_telefono() -> None:
    cal, http = _calendario(lambda *_: _pagina())
    for malo in ("", "57300;x=y", "573 001", "tel=5"):
        with pytest.raises(ErrorPermanente):
            cal.buscar_por_telefono(CAL, malo, _local(3, 0))
    assert http.peticiones == []


# ----------------------------------------------------------- servicio por hilo y construcción
def test_cada_hilo_usa_su_propio_cliente_http() -> None:
    # httplib2.Http no es seguro entre hilos: el worker y el agente llaman desde hilos distintos
    creados: list[int] = []
    http = HttpFalso(lambda *_: _pagina())

    def fabrica() -> Any:
        creados.append(threading.get_ident())
        return build("calendar", "v3", http=http, static_discovery=True, cache_discovery=False)

    cal = CalendarioGoogle(fabrica, BOGOTA)
    cal.ocupacion_en_vivo(CAL, _local(3, 0), _local(4, 0))
    cal.ocupacion_en_vivo(CAL, _local(3, 0), _local(4, 0))
    hilo = threading.Thread(target=lambda: cal.ocupacion_en_vivo(CAL, _local(3, 0), _local(4, 0)))
    hilo.start()
    hilo.join()
    assert len(creados) == 2
    assert len(set(creados)) == 2


def _json_de_cuenta_de_servicio() -> str:
    """Cuenta de servicio con una clave RSA generada al vuelo (nunca una credencial real)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = clave.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return json.dumps(
        {
            "type": "service_account",
            "project_id": "proyecto-de-prueba",
            "private_key_id": "abc",
            "private_key": pem,
            "client_email": "bot@proyecto-de-prueba.iam.gserviceaccount.com",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )


def test_construye_el_cliente_desde_el_json_de_la_cuenta_de_servicio() -> None:
    cal = CalendarioGoogle.desde_cuenta_de_servicio(_json_de_cuenta_de_servicio(), BOGOTA)
    assert hasattr(cal._servicio(), "events")  # el descubrimiento es local: sin red


def test_construye_el_cliente_desde_un_archivo(tmp_path: Path) -> None:
    ruta = tmp_path / "cuenta.json"
    ruta.write_text(_json_de_cuenta_de_servicio(), encoding="utf-8")
    cal = CalendarioGoogle.desde_cuenta_de_servicio(str(ruta), BOGOTA)
    assert hasattr(cal._servicio(), "events")


@pytest.mark.parametrize("credencial", ["{no es json", '{"type": "service_account"}'])
def test_credenciales_invalidas_se_rechazan_sin_filtrar_su_contenido(credencial: str) -> None:
    with pytest.raises(ConfiguracionInvalida) as info:
        CalendarioGoogle.desde_cuenta_de_servicio(credencial, BOGOTA)
    assert "service_account" not in str(info.value)


def test_un_archivo_inexistente_se_rechaza(tmp_path: Path) -> None:
    with pytest.raises(ConfiguracionInvalida):
        CalendarioGoogle.desde_cuenta_de_servicio(str(tmp_path / "no-existe.json"), BOGOTA)


def test_un_calendario_con_paginas_sin_fin_se_corta() -> None:
    cal, _ = _calendario(lambda *_: _pagina(nextPageToken="siempre-hay-mas"))
    with pytest.raises(ErrorPermanente):
        cal.ocupacion_en_vivo(CAL, _local(3, 0), _local(4, 0))


def test_un_evento_sin_horario_no_bloquea_la_ocupacion_en_vivo() -> None:
    raro = {"id": "raro", "status": "confirmed", "start": {}, "end": {}}
    cal, _ = _calendario(lambda *_: _pagina(raro))
    assert cal.ocupacion_en_vivo(CAL, _local(3, 0), _local(4, 0)) == []


def test_una_hora_sin_desfase_se_lee_en_la_zona_del_evento_o_la_del_negocio() -> None:
    sin_zona = {
        "id": "a",
        "status": "confirmed",
        "start": {"dateTime": "2026-11-03T09:00:00"},
        "end": {"dateTime": "2026-11-03T10:00:00", "timeZone": "America/Bogota"},
    }
    cal, _ = _calendario(lambda *_: _pagina(sin_zona))
    [intervalo] = cal.ocupacion_en_vivo(CAL, _local(3, 0), _local(4, 0))
    assert (intervalo.inicio, intervalo.fin) == (_local(3, 9), _local(3, 10))


@pytest.mark.parametrize("sync_token", [None, "T1"])
def test_un_evento_futuro_fuera_de_la_ventana_entra_al_espejo(sync_token: str | None) -> None:
    # Google no reenvía lo que no cambió: si se descartara hoy, nunca llegaría cuando la ventana
    # avance y el bot ofrecería una hora ya ocupada.
    lejano = _evento(
        "lejano", _local(30, 9) + timedelta(days=60), _local(30, 10) + timedelta(days=60)
    )
    cal, _ = _calendario(lambda *_: _pagina(lejano, nextSyncToken="T"))
    [evento] = cal.listar_eventos(CAL, sync_token=sync_token, desde=DESDE, hasta=HASTA).eventos
    assert (evento.event_id, evento.cancelado) == ("lejano", False)
