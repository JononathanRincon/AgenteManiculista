"""Aplicación FastAPI: fábrica, ciclo de vida (worker y limpieza) y rutas condicionales."""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Engine

from app.config import Config, Settings, cargar_config
from app.limite_tasa import LimiteTasa
from app.logs import configurar_logging, nuevo_correlation_id
from app.mensajes import cargar_mensajes, configurar
from app.modelos import crear_engine, crear_tablas
from app.puertos import Reloj
from app.salud import construir_salud
from app.simulador.demo import Demo
from app.simulador.rutas import ESTATICOS, crear_router
from app.webhook import Pasarela, crear_router_whatsapp

DIRECTORIO_CONFIG = Path(__file__).resolve().parents[1] / "config"
CABECERAS_DEMO = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


class MasDeUnProceso(RuntimeError):
    """El worker único y SQLite exigen un solo proceso (design.md §5)."""


def verificar_un_solo_proceso() -> None:
    """Falla al arrancar si la configuración pide más de un worker de Uvicorn."""
    if int(os.environ.get("WEB_CONCURRENCY", "1") or "1") > 1:
        raise MasDeUnProceso("WEB_CONCURRENCY > 1: ejecuta un solo proceso (--workers 1)")


def _montar_demo(app: FastAPI) -> None:
    """Rutas, archivos estáticos y cabeceras de seguridad de /demo (solo con SIMULADOR=true)."""
    app.state.limite_demo = LimiteTasa(maximo=300, ventana_s=60)
    app.include_router(crear_router())
    app.mount("/demo/static", StaticFiles(directory=ESTATICOS), name="demo-static")

    @app.middleware("http")
    async def cabeceras_demo(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        respuesta = await call_next(request)
        if request.url.path.startswith("/demo"):
            for nombre, valor in CABECERAS_DEMO.items():
                respuesta.headers[nombre] = valor
        return respuesta


def _montar_base(app: FastAPI, negocio: Config) -> None:
    """Correlation id por petición y /health."""

    @app.middleware("http")
    async def correlacion(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        respuesta_id = nuevo_correlation_id()
        respuesta = await call_next(request)
        respuesta.headers["X-Correlation-Id"] = respuesta_id
        return respuesta

    @app.get("/health")
    async def health(request: Request) -> Response:
        engine = getattr(request.app.state, "engine", None)
        if engine is None:
            return JSONResponse({"estado": "iniciando"}, status_code=503)
        datos = await asyncio.to_thread(construir_salud, engine, negocio)
        return JSONResponse(datos)


def _arrancar_simulador(
    app: FastAPI, ajustes: Settings, negocio: Config, reloj: Reloj | None, parar: asyncio.Event
) -> list[asyncio.Task[None]]:
    demo = Demo(
        app.state.engine, negocio, reloj, retardo_calendario_s=ajustes.demo_retardo_calendario_s
    )
    app.state.demo = demo
    return [
        asyncio.create_task(demo.worker.correr(parar)),
        asyncio.create_task(demo.worker.correr_limpieza(parar)),
    ]


def crear_app(
    settings: Settings | None = None,
    config: Config | None = None,
    reloj: Reloj | None = None,
    fabrica_pasarela: Callable[[Engine], Pasarela] | None = None,
) -> FastAPI:
    """Construye la aplicación; las rutas de /demo solo existen con SIMULADOR=true."""
    ajustes = settings or Settings()
    negocio = config or cargar_config(DIRECTORIO_CONFIG)

    @contextlib.asynccontextmanager
    async def ciclo_de_vida(app: FastAPI) -> AsyncIterator[None]:
        verificar_un_solo_proceso()
        configurar_logging(ajustes.log_level)
        configurar(cargar_mensajes())
        engine = crear_engine(ajustes.database_url)
        crear_tablas(engine)
        app.state.engine = engine
        if fabrica_pasarela is not None:
            app.state.pasarela = fabrica_pasarela(engine)
        parar = asyncio.Event()
        tareas = (
            _arrancar_simulador(app, ajustes, negocio, reloj, parar) if ajustes.simulador else []
        )
        try:
            yield
        finally:
            parar.set()
            pasarela = getattr(app.state, "pasarela", None)
            if pasarela is not None:
                await pasarela.esperar_tareas()
            await asyncio.gather(*tareas, return_exceptions=True)
            engine.dispose()

    app = FastAPI(
        title="AgenteManiculista",
        lifespan=ciclo_de_vida,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = ajustes
    app.state.config = negocio
    _montar_base(app, negocio)
    if ajustes.webhook_secret is not None:
        app.state.webhook_secreto = ajustes.webhook_secret.get_secret_value()
        app.include_router(crear_router_whatsapp())
    if ajustes.simulador:
        _montar_demo(app)
    return app


app = crear_app()
