"""Resolución determinista por palabras clave y FAQ, sin LLM (design.md §B7.1, EARS-INT-01)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
)

from app.config import ConfiguracionInvalida, leer_yaml
from app.mensajes import Mensajes

DIRECTORIO_CONFIG = Path(__file__).resolve().parents[1] / "config"
INTENCIONES_VALIDAS = frozenset(
    {
        "agendar",
        "reagendar",
        "cancelar",
        "consultar_precio",
        "consultar_horario",
        "consultar_ubicacion",
        "saludo",
    }
)


def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes ni signos; espacios colapsados."""
    base = unicodedata.normalize("NFD", texto.lower())
    sin_tildes = "".join(c for c in base if unicodedata.category(c) != "Mn")
    limpio = re.sub(r"[^a-z0-9ñ ]+", " ", sin_tildes)
    return " ".join(limpio.split())


def _coincide(texto_normalizado: str, palabra: str) -> bool:
    return re.search(rf"\b{re.escape(palabra)}\b", texto_normalizado) is not None


@dataclass(frozen=True)
class Reglas:
    intenciones: dict[str, tuple[str, ...]]
    faq: tuple[tuple[tuple[str, ...], str], ...]


def cargar_reglas(directorio: Path = DIRECTORIO_CONFIG, mensajes: Mensajes | None = None) -> Reglas:
    """Carga intenciones.yaml y faq.yaml; falla al arrancar si algo no cuadra (EARS-CFG-01)."""
    crudo = leer_yaml(directorio / "intenciones.yaml")
    if not isinstance(crudo, dict) or not isinstance(crudo.get("intenciones"), dict):
        raise ConfiguracionInvalida("intenciones.yaml: falta el mapa 'intenciones'")
    intenciones: dict[str, tuple[str, ...]] = {}
    for nombre, palabras in crudo["intenciones"].items():
        if nombre not in INTENCIONES_VALIDAS:
            raise ConfiguracionInvalida(f"intenciones.yaml: intención desconocida '{nombre}'")
        if not isinstance(palabras, list) or not all(isinstance(p, str) for p in palabras):
            raise ConfiguracionInvalida(
                f"intenciones.yaml: '{nombre}' debe ser una lista de textos"
            )
        intenciones[nombre] = tuple(normalizar(p) for p in palabras)
    faq_crudo = leer_yaml(directorio / "faq.yaml")
    if not isinstance(faq_crudo, dict) or not isinstance(faq_crudo.get("faq"), list):
        raise ConfiguracionInvalida("faq.yaml: falta la lista 'faq'")
    faq: list[tuple[tuple[str, ...], str]] = []
    for entrada in faq_crudo["faq"]:
        if not isinstance(entrada, dict) or "palabras" not in entrada or "respuesta" not in entrada:
            raise ConfiguracionInvalida("faq.yaml: cada entrada necesita 'palabras' y 'respuesta'")
        respuesta = str(entrada["respuesta"])
        if mensajes is not None and not mensajes.existe(respuesta):
            raise ConfiguracionInvalida(
                f"faq.yaml: la respuesta '{respuesta}' no está en mensajes.yaml"
            )
        faq.append((tuple(normalizar(str(p)) for p in entrada["palabras"]), respuesta))
    return Reglas(intenciones, tuple(faq))


def resolver_por_palabras_clave(texto: str, reglas: Reglas) -> str | None:
    """Intención por palabras clave (en el orden del YAML) o None si no hay coincidencia."""
    normalizado = normalizar(texto)
    for nombre, palabras in reglas.intenciones.items():
        if any(_coincide(normalizado, p) for p in palabras):
            return nombre
    return None


def resolver_faq(texto: str, reglas: Reglas) -> str | None:
    """Clave del mensaje de FAQ que responde al texto, o None."""
    normalizado = normalizar(texto)
    for palabras, respuesta in reglas.faq:
        if any(_coincide(normalizado, p) for p in palabras):
            return respuesta
    return None


# --- Esquema estricto de la intención interpretada por el LLM (design.md §B7.2) ---
class Intencion(BaseModel):
    """Resultado validado del LLM. Cualquier incoherencia invalida la respuesta completa."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intencion: Literal[
        "agendar",
        "reagendar",
        "cancelar",
        "consultar_precio",
        "consultar_horario",
        "consultar_ubicacion",
        "saludo",
        "desconocida",
    ]
    servicio_id: str | None = None
    fecha_tentativa: date | None = None
    franja: Literal["manana", "tarde", "noche"] | None = None
    confianza: float = Field(ge=0, le=1)

    @field_validator("servicio_id")
    @classmethod
    def _servicio_del_catalogo(cls, valor: str | None, info: ValidationInfo) -> str | None:
        if valor is None:
            return None
        contexto = info.context or {}
        if valor not in contexto.get("catalogo", ()):
            raise ValueError("servicio fuera del catálogo")
        return valor

    @field_validator("fecha_tentativa")
    @classmethod
    def _fecha_no_pasada(cls, valor: date | None, info: ValidationInfo) -> date | None:
        hoy = (info.context or {}).get("hoy")
        if valor is not None and hoy is not None and valor < hoy:
            raise ValueError("la fecha ya pasó")
        return valor


def validar_intencion(
    crudo: str | dict[str, Any], catalogo: set[str] | frozenset[str], hoy: date
) -> Intencion | None:
    """Valida la salida del LLM; devuelve None ante cualquier problema (nunca lanza)."""
    contexto = {"catalogo": frozenset(catalogo), "hoy": hoy}
    try:
        if isinstance(crudo, str):
            return Intencion.model_validate_json(crudo, context=contexto)
        return Intencion.model_validate(crudo, context=contexto)
    except (ValidationError, ValueError, TypeError):
        return None
