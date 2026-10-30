"""Aviso de datos, deduplicación y mensajes no textuales (EARS-CON-06, EARS-WA-04, EC-03, EC-18)."""

from app.config import Config
from app.modelos import Conversacion, MensajeProcesado, Reserva, unidad_de_trabajo
from sqlalchemy import Engine, select

from tests.helpers import Chat


def test_el_mismo_mensaje_dos_veces_produce_un_solo_efecto(engine: Engine, cfg: Config) -> None:
    chat = Chat(engine, cfg)
    primera = chat.escribe("hola", wa_id="wamid.X")
    segunda = chat.escribe("hola", wa_id="wamid.X")
    assert primera != []
    assert segunda == []
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert len(s.scalars(select(MensajeProcesado)).all()) == 1


def test_ids_distintos_se_procesan_los_dos(engine: Engine, cfg: Config) -> None:
    chat = Chat(engine, cfg)
    assert chat.escribe("hola", wa_id="wamid.A") != []
    assert chat.escribe("hola", wa_id="wamid.B") != []


def test_repetir_el_toque_de_aceptar_no_duplica_la_bienvenida(engine: Engine, cfg: Config) -> None:
    chat = Chat(engine, cfg)
    chat.escribe("hola")
    assert chat.toca("aviso:si", wa_id="wamid.OK") != []
    assert chat.toca("aviso:si", wa_id="wamid.OK") == []


def test_no_se_piden_ni_ofrecen_datos_antes_del_aviso(engine: Engine, cfg: Config) -> None:
    chat = Chat(engine, cfg)
    for entrada in ("menu:agendar", "srv:retiro", "mod:domicilio", "conf:si", "cita:1"):
        salida = chat.toca(entrada)
        assert chat.ids() == ["aviso:si", "aviso:no"], entrada
        assert "Ley 1581" in salida[0].cuerpo
    for texto in ("quiero agendar", "mi dirección es Calle 1", "573001112233"):
        chat.escribe(texto)
        assert chat.ids() == ["aviso:si", "aviso:no"]
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert s.scalars(select(Reserva)).all() == []
        fila = s.scalars(select(Conversacion)).one()
        assert fila.aviso_datos_aceptado is False


def test_el_aviso_se_acepta_por_telefono(engine: Engine, cfg: Config) -> None:
    ana = Chat(engine, cfg, telefono="573001111111")
    ana.acepta_aviso()
    luisa = Chat(engine, cfg, telefono="573002222222", calendario=ana.calendario)
    luisa.escribe("hola")
    assert luisa.ids() == ["aviso:si", "aviso:no"]


def test_audio_o_imagen_responde_con_el_menu(engine: Engine, cfg: Config) -> None:
    chat = Chat(engine, cfg)
    chat.acepta_aviso()
    salida = chat.envia_audio()
    assert "solo entiendo mensajes de texto" in salida[0].cuerpo
    assert chat.ids() == ["menu:agendar", "menu:consultar", "menu:gestionar"]


def test_audio_antes_del_aviso_muestra_el_aviso(engine: Engine, cfg: Config) -> None:
    chat = Chat(engine, cfg)
    chat.envia_audio()
    assert chat.ids() == ["aviso:si", "aviso:no"]


def test_tras_30_minutos_sin_actividad_se_reinicia_pero_no_vuelve_el_aviso(
    engine: Engine, cfg: Config
) -> None:
    chat = Chat(engine, cfg)
    chat.acepta_aviso()
    chat.toca("menu:agendar")
    chat.toca("srv:retiro")
    chat.toca(chat.primer_id("dia:"))
    ofrecida = chat.primer_id("hora:")
    chat.avanza(minutes=31)
    salida = chat.toca(ofrecida)  # botón viejo: la conversación ya se reinició
    assert "Hola" in salida[0].cuerpo
    assert chat.ids()[0] == "menu:agendar"
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert s.scalars(select(Reserva)).all() == []


def test_dentro_de_los_30_minutos_la_conversacion_continua(engine: Engine, cfg: Config) -> None:
    chat = Chat(engine, cfg)
    chat.acepta_aviso()
    chat.toca("menu:agendar")
    chat.avanza(minutes=29)
    chat.toca("srv:retiro")
    assert chat.ids()[0].startswith("dia:")
