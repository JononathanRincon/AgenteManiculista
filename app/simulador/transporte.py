"""Transporte simulado: `Mensajeria` que guarda la conversación en memoria (EARS-SIM-02)."""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from typing import Literal

from app.conversacion import Saliente
from app.puertos import Opcion

MAX_MENSAJES_POR_CHAT = 200


@dataclass(frozen=True)
class MensajeChat:
    de: Literal["clienta", "bot"]
    tipo: Literal["texto", "botones", "lista"]
    cuerpo: str
    opciones: list[Opcion] = field(default_factory=list)
    boton: str = ""

    def a_dict(self) -> dict[str, object]:
        return asdict(self)


class TransporteSimulado:
    """Guarda lo que vería la clienta en WhatsApp; no habla con ninguna red."""

    def __init__(self) -> None:
        self._chats: dict[str, list[MensajeChat]] = {}
        self._lock = threading.Lock()

    # --- puerto Mensajeria ---
    def enviar_texto(self, telefono: str, texto: str) -> None:
        self._agregar(telefono, MensajeChat("bot", "texto", texto))

    def enviar_botones(self, telefono: str, cuerpo: str, opciones: list[Opcion]) -> None:
        self._agregar(telefono, MensajeChat("bot", "botones", cuerpo, list(opciones)))

    def enviar_lista(self, telefono: str, cuerpo: str, boton: str, filas: list[Opcion]) -> None:
        self._agregar(telefono, MensajeChat("bot", "lista", cuerpo, list(filas), boton))

    # --- uso del simulador ---
    def mostrar(self, telefono: str, salientes: list[Saliente]) -> None:
        """Entrega al chat las respuestas del agente."""
        for s in salientes:
            self._agregar(telefono, MensajeChat("bot", s.tipo, s.cuerpo, list(s.opciones), s.boton))

    def registrar_clienta(self, telefono: str, texto: str) -> None:
        self._agregar(telefono, MensajeChat("clienta", "texto", texto))

    def titulo_de(self, telefono: str, opcion_id: str) -> str | None:
        """Título de la opción ofrecida con ese id en el último menú del chat."""
        with self._lock:
            historial = list(self._chats.get(telefono, []))
        for mensaje in reversed(historial):
            for opcion in mensaje.opciones:
                if opcion.id == opcion_id:
                    return opcion.titulo
        return None

    def historial(self, telefono: str) -> list[MensajeChat]:
        with self._lock:
            return list(self._chats.get(telefono, []))

    def reiniciar(self) -> None:
        with self._lock:
            self._chats.clear()

    def _agregar(self, telefono: str, mensaje: MensajeChat) -> None:
        with self._lock:
            chat = self._chats.setdefault(telefono, [])
            chat.append(mensaje)
            del chat[:-MAX_MENSAJES_POR_CHAT]
