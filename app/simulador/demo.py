"""Demo: misma lógica de conversación que WhatsApp, con transporte y calendario simulados.

Solo cambian el transporte (`TransporteSimulado`) y el calendario (`CalendarioFalso`); el agente,
las reservas y el worker son exactamente los de producción (design.md §B6.4).
"""

from __future__ import annotations

import re
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

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
NOMBRES_CARRERA = (
    ("Camila", "Valentina"),
    ("Daniela", "Sofía"),
    ("Laura", "Mariana"),
    ("Juliana", "Paola"),
)


class TelefonoInvalido(ValueError):
    """El teléfono de demo no tiene el formato esperado."""


class HorarioNoDisponible(Exception):  # noqa: N818 - nombre del dominio
    """El horario pedido para la prueba de carrera no está libre."""


class CarreraSinGanadora(RuntimeError):
    """No hubo exactamente una ganadora: la garantía anti cita doble habría fallado."""


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
        self._carreras = 0
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
        self,
        telefono: str,
        texto: str | None = None,
        boton_id: str | None = None,
        nombre: str = "Clienta de demo",
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
            nombre_perfil=nombre,
            wa_message_id=f"demo.{uuid.uuid4().hex}",
            texto=None if boton_id is not None else texto,
            id_interactivo=boton_id,
            ts=self.reloj.ahora(),
        )
        salidas = self.agente.procesar(entrante)
        self.transporte.mostrar(telefono, salidas)
        self._notificar()
        return salidas

    # --- prueba de carrera (EARS-SIM-04) ---
    def carrera(
        self, servicio_id: str = "retiro", inicio: datetime | None = None
    ) -> dict[str, dict[str, str]]:
        """Dos clientas ficticias confirman el mismo horario a la vez; gana exactamente una."""
        self.config.servicio(servicio_id)  # KeyError si no existe
        self._carreras += 1
        numero = self._carreras
        nombres = NOMBRES_CARRERA[(numero - 1) % len(NOMBRES_CARRERA)]
        telefonos = (f"5730001{numero:04d}1", f"5730001{numero:04d}2")
        hora_id = ""
        for indice, (telefono, nombre) in enumerate(zip(telefonos, nombres, strict=True)):
            for boton in ("aviso:si", "menu:agendar", f"srv:{servicio_id}"):
                if boton == "aviso:si":
                    self.mensaje(telefono, texto="hola", nombre=nombre)
                salidas = self.mensaje(telefono, boton_id=boton, nombre=nombre)
            salidas = self.mensaje(telefono, boton_id=salidas[-1].opciones[0].id, nombre=nombre)
            horas = [o.id for o in salidas[-1].opciones if o.id.startswith("hora:")]
            if indice == 0:
                hora_id = self._elegir_hora(horas, inicio)
            if hora_id not in horas:
                raise HorarioNoDisponible(hora_id)
            self.mensaje(telefono, boton_id=hora_id, nombre=nombre)
        barrera = threading.Barrier(2)

        def confirmar(telefono: str, nombre: str) -> list[Saliente]:
            barrera.wait(timeout=10)
            return self.mensaje(telefono, boton_id="conf:si", nombre=nombre)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futuros = [
                pool.submit(confirmar, telefono, nombre)
                for telefono, nombre in zip(telefonos, nombres, strict=True)
            ]
            respuestas = [f.result(timeout=30) for f in futuros]
        with unidad_de_trabajo(self.engine, escritura=False) as s:
            consulta = select(Reserva.telefono).where(Reserva.telefono.in_(telefonos))
            con_reserva = set(s.scalars(consulta))
        ganadoras = [i for i, t in enumerate(telefonos) if t in con_reserva]
        if len(ganadoras) != 1:
            raise CarreraSinGanadora(str(ganadoras))
        g, p = ganadoras[0], 1 - ganadoras[0]
        return {
            "ganadora": {"nombre": nombres[g], "telefono": telefonos[g]},
            "rechazada": {
                "nombre": nombres[p],
                "telefono": telefonos[p],
                "mensaje": respuestas[p][0].cuerpo,
            },
        }

    @staticmethod
    def _elegir_hora(horas: list[str], inicio: datetime | None) -> str:
        if not horas:
            raise HorarioNoDisponible("sin horarios")
        if inicio is None:
            return horas[0]
        pedido = f"hora:{inicio.astimezone(UTC).isoformat()}"  # los ids de hora van en UTC
        if pedido not in horas:
            raise HorarioNoDisponible(pedido)
        return pedido

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
