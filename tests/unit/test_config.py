"""Carga y validación de negocio.yaml y servicios.yaml (EARS-CFG-01/02, EARS-DOM-05)."""

from pathlib import Path

import pytest
import yaml
from app.config import ConfiguracionInvalida, cargar_config

RAIZ = Path(__file__).resolve().parents[2]


def _copiar(tmp_path: Path, **cambios: object) -> Path:
    negocio = yaml.safe_load((RAIZ / "config/negocio.yaml").read_text(encoding="utf-8"))
    servicios = yaml.safe_load((RAIZ / "config/servicios.yaml").read_text(encoding="utf-8"))
    for clave, valor in cambios.items():
        if clave == "servicios":
            servicios["servicios"] = valor
        else:
            negocio[clave] = valor
    (tmp_path / "negocio.yaml").write_text(yaml.safe_dump(negocio), encoding="utf-8")
    (tmp_path / "servicios.yaml").write_text(yaml.safe_dump(servicios), encoding="utf-8")
    return tmp_path


def test_config_real_es_valida() -> None:
    cfg = cargar_config(RAIZ / "config")
    assert cfg.negocio.bloque_minutos == 30
    assert cfg.negocio.domicilio.habilitado is False
    assert len(cfg.servicios) == 4
    assert cfg.servicio("retiro").duracion_min == 30


def test_dia_sin_horario_es_none() -> None:
    cfg = cargar_config(RAIZ / "config")
    assert cfg.negocio.horario["domingo"] is None
    assert cfg.negocio.horario["lunes"] is not None


def test_servicio_inexistente(tmp_path: Path) -> None:
    cfg = cargar_config(_copiar(tmp_path))
    with pytest.raises(KeyError):
        cfg.servicio("no_existe")


def test_servicio_duplicado(tmp_path: Path) -> None:
    s = {"id": "a", "nombre": "A", "duracion_min": 30, "precio_cop": 1, "descripcion": "x"}
    with pytest.raises(ConfiguracionInvalida, match="servicios.yaml"):
        cargar_config(_copiar(tmp_path, servicios=[s, dict(s)]))


def test_domicilio_habilitado_sin_recargo_falla(tmp_path: Path) -> None:
    dom = {"habilitado": True, "minutos_desplazamiento": 30}
    with pytest.raises(ConfiguracionInvalida, match="recargo_cop"):
        cargar_config(_copiar(tmp_path, domicilio=dom))


def test_domicilio_con_recargo_negativo_falla(tmp_path: Path) -> None:
    dom = {"habilitado": True, "recargo_cop": -1}
    with pytest.raises(ConfiguracionInvalida):
        cargar_config(_copiar(tmp_path, domicilio=dom))


def test_domicilio_habilitado_con_recargo_ok(tmp_path: Path) -> None:
    dom = {"habilitado": True, "recargo_cop": 8000}
    cfg = cargar_config(_copiar(tmp_path, domicilio=dom))
    assert cfg.negocio.domicilio.recargo_cop == 8000


def test_bloque_minutos_invalido(tmp_path: Path) -> None:
    with pytest.raises(ConfiguracionInvalida, match="bloque_minutos"):
        cargar_config(_copiar(tmp_path, bloque_minutos=25))


def test_duracion_no_multiplo_de_cinco(tmp_path: Path) -> None:
    s = {"id": "a", "nombre": "A", "duracion_min": 32, "precio_cop": 1, "descripcion": "x"}
    with pytest.raises(ConfiguracionInvalida, match="duracion_min"):
        cargar_config(_copiar(tmp_path, servicios=[s]))


def test_cierra_antes_de_abrir(tmp_path: Path) -> None:
    horario = {"lunes": {"abre": "18:00", "cierra": "08:00"}}
    with pytest.raises(ConfiguracionInvalida, match="cierra"):
        cargar_config(_copiar(tmp_path, horario=horario))


def test_dia_invalido(tmp_path: Path) -> None:
    with pytest.raises(ConfiguracionInvalida, match="horario"):
        cargar_config(_copiar(tmp_path, horario={"funday": None}))


def test_campo_desconocido(tmp_path: Path) -> None:
    with pytest.raises(ConfiguracionInvalida):
        cargar_config(_copiar(tmp_path, campo_raro=1))


def test_archivo_faltante(tmp_path: Path) -> None:
    with pytest.raises(ConfiguracionInvalida, match="negocio.yaml"):
        cargar_config(tmp_path)


def test_yaml_malformado(tmp_path: Path) -> None:
    (tmp_path / "negocio.yaml").write_text("a: [", encoding="utf-8")
    (tmp_path / "servicios.yaml").write_text("servicios: []", encoding="utf-8")
    with pytest.raises(ConfiguracionInvalida, match="negocio.yaml"):
        cargar_config(tmp_path)


def test_yaml_raiz_no_es_mapa(tmp_path: Path) -> None:
    (tmp_path / "negocio.yaml").write_text("- 1", encoding="utf-8")
    (tmp_path / "servicios.yaml").write_text("servicios: []", encoding="utf-8")
    with pytest.raises(ConfiguracionInvalida, match="mapa"):
        cargar_config(tmp_path)


def test_zona_horaria_invalida(tmp_path: Path) -> None:
    with pytest.raises(ConfiguracionInvalida, match="zona_horaria"):
        cargar_config(_copiar(tmp_path, zona_horaria="Mars/Base"))


def test_anti_ban_valores_por_defecto_y_pausa_humana() -> None:
    cfg = cargar_config(RAIZ / "config")
    ab = cfg.negocio.anti_ban
    assert (ab.debounce_segundos, ab.lectura_min_ms, ab.lectura_max_ms) == (4.5, 1500, 3000)
    assert (ab.escritura_ms_por_caracter, ab.escritura_min_ms, ab.escritura_max_ms) == (
        45,
        2500,
        6000,
    )
    assert (ab.jitter_min_ms, ab.jitter_max_ms) == (-400, 600)
    assert (ab.max_mensajes_por_telefono, ab.ventana_limite_minutos) == (15, 10)
    assert cfg.negocio.pausa_intervencion_humana_minutos == 120


@pytest.mark.parametrize(
    "cambio",
    [
        {"lectura_min_ms": 4000},
        {"escritura_min_ms": 7000},
        {"jitter_min_ms": 700},
        {"max_mensajes_globales": 5},
        {"debounce_segundos": -1},
        {"max_mensajes_por_telefono": 0},
        {"ventana_limite_minutos": 0},
    ],
)
def test_anti_ban_fuera_de_rango_impide_el_arranque(
    tmp_path: Path, cambio: dict[str, object]
) -> None:
    negocio = yaml.safe_load((RAIZ / "config/negocio.yaml").read_text(encoding="utf-8"))
    negocio["anti_ban"].update(cambio)
    (tmp_path / "negocio.yaml").write_text(yaml.safe_dump(negocio), encoding="utf-8")
    (tmp_path / "servicios.yaml").write_text(
        (RAIZ / "config/servicios.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    with pytest.raises(ConfiguracionInvalida, match="anti_ban"):
        cargar_config(tmp_path)


def test_pausa_humana_debe_ser_positiva(tmp_path: Path) -> None:
    with pytest.raises(ConfiguracionInvalida, match="pausa_intervencion_humana_minutos"):
        cargar_config(_copiar(tmp_path, pausa_intervencion_humana_minutos=0))
