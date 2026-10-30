"""Disponibilidad: bloques ocupados y horarios ofrecibles (EARS-DIS-01..04, EARS-DOM-01)."""

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from app.config import Config, Horario, Negocio, Servicio, cargar_config
from app.disponibilidad import bloques_ocupados, horarios_ofrecibles

RAIZ = Path(__file__).resolve().parents[2]
BOGOTA = ZoneInfo("America/Bogota")


@pytest.fixture(scope="module")
def cfg() -> Config:
    return cargar_config(RAIZ / "config")


@pytest.fixture
def negocio(cfg: Config) -> Negocio:
    return cfg.negocio


def _srv(cfg: Config, servicio_id: str) -> Servicio:
    return cfg.servicio(servicio_id)


def _local(y: int, mes: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, mes, d, h, mi, tzinfo=BOGOTA).astimezone(UTC)


# martes 3 de noviembre de 2026; "ahora" muy anterior para no estorbar
AHORA = _local(2026, 11, 2, 7)
MARTES = date(2026, 11, 3)


@pytest.mark.parametrize(
    ("servicio", "esperado"),
    [("manicure_tradicional", 2), ("semipermanente", 3), ("acrilicas", 6), ("retiro", 2)],
)
def test_bloques_por_servicio(cfg: Config, servicio: str, esperado: int) -> None:
    inicio = _local(2026, 11, 3, 9)
    bloques = bloques_ocupados(inicio, _srv(cfg, servicio), "local", cfg.negocio)
    assert len(bloques) == esperado
    assert bloques[0] == inicio
    assert all(b - a == timedelta(minutes=30) for a, b in zip(bloques, bloques[1:], strict=False))


def test_bloques_con_desplazamiento_de_domicilio(cfg: Config) -> None:
    dom = cfg.negocio.domicilio.model_copy(update={"habilitado": True, "recargo_cop": 5000})
    negocio = cfg.negocio.model_copy(update={"domicilio": dom})
    inicio = _local(2026, 11, 3, 9)
    # 45 + 15 + 30 = 90 -> 3 bloques (vs 2 en local)
    srv = _srv(cfg, "manicure_tradicional")
    assert len(bloques_ocupados(inicio, srv, "domicilio", negocio)) == 3
    assert len(bloques_ocupados(inicio, srv, "local", negocio)) == 2


def test_dia_laborable_ofrece_horarios_ordenados_y_maximo_diez(cfg: Config) -> None:
    horas = horarios_ofrecibles(MARTES, _srv(cfg, "retiro"), "local", set(), AHORA, cfg.negocio)
    assert len(horas) == 10
    assert horas == sorted(horas)
    assert horas[0] == _local(2026, 11, 3, 8)


def test_limite_configurable(cfg: Config) -> None:
    horas = horarios_ofrecibles(
        MARTES, _srv(cfg, "retiro"), "local", set(), AHORA, cfg.negocio, limite=3
    )
    assert len(horas) == 3


def test_la_duracion_debe_terminar_antes_del_cierre(cfg: Config) -> None:
    # sábado cierra 14:00; acrílicas dura 150 min -> la última salida es 11:30
    sabado = date(2026, 11, 7)
    horas = horarios_ofrecibles(
        sabado, _srv(cfg, "acrilicas"), "local", set(), AHORA, cfg.negocio, limite=50
    )
    assert horas[-1] == _local(2026, 11, 7, 11, 30)
    assert _local(2026, 11, 7, 12) not in horas


def test_cierre_exacto_se_ofrece(cfg: Config) -> None:
    sabado = date(2026, 11, 7)  # cierra 14:00; retiro 30 min -> 13:30 termina exacto
    horas = horarios_ofrecibles(
        sabado, _srv(cfg, "retiro"), "local", set(), AHORA, cfg.negocio, limite=50
    )
    assert horas[-1] == _local(2026, 11, 7, 13, 30)


def test_margen_no_cuenta_para_el_cierre(cfg: Config) -> None:
    # semipermanente 75 min a las 12:45 termina 14:00 aunque el margen pase del cierre
    sabado = date(2026, 11, 7)
    horas = horarios_ofrecibles(
        sabado, _srv(cfg, "semipermanente"), "local", set(), AHORA, cfg.negocio, limite=50
    )
    assert _local(2026, 11, 7, 12, 30) in horas  # termina 13:45
    assert _local(2026, 11, 7, 13, 0) not in horas  # terminaría 14:15


def test_anticipacion_minima(cfg: Config) -> None:
    ahora = _local(2026, 11, 3, 8)  # +2 h = 10:00
    horas = horarios_ofrecibles(
        MARTES, _srv(cfg, "retiro"), "local", set(), ahora, cfg.negocio, limite=50
    )
    assert horas[0] == _local(2026, 11, 3, 10)


def test_ventana_maxima(cfg: Config) -> None:
    hoy = date(2026, 11, 2)
    limite = hoy + timedelta(days=21)
    dentro = horarios_ofrecibles(limite, _srv(cfg, "retiro"), "local", set(), AHORA, cfg.negocio)
    fuera = horarios_ofrecibles(
        limite + timedelta(days=1), _srv(cfg, "retiro"), "local", set(), AHORA, cfg.negocio
    )
    assert dentro
    assert fuera == []


def test_dia_no_laborable(cfg: Config) -> None:
    domingo = date(2026, 11, 8)
    srv = _srv(cfg, "retiro")
    assert horarios_ofrecibles(domingo, srv, "local", set(), AHORA, cfg.negocio) == []


def test_dia_pasado(cfg: Config) -> None:
    ayer = date(2026, 11, 1)
    assert horarios_ofrecibles(ayer, _srv(cfg, "retiro"), "local", set(), AHORA, cfg.negocio) == []


def test_ocupacion_descarta_candidatos(cfg: Config) -> None:
    ocupados = {_local(2026, 11, 3, 8), _local(2026, 11, 3, 8, 30)}
    horas = horarios_ofrecibles(
        MARTES, _srv(cfg, "retiro"), "local", ocupados, AHORA, cfg.negocio, limite=50
    )
    assert _local(2026, 11, 3, 8) not in horas
    # retiro ocupa 2 bloques: 7:30 no existe, 9:00 libre pero 8:30 ocupado no afecta 9:00
    assert _local(2026, 11, 3, 9) in horas


def test_solape_de_servicio_largo_con_cita_corta_dentro(cfg: Config) -> None:
    # una cita de retiro (2 bloques) a las 10:00 ocupa 10:00 y 10:30
    ocupados = {_local(2026, 11, 3, 10), _local(2026, 11, 3, 10, 30)}
    horas = horarios_ofrecibles(
        MARTES, _srv(cfg, "acrilicas"), "local", ocupados, AHORA, cfg.negocio, limite=50
    )
    # acrílicas (6 bloques) que empiecen entre 7:30 y 10:30 chocarían: 8:00 ..10:30
    assert not any(_local(2026, 11, 3, 8) <= h <= _local(2026, 11, 3, 10, 30) for h in horas)
    assert _local(2026, 11, 3, 11) in horas


def test_horario_con_apertura_no_alineada_arranca_en_la_cuadricula(cfg: Config) -> None:

    horario = dict(cfg.negocio.horario)
    horario["martes"] = Horario(abre=time(8, 10), cierra=time(12, 0))
    negocio = cfg.negocio.model_copy(update={"horario": horario})
    horas = horarios_ofrecibles(MARTES, _srv(cfg, "retiro"), "local", set(), AHORA, negocio)
    assert horas[0] == _local(2026, 11, 3, 8, 30)


def test_sin_horario_de_domingo_no_falla_con_domicilio(cfg: Config) -> None:
    horas = horarios_ofrecibles(MARTES, _srv(cfg, "retiro"), "domicilio", set(), AHORA, cfg.negocio)
    assert horas
