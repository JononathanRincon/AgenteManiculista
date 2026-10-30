"""Adaptador del sidecar: reintentos sin duplicar envíos, humanización y registro de ids propios."""

import json
import threading
from typing import Any

import httpx
import pytest
from app.config import AntiBan, Config
from app.control_humano import ControlHumano
from app.conversacion import Saliente
from app.humanizacion import SecuenciaEnvio, sin_espera
from app.mensajes import configurar
from app.modelos import Conversacion, unidad_de_trabajo
from app.puertos import Opcion
from app.reloj import RelojFalso
from app.whatsapp import ClienteSidecar, ErrorSidecar, MensajeriaBaileys
from sqlalchemy import Engine

from tests.integration.conftest import AHORA

TEL = "573001112233"
TOKEN = "token-del-sidecar"  # noqa: S105 - valor de prueba


class ServidorFalso:
    """Sidecar simulado: registra peticiones y responde según un guion."""

    def __init__(self, guion: list[Any] | None = None) -> None:
        self.peticiones: list[tuple[str, str, dict[str, Any], dict[str, str]]] = []
        self.guion = list(guion or [])
        self.enviados: dict[str, str] = {}

    def __call__(self, peticion: httpx.Request) -> httpx.Response:
        cuerpo: dict[str, Any] = json.loads(peticion.content) if peticion.content else {}
        self.peticiones.append((peticion.method, peticion.url.path, cuerpo, dict(peticion.headers)))
        if self.guion:
            paso = self.guion.pop(0)
            if isinstance(paso, Exception):
                raise paso
            return httpx.Response(paso, json={"error": "x"})
        if peticion.url.path == "/enviar":
            clave = cuerpo["clave"]
            identificador = self.enviados.setdefault(clave, f"ID-{len(self.enviados) + 1}")
            return httpx.Response(200, json={"id": identificador})
        if peticion.url.path == "/estado":
            return httpx.Response(200, json={"conexion": "open"})
        return httpx.Response(200, json={"ok": True})


def _cliente(servidor: ServidorFalso, esperas: list[float] | None = None) -> ClienteSidecar:
    http = httpx.Client(base_url="http://sidecar:3000", transport=httpx.MockTransport(servidor))
    lista = esperas if esperas is not None else []
    return ClienteSidecar(
        "http://sidecar:3000", TOKEN, http, dormir=lista.append, azar=lambda a, b: 0.0
    )


def test_las_tres_primitivas_llevan_jid_y_token() -> None:
    servidor = ServidorFalso()
    cliente = _cliente(servidor)
    cliente.leer(TEL, "wamid.IN")
    cliente.presencia(TEL, "composing")
    assert cliente.enviar(TEL, "hola") == "ID-1"
    rutas = [(p[0], p[1]) for p in servidor.peticiones]
    assert rutas == [("POST", "/leer"), ("POST", "/presencia"), ("POST", "/enviar")]
    assert servidor.peticiones[0][2] == {"jid": f"{TEL}@s.whatsapp.net", "id": "wamid.IN"}
    assert servidor.peticiones[1][2] == {"jid": f"{TEL}@s.whatsapp.net", "estado": "composing"}
    assert servidor.peticiones[2][2]["texto"] == "hola"
    assert all(p[3]["authorization"] == f"Bearer {TOKEN}" for p in servidor.peticiones)


def test_estado_de_la_sesion() -> None:
    servidor = ServidorFalso()
    assert _cliente(servidor).estado() == {"conexion": "open"}
    assert servidor.peticiones[0][0] == "GET"


@pytest.mark.parametrize(
    "fallo", [503, 429, 500, httpx.ReadTimeout("lento"), httpx.ConnectError("no")]
)
def test_reintenta_con_backoff_exponencial_y_se_recupera(fallo: Any) -> None:
    servidor = ServidorFalso([fallo, fallo])
    esperas: list[float] = []
    assert _cliente(servidor, esperas).enviar(TEL, "hola") == "ID-1"
    assert esperas == [1.0, 2.0]  # 1 s, 2 s (+ jitter 0 en la prueba)
    assert len(servidor.peticiones) == 3


def test_los_reintentos_reutilizan_la_misma_clave_y_no_duplican_el_envio() -> None:
    servidor = ServidorFalso([503])
    cliente = _cliente(servidor)
    cliente.enviar(TEL, "hola")
    assert len(servidor.peticiones) == 2  # un fallo y un reintento
    assert servidor.peticiones[0][2] == servidor.peticiones[1][2]  # misma clave: no se duplica
    cliente.enviar(TEL, "otro")
    assert servidor.peticiones[2][2]["clave"] != servidor.peticiones[0][2]["clave"]
    assert len(servidor.enviados) == 2  # cada envío lógico llegó una sola vez


def test_tras_agotar_los_intentos_lanza_un_error_claro() -> None:
    servidor = ServidorFalso([503, 503, 503])
    with pytest.raises(ErrorSidecar, match="falló tras 3 intentos"):
        _cliente(servidor).enviar(TEL, "hola")
    assert len(servidor.peticiones) == 3


@pytest.mark.parametrize("codigo", [400, 401, 403, 404])
def test_los_errores_de_datos_no_se_reintentan(codigo: int) -> None:
    servidor = ServidorFalso([codigo])
    with pytest.raises(ErrorSidecar, match=str(codigo)):
        _cliente(servidor).leer(TEL, "x")
    assert len(servidor.peticiones) == 1


def test_respuestas_mal_formadas() -> None:
    def malo(contenido: bytes) -> httpx.Client:
        return httpx.Client(
            base_url="http://s",
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=contenido)),
        )

    for contenido in (b"no es json", b"[1, 2]", b'{"sin_id": true}', b'{"id": ""}'):
        cliente = ClienteSidecar("http://s", TOKEN, malo(contenido), dormir=lambda s: None)
        with pytest.raises(ErrorSidecar):
            cliente.enviar(TEL, "hola")


# ------------------------------------------------------------------ MensajeriaBaileys
@pytest.fixture
def mensajeria(
    engine: Engine, cfg: Config
) -> tuple[MensajeriaBaileys, ServidorFalso, ControlHumano, Engine]:
    configurar(None)
    servidor = ServidorFalso()
    anti = AntiBan(max_mensajes_por_telefono=3, max_mensajes_globales=10)
    negocio = cfg.negocio.model_copy(update={"anti_ban": anti})
    control = ControlHumano(engine, negocio)
    secuencia = SecuenciaEnvio(_cliente(servidor), anti, sin_espera)
    with unidad_de_trabajo(engine) as s:
        s.add(Conversacion(telefono=TEL, estado="MENU", actualizado_en=AHORA))
    return MensajeriaBaileys(secuencia, control, RelojFalso(AHORA)), servidor, control, engine


def _rutas(servidor: ServidorFalso) -> list[str]:
    return [p[1] for p in servidor.peticiones]


def test_enviar_texto_sigue_la_secuencia_de_presencia_sin_leer(
    mensajeria: tuple[MensajeriaBaileys, ServidorFalso, ControlHumano, Engine],
) -> None:
    m, servidor, _, _ = mensajeria
    m.enviar_texto(TEL, "Tu cita fue confirmada")
    assert _rutas(servidor) == ["/presencia", "/presencia", "/enviar"]
    assert [p[2]["estado"] for p in servidor.peticiones[:2]] == ["composing", "paused"]


def test_responder_lee_el_mensaje_une_la_respuesta_y_envia_uno_solo(
    mensajeria: tuple[MensajeriaBaileys, ServidorFalso, ControlHumano, Engine],
) -> None:
    m, servidor, _, _ = mensajeria
    salidas = [
        Saliente("texto", "Estos son mis servicios"),
        Saliente("botones", "Elige:", [Opcion("a", "Agendar cita"), Opcion("b", "Mis citas")]),
    ]
    m.responder(TEL, salidas, "wamid.IN")
    assert _rutas(servidor) == ["/leer", "/presencia", "/presencia", "/enviar"]
    enviado = servidor.peticiones[-1][2]["texto"]
    assert "Estos son mis servicios" in enviado
    assert "1️⃣ Agendar cita" in enviado
    assert sum(1 for r in _rutas(servidor) if r == "/enviar") == 1
    m.responder(TEL, [], "wamid.IN2")  # sin respuesta no se envía nada
    assert sum(1 for r in _rutas(servidor) if r == "/enviar") == 1


def test_botones_y_listas_se_degradan_a_texto_numerado(
    mensajeria: tuple[MensajeriaBaileys, ServidorFalso, ControlHumano, Engine],
) -> None:
    m, servidor, _, _ = mensajeria
    m.enviar_botones(TEL, "¿Confirmas?", [Opcion("conf:si", "Confirmar")])
    m.enviar_lista(TEL, "Servicios", "Ver", [Opcion("srv:retiro", "Retiro", "30 min")])
    textos = [p[2]["texto"] for p in servidor.peticiones if p[1] == "/enviar"]
    assert "1️⃣ Confirmar" in textos[0]
    assert "1️⃣ Retiro — 30 min" in textos[1]


def test_los_ids_emitidos_se_recuerdan_con_tope(
    mensajeria: tuple[MensajeriaBaileys, ServidorFalso, ControlHumano, Engine],
) -> None:
    m, _, _, _ = mensajeria
    m.enviar_texto(TEL, "uno")
    assert m.fue_emitido("ID-1") is True
    assert m.fue_emitido("ID-999") is False


def test_el_rate_limit_y_el_trafico_en_frio_descartan_el_envio(
    mensajeria: tuple[MensajeriaBaileys, ServidorFalso, ControlHumano, Engine],
) -> None:
    m, servidor, _, _ = mensajeria
    for _ in range(5):
        m.enviar_texto(TEL, "mensaje")  # el tope por teléfono es 3
    assert sum(1 for r in _rutas(servidor) if r == "/enviar") == 3
    antes = len(servidor.peticiones)
    m.enviar_texto("573009998877", "en frío")  # nunca escribió: no se envía
    assert len(servidor.peticiones) == antes


def test_los_envios_a_una_misma_clienta_no_se_intercalan(
    mensajeria: tuple[MensajeriaBaileys, ServidorFalso, ControlHumano, Engine],
) -> None:
    m, servidor, _, _ = mensajeria
    hilos = [threading.Thread(target=m.enviar_texto, args=(TEL, f"m{i}")) for i in range(3)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    rutas = _rutas(servidor)
    assert rutas == ["/presencia", "/presencia", "/enviar"] * 3  # cada secuencia completa


def test_se_recuerdan_solo_los_ultimos_500_ids(
    mensajeria: tuple[MensajeriaBaileys, ServidorFalso, ControlHumano, Engine],
) -> None:
    m, _, _, _ = mensajeria
    for n in range(520):
        m._secuencia._sidecar.enviar = lambda t, x, n=n: f"X{n}"  # type: ignore[method-assign,misc]  # noqa: SLF001
        m._enviar(TEL, "t", None)  # noqa: SLF001
    assert m.fue_emitido("X0") is False
    assert m.fue_emitido("X519") is True
