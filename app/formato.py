"""Formato de fechas, horas y precios en español colombiano, con textos del catálogo."""

from __future__ import annotations

from datetime import datetime, time

from app.config import Negocio
from app.mensajes import texto


def precio(valor: int) -> str:
    """30000 -> '$30.000'."""
    return texto("precio_formato", valor=f"{valor:,}".replace(",", "."))


def hora(instante: datetime, negocio: Negocio) -> str:
    """'3:00 p. m.' en la zona del negocio."""
    local = instante.astimezone(negocio.zona)
    sufijo = texto("hora_pm") if local.hour >= 12 else texto("hora_am")
    return f"{local.hour % 12 or 12}:{local.minute:02d} {sufijo}"


def hora_reloj(valor: time) -> str:
    """Hora de pared (apertura/cierre) como '8:00 a. m.'."""
    sufijo = texto("hora_pm") if valor.hour >= 12 else texto("hora_am")
    return f"{valor.hour % 12 or 12}:{valor.minute:02d} {sufijo}"


def fecha(instante: datetime, negocio: Negocio) -> str:
    """'martes 3 de noviembre'."""
    local = instante.astimezone(negocio.zona)
    dia = texto(f"dia_{local.weekday()}")
    return f"{dia} {local.day} de {texto(f'mes_{local.month}')}"


def fecha_corta(instante: datetime, negocio: Negocio) -> str:
    """'Mar 3 nov' para títulos de lista (máx. 24 caracteres)."""
    local = instante.astimezone(negocio.zona)
    return (
        f"{texto(f'dia_abrev_{local.weekday()}')} {local.day} {texto(f'mes_abrev_{local.month}')}"
    )
