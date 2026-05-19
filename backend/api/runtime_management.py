from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from backend.core.auth import verify_api_token
from backend.core.runtime_management import (
    build_runtime_status,
    delete_builtin_runtime_job,
    delete_builtin_runtime_queue_task,
    reset_builtin_runtime_job_schedule,
    toggle_builtin_runtime_job,
    trigger_command_runtime_item,
    trigger_builtin_runtime_job,
)
from backend.db import get_session
from backend.core.device import BaseDevice


router = APIRouter()


class RuntimeJobToggleRequest(BaseModel):
    enabled: bool


@router.get("/status")
def get_runtime_status(
    token_device: BaseDevice = Depends(verify_api_token),
    session: Session = Depends(get_session),
):
    return build_runtime_status(session, token_device.id)


@router.post("/jobs/{job_key}/trigger")
def trigger_runtime_job(
    job_key: str,
    _token_device: BaseDevice = Depends(verify_api_token),
    session: Session = Depends(get_session),
):
    return trigger_builtin_runtime_job(job_key, session)


@router.post("/items/{source}/{item_key}/trigger")
def trigger_runtime_item(
    source: str,
    item_key: str,
    _token_device: BaseDevice = Depends(verify_api_token),
    session: Session = Depends(get_session),
):
    if source == "builtin":
        return trigger_builtin_runtime_job(item_key, session)
    if source == "command":
        return trigger_command_runtime_item(item_key, session)
    raise HTTPException(status_code=400, detail="不支持的运行单元来源")


@router.post("/jobs/{job_key}/toggle")
def toggle_runtime_job(
    job_key: str,
    payload: RuntimeJobToggleRequest,
    _token_device: BaseDevice = Depends(verify_api_token),
    session: Session = Depends(get_session),
):
    return toggle_builtin_runtime_job(job_key, payload.enabled, session)


@router.delete("/jobs/queue/{task_id}")
def delete_runtime_queue_task(
    task_id: str,
    _token_device: BaseDevice = Depends(verify_api_token),
):
    return delete_builtin_runtime_queue_task(task_id)


@router.delete("/jobs/{job_key}")
def delete_runtime_job(
    job_key: str,
    _token_device: BaseDevice = Depends(verify_api_token),
):
    return delete_builtin_runtime_job(job_key)


@router.post("/jobs/{job_key}/reset-schedule")
def reset_runtime_job_schedule(
    job_key: str,
    _token_device: BaseDevice = Depends(verify_api_token),
):
    return reset_builtin_runtime_job_schedule(job_key)
