"""Adaptador de Gemini (Flash-Lite): una llamada, sin streaming, 3 s de tope, salida validada.

Campos verificados contra `google-genai` 2.34 (`types.GenerateContentConfig`:
`response_mime_type`, `response_json_schema`, `http_options.timeout` en milisegundos).
Nunca lanza ni registra el texto enviado: ante cualquier fallo devuelve None (EARS-INT-03/04).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from google import genai
from google.genai import errors, types

from app.config import Settings
from app.intencion import Intencion, construir_prompt, validar_intencion
from app.llm.base import ServicioResumen

log = logging.getLogger("llm.gemini")

TIMEOUT_S = 3.0
MAX_TOKENS_SALIDA = 200


class ProveedorGemini:
    def __init__(self, cliente: Any, modelo: str, timeout_s: float = TIMEOUT_S) -> None:
        self._cliente = cliente
        self._modelo = modelo
        self._timeout_s = timeout_s

    @classmethod
    def desde_settings(cls, settings: Settings) -> ProveedorGemini:
        """Crea el cliente real; la clave llega solo por entorno."""
        assert settings.gemini_api_key is not None  # noqa: S101 - Settings ya lo exige
        cliente = genai.Client(api_key=settings.gemini_api_key.get_secret_value())
        return cls(cliente, settings.gemini_model)

    def interpretar(
        self, texto: str, catalogo: list[ServicioResumen], hoy: date, estado: str
    ) -> Intencion | None:
        prompt = construir_prompt(texto, [(s.id, s.nombre) for s in catalogo], hoy, estado)
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=Intencion.model_json_schema(),
            temperature=0,
            max_output_tokens=MAX_TOKENS_SALIDA,
            http_options=types.HttpOptions(timeout=int(self._timeout_s * 1000)),
        )
        try:
            respuesta = self._cliente.models.generate_content(
                model=self._modelo, contents=prompt, config=config
            )
            crudo = respuesta.text
        except errors.APIError as exc:  # 429, cuota, 5xx, modelo inexistente
            log.warning("gemini_error_api", extra={"codigo": exc.code})
            return None
        except Exception as exc:  # noqa: BLE001 - timeout, red o cualquier fallo: cae al menú
            log.warning("gemini_error", extra={"tipo": type(exc).__name__})
            return None
        if not isinstance(crudo, str):
            return None
        return validar_intencion(crudo, {s.id for s in catalogo}, hoy)
