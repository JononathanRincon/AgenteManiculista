"""Evals: frases reales → resultado esperado, con LLM simulado (EARS-INT-01..04, US-11)."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from app.config import Config
from app.intencion import Intencion, validar_intencion
from app.llm.base import ServicioResumen
from app.modelos import Reserva, unidad_de_trabajo
from sqlalchemy import Engine, select

from tests.helpers import Chat

ARCHIVO = Path(__file__).parent / "frases.yaml"
FRASES: list[dict[str, Any]] = yaml.safe_load(ARCHIVO.read_text(encoding="utf-8"))


class ProveedorSimulado:
    """Valida la respuesta cruda igual que el adaptador real (esquema + catálogo)."""

    def __init__(self, crudo: dict[str, Any] | None) -> None:
        self.crudo = crudo
        self.invocado = False

    def interpretar(
        self, texto: str, catalogo: list[ServicioResumen], hoy: date, estado: str
    ) -> Intencion | None:
        self.invocado = True
        if self.crudo is None:
            return None
        return validar_intencion(self.crudo, {s.id for s in catalogo}, hoy)


def _marca(chat: Chat, resultado: str, extra: str | None) -> bool:
    ids = chat.ids()
    cuerpos = chat.cuerpos()
    return {
        "lista_servicios": bool(ids) and ids[0].startswith("srv:"),
        "lista_dias": bool(ids) and ids[0].startswith("dia:"),
        "lista_horas": bool(ids) and ids[0].startswith("hora:"),
        "precios": "$" in cuerpos and "Retiro" in cuerpos,
        "horario": "Mi horario" in cuerpos,
        "ubicacion": "Calle 00" in cuerpos,
        "saludo": "Hola" in cuerpos and ids[:1] == ["menu:agendar"],
        "mis_citas": "No tienes citas" in cuerpos,
        "faq": extra is not None and extra in cuerpos,
        "menu": "No te entendí" in cuerpos and ids[:1] == ["menu:agendar"],
    }[resultado]


def test_hay_al_menos_treinta_frases() -> None:
    assert len(FRASES) >= 30
    assert {f["esperado"] for f in FRASES} >= {"lista_servicios", "precios", "menu", "faq"}


@pytest.mark.parametrize("caso", FRASES, ids=[f["frase"][:40] for f in FRASES])
def test_cada_frase_produce_lo_esperado(engine: Engine, cfg: Config, caso: dict[str, Any]) -> None:
    proveedor = ProveedorSimulado(caso.get("llm"))
    chat = Chat(engine, cfg, proveedor=proveedor)
    chat.acepta_aviso()
    chat.escribe(caso["frase"])
    assert _marca(chat, caso["esperado"], caso.get("contiene")), (caso["frase"], chat.cuerpos())
    # el LLM solo se consulta cuando ninguna palabra clave ni FAQ resolvió la frase
    if "llm" not in caso:
        assert not proveedor.invocado or caso["esperado"] == "menu"
    # y jamás produce una reserva ni llega a CONFIRMANDO por sí solo
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert s.scalars(select(Reserva)).all() == []
    assert chat.ids()[:1] != ["conf:si"]


def test_las_frases_resueltas_por_palabras_clave_no_llaman_al_llm(
    engine: Engine, cfg: Config
) -> None:
    sin_llm = [f for f in FRASES if "llm" not in f and f["esperado"] not in ("menu",)]
    assert len(sin_llm) >= 20
    for caso in sin_llm:
        proveedor = ProveedorSimulado(None)
        telefono = "5730011122" + str(len(caso["frase"]) % 10)
        chat = Chat(engine, cfg, telefono=telefono, proveedor=proveedor)
        chat.acepta_aviso()
        chat.escribe(caso["frase"])
        assert proveedor.invocado is False, caso["frase"]
