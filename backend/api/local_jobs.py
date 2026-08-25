from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.core.access.auth import get_current_active_user
from backend.core.jobs.local_runtime import (
    get_local_job_run,
    get_local_job_spec,
    list_local_job_runs,
    list_local_job_specs,
    request_local_job_cancel,
    serialize_local_job_run,
    submit_local_job,
)
from backend.models import User


router = APIRouter()


class LocalJobSubmitRequest(BaseModel):
    job_type: str = Field(min_length=1, max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)


def _owned_run_or_404(run_id: str, user_id: int):
    run = get_local_job_run(run_id)
    if run is None or run.user_id != int(user_id):
        raise HTTPException(status_code=404, detail="Local Job 不存在")
    return run


@router.get("/types")
def get_local_job_types(_current_user: User = Depends(get_current_active_user)):
    return {"items": list_local_job_specs(user_submittable_only=True)}


@router.get("/runs")
def get_local_job_runs(
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_active_user),
):
    return {
        "items": [
            serialize_local_job_run(run)
            for run in list_local_job_runs(user_id=current_user.id, limit=limit)
        ]
    }


@router.post("/runs")
def start_local_job(
    payload: LocalJobSubmitRequest,
    current_user: User = Depends(get_current_active_user),
):
    try:
        spec = get_local_job_spec(payload.job_type)
        if not spec.user_submittable:
            raise HTTPException(status_code=403, detail="该 Local Job 只能由受信业务入口触发")
        run = submit_local_job(
            job_type=payload.job_type,
            payload=payload.payload,
            user_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return serialize_local_job_run(run)


@router.get("/runs/{run_id}")
def get_local_job(
    run_id: str,
    current_user: User = Depends(get_current_active_user),
):
    return serialize_local_job_run(_owned_run_or_404(run_id, current_user.id))


@router.post("/runs/{run_id}/cancel")
def cancel_local_job(
    run_id: str,
    current_user: User = Depends(get_current_active_user),
):
    _owned_run_or_404(run_id, current_user.id)
    try:
        run = request_local_job_cancel(run_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return serialize_local_job_run(run)
