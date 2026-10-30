"""Aplicación FastAPI: fábrica, ciclo de vida (worker y limpieza) y rutas condicionales."""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request, Response

from app.config import Config, Settings, cargar_config
from app.limite_tasa import LimiteTasa
from app.mensajes import cargar_mensajes, configurar
from app.modelos import crear_engine, crear_tablas
from app.puertos import Reloj
from app.simulador.demo import Demo
from app.simulador.rutas import crear_router

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


def crear_app(
    settings: Settings | None = None,
    config: Config | None = None,
    reloj: Reloj | None = None,
) -> FastAPI:
    """Construye la aplicación; las rutas de /demo solo existen con SIMULADOR=true."""
    ajustes = settings or Settings()
    negocio = config or cargar_config(DIRECTORIO_CONFIG)

    @contextlib.asynccontextmanager
    async def ciclo_de_vida(app: FastAPI) -> AsyncIterator[None]:
        verificar_un_solo_proceso()
        configurar(cargar_mensajes())
        engine = crear_engine(ajustes.database_url)
        crear_tablas(engine)
        app.state.engine = engine
        parar = asyncio.Event()
        tareas: list[asyncio.Task[None]] = []
        if ajustes.simulador:
            demo = Demo(
                engine, negocio, reloj, retardo_calendario_s=ajustes.demo_retardo_calendario_s
            )
            app.state.demo = demo
            tareas = [
                asyncio.create_task(demo.worker.correr(parar)),
                asyncio.create_task(demo.worker.correr_limpieza(parar)),
            ]
        try:
            yield
        finally:
            parar.set()
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

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"estado": "ok"}

    if ajustes.simulador:
        app.state.limite_demo = LimiteTasa(maximo=300, ventana_s=60)
        app.include_router(crear_router())

        @app.middleware("http")
        async def cabeceras_demo(
            request: Request, call_next: Callable[[Request], Awaitable[Response]]
        ) -> Response:
            respuesta = await call_next(request)
            if request.url.path.startswith("/demo"):
                for nombre, valor in CABECERAS_DEMO.items():
                    respuesta.headers[nombre] = valor
            return respuesta

    return app


app = crear_app()
