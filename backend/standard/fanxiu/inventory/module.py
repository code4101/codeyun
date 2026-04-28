from __future__ import annotations

from fastapi import FastAPI

from backend.api.fanxiu import inventory_router as fanxiu_inventory_router


def register(app: FastAPI) -> None:
    app.include_router(fanxiu_inventory_router, prefix="/api/fanxiu", tags=["fanxiu"])
