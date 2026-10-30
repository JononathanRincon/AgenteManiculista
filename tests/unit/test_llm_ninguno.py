"""Proveedor `none` y fábrica (EARS-INT-05): el sistema no necesita un LLM."""

import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from app.config import Settings
from app.llm import crear_proveedor
from app.llm.base import ServicioResumen
from app.llm.ninguno import Ninguno

RAIZ = Path(__file__).resolve().parents[2]


def test_ninguno_siempre_devuelve_none() -> None:
    catalogo = [ServicioResumen("retiro", "Retiro")]
    respuesta = Ninguno().interpretar("quiero un retiro", catalogo, date(2026, 11, 3), "MENU")
    assert respuesta is None


def test_la_fabrica_entrega_el_proveedor_nulo_con_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROVEEDOR_LLM", "none")
    proveedor = crear_proveedor(Settings(_env_file=None))  # type: ignore[call-arg]
    assert isinstance(proveedor, Ninguno)


def test_con_none_no_se_importa_el_sdk_de_google() -> None:
    codigo = (
        "import sys\n"
        "from app.config import Settings\n"
        "from app.llm import crear_proveedor\n"
        "crear_proveedor(Settings(_env_file=None, proveedor_llm='none'))\n"
        "assert 'google.genai' not in sys.modules\n"
        "assert 'app.llm.gemini' not in sys.modules\n"
    )
    resultado = subprocess.run(  # noqa: S603 - ejecuta el mismo intérprete con código fijo
        [sys.executable, "-c", codigo], cwd=RAIZ, capture_output=True, text=True, check=False
    )
    assert resultado.returncode == 0, resultado.stderr
