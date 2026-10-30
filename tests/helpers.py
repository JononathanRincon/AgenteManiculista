"""Utilidades de prueba para conversar con el agente como lo haría una clienta."""

from __future__ import annotations

from app.agente import Agente
from app.cache import CacheDisponibilidad
from app.config import Config
from app.conversacion import Entrante, Saliente
from app.llm.base import ProveedorLLM
from app.mensajes import configurar
from app.reloj import RelojFalso
from app.simulador.calendario_falso import CalendarioFalso
from sqlalchemy import Engine

from tests.integration.conftest import AHORA


class Chat:
    """Una clienta conversando con el agente; guarda lo último que recibió."""

    def __init__(
        self,
        engine: Engine,
        config: Config,
        *,
        telefono: str = "573001112233",
        nombre: str | None = "María Pérez",
        calendario: CalendarioFalso | None = None,
        reloj: RelojFalso | None = None,
        proveedor: ProveedorLLM | None = None,
    ) -> None:
        configurar(None)
        self.engine, self.config = engine, config
        self.calendario = calendario or CalendarioFalso()
        self.reloj = reloj or RelojFalso(AHORA)
        self.cache = CacheDisponibilidad(engine, self.calendario, config.negocio, self.reloj)
        self.agente = Agente(engine, config, self.cache, self.reloj, proveedor=proveedor)
        self.telefono, self.nombre = telefono, nombre
        self.n = 0
        self.ultimo: list[Saliente] = []

    def _entrante(self, texto: str | None, boton: str | None, wa_id: str | None) -> Entrante:
        self.n += 1
        return Entrante(
            self.telefono,
            self.nombre,
            wa_id or f"wamid.{self.telefono}.{self.n}",
            texto,
            boton,
            self.reloj.ahora(),
        )

    def escribe(self, texto: str, wa_id: str | None = None) -> list[Saliente]:
        self.ultimo = self.agente.procesar(self._entrante(texto, None, wa_id))
        return self.ultimo

    def toca(self, boton: str, wa_id: str | None = None) -> list[Saliente]:
        self.ultimo = self.agente.procesar(self._entrante(None, boton, wa_id))
        return self.ultimo

    def envia_audio(self) -> list[Saliente]:
        self.ultimo = self.agente.procesar(self._entrante(None, None, None))
        return self.ultimo

    def acepta_aviso(self) -> None:
        self.escribe("hola")
        self.toca("aviso:si")

    def ids(self) -> list[str]:
        return [o.id for s in self.ultimo for o in s.opciones]

    def cuerpos(self) -> str:
        return "\n".join(s.cuerpo for s in self.ultimo)

    def primer_id(self, prefijo: str) -> str:
        return next(i for i in self.ids() if i.startswith(prefijo))

    def avanza(self, **kw: float) -> None:
        self.reloj.avanzar(**kw)
