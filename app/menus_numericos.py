"""Menús numerados: botones y listas como un texto y la respuesta de vuelta al id (EARS-WA-10).

`CONFIRMANDO` sigue siendo inalcanzable salvo tocando una hora que el propio agente ofreció: este
módulo solo traduce "2" o el nombre de una opción al id que se ofreció en el último menú; todo lo
demás sigue como texto libre.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.conversacion import Saliente
from app.intencion import normalizar
from app.mensajes import texto
from app.modelos import OpcionesOfrecidas
from app.puertos import Opcion

_NUMERO = re.compile(r"^(?:(?:la|el)\s+)?(?:(?:opcion|numero|n)\s+)?(\d{1,2})$")
MIN_PARCIAL = 3


@dataclass(frozen=True)
class MensajeTexto:
    """Lo que realmente se envía por WhatsApp: un texto y el menú que ofrece (si lo hay)."""

    cuerpo: str
    opciones: list[Opcion]


def _keycap(numero: int) -> str:
    return "".join(f"{digito}️⃣" for digito in str(numero))


def degradar(salida: Saliente) -> MensajeTexto:
    """Convierte botones o lista en un único texto numerado."""
    if not salida.opciones:
        return MensajeTexto(salida.cuerpo, [])
    lineas = [salida.cuerpo, ""]
    for indice, opcion in enumerate(salida.opciones, start=1):
        detalle = f" — {opcion.descripcion}" if opcion.descripcion else ""
        lineas.append(f"{_keycap(indice)} {opcion.titulo}{detalle}")
    lineas.extend(["", texto("menu_numerico_pie")])
    return MensajeTexto("\n".join(lineas), list(salida.opciones))


def componer(salidas: list[Saliente]) -> MensajeTexto | None:
    """Une toda la respuesta del agente en UN mensaje (menos mensajes = menos señal de bot)."""
    if not salidas:
        return None
    partes = [degradar(s) for s in salidas]
    menu = next((p.opciones for p in reversed(partes) if p.opciones), [])
    return MensajeTexto("\n\n".join(p.cuerpo for p in partes), menu)


def _clave(valor: str) -> str:
    return normalizar(valor).replace(" ", "")


def mapear_respuesta(mensaje: str, opciones: list[Opcion]) -> str | None:
    """'2', 'opción 2' o el nombre de una opción → id ofrecido; None si no corresponde."""
    normalizado = normalizar(mensaje)
    if not normalizado or not opciones:
        return None
    numero = _NUMERO.match(normalizado)
    if numero is not None:
        posicion = int(numero.group(1))
        return opciones[posicion - 1].id if 1 <= posicion <= len(opciones) else None
    compacto = _clave(mensaje)
    exactas = [o for o in opciones if _clave(o.titulo) == compacto]
    if len(exactas) == 1:
        return exactas[0].id
    if len(compacto) >= MIN_PARCIAL:
        parciales = [o for o in opciones if compacto in _clave(o.titulo)]
        if len(parciales) == 1:
            return parciales[0].id
    return None


# --- persistencia del último menú ofrecido a cada teléfono ---
def guardar_menu(sesion: Session, telefono: str, opciones: list[Opcion], ahora: datetime) -> None:
    """Recuerda el menú enviado; un mensaje sin opciones borra el anterior."""
    fila = sesion.get(OpcionesOfrecidas, telefono)
    cuerpo = json.dumps([{"id": o.id, "titulo": o.titulo} for o in opciones])
    if fila is None:
        sesion.add(OpcionesOfrecidas(telefono=telefono, opciones=cuerpo, actualizado_en=ahora))
    else:
        fila.opciones, fila.actualizado_en = cuerpo, ahora


def resolver_entrada(
    sesion: Session, telefono: str, mensaje: str, ahora: datetime, vigencia_minutos: int
) -> str | None:
    """Id interactivo correspondiente a la respuesta de la clienta, o None si es texto libre."""
    fila = sesion.get(OpcionesOfrecidas, telefono)
    if fila is None or ahora - fila.actualizado_en > timedelta(minutes=vigencia_minutos):
        return None
    opciones = [Opcion(o["id"], o["titulo"]) for o in json.loads(fila.opciones)]
    return mapear_respuesta(mensaje, opciones)
