from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from backend.core.project_graph.runtime_bridge import (
    ProjectGraphRuntimeSnapshot,
    is_loopback_host,
    runtime_snapshot_store,
)


router = APIRouter()


def _require_loopback(request: Request) -> None:
    client_host = request.client.host if request.client is not None else None
    if not is_loopback_host(client_host):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Project Graph runtime bridge is only available from this machine",
        )


@router.post("/snapshot")
def publish_project_graph_runtime_snapshot(
    snapshot: ProjectGraphRuntimeSnapshot,
    request: Request,
):
    _require_loopback(request)
    return runtime_snapshot_store.publish(snapshot)


@router.get("/latest")
def get_latest_project_graph_runtime_snapshot(request: Request):
    _require_loopback(request)
    return runtime_snapshot_store.latest()
