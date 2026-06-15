from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from backend.core.access.auth import verify_api_token
from backend.core.runtime.proxy_traffic_audit import summarize_proxy_traffic
from backend.core.devices.device import BaseDevice


router = APIRouter()


@router.get("/summary")
def get_proxy_traffic_audit_summary(
    hours: int = Query(default=24, ge=1, le=24 * 30),
    limit: int = Query(default=30, ge=1, le=200),
    group_by: str = Query(default="host", pattern="^(host|rule|process|chain)$"),
    _token_device: BaseDevice = Depends(verify_api_token),
):
    return summarize_proxy_traffic(hours=hours, limit=limit, group_by=group_by)
