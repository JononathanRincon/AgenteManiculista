"""Contadores persistentes en la tabla `metricas` (design.md §1.6)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.modelos import Metrica

CACHE_HIT = "cache_hit"
CACHE_MISS = "cache_miss"
MENSAJES_TOTAL = "mensajes_total"
MENSAJES_SIN_LLM = "mensajes_sin_llm"
TRABAJOS_FALLIDOS = "trabajos_fallidos"


def incrementar(sesion: Session, nombre: str, cantidad: int = 1) -> None:
    """Suma `cantidad` al contador, creándolo si no existe (atómico en SQLite)."""
    sentencia = insert(Metrica).values(nombre=nombre, valor=cantidad)
    sesion.execute(
        sentencia.on_conflict_do_update(
            index_elements=[Metrica.nombre], set_={"valor": Metrica.valor + cantidad}
        )
    )


def leer(sesion: Session, nombre: str) -> int:
    """Valor actual del contador (0 si no existe)."""
    valor = sesion.scalar(select(Metrica.valor).where(Metrica.nombre == nombre))
    return valor or 0


def tasa_acierto_cache(sesion: Session) -> float:
    """Aciertos / (aciertos + fallos) del caché; 0.0 sin consultas."""
    hits, misses = leer(sesion, CACHE_HIT), leer(sesion, CACHE_MISS)
    total = hits + misses
    return hits / total if total else 0.0
