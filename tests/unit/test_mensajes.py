"""Mensajes externalizados (NFR-MNT-01, EARS-CFG-01)."""

import ast
from pathlib import Path

import pytest
import yaml
from app.config import ConfiguracionInvalida
from app.mensajes import CLAVES_REQUERIDAS, cargar_mensajes, configurar, texto

RAIZ = Path(__file__).resolve().parents[2]
YAML = RAIZ / "config" / "mensajes.yaml"


@pytest.fixture(autouse=True)
def _limpiar() -> None:
    configurar(None)


def _yaml_modificado(tmp_path: Path, cambios: dict[str, str | None]) -> Path:
    datos = yaml.safe_load(YAML.read_text(encoding="utf-8"))
    for clave, valor in cambios.items():
        if valor is None:
            del datos[clave]
        else:
            datos[clave] = valor
    ruta = tmp_path / "mensajes.yaml"
    ruta.write_text(yaml.safe_dump(datos, allow_unicode=True), encoding="utf-8")
    return ruta


def test_catalogo_real_cumple_las_claves_requeridas() -> None:
    m = cargar_mensajes(YAML)
    assert all(m.existe(c) for c in CLAVES_REQUERIDAS)


def test_texto_con_variables() -> None:
    assert "Ana" in texto("bienvenida", nombre=" Ana")
    assert texto("precio_formato", valor="30.000") == "$30.000"


def test_variable_faltante_lanza() -> None:
    with pytest.raises(KeyError):
        texto("bienvenida")


def test_clave_inexistente_lanza() -> None:
    with pytest.raises(KeyError):
        texto("no_existe")


def test_falta_clave_requerida_falla_el_arranque(tmp_path: Path) -> None:
    ruta = _yaml_modificado(tmp_path, {"aviso_datos": None})
    with pytest.raises(ConfiguracionInvalida, match="aviso_datos"):
        cargar_mensajes(ruta)


def test_plantilla_invalida_falla(tmp_path: Path) -> None:
    ruta = _yaml_modificado(tmp_path, {"aviso_datos": "hola {sin_cerrar"})
    with pytest.raises(ConfiguracionInvalida, match="aviso_datos"):
        cargar_mensajes(ruta)


def test_raiz_no_es_mapa(tmp_path: Path) -> None:
    ruta = tmp_path / "mensajes.yaml"
    ruta.write_text("- a", encoding="utf-8")
    with pytest.raises(ConfiguracionInvalida, match="mapa"):
        cargar_mensajes(ruta)


def test_dias_y_meses_completos() -> None:
    m = cargar_mensajes(YAML)
    assert m.texto("dia_2") == "miércoles"
    assert m.texto("mes_abrev_11") == "nov"


CAMPOS_VISIBLES = {"cuerpo", "titulo", "boton", "descripcion"}


def _literales_visibles(arbol: ast.AST) -> list[int]:
    """Líneas con un texto literal pasado como campo visible a una clienta."""
    lineas: list[int] = []
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Call):
            continue
        for kw in nodo.keywords:
            es_texto = isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)
            if kw.arg in CAMPOS_VISIBLES and es_texto and kw.value.value:  # type: ignore[attr-defined]
                lineas.append(kw.value.lineno)
    return lineas


def test_no_hay_textos_visibles_hardcodeados_en_app() -> None:
    hallazgos = []
    for ruta in (RAIZ / "app").rglob("*.py"):
        arbol = ast.parse(ruta.read_text(encoding="utf-8"))
        hallazgos += [f"{ruta.relative_to(RAIZ)}:{n}" for n in _literales_visibles(arbol)]
    assert hallazgos == []


def test_detector_encuentra_un_literal_visible() -> None:
    arbol = ast.parse('Saliente(tipo="texto", cuerpo="Hola, ¿cómo estás?")')
    assert _literales_visibles(arbol)
