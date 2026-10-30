"""Ramas de borde del agente: botones viejos, estados desfasados y errores al confirmar."""

from datetime import date, datetime, time, timedelta

import pytest
from app.config import Config, Horario
from app.modelos import Reserva, unidad_de_trabajo
from app.reservas import SolicitudReserva, reagendar, reservar
from app.worker import Worker
from sqlalchemy import Engine, select

from tests.helpers import Chat
from tests.integration.conftest import AHORA, MensajeriaRegistro, con_domicilio, local


def _hasta(chat: Chat, paso: str, servicio: str = "retiro") -> None:
    """Lleva la conversación hasta el paso indicado."""
    chat.acepta_aviso()
    if paso == "menu":
        return
    chat.toca("menu:agendar")
    if paso == "servicio":
        return
    chat.toca(f"srv:{servicio}")
    if paso == "modalidad":
        return
    if chat.ids()[0].startswith("mod:"):
        chat.toca("mod:local")
    if paso in ("dia", "hora", "confirmando"):
        if paso == "dia":
            return
        chat.toca(chat.primer_id("dia:"))
        if paso == "hora":
            return
        chat.toca(chat.primer_id("hora:"))


def _con_cita(chat: Chat, cfg: Config) -> Worker:
    worker = Worker(
        chat.engine, chat.calendario, MensajeriaRegistro(), cfg, chat.reloj, cache=chat.cache
    )
    _hasta(chat, "confirmando")
    chat.toca("conf:si")
    worker.procesar_uno()
    return worker


@pytest.mark.parametrize("boton", ["zzz:1", "menu:otro", "hora", ""])
def test_botones_desconocidos_se_reofrecen(engine: Engine, cfg: Config, boton: str) -> None:
    chat = Chat(engine, cfg)
    chat.acepta_aviso()
    assert "No te entendí" in chat.toca(boton)[0].cuerpo


@pytest.mark.parametrize(
    ("estado_previo", "boton"),
    [
        ("menu", "srv:retiro"),
        ("servicio", "srv:no_existe"),
        ("servicio", "mod:local"),
        ("menu", "mod:domicilio"),
        ("menu", "dia:2026-11-03"),
        ("menu", "hora:2026-11-03T14:00:00+00:00"),
        ("menu", "conf:si"),
        ("menu", "cita:1"),
        ("menu", "ger:si:1"),
    ],
)
def test_botones_fuera_de_estado_se_ignoran(
    engine: Engine, cfg: Config, estado_previo: str, boton: str
) -> None:
    chat = Chat(engine, con_domicilio(cfg))
    _hasta(chat, estado_previo)
    assert "No te entendí" in chat.toca(boton)[0].cuerpo


@pytest.mark.parametrize(
    ("paso", "esperado"),
    [
        ("servicio", "srv:"),
        ("modalidad", "mod:"),
        ("dia", "dia:"),
        ("hora", "hora:"),
        ("confirmando", "conf:si"),
    ],
)
def test_texto_libre_reofrece_la_pantalla_actual(
    engine: Engine, cfg: Config, paso: str, esperado: str
) -> None:
    chat = Chat(engine, con_domicilio(cfg))
    _hasta(chat, paso)
    salida = chat.escribe("asdf qwer")
    assert "No te entendí" in salida[0].cuerpo
    assert chat.ids()[0].startswith(esperado)


def test_texto_libre_en_mis_citas_reofrece_la_lista(engine: Engine, cfg: Config) -> None:
    chat = Chat(engine, cfg)
    _con_cita(chat, cfg)
    chat.toca("menu:gestionar")
    chat.escribe("asdf qwer")
    assert chat.ids()[0].startswith("cita:")


def test_texto_libre_en_el_menu_reofrece_el_menu(engine: Engine, cfg: Config) -> None:
    chat = Chat(engine, cfg)
    chat.acepta_aviso()
    assert "No te entendí" in chat.escribe("asdf qwer")[0].cuerpo
    assert chat.ids()[0] == "menu:agendar"


def test_sin_dias_con_cupo_vuelve_al_menu(engine: Engine, cfg: Config) -> None:
    cerrado = cfg.negocio.model_copy(update={"horario": {d: None for d in cfg.negocio.horario}})
    chat = Chat(engine, cfg.model_copy(update={"negocio": cerrado}))
    _hasta(chat, "dia")
    assert "No tengo horarios libres" in chat.cuerpos()
    assert chat.ids()[0] == "menu:agendar"


def test_pocos_dias_en_la_ventana_se_listan_completos(engine: Engine, cfg: Config) -> None:
    corto = cfg.negocio.model_copy(update={"ventana_maxima_dias": 3})
    chat = Chat(engine, cfg.model_copy(update={"negocio": corto}))
    _hasta(chat, "dia")
    assert 1 <= len(chat.ultimo[0].opciones) <= 4


def test_si_el_dia_se_llena_mientras_elige_se_ofrecen_otros_dias(
    engine: Engine, cfg: Config
) -> None:
    chat = Chat(engine, cfg)
    _hasta(chat, "dia")
    dia = chat.primer_id("dia:")
    fecha = date.fromisoformat(dia.split(":", 1)[1])
    zona = cfg.negocio.zona
    desde = datetime.combine(fecha, time(6, 0), tzinfo=zona)
    hasta = datetime.combine(fecha, time(20, 0), tzinfo=zona)
    chat.calendario.agregar_evento_manual(desde, hasta)
    chat.avanza(seconds=61)  # vence el caché
    salida = chat.toca(dia)
    assert "Ya no quedan horarios" in salida[0].cuerpo
    assert chat.ids()[0].startswith("dia:")
    assert dia not in chat.ids()


def test_limite_de_citas_alcanzado_mientras_confirma(engine: Engine, cfg: Config) -> None:
    chat = Chat(engine, cfg)
    _hasta(chat, "confirmando")
    with unidad_de_trabajo(engine) as s:
        for hora in (14, 16):
            solicitud = SolicitudReserva(chat.telefono, "X", "retiro", local(4, hora))
            reservar(s, solicitud, cfg, AHORA)
    salida = chat.toca("conf:si")
    assert "Ya tienes 2 citas próximas" in salida[0].cuerpo
    assert chat.ids()[0].startswith("cita:")


def test_si_la_hora_deja_de_ser_valida_al_confirmar_se_pide_empezar_de_nuevo(
    engine: Engine, cfg: Config
) -> None:
    chat = Chat(engine, cfg)
    _hasta(chat, "confirmando")
    chat.avanza(minutes=25)  # la primera hora (9:00) queda dentro de las 2 h de anticipación
    salida = chat.toca("conf:si")
    assert "problema técnico" in salida[0].cuerpo
    assert chat.ids()[0] == "menu:agendar"
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert s.scalars(select(Reserva)).all() == []


def test_ids_de_cita_invalidos_o_ajenos(engine: Engine, cfg: Config) -> None:
    chat = Chat(engine, cfg)
    _con_cita(chat, cfg)
    chat.toca("menu:gestionar")
    for boton in ("cita:9999", "cita:abc", "ger:cancelar:9999", "ger:cancelar:abc"):
        assert "No te entendí" in chat.toca(boton)[0].cuerpo
    cita = chat.toca("menu:gestionar")[0].opciones[0].id.split(":")[1]
    assert "No te entendí" in chat.toca(f"ger:inventada:{cita}")[0].cuerpo


def test_cancelar_una_cita_con_reagendamiento_en_curso_muestra_el_error(
    engine: Engine, cfg: Config
) -> None:
    chat = Chat(engine, cfg)
    _con_cita(chat, cfg)
    with unidad_de_trabajo(engine) as s:
        vieja = s.scalars(select(Reserva)).one()
        reagendar(s, vieja.id, chat.telefono, vieja.inicio + timedelta(hours=3), cfg, AHORA)
    chat.toca("menu:gestionar")
    chat.toca(chat.ids()[0])
    chat.toca(chat.primer_id("ger:cancelar"))
    salida = chat.toca(chat.primer_id("ger:si"))
    assert "problema técnico" in salida[0].cuerpo
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert s.get(Reserva, vieja.id).estado == "CONFIRMADA"  # type: ignore[union-attr]


def test_horario_de_un_solo_dia_no_rompe_la_lista(engine: Engine, cfg: Config) -> None:
    solo_martes: dict[str, Horario | None] = {d: None for d in cfg.negocio.horario}
    solo_martes["martes"] = Horario(abre=time(8), cierra=time(12))
    negocio = cfg.negocio.model_copy(update={"horario": solo_martes})
    chat = Chat(engine, cfg.model_copy(update={"negocio": negocio}))
    _hasta(chat, "dia")
    assert all(i.startswith("dia:") for i in chat.ids())


def test_tocar_un_boton_viejo_mientras_se_espera_la_direccion_repite_la_pregunta(
    engine: Engine, cfg: Config
) -> None:
    chat = Chat(engine, con_domicilio(cfg))
    _hasta(chat, "modalidad")
    chat.toca("mod:domicilio")
    salida = chat.toca("zzz:1")
    assert "No te entendí" in salida[0].cuerpo
    assert "dirección" in salida[1].cuerpo
