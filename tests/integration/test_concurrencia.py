"""La carrera de confirmaciones: exactamente una gana, 0 solapes (NFR-INT-01, EC-01, EC-02)."""

import random
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from app.config import Config
from app.disponibilidad import minutos_ocupados
from app.modelos import Reserva, ReservaBloque, unidad_de_trabajo
from app.reservas import ErrorReserva, HorarioOcupado, SolicitudReserva, reservar
from sqlalchemy import Engine, select

from tests.integration.conftest import AHORA, local


def _intentar(
    engine: Engine,
    cfg: Config,
    tel: str,
    servicio: str,
    inicio: datetime,
    barrera: threading.Barrier,
) -> str:
    barrera.wait(10)
    try:
        with unidad_de_trabajo(engine) as s:
            reservar(s, SolicitudReserva(tel, "Ana", servicio, inicio), cfg, AHORA)
    except HorarioOcupado:
        return "ocupado"
    except ErrorReserva:
        return "otro"
    return "ok"


def _correr(engine: Engine, cfg: Config, casos: list[tuple[str, str, datetime]]) -> list[str]:
    barrera = threading.Barrier(len(casos))
    with ThreadPoolExecutor(max_workers=len(casos)) as pool:
        futuros = [
            pool.submit(_intentar, engine, cfg, tel, srv, ini, barrera) for tel, srv, ini in casos
        ]
        return [f.result(timeout=60) for f in futuros]


def _intervalos_activos(engine: Engine, cfg: Config) -> list[tuple[datetime, datetime]]:
    with unidad_de_trabajo(engine, escritura=False) as s:
        reservas = s.scalars(select(Reserva).where(Reserva.estado == "PENDIENTE")).all()
        return [
            (
                r.inicio,
                r.inicio
                + timedelta(
                    minutes=minutos_ocupados(cfg.servicio(r.servicio_id), "local", cfg.negocio)
                ),
            )
            for r in reservas
        ]


def _sin_solapes(intervalos: list[tuple[datetime, datetime]]) -> bool:
    ordenados = sorted(intervalos)
    return all(a[1] <= b[0] for a, b in zip(ordenados, ordenados[1:], strict=False))


def test_dos_confirmaciones_simultaneas_una_gana(engine: Engine, cfg: Config) -> None:
    casos = [
        ("573001", "manicure_tradicional", local(3, 9)),
        ("573002", "manicure_tradicional", local(3, 9)),
    ]
    resultados = _correr(engine, cfg, casos)
    assert sorted(resultados) == ["ocupado", "ok"]
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert len(s.scalars(select(Reserva)).all()) == 1
        assert len(s.scalars(select(ReservaBloque)).all()) == 2


def test_servicios_de_distinta_duracion_que_se_solapan(engine: Engine, cfg: Config) -> None:
    casos = [
        ("573001", "acrilicas", local(3, 9)),  # 9:00 a 11:45 (+margen)
        ("573002", "retiro", local(3, 10)),  # dentro de la anterior
    ]
    assert sorted(_correr(engine, cfg, casos)) == ["ocupado", "ok"]


def test_cincuenta_intentos_en_paralelo_no_producen_solapes(engine: Engine, cfg: Config) -> None:
    azar = random.Random(42)  # noqa: S311 - datos de prueba reproducibles
    servicios = [s.id for s in cfg.servicios]
    horas = [(h, m) for h in range(8, 17) for m in (0, 30)]
    casos = [
        (f"5730{i:05d}", azar.choice(servicios), local(3, *azar.choice(horas))) for i in range(50)
    ]
    resultados = _correr(engine, cfg, casos)
    assert resultados.count("ok") >= 1
    assert set(resultados) <= {"ok", "ocupado", "otro"}
    assert _sin_solapes(_intervalos_activos(engine, cfg))
    with unidad_de_trabajo(engine, escritura=False) as s:
        n_reservas = len(s.scalars(select(Reserva)).all())
    assert n_reservas == resultados.count("ok")


def test_cincuenta_intentos_al_mismo_horario_gana_uno(engine: Engine, cfg: Config) -> None:
    casos = [(f"5731{i:05d}", "semipermanente", local(4, 14)) for i in range(50)]
    resultados = _correr(engine, cfg, casos)
    assert resultados.count("ok") == 1
    assert resultados.count("ocupado") == 49
