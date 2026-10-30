"""Relojes inyectables (design.md §1.3)."""

from datetime import UTC, datetime, timedelta

from app.reloj import RelojFalso, RelojSistema

T0 = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def test_reloj_falso_avanza_y_se_fija() -> None:
    reloj = RelojFalso(T0)
    reloj.avanzar(seconds=90)
    assert reloj.ahora() == T0 + timedelta(seconds=90)
    reloj.fijar(T0)
    assert reloj.ahora() == T0


def test_reloj_sistema_tiene_zona_utc() -> None:
    ahora = RelojSistema().ahora()
    assert ahora.tzinfo is not None
    assert ahora.utcoffset() == timedelta(0)
