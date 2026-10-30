"""Orden determinista: FAQ y palabras clave, luego LLM, luego menú (EARS-INT-01..07)."""

from datetime import date, datetime

import pytest
from app.config import Config
from app.intencion import Intencion
from app.llm.base import ServicioResumen
from app.metricas import MENSAJES_SIN_LLM, MENSAJES_TOTAL, leer, porcentaje_sin_llm
from app.modelos import Reserva, unidad_de_trabajo
from app.reservas import SolicitudReserva, reservar
from sqlalchemy import Engine, select

from tests.helpers import Chat
from tests.integration.conftest import AHORA, con_domicilio, local

RARA = "frase rara sin palabras clave"


class ProveedorFalso:
    """Devuelve lo configurado; cuenta llamadas y guarda lo recibido."""

    def __init__(self, respuesta: Intencion | None | Exception) -> None:
        self.respuesta = respuesta
        self.llamadas: list[tuple[str, str]] = []

    def interpretar(
        self, texto: str, catalogo: list[ServicioResumen], hoy: date, estado: str
    ) -> Intencion | None:
        self.llamadas.append((texto, estado))
        if isinstance(self.respuesta, Exception):
            raise self.respuesta
        return self.respuesta


def _i(intencion: str = "agendar", confianza: float = 0.9, **extra: object) -> Intencion:
    return Intencion.model_validate(
        {"intencion": intencion, "confianza": confianza, **extra},
        context={"catalogo": frozenset({"retiro", "acrilicas"}), "hoy": date(2026, 11, 2)},
    )


def _chat(
    engine: Engine, cfg: Config, respuesta: Intencion | None | Exception
) -> tuple[Chat, ProveedorFalso]:
    proveedor = ProveedorFalso(respuesta)
    chat = Chat(engine, cfg, proveedor=proveedor)
    chat.acepta_aviso()
    return chat, proveedor


def test_las_palabras_clave_ganan_y_el_llm_no_se_invoca(engine: Engine, cfg: Config) -> None:
    chat, proveedor = _chat(engine, cfg, _i("cancelar"))
    chat.escribe("cuánto vale el retiro")
    assert proveedor.llamadas == []


def test_confianza_069_cae_al_menu_y_070_pasa(engine: Engine, cfg: Config) -> None:
    chat, proveedor = _chat(engine, cfg, _i("consultar_precio", 0.69))
    salida = chat.escribe(RARA)
    assert "No te entendí" in salida[0].cuerpo
    assert chat.ids()[0] == "menu:agendar"
    proveedor.respuesta = _i("consultar_precio", 0.70)
    assert "$30.000" in chat.escribe("otra frase rara")[0].cuerpo


@pytest.mark.parametrize("respuesta", [None, RuntimeError("roto"), _i("desconocida", 0.95)])
def test_fallos_y_desconocida_caen_al_menu_sin_mensaje_de_error(
    engine: Engine, cfg: Config, respuesta: Intencion | None | Exception
) -> None:
    chat, _ = _chat(engine, cfg, respuesta)
    salida = chat.escribe(RARA)
    assert "No te entendí" in salida[0].cuerpo
    assert "problema técnico" not in chat.cuerpos()
    assert chat.ids()[0] == "menu:agendar"


def test_el_llm_recibe_el_texto_y_el_estado_del_flujo(engine: Engine, cfg: Config) -> None:
    chat, proveedor = _chat(engine, cfg, None)
    chat.escribe(RARA)
    assert proveedor.llamadas == [(RARA, "MENU")]


def test_agendar_con_servicio_fecha_y_franja_salta_a_elegir_hora_sin_confirmar(
    engine: Engine, cfg: Config
) -> None:
    intencion = _i(servicio_id="retiro", fecha_tentativa="2026-11-04", franja="tarde")
    chat, _ = _chat(engine, cfg, intencion)
    salida = chat.escribe("quisiera el retiro el miércoles en la tarde")
    assert salida[0].tipo == "lista"
    horas = [i for i in chat.ids() if i.startswith("hora:")]
    assert horas
    for hora_id in horas:
        inicio = datetime.fromisoformat(hora_id.split(":", 1)[1]).astimezone(cfg.negocio.zona)
        assert 12 <= inicio.hour < 18
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert s.scalars(select(Reserva)).all() == []  # nada se reservó
    assert "No te entendí" in chat.toca("conf:si")[0].cuerpo  # sin hora ofrecida no se confirma
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert s.scalars(select(Reserva)).all() == []
    chat.toca(horas[0])
    assert chat.ids() == ["conf:si", "conf:cambiar"]


def test_franja_sin_horarios_ofrece_todos_los_del_dia(engine: Engine, cfg: Config) -> None:
    intencion = _i(servicio_id="retiro", fecha_tentativa="2026-11-07", franja="noche")
    chat, _ = _chat(engine, cfg, intencion)
    chat.escribe("el sábado en la noche")  # el sábado cierra a las 2 p. m.
    assert [i for i in chat.ids() if i.startswith("hora:")]


def test_agendar_solo_con_servicio_ofrece_los_dias(engine: Engine, cfg: Config) -> None:
    chat, _ = _chat(engine, cfg, _i(servicio_id="retiro"))
    assert chat.escribe("quiero un retiro")[0].tipo == "lista"
    assert chat.ids()[0].startswith("dia:")


def test_agendar_sin_servicio_empieza_el_flujo_normal(engine: Engine, cfg: Config) -> None:
    chat, _ = _chat(engine, cfg, _i())
    chat.escribe("necesito ponerme bonita")
    assert chat.ids()[0].startswith("srv:")


def test_con_domicilio_el_servicio_entendido_lleva_a_elegir_modalidad(
    engine: Engine, cfg: Config
) -> None:
    intencion = _i(servicio_id="retiro", fecha_tentativa="2026-11-04")
    chat, _ = _chat(engine, con_domicilio(cfg), intencion)
    chat.escribe("un retiro el miércoles")
    assert chat.ids() == ["mod:local", "mod:domicilio"]


def test_fecha_sin_horarios_ofrece_otros_dias(engine: Engine, cfg: Config) -> None:
    chat, _ = _chat(engine, cfg, _i(servicio_id="retiro", fecha_tentativa="2026-11-08"))
    salida = chat.escribe("un retiro el domingo")
    assert "Ya no quedan horarios" in salida[0].cuerpo
    assert chat.ids()[0].startswith("dia:")


def test_con_el_maximo_de_citas_el_llm_tambien_ofrece_gestionar(
    engine: Engine, cfg: Config
) -> None:
    chat, _ = _chat(engine, cfg, _i(servicio_id="retiro", fecha_tentativa="2026-11-04"))
    with unidad_de_trabajo(engine) as s:
        for hora in (8, 10):
            solicitud = SolicitudReserva(chat.telefono, "X", "retiro", local(4, hora))
            reservar(s, solicitud, cfg, AHORA)
    assert "Ya tienes 2 citas próximas" in chat.escribe("un retiro el miércoles")[0].cuerpo


@pytest.mark.parametrize(
    ("intencion", "esperado"),
    [
        ("consultar_horario", "Mi horario"),
        ("consultar_ubicacion", "Calle 00"),
        ("saludo", "Hola"),
        ("cancelar", "No tienes citas"),
        ("reagendar", "No tienes citas"),
    ],
)
def test_otras_intenciones_del_llm_reutilizan_las_respuestas_fijas(
    engine: Engine, cfg: Config, intencion: str, esperado: str
) -> None:
    chat, _ = _chat(engine, cfg, _i(intencion))
    assert esperado in chat.escribe(RARA)[0].cuerpo


def test_porcentaje_de_mensajes_sin_llm(engine: Engine, cfg: Config) -> None:
    chat, proveedor = _chat(engine, cfg, _i("saludo"))  # "hola" y el aviso: 2 mensajes sin LLM
    chat.escribe("cuánto vale")  # palabras clave: sin LLM
    chat.escribe("frase rara uno")  # el LLM aporta la interpretación
    chat.toca("menu:consultar")  # botón: sin LLM
    proveedor.respuesta = None
    chat.escribe("frase rara dos")  # el LLM no aporta nada: cuenta como sin LLM
    with unidad_de_trabajo(engine, escritura=False) as s:
        total, sin_llm = leer(s, MENSAJES_TOTAL), leer(s, MENSAJES_SIN_LLM)
        assert (total, sin_llm) == (6, 5)
        assert porcentaje_sin_llm(s) == pytest.approx(100 * 5 / 6)


def test_porcentaje_sin_mensajes_es_cero(engine: Engine) -> None:
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert porcentaje_sin_llm(s) == 0.0


def test_tope_de_consultas_al_llm_por_clienta_y_por_dia(engine: Engine, cfg: Config) -> None:
    topes = {"llm_max_por_telefono_hora": 2, "llm_max_global_dia": 3}
    negocio = cfg.negocio.model_copy(update=topes)
    config = cfg.model_copy(update={"negocio": negocio})
    ana, proveedor = _chat(engine, config, None)
    for i in range(5):
        ana.escribe(f"frase rara {i}")
    assert len(proveedor.llamadas) == 2  # el tope por clienta corta las demás: van al menú
    assert "No te entendí" in ana.escribe("otra frase rara")[0].cuerpo
    ana.avanza(hours=1, minutes=1)  # pasa la hora: vuelve a tener cupo
    ana.escribe("frase rara tras la hora")
    assert len(proveedor.llamadas) == 3
    luisa = Chat(engine, config, telefono="573009998877", proveedor=proveedor, reloj=ana.reloj)
    luisa.acepta_aviso()
    luisa.escribe("frase rara de luisa")
    assert len(proveedor.llamadas) == 3  # el tope global diario (3) ya se alcanzó
    ana.avanza(days=1, minutes=1)
    ana.escribe("frase rara al día siguiente")
    assert len(proveedor.llamadas) == 4


def test_la_limpieza_borra_las_consultas_viejas(engine: Engine, cfg: Config) -> None:
    from app.mensajes import configurar
    from app.modelos import LlamadaLLM
    from app.worker import Worker

    from tests.integration.conftest import MensajeriaRegistro

    chat, _ = _chat(engine, cfg, None)
    chat.escribe(RARA)
    worker = Worker(engine, chat.calendario, MensajeriaRegistro(), cfg, chat.reloj)
    configurar(None)
    chat.avanza(days=3)
    worker.limpiar()
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert s.scalars(select(LlamadaLLM)).all() == []
