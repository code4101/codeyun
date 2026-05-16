from __future__ import annotations

from fastapi import FastAPI

from backend.api.cluster_services import router as cluster_services_router


def register(app: FastAPI) -> None:
    app.include_router(cluster_services_router, prefix="/api/cluster/services", tags=["cluster-services"])
