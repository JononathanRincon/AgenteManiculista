"""Grafo de dependencias de producción: Calendar real, agente, worker, WhatsApp y canal push.

`crear_app` lo construye al arrancar con `ENTORNO=produccion` (y sin simulador). Los adaptadores
pueden sustituirse (pruebas); por defecto se crean los reales.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass

from pydantic import SecretStr
from sqlalchemy import Engine

from app.agente import Agente
from app.cache import CacheDisponibilidad
from app.calendario import CalendarioGoogle
from app.canal import GestorCanal
from app.config import Config, ConfiguracionInvalida, Settings
from app.control_humano import ControlHumano
from app.humanizacion import SecuenciaEnvio, Sidecar
from app.llm import crear_proveedor
from app.puertos import Calendario, CanalesCalendario, Reloj
from app.reloj import RelojSistema
from app.webhook import Pasarela
from app.whatsapp import ClienteSidecar, MensajeriaBaileys
from app.worker import Worker

OBLIGATORIAS = {
    "GOOGLE_SERVICE_ACCOUNT_JSON": "google_service_account_json",
    "SIDECAR_URL": "sidecar_url",
    "SIDECAR_TOKEN": "sidecar_token",
    "WEBHOOK_SECRET": "webhook_secret",
}


@dataclass
class Produccion:
    calendario: Calendario
    cache: CacheDisponibilidad
    agente: Agente
    mensajeria: MensajeriaBaileys
    pasarela: Pasarela
    worker: Worker
    gestor_canal: GestorCanal
    calendario_id: str

    def alinear_calendario(self) -> None:
        """Push recibido: invalida el caché y resincroniza el espejo (EARS-CAC-02)."""
        self.cache.resincronizar(self.calendario_id)


def exigir_ajustes(ajustes: Settings) -> None:
    """Falla al arrancar, con los nombres de lo que falta, antes de aceptar tráfico."""
    faltan = [nombre for nombre, campo in OBLIGATORIAS.items() if not getattr(ajustes, campo)]
    if faltan:
        raise ConfiguracionInvalida(f"producción exige: {', '.join(faltan)}")


def _valor(valor: SecretStr | str | None) -> str:
    """Valor ya validado por `exigir_ajustes`; sin él, falla en lugar de seguir a medias."""
    if valor is None:
        raise ConfiguracionInvalida("falta una variable obligatoria de producción")
    return valor.get_secret_value() if isinstance(valor, SecretStr) else valor


def construir_produccion(
    ajustes: Settings,
    config: Config,
    engine: Engine,
    reloj: Reloj | None = None,
    *,
    calendario: Calendario | None = None,
    canales: CanalesCalendario | None = None,
    sidecar: Sidecar | None = None,
) -> Produccion:
    exigir_ajustes(ajustes)
    reloj = reloj or RelojSistema()
    negocio = config.negocio
    if calendario is None:
        calendario = CalendarioGoogle.desde_cuenta_de_servicio(
            _valor(ajustes.google_service_account_json), negocio.zona
        )
    if canales is None:
        if not isinstance(calendario, CalendarioGoogle):
            raise ConfiguracionInvalida("sin CalendarioGoogle hay que indicar los canales")
        canales = calendario
    if sidecar is None:
        sidecar = ClienteSidecar(_valor(ajustes.sidecar_url), _valor(ajustes.sidecar_token))
    cache = CacheDisponibilidad(engine, calendario, negocio, reloj)
    control = ControlHumano(engine, negocio)
    secuencia = SecuenciaEnvio(sidecar, negocio.anti_ban, dormir=time.sleep, azar=random.uniform)
    mensajeria = MensajeriaBaileys(secuencia, control, reloj)
    agente = Agente(engine, config, cache, reloj, proveedor=crear_proveedor(ajustes))
    canal_token = ajustes.calendar_channel_token
    gestor = GestorCanal(
        engine,
        canales,
        reloj,
        negocio.calendario_id,
        url=ajustes.calendar_webhook_url,
        token=canal_token.get_secret_value() if canal_token else None,
    )
    return Produccion(
        calendario=calendario,
        cache=cache,
        agente=agente,
        mensajeria=mensajeria,
        pasarela=Pasarela(engine, config, agente, control, mensajeria, reloj),
        worker=Worker(engine, calendario, mensajeria, config, reloj, cache=cache),
        gestor_canal=gestor,
        calendario_id=negocio.calendario_id,
    )
