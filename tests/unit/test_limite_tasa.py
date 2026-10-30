"""Límite de tasa por clave con ventana deslizante."""

from app.limite_tasa import LimiteTasa


class Reloj:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_permite_hasta_el_maximo_y_libera_al_pasar_la_ventana() -> None:
    reloj = Reloj()
    limite = LimiteTasa(maximo=3, ventana_s=10, reloj=reloj)
    assert [limite.permitir("a") for _ in range(4)] == [True, True, True, False]
    assert limite.permitir("b") is True  # otra clave no se ve afectada
    reloj.t = 10.0
    assert limite.permitir("a") is True


def test_poda_las_claves_vencidas_cuando_hay_demasiadas() -> None:
    reloj = Reloj()
    limite = LimiteTasa(maximo=1, ventana_s=5, reloj=reloj)
    for i in range(10_001):
        limite.permitir(f"ip{i}")
    reloj.t = 6.0
    assert limite.permitir("nueva") is True
    assert limite.permitir("nueva") is False
    assert len(limite._eventos) < 10_001  # noqa: SLF001 - se podaron las vencidas
