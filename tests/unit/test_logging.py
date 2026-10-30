"""Logging JSON sin teléfonos completos ni tokens (NFR-PRI-02, NFR-OBS-01)."""

import io
import json
import logging

import pytest
from app.logs import (
    FiltroSecretos,
    FormateadorJSON,
    configurar_logging,
    correlation_id,
    enmascarar,
    limpiar_texto,
    nuevo_correlation_id,
)


@pytest.fixture
def registro() -> tuple[logging.Logger, io.StringIO]:
    flujo = io.StringIO()
    manejador = logging.StreamHandler(flujo)
    manejador.setFormatter(FormateadorJSON())
    manejador.addFilter(FiltroSecretos())
    logger = logging.getLogger("prueba.logs")
    logger.handlers = [manejador]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return logger, flujo


def _linea(flujo: io.StringIO) -> dict[str, object]:
    return json.loads(flujo.getvalue().strip().splitlines()[-1])  # type: ignore[no-any-return]


def test_enmascarar_deja_solo_los_ultimos_cuatro_digitos() -> None:
    assert enmascarar("573001112233") == "***2233"
    assert enmascarar("+57 300 111 2233") == "***2233"
    assert enmascarar("") == "***"


def test_el_registro_es_json_con_los_campos_basicos(
    registro: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, flujo = registro
    correlation_id.set(None)
    id_ = nuevo_correlation_id()
    logger.info("reserva_apartada", extra={"reserva": 7})
    datos = _linea(flujo)
    assert datos["evento"] == "reserva_apartada"
    assert datos["nivel"] == "INFO"
    assert datos["correlation_id"] == id_
    assert datos["reserva"] == 7
    assert str(datos["ts"]).endswith("+00:00")


def test_el_telefono_nunca_sale_completo(registro: tuple[logging.Logger, io.StringIO]) -> None:
    logger, flujo = registro
    logger.info("cliente 573001112233 escribió", extra={"telefono": "573001112233"})
    crudo = flujo.getvalue()
    assert "573001112233" not in crudo
    datos = _linea(flujo)
    assert datos["telefono"] == "***2233"
    assert "***2233" in str(datos["evento"])


def test_los_tokens_y_secretos_se_ocultan(registro: tuple[logging.Logger, io.StringIO]) -> None:
    logger, flujo = registro
    secretos = [
        "Authorization: Bearer abc.def-123",
        "AIza" + "SyA1234567890abcdefghijklmnopqrstuvw",  # partido para no parecer una clave real
        "EAAGm0PX4ZCpsBAKZCzzZCexampletoken12345",
        "api_key=supersecreto",
        "token: otro-secreto",
        "X-Firma: sha256=0123456789abcdef0123456789abcdef",
    ]
    for secreto in secretos:
        logger.warning("fallo con %s", secreto, extra={"detalle": secreto})
    crudo = flujo.getvalue()
    ocultos = (
        "abc.def-123",
        "AIzaSyA",
        "EAAGm0",
        "supersecreto",
        "otro-secreto",
        "0123456789abcdef",
    )
    for fragmento in ocultos:
        assert fragmento not in crudo, fragmento
    assert crudo.count("[oculto]") >= len(secretos)


def test_una_excepcion_registra_solo_su_tipo(registro: tuple[logging.Logger, io.StringIO]) -> None:
    logger, flujo = registro
    try:
        raise ValueError("dato personal 573001112233")
    except ValueError:
        logger.exception("fallo")
    datos = _linea(flujo)
    assert datos["excepcion"] == "ValueError"
    assert "573001112233" not in flujo.getvalue()


def test_limpiar_texto_no_toca_texto_normal() -> None:
    assert limpiar_texto("reserva 12 confirmada a las 10:30") == "reserva 12 confirmada a las 10:30"


def test_configurar_logging_es_idempotente() -> None:
    raiz = logging.getLogger()
    antes = list(raiz.handlers)
    try:
        configurar_logging("INFO")
        configurar_logging("DEBUG")
        propios = [h for h in raiz.handlers if getattr(h, "_agente_manicurista", False)]
        assert len(propios) == 1
        assert raiz.level == logging.DEBUG
    finally:
        raiz.handlers = antes
        raiz.setLevel(logging.WARNING)
