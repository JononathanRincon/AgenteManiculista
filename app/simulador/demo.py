"""Demo: misma lógica de conversación que WhatsApp, con transporte y calendario simulados.

Solo cambian el transporte (`TransporteSimulado`) y el calendario (`CalendarioFalso`); el agente,
las reservas y el worker son exactamente los de producción (design.md §B6.4).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import Engine, delete, select

from app.agente import Agente
from app.cache import CacheDisponibilidad
from app.config import Config
from app.conversacion import Entrante, Saliente
from app.modelos import Base, Reserva, unidad_de_trabajo
from app.puertos import Reloj
from app.reloj import RelojSistema
from app.simulador.calendario_falso import CalendarioFalso
from app.simulador.transporte import TransporteSimulado
from app.worker import Worker

TELEFONO_VALIDO = re.compile(r"^[0-9]{6,20}$")


class TelefonoInvalido(ValueError):
    """El teléfono de demo no tiene el formato esperado."""


class Demo:
    def __init__(
        self,
        engine: Engine,
        config: Config,
        reloj: Reloj | None = None,
        *,
        retardo_calendario_s: float = 0.0,
    ) -> None:
        self.engine, self.config = engine, config
        self.reloj: Reloj = reloj or RelojSistema()
        self.calendario = CalendarioFalso(retardo_s=retardo_calendario_s)
        self.transporte = TransporteSimulado()
        self.cache = CacheDisponibilidad(engine, self.calendario, config.negocio, self.reloj)
        self.agente = Agente(engine, config, self.cache, self.reloj)
        self.worker = Worker(
            engine, self.calendario, self.transporte, config, self.reloj, cache=self.cache
        )
        self._oyentes: list[Callable[[], None]] = []
        self.calendario.suscribir(self._notificar)

    # --- eventos en vivo (SSE) ---
    def suscribir(self, oyente: Callable[[], None]) -> None:
        self._oyentes.append(oyente)

    def desuscribir(self, oyente: Callable[[], None]) -> None:
        if oyente in self._oyentes:
            self._oyentes.remove(oyente)

    def _notificar(self) -> None:
        for oyente in list(self._oyentes):
            oyente()

    # --- conversación ---
    def mensaje(
        self, telefono: str, texto: str | None = None, boton_id: str | None = None
    ) -> list[Saliente]:
        """Procesa un mensaje de la clienta con la misma lógica que WhatsApp."""
        if not TELEFONO_VALIDO.match(telefono):
            raise TelefonoInvalido(telefono)
        if boton_id is not None:
            visible = self.transporte.titulo_de(telefono, boton_id) or boton_id
        else:
            visible = texto or ""
        if visible:
            self.transporte.registrar_clienta(telefono, visible)
        entrante = Entrante(
            telefono=telefono,
            nombre_perfil="Clienta de demo",
            wa_message_id=f"demo.{uuid.uuid4().hex}",
            texto=None if boton_id is not None else texto,
            id_interactivo=boton_id,
            ts=self.reloj.ahora(),
        )
        salidas = self.agente.procesar(entrante)
        self.transporte.mostrar(telefono, salidas)
        self._notificar()
        return salidas

    # --- calendario en vivo ---
    def calendario_actual(self) -> list[dict[str, object]]:
        """Eventos confirmados (Calendar simulado) y horarios apartados aún sin confirmar."""
        zona = self.config.negocio.zona
        filas: list[dict[str, object]] = []
        for e in self.calendario.eventos_activos():
            detalle = self.calendario.detalle(e.event_id)
            filas.append(
                {
                    "id": e.event_id,
                    "estado": "confirmada",
                    "titulo": detalle.titulo if detalle else "Evento",
                    "inicio": e.inicio.astimezone(zona).isoformat(),
                    "fin": e.fin.astimezone(zona).isoformat(),
                }
            )
        with unidad_de_trabajo(self.engine, escritura=False) as s:
            pendientes = s.scalars(select(Reserva).where(Reserva.estado == "PENDIENTE")).all()
            for r in pendientes:
                filas.append(
                    {
                        "id": f"reserva-{r.id}",
                        "estado": "apartada",
                        "titulo": self.config.servicio(r.servicio_id).nombre,
                        "inicio": r.inicio.astimezone(zona).isoformat(),
                        "fin": r.fin.astimezone(zona).isoformat(),
                    }
                )
        return sorted(filas, key=lambda f: str(f["inicio"]))

    def reiniciar(self) -> None:
        """Vuelve la demo a cero: calendario, chats y base de datos."""
        self.calendario.reiniciar()
        self.transporte.reiniciar()
        self.cache.reiniciar()
        with unidad_de_trabajo(self.engine) as s:
            for tabla in reversed(Base.metadata.sorted_tables):
                s.execute(delete(tabla))
        self._notificar()

    def ahora(self) -> datetime:
        return self.reloj.ahora()
