"""Rutas administrativas protegidas por token (vinculación del WhatsApp de la dueña)."""

from __future__ import annotations

import asyncio
import hmac

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.limite_tasa import LimiteTasa
from app.whatsapp import ClienteSidecar, ErrorSidecar


def _autorizar(request: Request) -> None:
    limite: LimiteTasa = request.app.state.limite_admin
    origen = request.client.host if request.client else "desconocido"
    if not limite.permitir(origen):
        raise HTTPException(status_code=429, detail="demasiados intentos")
    recibido = request.headers.get("x-admin-token", "")
    esperado: str = request.app.state.admin_token
    if not recibido or not hmac.compare_digest(recibido.encode(), esperado.encode()):
        raise HTTPException(status_code=403, detail="no autorizado")


def crear_router_admin() -> APIRouter:
    router = APIRouter(prefix="/admin")

    @router.get("/whatsapp/qr")
    async def qr(request: Request) -> PlainTextResponse:
        """QR para vincular el celular (WhatsApp > Dispositivos vinculados), solo con token."""
        _autorizar(request)
        sidecar: ClienteSidecar | None = getattr(request.app.state, "sidecar", None)
        if sidecar is None:
            raise HTTPException(status_code=503, detail="sidecar no configurado")
        try:
            codigo = await asyncio.to_thread(sidecar.qr)
        except ErrorSidecar as exc:
            raise HTTPException(status_code=503, detail="sidecar no disponible") from exc
        if codigo is None:
            raise HTTPException(status_code=404, detail="no hay un QR pendiente")
        return PlainTextResponse(codigo, headers={"Cache-Control": "no-store"})

    return router
