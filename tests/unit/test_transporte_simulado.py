"""Transporte simulado y núcleo de la demo (EARS-SIM-02, EARS-SIM-03, EARS-SIM-05)."""

import pytest
from app.config import Config
from app.conversacion import Entrante, Saliente
from app.mensajes import configurar
from app.modelos import Reserva, unidad_de_trabajo
from app.puertos import Opcion
from app.reloj import RelojFalso
from app.simulador.demo import Demo, TelefonoInvalido
from app.simulador.transporte import TransporteSimulado
from sqlalchemy import Engine, select

from tests.integration.conftest import AHORA

TEL = "573001112233"


@pytest.fixture
def demo(engine: Engine, cfg: Config) -> Demo:
    configurar(None)
    return Demo(engine, cfg, RelojFalso(AHORA))


def _aceptar(demo: Demo) -> None:
    demo.mensaje(TEL, texto="hola")
    demo.mensaje(TEL, boton_id="aviso:si")


def test_el_transporte_guarda_lo_que_veria_la_clienta() -> None:
    t = TransporteSimulado()
    t.enviar_texto(TEL, "hola")
    t.enviar_botones(TEL, "elige", [Opcion("a", "A")])
    t.enviar_lista(TEL, "lista", "Ver", [Opcion("b", "B", "desc")])
    t.registrar_clienta(TEL, "quiero")
    historial = t.historial(TEL)
    assert [(m.de, m.tipo) for m in historial] == [
        ("bot", "texto"),
        ("bot", "botones"),
        ("bot", "lista"),
        ("clienta", "texto"),
    ]
    assert historial[2].boton == "Ver"
    assert historial[2].a_dict()["opciones"] == [{"id": "b", "titulo": "B", "descripcion": "desc"}]
    assert t.titulo_de(TEL, "b") == "B"
    assert t.titulo_de(TEL, "zzz") is None
    assert t.historial("otro") == []
    t.reiniciar()
    assert t.historial(TEL) == []


def test_el_transporte_limita_el_historial() -> None:
    t = TransporteSimulado()
    for i in range(250):
        t.enviar_texto(TEL, str(i))
    assert len(t.historial(TEL)) == 200
    assert t.historial(TEL)[-1].cuerpo == "249"


def test_mostrar_entrega_las_salidas_del_agente() -> None:
    t = TransporteSimulado()
    t.mostrar(TEL, [Saliente("texto", "uno"), Saliente("botones", "dos", [Opcion("x", "X")])])
    assert [m.cuerpo for m in t.historial(TEL)] == ["uno", "dos"]


def test_una_conversacion_simulada_invoca_al_agente_y_registra_el_chat(demo: Demo) -> None:
    llamadas: list[Entrante] = []
    original = demo.agente.procesar

    def espia(entrante: Entrante) -> list[Saliente]:
        llamadas.append(entrante)
        return original(entrante)

    demo.agente.procesar = espia  # type: ignore[method-assign]
    demo.mensaje(TEL, texto="hola")
    assert len(llamadas) == 1
    assert llamadas[0].telefono == TEL
    assert llamadas[0].texto == "hola"
    historial = demo.transporte.historial(TEL)
    assert historial[0].de == "clienta"
    assert "Ley 1581" in historial[1].cuerpo


def test_el_toque_de_un_boton_se_muestra_con_su_titulo(demo: Demo) -> None:
    demo.mensaje(TEL, texto="hola")
    demo.mensaje(TEL, boton_id="aviso:si")
    clienta = [m.cuerpo for m in demo.transporte.historial(TEL) if m.de == "clienta"]
    assert clienta == ["hola", "Acepto"]


def test_telefono_invalido_se_rechaza(demo: Demo) -> None:
    for malo in ("", "abc", "57300<script>", "1" * 30):
        with pytest.raises(TelefonoInvalido):
            demo.mensaje(malo, texto="hola")


def test_agendar_actualiza_el_calendario_y_emite_el_cambio(demo: Demo) -> None:
    avisos: list[int] = []
    demo.suscribir(lambda: avisos.append(1))
    _aceptar(demo)
    demo.mensaje(TEL, boton_id="menu:agendar")
    demo.mensaje(TEL, boton_id="srv:retiro")
    dia = demo.transporte.historial(TEL)[-1].opciones[0].id
    demo.mensaje(TEL, boton_id=dia)
    hora = demo.transporte.historial(TEL)[-1].opciones[0].id
    demo.mensaje(TEL, boton_id=hora)
    demo.mensaje(TEL, boton_id="conf:si")
    apartada = demo.calendario_actual()
    assert [f["estado"] for f in apartada] == ["apartada"]
    before = len(avisos)
    assert demo.worker.procesar_uno() is True  # el worker crea el evento en el calendario simulado
    assert len(avisos) > before  # el calendario emitió el cambio
    confirmada = demo.calendario_actual()
    assert [f["estado"] for f in confirmada] == ["confirmada"]
    assert confirmada[0]["titulo"] == "Retiro · Clienta de demo"
    assert "Cita confirmada" in demo.transporte.historial(TEL)[-1].cuerpo


def test_desuscribir_y_reiniciar(demo: Demo, engine: Engine) -> None:
    avisos: list[int] = []

    def oyente() -> None:
        avisos.append(1)

    demo.suscribir(oyente)
    demo.mensaje(TEL, texto="hola")
    demo.desuscribir(oyente)
    demo.desuscribir(oyente)  # idempotente
    cantidad = len(avisos)
    _aceptar(demo)
    assert len(avisos) == cantidad
    demo.reiniciar()
    assert demo.transporte.historial(TEL) == []
    assert demo.calendario_actual() == []
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert s.scalars(select(Reserva)).all() == []
    demo.mensaje(TEL, texto="hola")  # vuelve a pedir el aviso: la conversación se borró
    assert "Ley 1581" in demo.transporte.historial(TEL)[-1].cuerpo
    assert demo.ahora() == AHORA
