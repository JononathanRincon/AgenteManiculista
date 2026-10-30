"""E2E de agendar con PROVEEDOR_LLM=none: el sistema funciona completo sin LLM (NFR-DIS-01)."""

import asyncio

import pytest
from app.config import Config, Settings
from app.llm import crear_proveedor
from app.llm.ninguno import Ninguno
from app.modelos import ColaEscritura, Reserva, unidad_de_trabajo
from app.worker import Worker
from sqlalchemy import Engine, select

from tests.helpers import Chat
from tests.integration.conftest import MensajeriaRegistro


@pytest.fixture
def sin_llm(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("PROVEEDOR_LLM", "none")
    return Settings(_env_file=None)  # type: ignore[call-arg]


async def test_agendar_de_punta_a_punta_con_el_worker_en_marcha(
    engine: Engine, cfg: Config, sin_llm: Settings
) -> None:
    assert sin_llm.proveedor_llm == "none"
    chat = Chat(engine, cfg)  # sin proveedor de lenguaje: solo palabras clave y botones
    mensajeria = MensajeriaRegistro()
    worker = Worker(engine, chat.calendario, mensajeria, cfg, chat.reloj, cache=chat.cache)
    parar = asyncio.Event()
    tarea = asyncio.create_task(worker.correr(parar, intervalo_s=0.01))

    chat.escribe("hola")
    chat.toca("aviso:si")
    chat.escribe("quiero agendar una cita")  # palabras clave, sin LLM
    chat.toca("srv:semipermanente")
    chat.toca(chat.primer_id("dia:"))
    chat.toca(chat.primer_id("hora:"))
    assert "Semipermanente" in chat.cuerpos()
    chat.toca("conf:si")

    for _ in range(300):
        with unidad_de_trabajo(engine, escritura=False) as s:
            if s.scalars(select(Reserva)).one().estado == "CONFIRMADA":
                break
        await asyncio.sleep(0.01)
    parar.set()
    await asyncio.wait_for(tarea, timeout=5)

    with unidad_de_trabajo(engine, escritura=False) as s:
        reserva = s.scalars(select(Reserva)).one()
        trabajo = s.scalars(select(ColaEscritura)).one()
    assert reserva.estado == "CONFIRMADA"
    assert trabajo.estado == "COMPLETADA"
    eventos = chat.calendario.eventos_activos()
    assert len(eventos) == 1
    assert eventos[0].inicio == reserva.inicio
    detalle = chat.calendario.detalle(reserva.event_id or "")
    assert detalle is not None
    assert detalle.titulo == "Semipermanente · María Pérez"
    assert chat.telefono in detalle.descripcion
    assert "Cita confirmada" in mensajeria.enviados[0][1]
    assert isinstance(crear_proveedor(sin_llm), Ninguno)  # el proveedor configurado no hace nada


async def test_dos_clientas_a_la_vez_nunca_comparten_horario(engine: Engine, cfg: Config) -> None:
    ana = Chat(engine, cfg, telefono="573001111111", nombre="Ana")
    luisa = Chat(engine, cfg, telefono="573002222222", nombre="Luisa", calendario=ana.calendario)
    for chat in (ana, luisa):
        chat.acepta_aviso()
        chat.toca("menu:agendar")
        chat.toca("srv:retiro")
        chat.toca(chat.primer_id("dia:"))
    hora = ana.primer_id("hora:")
    ana.toca(hora)
    luisa.toca(hora)
    resultados = await asyncio.gather(
        asyncio.to_thread(ana.toca, "conf:si"), asyncio.to_thread(luisa.toca, "conf:si")
    )
    textos = sorted(r[0].cuerpo[:12] for r in resultados)
    assert any("aparté" in r[0].cuerpo for r in resultados)
    assert any("ocupar" in r[0].cuerpo for r in resultados), textos
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert len(s.scalars(select(Reserva)).all()) == 1
