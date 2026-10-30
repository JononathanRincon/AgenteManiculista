"""Menús numerados: degradar botones y listas y mapear la respuesta (EARS-WA-10, EC-24)."""

import pytest
from app.config import Config
from app.conversacion import Saliente
from app.menus_numericos import (
    componer,
    degradar,
    guardar_menu,
    mapear_respuesta,
    resolver_entrada,
)
from app.modelos import OpcionesOfrecidas, Reserva, unidad_de_trabajo
from app.puertos import Opcion
from sqlalchemy import Engine, select

from tests.helpers import Chat
from tests.integration.conftest import AHORA

TEL = "573001112233"
SERVICIOS = [
    Opcion("srv:manicure_tradicional", "Manicure tradicional", "45 min · $30.000"),
    Opcion("srv:semipermanente", "Semipermanente", "75 min · $50.000"),
    Opcion("srv:retiro", "Retiro", "30 min · $15.000"),
]


@pytest.fixture(autouse=True)
def _catalogo() -> None:
    from app.mensajes import configurar

    configurar(None)


def test_degradar_lista_a_un_solo_texto_numerado() -> None:
    mensaje = degradar(Saliente("lista", "¿Qué servicio quieres?", SERVICIOS, "Ver servicios"))
    lineas = mensaje.cuerpo.splitlines()
    assert lineas[0] == "¿Qué servicio quieres?"
    assert lineas[2] == "1️⃣ Manicure tradicional — 45 min · $30.000"
    assert lineas[3].startswith("2️⃣ Semipermanente")
    assert lineas[4].startswith("3️⃣ Retiro")
    assert "Responde con el número o el nombre" in lineas[-1]
    assert [o.id for o in mensaje.opciones] == [o.id for o in SERVICIOS]


def test_degradar_botones_y_texto_sin_opciones() -> None:
    botones = degradar(Saliente("botones", "¿Confirmas?", [Opcion("conf:si", "Confirmar")]))
    assert "1️⃣ Confirmar" in botones.cuerpo
    plano = degradar(Saliente("texto", "Hola"))
    assert (plano.cuerpo, plano.opciones) == ("Hola", [])


def test_mas_de_nueve_opciones_usan_dos_emojis() -> None:
    opciones = [Opcion(f"hora:{i}", f"Hora {i}") for i in range(10)]
    texto = degradar(Saliente("lista", "Horas", opciones)).cuerpo
    assert "1️⃣0️⃣ Hora 9" in texto


def test_componer_une_toda_la_respuesta_en_un_solo_mensaje() -> None:
    salidas = [
        Saliente("texto", "Estos son mis servicios"),
        Saliente("botones", "Elige una opción:", [Opcion("menu:agendar", "Agendar cita")]),
    ]
    mensaje = componer(salidas)
    assert mensaje is not None
    assert mensaje.cuerpo.startswith("Estos son mis servicios\n\nElige una opción:")
    assert [o.id for o in mensaje.opciones] == ["menu:agendar"]
    assert componer([]) is None
    assert componer([Saliente("texto", "solo texto")]).opciones == []  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("escrito", "esperado"),
    [
        ("1", "srv:manicure_tradicional"),
        ("2", "srv:semipermanente"),
        (" 3 ", "srv:retiro"),
        ("3.", "srv:retiro"),
        ("opción 2", "srv:semipermanente"),
        ("la 1", "srv:manicure_tradicional"),
        ("el número 3", "srv:retiro"),
        ("Retiro", "srv:retiro"),
        ("semipermanente", "srv:semipermanente"),
        ("SEMIPERMANENTE!", "srv:semipermanente"),
        ("manicure", "srv:manicure_tradicional"),  # parcial y único
    ],
)
def test_mapea_numero_nombre_y_variantes(escrito: str, esperado: str) -> None:
    assert mapear_respuesta(escrito, SERVICIOS) == esperado


@pytest.mark.parametrize("escrito", ["0", "4", "99", "", "hola", "ok", "me", "un cafe", "e", "10"])
def test_lo_que_no_es_una_opcion_ofrecida_no_se_mapea(escrito: str) -> None:
    assert mapear_respuesta(escrito, SERVICIOS) is None


def test_un_nombre_ambiguo_no_se_mapea() -> None:
    opciones = [Opcion("a", "Manicure rápido"), Opcion("b", "Manicure largo")]
    assert mapear_respuesta("manicure", opciones) is None
    assert mapear_respuesta("2", opciones) == "b"


def test_horas_se_reconocen_con_o_sin_formato() -> None:
    horas = [
        Opcion("hora:1", "9:00 a. m."),
        Opcion("hora:2", "9:30 a. m."),
        Opcion("dia:otro", "Otro día"),
    ]
    assert mapear_respuesta("9:30 am", horas) == "hora:2"
    assert mapear_respuesta("9:00am", horas) == "hora:1"
    assert mapear_respuesta("otro día", horas) == "dia:otro"
    assert mapear_respuesta("10:00 am", horas) is None


def test_sin_menu_ofrecido_no_hay_mapeo(engine: Engine) -> None:
    assert mapear_respuesta("1", []) is None
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert resolver_entrada(s, TEL, "1", AHORA, 30) is None


def test_el_menu_se_guarda_se_reemplaza_y_vence(engine: Engine) -> None:
    from datetime import timedelta

    with unidad_de_trabajo(engine) as s:
        guardar_menu(s, TEL, SERVICIOS, AHORA)
        guardar_menu(s, TEL, SERVICIOS[:2], AHORA)  # el último menú reemplaza al anterior
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert len(s.scalars(select(OpcionesOfrecidas)).all()) == 1
        assert resolver_entrada(s, TEL, "2", AHORA, 30) == "srv:semipermanente"
        assert resolver_entrada(s, TEL, "3", AHORA, 30) is None  # el menú nuevo solo tiene 2
        assert resolver_entrada(s, TEL, "2", AHORA + timedelta(minutes=31), 30) is None  # vencido
    with unidad_de_trabajo(engine) as s:
        guardar_menu(s, TEL, [], AHORA)  # un mensaje sin opciones borra el menú vigente
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert resolver_entrada(s, TEL, "1", AHORA, 30) is None


def _responder(chat: Chat, escrito: str) -> None:
    """Simula el transporte: traduce número o nombre al id del último menú y llama al agente."""
    with unidad_de_trabajo(chat.engine) as s:
        id_ = resolver_entrada(s, chat.telefono, escrito, chat.reloj.ahora(), 30)
    if id_ is None:
        chat.escribe(escrito)
    else:
        chat.toca(id_)
    mensaje = componer(chat.ultimo)
    with unidad_de_trabajo(chat.engine) as s:
        guardar_menu(s, chat.telefono, mensaje.opciones if mensaje else [], chat.reloj.ahora())


def test_flujo_completo_por_numeros_hasta_apartar_la_cita(engine: Engine, cfg: Config) -> None:
    chat = Chat(engine, cfg)
    for escrito in ("hola", "1", "1", "2", "1", "1", "1"):
        # aviso, menú, agendar, servicio 2 (semipermanente), primer día, primera hora, confirmar
        _responder(chat, escrito)
    with unidad_de_trabajo(engine, escritura=False) as s:
        reserva = s.scalars(select(Reserva)).one()
        assert reserva.servicio_id == "semipermanente"
        assert reserva.estado == "PENDIENTE"


def test_un_numero_fuera_de_rango_no_cambia_el_estado_ni_confirma(
    engine: Engine, cfg: Config
) -> None:
    chat = Chat(engine, cfg)
    for escrito in ("hola", "1", "1", "2", "1"):  # llega a la lista de horas
        _responder(chat, escrito)
    assert chat.ids()[0].startswith("hora:")
    _responder(chat, "99")  # no es una opción ofrecida: texto libre
    assert "No te entendí" in chat.cuerpos()
    assert chat.ids()[0].startswith("hora:")  # sigue eligiendo hora
    _responder(chat, "confirmar")  # tampoco se puede confirmar sin haber elegido una hora
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert s.scalars(select(Reserva)).all() == []


def test_el_nombre_de_la_hora_tambien_lleva_a_confirmar(engine: Engine, cfg: Config) -> None:
    chat = Chat(engine, cfg)
    for escrito in ("hola", "1", "1", "3", "1"):
        _responder(chat, escrito)
    primera = chat.ultimo[0].opciones[0].titulo
    _responder(chat, primera)
    assert chat.ids() == ["conf:si", "conf:cambiar"]
