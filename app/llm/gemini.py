"""Adaptador de Gemini (Flash-Lite): una llamada, sin streaming, 3 s de tope, salida validada.

Campos verificados contra `google-genai` 2.34 (`types.GenerateContentConfig`:
`response_mime_type`, `response_json_schema`, `http_options.timeout` en milisegundos).
Nunca lanza ni registra el texto enviado: ante cualquier fallo devuelve None (EARS-INT-03/04).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
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
FALLOS_PARA_ABRIR = 3
PAUSA_CIRCUITO_S = 60.0


class ProveedorGemini:
    def __init__(
        self,
        cliente: Any,
        modelo: str,
        timeout_s: float = TIMEOUT_S,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cliente = cliente
        self._modelo = modelo
        self._timeout_s = timeout_s
        self._reloj = reloj
        self._fallos_seguidos = 0
        self._abierto_hasta = 0.0

    @classmethod
    def desde_settings(cls, settings: Settings) -> ProveedorGemini:
        """Crea el cliente real; la clave llega solo por entorno."""
        assert settings.gemini_api_key is not None  # noqa: S101 - Settings ya lo exige
        cliente = genai.Client(api_key=settings.gemini_api_key.get_secret_value())
        return cls(cliente, settings.gemini_model)

    def _registrar_fallo(self) -> None:
        """Tras varios fallos seguidos se pausa el proveedor un minuto (cuota o clave inválida)."""
        self._fallos_seguidos += 1
        if self._fallos_seguidos >= FALLOS_PARA_ABRIR:
            self._abierto_hasta = self._reloj() + PAUSA_CIRCUITO_S
            self._fallos_seguidos = 0
            log.error("gemini_circuito_abierto")

    def interpretar(
        self, texto: str, catalogo: list[ServicioResumen], hoy: date, estado: str
    ) -> Intencion | None:
        if self._reloj() < self._abierto_hasta:  # circuito abierto: no se insiste
            return None
        prompt = construir_prompt(texto, [(s.id, s.nombre) for s in catalogo], hoy, estado)
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=Intencion.model_json_schema(),
            temperature=0,
            max_output_tokens=MAX_TOKENS_SALIDA,
            http_options=types.HttpOptions(
                timeout=int(self._timeout_s * 1000),
                retry_options=types.HttpRetryOptions(attempts=1),  # una sola llamada
            ),
        )
        try:
            respuesta = self._cliente.models.generate_content(
                model=self._modelo, contents=prompt, config=config
            )
            crudo = respuesta.text
        except errors.APIError as exc:  # 429, cuota, 5xx, modelo inexistente
            log.warning("gemini_error_api", extra={"codigo": exc.code})
            self._registrar_fallo()
            return None
        except Exception as exc:  # noqa: BLE001 - timeout, red o cualquier fallo: cae al menú
            log.warning("gemini_error", extra={"tipo": type(exc).__name__})
            self._registrar_fallo()
            return None
        self._fallos_seguidos = 0
        if not isinstance(crudo, str):
            return None
        return validar_intencion(crudo, {s.id for s in catalogo}, hoy)
