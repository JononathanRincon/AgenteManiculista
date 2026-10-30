"""Protección de la línea: pausa por intervención humana, rate limit y nunca en frío.

- Si la dueña responde a mano desde su celular, el bot se calla con esa clienta durante
  `pausa_intervencion_humana_minutos` (EARS-WA-17).
- Tope de mensajes por teléfono y global en una ventana deslizante (EARS-WA-18).
- Solo se escribe a quien escribió primero: debe existir su conversación (EARS-WA-15).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import Engine, delete, func, select

from app.config import Negocio
from app.modelos import Conversacion, EnvioReciente, unidad_de_trabajo


class ControlHumano:
    def __init__(self, engine: Engine, negocio: Negocio) -> None:
        self._engine = engine
        self._pausa = timedelta(minutes=negocio.pausa_intervencion_humana_minutos)
        self._cfg = negocio.anti_ban

    # --- pausa por intervención humana ---
    def pausar(self, telefono: str, ahora: datetime) -> bool:
        """Calla al bot con esta clienta; solo si ella ya escribió (no crea filas nuevas)."""
        with unidad_de_trabajo(self._engine) as s:
            conversacion = s.get(Conversacion, telefono)
            if conversacion is None:
                return False
            conversacion.pausa_humana_hasta = ahora + self._pausa
            return True

    def reanudar(self, telefono: str) -> None:
        """Quita la pausa (por ejemplo, si la dueña lo pide)."""
        with unidad_de_trabajo(self._engine) as s:
            conversacion = s.get(Conversacion, telefono)
            if conversacion is not None:
                conversacion.pausa_humana_hasta = None

    def esta_pausado(self, telefono: str, ahora: datetime) -> bool:
        with unidad_de_trabajo(self._engine, escritura=False) as s:
            conversacion = s.get(Conversacion, telefono)
            hasta = conversacion.pausa_humana_hasta if conversacion is not None else None
        return hasta is not None and hasta > ahora

    # --- rate limit y tráfico solo entrante ---
    def autorizar_envio(self, telefono: str, ahora: datetime) -> bool:
        """Verifica y registra el envío en una sola transacción; False si no corresponde enviar."""
        ventana = ahora - timedelta(minutes=self._cfg.ventana_limite_minutos)
        with unidad_de_trabajo(self._engine) as s:
            if s.get(Conversacion, telefono) is None:  # nunca en frío
                return False
            por_telefono = s.scalar(
                select(func.count())
                .select_from(EnvioReciente)
                .where(EnvioReciente.telefono == telefono, EnvioReciente.enviado_en > ventana)
            )
            global_ = s.scalar(
                select(func.count())
                .select_from(EnvioReciente)
                .where(EnvioReciente.enviado_en > ventana)
            )
            if (por_telefono or 0) >= self._cfg.max_mensajes_por_telefono:
                return False
            if (global_ or 0) >= self._cfg.max_mensajes_globales:
                return False
            s.add(EnvioReciente(telefono=telefono, enviado_en=ahora))
            return True

    def limpiar(self, ahora: datetime) -> int:
        """Borra los envíos que ya salieron de la ventana (mantenimiento)."""
        corte = ahora - timedelta(minutes=self._cfg.ventana_limite_minutos)
        with unidad_de_trabajo(self._engine) as s:
            resultado = s.execute(delete(EnvioReciente).where(EnvioReciente.enviado_en <= corte))
            return int(resultado.rowcount)  # type: ignore[attr-defined]
