from __future__ import annotations

from fastapi import FastAPI

from backend.api.local_jobs import router as local_jobs_router
from backend.api.task_manager import router as task_router


def register(app: FastAPI) -> None:
    app.include_router(task_router, prefix="/api/task", tags=["task"])
    app.include_router(local_jobs_router, prefix="/api/local-jobs", tags=["local-jobs"])
