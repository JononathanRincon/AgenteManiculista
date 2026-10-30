"""Métricas operativas para `/health`: sin datos personales (EARS-OBS-01, NFR-OBS-01)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine

from app.cola import fallidos, profundidad
from app.config import Config
from app.metricas import porcentaje_sin_llm, tasa_acierto_cache
from app.modelos import CalendarioEstado, unidad_de_trabajo


def construir_salud(engine: Engine, config: Config) -> dict[str, Any]:
    """Estado del servicio y de la cola, el caché y el calendario."""
    calendario_id = config.negocio.calendario_id
    with unidad_de_trabajo(engine, escritura=False) as s:
        estado = s.get(CalendarioEstado, calendario_id)
        return {
            "estado": "ok",
            "cola_profundidad": profundidad(s, calendario_id),
            "cache_tasa_acierto": round(tasa_acierto_cache(s), 4),
            "mensajes_sin_llm_pct": round(porcentaje_sin_llm(s), 2),
            "trabajos_fallidos": fallidos(s),
            "modo_degradado": estado.modo_degradado if estado is not None else True,
            "canal_expira_en": _iso(estado.canal_expira_en if estado else None),
            "cola_pausada_hasta": _iso(estado.cola_pausada_hasta if estado else None),
        }


def _iso(instante: Any) -> str | None:
    return instante.isoformat() if instante is not None else None
