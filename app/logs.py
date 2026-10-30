"""Logging JSON: correlation id, teléfono enmascarado y sin secretos (NFR-PRI-02)."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)

_ESTANDAR = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}
_TELEFONO = re.compile(r"(?<![\w.])\+?\d{10,15}(?![\w.])")
_SECRETOS = (
    re.compile(r"(?i)bearer\s+[\w.~+/=-]+"),
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"EAA[A-Za-z0-9]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"(?i)\b(token|secret|api[_-]?key|authorization|password|clave)\b(\s*[=:]\s*)\S+"),
    re.compile(r"sha256=[0-9a-fA-F]{16,}"),
)
MARCA_PROPIA = "_agente_manicurista"


def enmascarar(telefono: str) -> str:
    """'573001112233' -> '***2233' (solo los últimos 4 dígitos)."""
    digitos = "".join(c for c in telefono if c.isdigit())
    return "***" + digitos[-4:] if digitos else "***"


def limpiar_texto(valor: str) -> str:
    """Quita secretos y enmascara cualquier teléfono que aparezca dentro de un texto."""
    for patron in _SECRETOS:
        valor = patron.sub(
            lambda m: f"{m.group(1)}{m.group(2)}[oculto]" if m.lastindex == 2 else "[oculto]", valor
        )
    return _TELEFONO.sub(lambda m: enmascarar(m.group(0)), valor)


def nuevo_correlation_id() -> str:
    """Genera y fija el id de correlación del mensaje o petición en curso."""
    valor = uuid.uuid4().hex[:12]
    correlation_id.set(valor)
    return valor


class FiltroSecretos(logging.Filter):
    """Última defensa: limpia el mensaje y los campos extra de cualquier registro."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = limpiar_texto(record.getMessage())
        record.args = None
        for nombre, valor in list(record.__dict__.items()):
            if nombre in _ESTANDAR:
                continue
            if nombre.startswith("telefono") and isinstance(valor, str):
                record.__dict__[nombre] = enmascarar(valor)
            elif isinstance(valor, str):
                record.__dict__[nombre] = limpiar_texto(valor)
        return True


class FormateadorJSON(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        cuerpo: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "nivel": record.levelname,
            "evento": limpiar_texto(record.getMessage()),
            "logger": record.name,
            "correlation_id": correlation_id.get(),
        }
        for nombre, valor in record.__dict__.items():
            if nombre not in _ESTANDAR and nombre != MARCA_PROPIA:
                cuerpo[nombre] = valor
        if record.exc_info and record.exc_info[0] is not None:
            cuerpo["excepcion"] = record.exc_info[0].__name__  # sin traza: puede traer datos
        return json.dumps(cuerpo, ensure_ascii=False, default=str)


def configurar_logging(nivel: str = "INFO") -> None:
    """Instala el handler JSON en la raíz una sola vez; vuelve a llamar solo ajusta el nivel."""
    raiz = logging.getLogger()
    raiz.setLevel(nivel)
    if any(getattr(h, MARCA_PROPIA, False) for h in raiz.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(FormateadorJSON())
    handler.addFilter(FiltroSecretos())
    setattr(handler, MARCA_PROPIA, True)
    raiz.addHandler(handler)
