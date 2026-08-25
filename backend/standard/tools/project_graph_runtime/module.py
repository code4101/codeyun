from __future__ import annotations

from fastapi import FastAPI

from backend.api.project_graph_runtime import router as project_graph_runtime_router


def register(app: FastAPI) -> None:
    app.include_router(
        project_graph_runtime_router,
        prefix="/api/project-graph/runtime",
        tags=["project-graph-runtime"],
    )
