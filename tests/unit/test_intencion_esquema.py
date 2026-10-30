"""Esquema estricto de la intención: cualquier incoherencia invalida toda la respuesta (EC-09)."""

import json
from datetime import date

import pytest
from app.intencion import Intencion, validar_intencion

HOY = date(2026, 11, 3)
CATALOGO = frozenset({"retiro", "semipermanente"})


def _bueno(**extra: object) -> dict[str, object]:
    datos: dict[str, object] = {
        "intencion": "agendar",
        "servicio_id": "retiro",
        "fecha_tentativa": "2026-11-05",
        "franja": "tarde",
        "confianza": 0.9,
    }
    datos.update(extra)
    return datos


def test_respuesta_valida_en_dict_y_en_json() -> None:
    a = validar_intencion(_bueno(), CATALOGO, HOY)
    b = validar_intencion(json.dumps(_bueno()), CATALOGO, HOY)
    assert a == b
    assert a is not None
    assert (a.intencion, a.servicio_id, a.fecha_tentativa, a.franja) == (
        "agendar",
        "retiro",
        date(2026, 11, 5),
        "tarde",
    )


def test_campos_opcionales_pueden_ser_nulos_o_faltar() -> None:
    minimo = validar_intencion({"intencion": "saludo", "confianza": 1}, CATALOGO, HOY)
    assert minimo is not None
    assert (minimo.servicio_id, minimo.fecha_tentativa, minimo.franja) == (None, None, None)
    sin_nada = _bueno(servicio_id=None, fecha_tentativa=None, franja=None)
    nulos = validar_intencion(sin_nada, CATALOGO, HOY)
    assert nulos is not None


@pytest.mark.parametrize(
    "cambio",
    [
        {"campo_extra": "x"},  # campo extra
        {"fecha_tentativa": "2026-02-30"},  # fecha imposible
        {"fecha_tentativa": "2026-11-02"},  # fecha pasada
        {"fecha_tentativa": "mañana"},
        {"servicio_id": "inventado"},  # servicio inexistente
        {"intencion": "confirmar_cita"},  # intención no permitida
        {"franja": "madrugada"},
        {"confianza": 1.2},
        {"confianza": -0.1},
        {"confianza": "alta"},
    ],
)
def test_una_incoherencia_invalida_toda_la_respuesta(cambio: dict[str, object]) -> None:
    assert validar_intencion(_bueno(**cambio), CATALOGO, HOY) is None


ROTOS = ["", "no es json", "[]", "null", "{}", '{"intencion": "agendar"}']


@pytest.mark.parametrize("crudo", ROTOS)
def test_json_roto_o_incompleto_se_descarta(crudo: str) -> None:
    assert validar_intencion(crudo, CATALOGO, HOY) is None


def test_un_servicio_sin_catalogo_en_el_contexto_se_rechaza() -> None:
    with pytest.raises(ValueError, match="catálogo"):
        Intencion.model_validate(_bueno())


def test_la_intencion_es_inmutable() -> None:
    intencion = validar_intencion(_bueno(), CATALOGO, HOY)
    assert intencion is not None
    with pytest.raises(ValueError, match="frozen"):
        intencion.confianza = 0.1
