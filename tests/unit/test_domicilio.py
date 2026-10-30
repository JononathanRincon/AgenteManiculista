"""Modalidad domicilio con dirección, zona, recargo y total (EARS-CON-04, EARS-DOM-01..04)."""

from app.config import Config
from app.modelos import Reserva, ReservaBloque, unidad_de_trabajo
from sqlalchemy import Engine, select

from tests.helpers import Chat
from tests.integration.conftest import con_domicilio


def _listo(chat: Chat, servicio: str = "retiro") -> None:
    chat.acepta_aviso()
    chat.toca("menu:agendar")
    chat.toca(f"srv:{servicio}")


def test_apagado_el_flujo_no_menciona_domicilio(engine: Engine, cfg: Config) -> None:
    chat = Chat(engine, cfg)
    _listo(chat)
    assert chat.ids()[0].startswith("dia:")  # directo a los días, sin preguntar modalidad
    chat.toca(chat.primer_id("dia:"))
    chat.toca(chat.primer_id("hora:"))
    assert "domicilio" not in chat.cuerpos().lower()
    chat.toca("menu:consultar")
    chat.toca("menu:consultar")
    assert "domicilio" not in chat.cuerpos().lower()
    chat.escribe("hola")
    assert "domicilio" not in chat.cuerpos().lower()


def test_encendido_pregunta_la_modalidad_solo_si_el_servicio_lo_permite(
    engine: Engine, cfg: Config
) -> None:
    chat = Chat(engine, con_domicilio(cfg))
    _listo(chat, "retiro")
    assert chat.ids() == ["mod:local", "mod:domicilio"]
    chat2 = Chat(engine, con_domicilio(cfg), telefono="573009990000")
    _listo(chat2, "acrilicas")  # el servicio no permite domicilio (EC-17)
    assert chat2.ids()[0].startswith("dia:")


def test_domicilio_pide_direccion_y_muestra_servicio_recargo_y_total(
    engine: Engine, cfg: Config
) -> None:
    chat = Chat(engine, con_domicilio(cfg, recargo=8000))
    _listo(chat)
    assert "dirección" in chat.toca("mod:domicilio")[0].cuerpo
    dias = chat.escribe("Cra 7 # 45-10, barrio Chapinero")
    assert dias[0].tipo == "lista"
    chat.toca(chat.primer_id("dia:"))
    resumen = chat.toca(chat.primer_id("hora:"))[0].cuerpo
    assert "Cra 7 # 45-10, barrio Chapinero" in resumen
    assert "$15.000" in resumen
    assert "$8.000" in resumen
    assert "$23.000" in resumen


def test_reserva_a_domicilio_guarda_recargo_direccion_y_suma_el_desplazamiento(
    engine: Engine, cfg: Config
) -> None:
    chat = Chat(engine, con_domicilio(cfg, recargo=8000))
    _listo(chat)
    chat.toca("mod:domicilio")
    chat.escribe("Calle 10 # 5-5")
    chat.toca(chat.primer_id("dia:"))
    chat.toca(chat.primer_id("hora:"))
    chat.toca("conf:si")
    with unidad_de_trabajo(engine, escritura=False) as s:
        r = s.scalars(select(Reserva)).one()
        assert (r.modalidad, r.recargo_cop, r.direccion) == ("domicilio", 8000, "Calle 10 # 5-5")
        assert len(s.scalars(select(ReservaBloque)).all()) == 3  # 30 + 15 + 30 min


def test_direccion_fuera_de_la_zona_se_rechaza_con_amabilidad(engine: Engine, cfg: Config) -> None:
    base = con_domicilio(cfg)
    dom = base.negocio.domicilio.model_copy(update={"zona_cobertura": ["Chapinero", "Usaquén"]})
    cfg2 = base.model_copy(update={"negocio": base.negocio.model_copy(update={"domicilio": dom})})
    chat = Chat(engine, cfg2)
    _listo(chat)
    chat.toca("mod:domicilio")
    salida = chat.escribe("Calle 1 en Suba")
    assert "solo atiendo a domicilio en: Chapinero, Usaquén" in salida[0].cuerpo
    assert chat.ids() == ["mod:local", "mod:domicilio"]
    chat.toca("mod:domicilio")
    assert chat.escribe("Cra 9 # 1-1, USAQUÉN")[0].tipo == "lista"  # sin distinguir mayúsculas


def test_elegir_local_salta_la_direccion(engine: Engine, cfg: Config) -> None:
    chat = Chat(engine, con_domicilio(cfg))
    _listo(chat)
    assert chat.toca("mod:local")[0].tipo == "lista"
    chat.toca(chat.primer_id("dia:"))
    resumen = chat.toca(chat.primer_id("hora:"))[0].cuerpo
    assert "Calle 00" in resumen


def test_consultar_servicios_menciona_el_domicilio_cuando_esta_encendido(
    engine: Engine, cfg: Config
) -> None:
    chat = Chat(engine, con_domicilio(cfg, recargo=8000))
    chat.acepta_aviso()
    salida = chat.toca("menu:consultar")
    assert "a domicilio (recargo $8.000)" in salida[0].cuerpo
