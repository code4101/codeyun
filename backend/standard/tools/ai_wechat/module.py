from __future__ import annotations

from fastapi import FastAPI

from backend.api.wechat_ilink import router as wechat_ilink_router


def register(app: FastAPI) -> None:
    app.include_router(wechat_ilink_router, prefix="/api/wechat-ilink", tags=["wechat-ilink"])
