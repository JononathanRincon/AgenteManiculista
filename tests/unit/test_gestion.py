"""Reagendar y cancelar desde la conversación; la clienta solo ve lo suyo (US-04, US-05)."""

from app.config import Config
from app.modelos import Reserva, unidad_de_trabajo
from app.worker import Worker
from sqlalchemy import Engine, select

from tests.helpers import Chat
from tests.integration.conftest import MensajeriaRegistro


def _agendar(chat: Chat, worker: Worker, indice_hora: int = 0) -> None:
    chat.toca("menu:agendar")
    chat.toca("srv:retiro")
    chat.toca(chat.primer_id("dia:"))
    horas = [i for i in chat.ids() if i.startswith("hora:")]
    chat.toca(horas[indice_hora])
    chat.toca("conf:si")
    worker.procesar_uno()


def _montar(
    engine: Engine, cfg: Config, telefono: str = "573001112233"
) -> tuple[Chat, Worker, MensajeriaRegistro]:
    chat = Chat(engine, cfg, telefono=telefono)
    mensajeria = MensajeriaRegistro()
    worker = Worker(engine, chat.calendario, mensajeria, cfg, chat.reloj, cache=chat.cache)
    chat.acepta_aviso()
    return chat, worker, mensajeria


def _estados(engine: Engine) -> list[str]:
    with unidad_de_trabajo(engine, escritura=False) as s:
        return [r.estado for r in s.scalars(select(Reserva).order_by(Reserva.id))]


def test_sin_citas_lo_dice_y_vuelve_al_menu(engine: Engine, cfg: Config) -> None:
    chat, _, _ = _montar(engine, cfg)
    salida = chat.toca("menu:gestionar")
    assert "No tienes citas próximas" in salida[0].cuerpo
    assert chat.ids() == ["menu:agendar", "menu:consultar", "menu:gestionar"]


def test_lista_solo_las_citas_de_la_clienta(engine: Engine, cfg: Config) -> None:
    ana, w, _ = _montar(engine, cfg, telefono="573001111111")
    luisa = Chat(engine, cfg, telefono="573002222222", calendario=ana.calendario)
    luisa.acepta_aviso()
    _agendar(ana, w, 0)
    _agendar(luisa, w, 3)
    assert len(ana.toca("menu:gestionar")[0].opciones) == 1
    assert len(luisa.toca("menu:gestionar")[0].opciones) == 1
    assert ana.ids() != luisa.ids()


def test_cancelar_con_confirmacion(engine: Engine, cfg: Config) -> None:
    chat, worker, _ = _montar(engine, cfg)
    _agendar(chat, worker)
    cita = chat.toca("menu:gestionar")[0].opciones[0].id
    acciones = chat.toca(cita)
    assert [o.id.split(":")[1] for o in acciones[0].opciones] == ["reagendar", "cancelar"]
    chat.toca(chat.primer_id("ger:cancelar"))
    assert "¿Seguro" in chat.cuerpos()
    salida = chat.toca(chat.primer_id("ger:si"))
    assert "cancelé tu cita" in salida[0].cuerpo
    assert _estados(engine) == ["CANCELANDO"]
    worker.procesar_uno()
    assert _estados(engine) == ["CANCELADA"]
    assert chat.calendario.eventos_activos() == []


def test_no_cancelar_vuelve_al_menu(engine: Engine, cfg: Config) -> None:
    chat, worker, _ = _montar(engine, cfg)
    _agendar(chat, worker)
    chat.toca(chat.toca("menu:gestionar")[0].opciones[0].id)
    chat.toca(chat.primer_id("ger:cancelar"))
    chat.toca("ger:no")
    assert chat.ids()[0] == "menu:agendar"
    assert _estados(engine) == ["CONFIRMADA"]


def test_reagendar_crea_la_nueva_y_elimina_la_anterior(engine: Engine, cfg: Config) -> None:
    chat, worker, mensajeria = _montar(engine, cfg)
    _agendar(chat, worker)
    original = chat.calendario.eventos_activos()[0].inicio
    chat.toca(chat.toca("menu:gestionar")[0].opciones[0].id)
    dias = chat.toca(chat.primer_id("ger:reagendar"))
    assert dias[0].tipo == "lista"
    chat.toca(chat.primer_id("dia:"))
    horas = [i for i in chat.ids() if i.startswith("hora:")]
    chat.toca(horas[5])
    assert chat.ids() == ["conf:si", "conf:cambiar"]
    assert "te aparté" in chat.toca("conf:si")[0].cuerpo
    worker.procesar_uno()
    assert _estados(engine) == ["CANCELADA", "CONFIRMADA"]
    activos = chat.calendario.eventos_activos()
    assert len(activos) == 1
    assert activos[0].inicio != original
    assert "Reagendada" in mensajeria.enviados[-1][1]


def test_otra_clienta_no_puede_tocar_citas_ajenas(engine: Engine, cfg: Config) -> None:
    ana, w, _ = _montar(engine, cfg, telefono="573001111111")
    _agendar(ana, w)
    with unidad_de_trabajo(engine, escritura=False) as s:
        ajena = s.scalars(select(Reserva)).one().id
    intrusa = Chat(engine, cfg, telefono="573009999999", calendario=ana.calendario)
    intrusa.acepta_aviso()
    intrusa.toca("menu:gestionar")  # sin citas propias
    botones = [f"cita:{ajena}", *(f"ger:{a}:{ajena}" for a in ("si", "cancelar", "reagendar"))]
    for boton in botones:
        salida = intrusa.toca(boton)
        assert "No te entendí" in salida[0].cuerpo
    assert _estados(engine) == ["CONFIRMADA"]


def test_con_el_maximo_de_citas_ofrece_reagendar_o_cancelar(engine: Engine, cfg: Config) -> None:
    chat, worker, _ = _montar(engine, cfg)
    _agendar(chat, worker, 0)
    _agendar(chat, worker, 4)
    salida = chat.toca("menu:agendar")
    assert "Ya tienes 2 citas próximas" in salida[0].cuerpo
    assert salida[1].tipo == "lista"
    assert chat.ids()[0].startswith("cita:")


def test_boton_viejo_de_gestion_fuera_de_estado_se_ignora(engine: Engine, cfg: Config) -> None:
    chat, worker, _ = _montar(engine, cfg)
    _agendar(chat, worker)
    with unidad_de_trabajo(engine, escritura=False) as s:
        rid = s.scalars(select(Reserva)).one().id
    salida = chat.toca(f"ger:si:{rid}")  # sin haber pasado por "mis citas"
    assert "Hola" in salida[0].cuerpo  # la conversación ya terminó: vuelve a empezar
    assert _estados(engine) == ["CONFIRMADA"]
