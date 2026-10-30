"""Settings de entorno con valores por defecto seguros (NFR-SEG-02, EARS-INT-05)."""

import re
from pathlib import Path

import pytest
from app.config import Settings
from pydantic import ValidationError

RAIZ = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _entorno_limpio(monkeypatch: pytest.MonkeyPatch) -> None:
    for linea in (RAIZ / ".env.example").read_text(encoding="utf-8").splitlines():
        if re.match(r"^[A-Z_]+=", linea):
            monkeypatch.delenv(linea.split("=")[0], raising=False)


def test_valores_por_defecto_son_seguros() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.simulador is False
    assert s.proveedor_llm == "none"
    assert s.entorno == "desarrollo"
    assert s.gemini_tier == "free"
    assert s.database_url.startswith("sqlite")


def test_lee_variables_de_entorno(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIMULADOR", "true")
    monkeypatch.setenv("ENTORNO", "demo")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.simulador is True
    assert s.entorno == "demo"


def test_valores_vacios_se_ignoran(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIMULADOR", "")
    monkeypatch.setenv("PROVEEDOR_LLM", "")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.simulador is False
    assert s.proveedor_llm == "none"


def test_gemini_exige_clave(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROVEEDOR_LLM", "gemini")
    with pytest.raises(ValidationError, match="GEMINI_API_KEY"):
        Settings(_env_file=None)  # type: ignore[call-arg]
    monkeypatch.setenv("GEMINI_API_KEY", "clave-de-prueba")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert "clave-de-prueba" not in repr(s)
    assert s.gemini_api_key is not None
    assert s.gemini_api_key.get_secret_value() == "clave-de-prueba"


def test_proveedor_invalido(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROVEEDOR_LLM", "otro")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_env_example_documenta_todas_las_variables_sin_valores() -> None:
    texto = (RAIZ / ".env.example").read_text(encoding="utf-8")
    declaradas = {m.group(1) for m in re.finditer(r"^([A-Z_]+)=(.*)$", texto, re.M)}
    valores = [m.group(2) for m in re.finditer(r"^([A-Z_]+)=(.*)$", texto, re.M)]
    assert all(v == "" for v in valores)
    esperadas = {n.upper() for n in Settings.model_fields}
    assert esperadas == declaradas


def test_en_produccion_gemini_exige_el_tier_de_pago(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENTORNO", "produccion")
    monkeypatch.setenv("PROVEEDOR_LLM", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "clave-de-prueba")
    with pytest.raises(ValidationError, match="GEMINI_TIER"):
        Settings(_env_file=None)  # type: ignore[call-arg]
    monkeypatch.setenv("GEMINI_TIER", "paid")
    assert Settings(_env_file=None).gemini_tier == "paid"  # type: ignore[call-arg]
    monkeypatch.setenv("ENTORNO", "demo")
    monkeypatch.setenv("GEMINI_TIER", "free")
    assert Settings(_env_file=None).gemini_tier == "free"  # type: ignore[call-arg]
