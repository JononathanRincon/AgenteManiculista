"""Palabras clave, intenciones fijas y FAQ sin LLM (EARS-INT-01, EARS-CON-08)."""

from datetime import date
from pathlib import Path

import pytest
import yaml
from app.config import Config, ConfiguracionInvalida
from app.conversacion import Saliente
from app.intencion import (
    Intencion,
    cargar_reglas,
    normalizar,
    resolver_faq,
    resolver_por_palabras_clave,
)
from app.llm.base import ServicioResumen
from app.mensajes import cargar_mensajes
from sqlalchemy import Engine

from tests.helpers import Chat

RAIZ = Path(__file__).resolve().parents[2]
REGLAS = cargar_reglas(RAIZ / "config", cargar_mensajes())


def test_normalizar_quita_tildes_mayusculas_y_signos() -> None:
    assert normalizar("¿Cuánto VALE el Retiro?!") == "cuanto vale el retiro"
    assert normalizar("  Dirección,   por favor ") == "direccion por favor"


@pytest.mark.parametrize(
    ("frase", "intencion"),
    [
        ("Quiero agendar una cita", "agendar"),
        ("necesito reservar para el sábado", "agendar"),
        ("quiero reprogramar mi cita", "reagendar"),
        ("Cancelar mi cita porfa", "cancelar"),
        ("¿Cuánto vale el semipermanente?", "consultar_precio"),
        ("qué precios manejan", "consultar_precio"),
        ("¿A qué hora abren?", "consultar_horario"),
        ("cual es el horario", "consultar_horario"),
        ("¿Dónde queda?", "consultar_ubicacion"),
        ("me das la dirección", "consultar_ubicacion"),
        ("Buenas tardes", "saludo"),
        ("hola", "saludo"),
    ],
)
def test_frases_resueltas_por_palabras_clave(frase: str, intencion: str) -> None:
    assert resolver_por_palabras_clave(frase, REGLAS) == intencion


def test_la_primera_intencion_del_yaml_gana() -> None:
    assert resolver_por_palabras_clave("hola quiero agendar", REGLAS) == "agendar"
    assert resolver_por_palabras_clave("cancelar mi cita", REGLAS) == "cancelar"
    assert resolver_por_palabras_clave("reagendar mi cita", REGLAS) == "reagendar"


def test_palabras_completas_no_subcadenas() -> None:
    assert resolver_por_palabras_clave("holanda", REGLAS) is None
    assert resolver_por_palabras_clave("quiero algo distinto del menú xyz", REGLAS) is None


def test_faq() -> None:
    assert resolver_faq("¿Reciben Nequi?", REGLAS) == "faq_pagos"
    assert resolver_faq("cuánto demora el semipermanente", REGLAS) == "faq_demora"
    assert resolver_faq("hola", REGLAS) is None


def _con_yaml(tmp_path: Path, intenciones: object, faq: object) -> Path:
    (tmp_path / "intenciones.yaml").write_text(yaml.safe_dump(intenciones), encoding="utf-8")
    (tmp_path / "faq.yaml").write_text(yaml.safe_dump(faq), encoding="utf-8")
    return tmp_path


def test_yaml_invalido_falla_al_cargar(tmp_path: Path) -> None:
    bueno = {"intenciones": {"agendar": ["agendar"]}}
    with pytest.raises(ConfiguracionInvalida, match="desconocida"):
        cargar_reglas(_con_yaml(tmp_path, {"intenciones": {"inventada": ["x"]}}, {"faq": []}))
    with pytest.raises(ConfiguracionInvalida, match="lista de textos"):
        cargar_reglas(_con_yaml(tmp_path, {"intenciones": {"agendar": "agendar"}}, {"faq": []}))
    with pytest.raises(ConfiguracionInvalida, match="mapa"):
        cargar_reglas(_con_yaml(tmp_path, ["x"], {"faq": []}))
    with pytest.raises(ConfiguracionInvalida, match="lista 'faq'"):
        cargar_reglas(_con_yaml(tmp_path, bueno, {"otra": 1}))
    with pytest.raises(ConfiguracionInvalida, match="necesita"):
        cargar_reglas(_con_yaml(tmp_path, bueno, {"faq": [{"palabras": ["a"]}]}))
    with pytest.raises(ConfiguracionInvalida, match="no está en mensajes"):
        faq = {"faq": [{"palabras": ["a"], "respuesta": "no_existe"}]}
        cargar_reglas(_con_yaml(tmp_path, bueno, faq), cargar_mensajes())


class ProveedorEspia:
    """Proveedor de lenguaje que anota lo que recibe y no entiende nada."""

    def __init__(self) -> None:
        self.recibidos: list[str] = []

    def interpretar(
        self, texto: str, catalogo: list[ServicioResumen], hoy: date, estado: str
    ) -> Intencion | None:
        self.recibidos.append(texto)
        return None


def test_precio_y_ubicacion_se_resuelven_sin_invocar_al_proveedor(
    engine: Engine, cfg: Config
) -> None:
    proveedor = ProveedorEspia()
    chat = Chat(engine, cfg, proveedor=proveedor)
    chat.acepta_aviso()
    assert "$30.000" in chat.escribe("cuánto vale el manicure")[0].cuerpo
    assert "Calle 00" in chat.escribe("dónde queda")[0].cuerpo
    assert "Lunes: 8:00 a. m. a 6:00 p. m." in chat.escribe("a qué hora abren")[0].cuerpo
    assert "Domingo" not in chat.cuerpos()
    assert "efectivo y transferencia" in chat.escribe("puedo pagar con nequi")[0].cuerpo
    assert proveedor.recibidos == []


def test_lo_que_no_coincide_llega_al_proveedor_y_sin_el_cae_al_menu(
    engine: Engine, cfg: Config
) -> None:
    proveedor = ProveedorEspia()
    chat = Chat(engine, cfg, proveedor=proveedor)
    chat.acepta_aviso()
    salida = chat.escribe("blablabla quiero algo raro")
    assert proveedor.recibidos == ["blablabla quiero algo raro"]
    assert "No te entendí" in salida[0].cuerpo
    assert chat.ids()[0] == "menu:agendar"


def test_el_texto_agendar_inicia_el_flujo_y_cancelar_lleva_a_mis_citas(
    engine: Engine, cfg: Config
) -> None:
    chat = Chat(engine, cfg)
    chat.acepta_aviso()
    assert chat.escribe("quiero agendar una cita")[0].tipo == "lista"
    assert chat.ids()[0].startswith("srv:")
    assert "No tienes citas" in chat.escribe("quiero cancelar")[0].cuerpo


def test_un_saludo_en_medio_del_flujo_vuelve_al_menu(engine: Engine, cfg: Config) -> None:
    chat = Chat(engine, cfg)
    chat.acepta_aviso()
    chat.toca("menu:agendar")
    salida: list[Saliente] = chat.escribe("hola")
    assert "Hola" in salida[0].cuerpo
    assert chat.ids()[0] == "menu:agendar"
