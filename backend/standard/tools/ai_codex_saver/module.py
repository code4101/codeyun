from __future__ import annotations

from fastapi import FastAPI

from backend.api.codex_saver import router as codex_saver_router
from backend.core.codex_saver.mcp_server import create_codex_saver_streamable_http_app


def register(app: FastAPI) -> None:
    app.include_router(codex_saver_router, prefix="/api/codex-saver", tags=["codex-saver"])
    app.mount("/api/codex-saver/mcp", create_codex_saver_streamable_http_app(), name="codex-saver-mcp")
