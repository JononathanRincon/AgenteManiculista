"""Cola de escritura: FIFO, reclamo atómico e idempotencia (EARS-COL-01/02)."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

from app.cola import (
    clave_idempotencia,
    completar,
    encolar,
    fallar,
    fallidos,
    pausada_hasta,
    pausar,
    profundidad,
    reclamar_siguiente,
    recuperar_huerfanos,
    reintentar,
)
from app.modelos import unidad_de_trabajo
from sqlalchemy import Engine

from tests.integration.conftest import AHORA, local

CAL = "cal"


def _encolar(engine: Engine, n: int, **extra: Any) -> int:
    with unidad_de_trabajo(engine) as s:
        t = encolar(
            s,
            calendario_id=extra.get("calendario_id", CAL),
            tipo="CREAR",
            payload={"n": n},
            clave=f"clave-{n}",
            reserva_id=None,
            ahora=AHORA,
        )
        return t.id


def test_clave_de_idempotencia_es_estable_y_distingue_campos() -> None:
    a = clave_idempotencia("573001", local(3, 9), "CREAR", 1)
    assert a == clave_idempotencia("573001", local(3, 9), "CREAR", 1)
    assert a != clave_idempotencia("573001", local(3, 9), "ELIMINAR", 1)
    assert a != clave_idempotencia("573001", local(3, 9), "CREAR", 2)
    assert a != clave_idempotencia("573002", local(3, 9), "CREAR", 1)
    assert len(a) == 64


def test_clave_duplicada_devuelve_el_mismo_trabajo(engine: Engine) -> None:
    assert _encolar(engine, 1) == _encolar(engine, 1)
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert profundidad(s) == 1


def test_fifo_estricta_y_reclamo_marca_procesando(engine: Engine) -> None:
    primero, segundo = _encolar(engine, 1), _encolar(engine, 2)
    t = reclamar_siguiente(engine, CAL, AHORA)
    assert t is not None
    assert (t.id, t.intentos, t.payload) == (primero, 1, {"n": 1})
    # mientras el primero se procesa, nadie más avanza (FIFO estricta)
    assert reclamar_siguiente(engine, CAL, AHORA) is None
    completar(engine, primero, AHORA, "ok")
    t2 = reclamar_siguiente(engine, CAL, AHORA)
    assert t2 is not None
    assert t2.id == segundo


def test_trabajo_con_proxima_ejecucion_futura_bloquea_la_fila(engine: Engine) -> None:
    primero = _encolar(engine, 1)
    _encolar(engine, 2)
    t = reclamar_siguiente(engine, CAL, AHORA)
    assert t is not None
    reintentar(engine, primero, AHORA + timedelta(seconds=30), AHORA, "503")
    assert reclamar_siguiente(engine, CAL, AHORA) is None  # el segundo no se adelanta
    t_reintento = reclamar_siguiente(engine, CAL, AHORA + timedelta(seconds=31))
    assert t_reintento is not None
    assert (t_reintento.id, t_reintento.intentos) == (primero, 2)


def test_fallar_libera_la_fila_y_cuenta(engine: Engine) -> None:
    primero, segundo = _encolar(engine, 1), _encolar(engine, 2)
    reclamar_siguiente(engine, CAL, AHORA)
    fallar(engine, primero, AHORA, "permanente")
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert fallidos(s) == 1
        assert profundidad(s) == 1
    siguiente = reclamar_siguiente(engine, CAL, AHORA)
    assert siguiente is not None
    assert siguiente.id == segundo


def test_calendarios_independientes(engine: Engine) -> None:
    _encolar(engine, 1)
    assert reclamar_siguiente(engine, "otro", AHORA) is None
    with unidad_de_trabajo(engine, escritura=False) as s:
        assert profundidad(s, CAL) == 1
        assert profundidad(s, "otro") == 0


def test_cola_vacia(engine: Engine) -> None:
    assert reclamar_siguiente(engine, CAL, AHORA) is None


def test_recuperar_huerfanos(engine: Engine) -> None:
    _encolar(engine, 1)
    reclamar_siguiente(engine, CAL, AHORA)
    assert recuperar_huerfanos(engine, AHORA) == 1
    recuperado = reclamar_siguiente(engine, CAL, AHORA)
    assert recuperado is not None
    assert recuperado.intentos == 2


def test_dos_reclamos_simultaneos_nunca_devuelven_el_mismo_trabajo(engine: Engine) -> None:
    for i in range(20):
        _encolar(engine, i)
    barrera = threading.Barrier(8)
    obtenidos: list[int] = []

    def reclamar() -> None:
        barrera.wait(10)
        for _ in range(5):
            t = reclamar_siguiente(engine, CAL, AHORA)
            if t is not None:
                obtenidos.append(t.id)
                completar(engine, t.id, AHORA)

    with ThreadPoolExecutor(max_workers=8) as pool:
        for f in [pool.submit(reclamar) for _ in range(8)]:
            f.result(timeout=60)
    assert len(obtenidos) == len(set(obtenidos))
    assert obtenidos
    assert obtenidos == sorted(obtenidos)  # FIFO: se reclaman en orden de id


def test_pausa_de_cola(engine: Engine) -> None:
    assert pausada_hasta(engine, CAL) is None
    hasta = AHORA + timedelta(seconds=64)
    pausar(engine, CAL, hasta)
    assert pausada_hasta(engine, CAL) == hasta
    pausar(engine, CAL, hasta + timedelta(seconds=1))
    assert pausada_hasta(engine, CAL) == hasta + timedelta(seconds=1)
