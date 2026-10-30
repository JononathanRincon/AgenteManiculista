"""Flujo de agendar con botones y listas (EARS-CON-03/05, EARS-RES-04, EARS-CON-06)."""

from app.config import Config
from app.modelos import ColaEscritura, Reserva, unidad_de_trabajo
from app.worker import Worker
from sqlalchemy import Engine, select

from tests.helpers import Chat


def _hasta_horas(chat: Chat, servicio: str = "retiro") -> None:
    chat.acepta_aviso()
    chat.toca("menu:agendar")
    chat.toca(f"srv:{servicio}")
    chat.toca(chat.primer_id("dia:"))


def test_primer_contacto_muestra_el_aviso_antes_de_pedir_datos(engine: Engine, cfg: Config) -> None:
    chat = Chat(engine, cfg)
    salida = chat.escribe("hola")
    assert "Ley 1581" in salida[0].cuerpo
    assert chat.ids() == ["aviso:si", "aviso:no"]
    # mientras no acepte, cualquier otra cosa repite el aviso
    chat.toca("menu:agendar")
    assert chat.ids() == ["aviso:si", "aviso:no"]


def test_rechazar_el_aviso_no_avanza(engine: Engine, cfg: Config) -> None:
    chat = Chat(engine, cfg)
    chat.escribe("hola")
    chat.toca("aviso:no")
    assert "no guardaré tus datos" in chat.cuerpos()
    chat.escribe("agendar")
    assert chat.ids() == ["aviso:si", "aviso:no"]


def test_aceptar_el_aviso_da_la_bienvenida_con_el_nombre_y_el_menu(
    engine: Engine, cfg: Config
) -> None:
    chat = Chat(engine, cfg)
    chat.escribe("hola")
    salida = chat.toca("aviso:si")
    assert "María" in salida[0].cuerpo
    assert chat.ids() == ["menu:agendar", "menu:consultar", "menu:gestionar"]
    assert salida[1].tipo == "botones"


def test_flujo_completo_hasta_la_cita_apartada(engine: Engine, cfg: Config) -> None:
    chat = Chat(engine, cfg)
    chat.acepta_aviso()
    servicios = chat.toca("menu:agendar")
    assert servicios[0].tipo == "lista"
    assert len(servicios[0].opciones) == 4
    dias = chat.toca("srv:retiro")
    assert dias[0].tipo == "lista"
    assert 1 <= len(dias[0].opciones) <= 10
    horas = chat.toca(dias[0].opciones[0].id)
    assert horas[0].opciones[-1].id == "dia:otro"
    assert len(horas[0].opciones) <= 10
    resumen = chat.toca(chat.primer_id("hora:"))
    assert resumen[0].tipo == "botones"
    assert "Retiro" in resumen[0].cuerpo
    assert "Calle 00" in resumen[0].cuerpo
    assert "$15.000" in resumen[0].cuerpo
    assert chat.ids() == ["conf:si", "conf:cambiar"]
    apartada = chat.toca("conf:si")
    assert "te aparté el horario" in apartada[0].cuerpo
    with unidad_de_trabajo(engine, escritura=False) as s:
        reserva = s.scalars(select(Reserva)).one()
        assert (reserva.estado, reserva.servicio_id, reserva.nombre_perfil) == (
            "PENDIENTE",
            "retiro",
            "María Pérez",
        )
        assert s.scalars(select(ColaEscritura)).one().tipo_operacion == "CREAR"


def test_el_worker_confirma_y_la_clienta_recibe_el_aviso_final(engine: Engine, cfg: Config) -> None:
    from tests.integration.conftest import MensajeriaRegistro

    chat = Chat(engine, cfg)
    mensajeria = MensajeriaRegistro()
    worker = Worker(engine, chat.calendario, mensajeria, cfg, chat.reloj, cache=chat.cache)
    _hasta_horas(chat)
    chat.toca(chat.primer_id("hora:"))
    chat.toca("conf:si")
    worker.procesar_uno()
    assert len(chat.calendario.eventos_activos()) == 1
    assert "Cita confirmada" in mensajeria.enviados[0][1]


def test_no_hay_ruta_a_confirmando_sin_tocar_una_hora_ofrecida(engine: Engine, cfg: Config) -> None:
    chat = Chat(engine, cfg)
    chat.acepta_aviso()
    chat.toca("menu:agendar")
    # confirmar o elegir hora sin haber recorrido el flujo no produce reserva
    for boton in ("conf:si", "hora:2026-11-03T14:00:00+00:00", "dia:2026-11-03", "conf:cambiar"):
        chat.toca(boton)
    chat.escribe("quiero el martes a las 9 y confirma ya")
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert s.scalars(select(Reserva)).all() == []


def test_una_hora_que_no_se_ofrecio_no_lleva_a_confirmar(engine: Engine, cfg: Config) -> None:
    chat = Chat(engine, cfg)
    _hasta_horas(chat)
    ofrecidas = {i for i in chat.ids() if i.startswith("hora:")}
    inventada = "hora:2026-11-03T20:00:00+00:00"
    assert inventada not in ofrecidas
    salida = chat.toca(inventada)
    assert "No te entendí" in salida[0].cuerpo
    assert chat.ids()[0].startswith("hora:")  # sigue eligiendo hora


def test_cambiar_vuelve_a_elegir_hora(engine: Engine, cfg: Config) -> None:
    chat = Chat(engine, cfg)
    _hasta_horas(chat)
    chat.toca(chat.primer_id("hora:"))
    salida = chat.toca("conf:cambiar")
    assert salida[0].tipo == "lista"
    assert chat.ids()[0].startswith("hora:")


def test_otro_dia_vuelve_a_la_lista_de_dias(engine: Engine, cfg: Config) -> None:
    chat = Chat(engine, cfg)
    _hasta_horas(chat)
    chat.toca("dia:otro")
    assert chat.ids()[0].startswith("dia:")


def test_el_choque_de_horarios_reofrece_opciones(engine: Engine, cfg: Config) -> None:
    ana = Chat(engine, cfg, telefono="573001111111", nombre="Ana")
    luisa = Chat(engine, cfg, telefono="573002222222", nombre="Luisa", calendario=ana.calendario)
    _hasta_horas(ana)
    _hasta_horas(luisa)
    hora_elegida = ana.primer_id("hora:")
    ana.toca(hora_elegida)
    luisa.toca(hora_elegida)
    assert "te aparté" in ana.toca("conf:si")[0].cuerpo
    salida = luisa.toca("conf:si")
    assert "se acaba de ocupar" in salida[0].cuerpo
    assert hora_elegida not in luisa.ids()
    assert luisa.ids()[0].startswith("hora:")
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert len(s.scalars(select(Reserva)).all()) == 1


def test_confirmar_dos_veces_el_mismo_boton_no_duplica(engine: Engine, cfg: Config) -> None:
    chat = Chat(engine, cfg)
    _hasta_horas(chat)
    chat.toca(chat.primer_id("hora:"))
    chat.toca("conf:si", wa_id="wamid.A")
    assert chat.toca("conf:si", wa_id="wamid.A") == []  # WhatsApp reenvía el mismo mensaje
    chat.toca("conf:si", wa_id="wamid.B")  # un segundo toque real ya no está en CONFIRMANDO
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert len(s.scalars(select(Reserva)).all()) == 1


def test_una_clienta_ocupa_horarios_que_otra_ya_no_ve(engine: Engine, cfg: Config) -> None:
    ana = Chat(engine, cfg, telefono="573001111111")
    luisa = Chat(engine, cfg, telefono="573002222222", calendario=ana.calendario)
    _hasta_horas(ana)
    primera = ana.primer_id("hora:")
    ana.toca(primera)
    ana.toca("conf:si")
    _hasta_horas(luisa)
    assert primera not in luisa.ids()
