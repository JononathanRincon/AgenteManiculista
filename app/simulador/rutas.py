"""Rutas de la demo. Solo se registran con SIMULADOR=true (EARS-SIM-01, design.md §B6.3)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.limite_tasa import LimiteTasa
from app.simulador.demo import Demo, TelefonoInvalido

ESTATICOS = Path(__file__).resolve().parent / "static"
LATIDO_S = 15.0


class MensajeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    telefono_demo: str = Field(pattern=r"^[0-9]{6,20}$")
    texto: str | None = Field(default=None, max_length=500)
    boton_id: str | None = Field(default=None, max_length=200)


class CarreraIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    servicio_id: str = Field(default="retiro", max_length=64)


def _demo(request: Request) -> Demo:
    demo: Demo = request.app.state.demo
    return demo


def _limite(request: Request) -> None:
    limite: LimiteTasa = request.app.state.limite_demo
    origen = request.client.host if request.client else "desconocido"
    if not limite.permitir(origen):
        detalle = {"codigo": "DEMASIADAS", "mensaje": "Más despacio"}
        raise HTTPException(status_code=429, detail=detalle)


async def flujo_eventos(demo: Demo, latido_s: float = LATIDO_S) -> AsyncGenerator[str, None]:
    """Flujo SSE: emite `cambio` cada vez que el calendario o un chat cambian."""
    cola: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
    bucle = asyncio.get_running_loop()

    def avisar() -> None:
        def poner() -> None:
            if cola.empty():
                cola.put_nowait(None)

        bucle.call_soon_threadsafe(poner)

    demo.suscribir(avisar)
    try:
        yield "retry: 2000\n\n"
        while True:
            try:
                await asyncio.wait_for(cola.get(), timeout=latido_s)
                yield "event: cambio\ndata: {}\n\n"
            except TimeoutError:
                yield ": latido\n\n"
    finally:
        demo.desuscribir(avisar)


def crear_router() -> APIRouter:
    router = APIRouter(prefix="/demo", dependencies=[Depends(_limite)])

    @router.get("", include_in_schema=False)
    async def pagina() -> FileResponse:
        return FileResponse(ESTATICOS / "index.html", media_type="text/html; charset=utf-8")

    @router.post("/api/mensaje")
    async def mensaje(datos: MensajeIn, demo: Annotated[Demo, Depends(_demo)]) -> dict[str, object]:
        try:
            salidas = await asyncio.to_thread(
                demo.mensaje, datos.telefono_demo, datos.texto, datos.boton_id
            )
        except TelefonoInvalido as exc:  # pragma: no cover - ya validado por el esquema
            raise HTTPException(status_code=422, detail="teléfono inválido") from exc
        respuestas = [
            {
                "tipo": s.tipo,
                "cuerpo": s.cuerpo,
                "boton": s.boton,
                "opciones": [o.__dict__ for o in s.opciones],
            }
            for s in salidas
        ]
        return {"respuestas": respuestas}

    @router.get("/api/chat/{telefono}")
    async def chat(telefono: str, demo: Annotated[Demo, Depends(_demo)]) -> dict[str, object]:
        if not telefono.isdigit() or not 6 <= len(telefono) <= 20:
            raise HTTPException(status_code=422, detail="teléfono inválido")
        return {"mensajes": [m.a_dict() for m in demo.transporte.historial(telefono)]}

    @router.get("/api/calendario")
    async def calendario(demo: Annotated[Demo, Depends(_demo)]) -> dict[str, object]:
        return {"eventos": await asyncio.to_thread(demo.calendario_actual)}

    @router.get("/api/eventos")
    async def eventos(demo: Annotated[Demo, Depends(_demo)]) -> StreamingResponse:
        return StreamingResponse(
            flujo_eventos(demo),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/api/reiniciar", status_code=204)
    async def reiniciar(demo: Annotated[Demo, Depends(_demo)]) -> Response:
        await asyncio.to_thread(demo.reiniciar)
        return Response(status_code=204)

    return router
