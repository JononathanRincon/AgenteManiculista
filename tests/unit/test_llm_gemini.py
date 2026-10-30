"""Adaptador de Gemini con cliente simulado: nunca lanza y siempre valida (EARS-INT-02..04)."""

import json
import logging
from datetime import date
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from app.config import Settings
from app.llm import crear_proveedor
from app.llm.base import ServicioResumen
from app.llm.gemini import ProveedorGemini
from google.genai import errors

HOY = date(2026, 11, 3)
CATALOGO = [
    ServicioResumen("retiro", "Retiro"),
    ServicioResumen("semipermanente", "Semipermanente"),
]
BUENO = {
    "intencion": "agendar",
    "servicio_id": "retiro",
    "fecha_tentativa": "2026-11-05",
    "franja": "tarde",
    "confianza": 0.9,
}


class ModelosFalsos:
    def __init__(self, resultado: Any) -> None:
        self.resultado = resultado
        self.llamadas: list[dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> Any:
        self.llamadas.append(kwargs)
        if isinstance(self.resultado, BaseException):
            raise self.resultado
        return SimpleNamespace(text=self.resultado)


def _proveedor(resultado: Any) -> tuple[ProveedorGemini, ModelosFalsos]:
    modelos = ModelosFalsos(resultado)
    return ProveedorGemini(SimpleNamespace(models=modelos), "gemini-3.5-flash-lite"), modelos


def test_respuesta_valida() -> None:
    proveedor, modelos = _proveedor(json.dumps(BUENO))
    texto = "quiero un retiro el jueves por la tarde"
    intencion = proveedor.interpretar(texto, CATALOGO, HOY, "MENU")
    assert intencion is not None
    assert (intencion.intencion, intencion.servicio_id, intencion.franja) == (
        "agendar",
        "retiro",
        "tarde",
    )
    llamada = modelos.llamadas[0]
    assert llamada["model"] == "gemini-3.5-flash-lite"
    config = llamada["config"]
    assert config.response_mime_type == "application/json"
    assert config.http_options.timeout == 3000  # 3 s, en milisegundos
    assert config.temperature == 0


def test_el_prompt_enviado_solo_lleva_texto_catalogo_fecha_y_estado() -> None:
    proveedor, modelos = _proveedor(json.dumps(BUENO))
    proveedor.interpretar("quiero un retiro", CATALOGO, HOY, "ELIGIENDO_DIA")
    prompt = modelos.llamadas[0]["contents"]
    assert "quiero un retiro" in prompt
    assert "- retiro: Retiro" in prompt
    assert "2026-11-03" in prompt
    assert "ELIGIENDO_DIA" in prompt


@pytest.mark.parametrize(
    "falla",
    [
        TimeoutError("3 s"),
        httpx.ReadTimeout("lento"),
        ConnectionError("sin red"),
        RuntimeError("inesperado"),
        errors.APIError(429, {"error": {"message": "cuota", "status": "RESOURCE_EXHAUSTED"}}),
        errors.ServerError(503, {"error": {"message": "caído"}}),
    ],
)
def test_timeout_429_y_errores_devuelven_none_sin_propagar(falla: BaseException) -> None:
    proveedor, _ = _proveedor(falla)
    assert proveedor.interpretar("hola", CATALOGO, HOY, "MENU") is None


@pytest.mark.parametrize(
    "crudo",
    [
        "no es json",
        "",
        None,
        json.dumps({**BUENO, "servicio_id": "inventado"}),
        json.dumps({**BUENO, "fecha_tentativa": "2026-02-30"}),
        json.dumps({**BUENO, "extra": 1}),
        json.dumps({**BUENO, "confianza": 7}),
    ],
)
def test_json_invalido_o_incoherente_devuelve_none(crudo: Any) -> None:
    proveedor, _ = _proveedor(crudo)
    assert proveedor.interpretar("hola", CATALOGO, HOY, "MENU") is None


def test_los_logs_no_incluyen_el_texto_de_la_clienta(caplog: pytest.LogCaptureFixture) -> None:
    proveedor, _ = _proveedor(RuntimeError("fallo con SECRETO-CLIENTA dentro"))
    with caplog.at_level(logging.DEBUG, logger="llm.gemini"):
        proveedor.interpretar("mi dirección es la calle secreta 123", CATALOGO, HOY, "MENU")
    registro = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "calle secreta" not in registro
    assert "SECRETO-CLIENTA" not in registro
    assert "RuntimeError" in registro


def test_la_fabrica_crea_el_proveedor_de_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROVEEDOR_LLM", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "clave-de-prueba")
    monkeypatch.setenv("GEMINI_MODEL", "modelo-x")
    proveedor = crear_proveedor(Settings(_env_file=None))  # type: ignore[call-arg]
    assert isinstance(proveedor, ProveedorGemini)
    assert proveedor._modelo == "modelo-x"  # noqa: SLF001
