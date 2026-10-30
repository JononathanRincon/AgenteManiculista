"""Máquina de estados de la conversación (design.md §B5; SDD §8.1).

Todo texto visible sale de config/mensajes.yaml. El LLM jamás decide transiciones: solo la
selección de una hora ofrecida por el propio sistema puede llevar a CONFIRMANDO.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal

from sqlalchemy.orm import Session

from app.modelos import Conversacion
from app.puertos import Opcion


class Estado(StrEnum):
    INICIO = "INICIO"
    MENU = "MENU"
    ELIGIENDO_SERVICIO = "ELIGIENDO_SERVICIO"
    ELIGIENDO_MODALIDAD = "ELIGIENDO_MODALIDAD"
    ELIGIENDO_DIA = "ELIGIENDO_DIA"
    ELIGIENDO_HORA = "ELIGIENDO_HORA"
    CONFIRMANDO = "CONFIRMANDO"
    GESTIONANDO_CITA = "GESTIONANDO_CITA"
    FINALIZADO = "FINALIZADO"


E = Estado

# Transiciones del diagrama SDD §8.1. Aclaraciones necesarias para que los flujos funcionen:
# volver a elegir otro día desde la hora (ELIGIENDO_HORA -> ELIGIENDO_DIA), elegir el nuevo día al
# reagendar (GESTIONANDO_CITA -> ELIGIENDO_DIA) y volver al menú desde cualquier paso intermedio.
TRANSICIONES: dict[Estado, frozenset[Estado]] = {
    E.INICIO: frozenset({E.MENU, E.ELIGIENDO_HORA}),
    E.MENU: frozenset({E.ELIGIENDO_SERVICIO, E.GESTIONANDO_CITA, E.MENU}),
    E.ELIGIENDO_SERVICIO: frozenset({E.ELIGIENDO_MODALIDAD, E.ELIGIENDO_DIA, E.MENU}),
    E.ELIGIENDO_MODALIDAD: frozenset({E.ELIGIENDO_DIA, E.MENU}),
    E.ELIGIENDO_DIA: frozenset({E.ELIGIENDO_HORA, E.MENU}),
    E.ELIGIENDO_HORA: frozenset({E.CONFIRMANDO, E.ELIGIENDO_DIA, E.MENU}),
    E.CONFIRMANDO: frozenset({E.FINALIZADO, E.ELIGIENDO_HORA, E.MENU}),
    E.GESTIONANDO_CITA: frozenset({E.FINALIZADO, E.ELIGIENDO_DIA, E.MENU}),
    E.FINALIZADO: frozenset({E.INICIO}),
}


class TransicionInvalida(Exception):  # noqa: N818 - nombre del dominio
    """Se intentó una transición que el diagrama no permite."""


@dataclass(frozen=True)
class Entrante:
    telefono: str
    nombre_perfil: str | None
    wa_message_id: str
    texto: str | None
    id_interactivo: str | None
    ts: datetime


@dataclass(frozen=True)
class Saliente:
    tipo: Literal["texto", "botones", "lista"]
    cuerpo: str
    opciones: list[Opcion] = field(default_factory=list)
    boton: str = ""


@dataclass
class Contexto:
    estado: Estado = Estado.INICIO
    servicio_id: str | None = None
    modalidad: str | None = None
    direccion: str | None = None
    esperando_direccion: bool = False
    fecha: str | None = None
    dias_ofrecidos: list[str] = field(default_factory=list)
    horas_ofrecidas: list[str] = field(default_factory=list)
    hora: str | None = None
    reserva_id: int | None = None
    reagendando: int | None = None

    def ir(self, nuevo: Estado) -> None:
        """Cambia de estado solo si el diagrama lo permite."""
        if nuevo not in TRANSICIONES[self.estado]:
            raise TransicionInvalida(f"{self.estado} -> {nuevo}")
        self.estado = nuevo

    def limpiar_seleccion(self) -> None:
        """Olvida lo elegido en el flujo actual."""
        self.servicio_id = self.modalidad = self.direccion = self.fecha = self.hora = None
        self.esperando_direccion = False
        self.dias_ofrecidos, self.horas_ofrecidas = [], []
        self.reserva_id = self.reagendando = None

    def a_json(self) -> str:
        datos = asdict(self)
        datos.pop("estado")
        return json.dumps(datos)

    @classmethod
    def desde(cls, estado: str, crudo: str) -> Contexto:
        return cls(estado=Estado(estado), **json.loads(crudo))


@dataclass
class Sesion:
    """Conversación cargada: contexto y si ya aceptó el aviso de datos."""

    contexto: Contexto
    aviso_aceptado: bool


def cargar_sesion(
    sesion: Session, telefono: str, ahora: datetime, inactividad_minutos: int
) -> Sesion:
    """Carga (o crea) la conversación; reinicia a INICIO tras la inactividad (EARS-CON-02)."""
    fila = sesion.get(Conversacion, telefono)
    if fila is None:
        fila = Conversacion(telefono=telefono, estado=Estado.INICIO, actualizado_en=ahora)
        sesion.add(fila)
        return Sesion(Contexto(), False)
    contexto = Contexto.desde(fila.estado, fila.contexto)
    if ahora - fila.actualizado_en > timedelta(minutes=inactividad_minutos):
        contexto = Contexto()
    return Sesion(contexto, fila.aviso_datos_aceptado)


def guardar_sesion(sesion: Session, telefono: str, estado: Sesion, ahora: datetime) -> None:
    """Persiste el contexto; actualiza `actualizado_en` (también define la ventana de 24 h)."""
    fila = sesion.get(Conversacion, telefono)
    assert fila is not None  # noqa: S101 - cargar_sesion la crea siempre antes
    fila.estado = estado.contexto.estado
    fila.contexto = estado.contexto.a_json()
    fila.aviso_datos_aceptado = estado.aviso_aceptado
    fila.actualizado_en = ahora
