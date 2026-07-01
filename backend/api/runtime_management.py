from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from backend.core.access.auth import verify_api_token
from backend.core.runtime.management import (
    add_builtin_runtime_job,
    build_runtime_status,
    configure_builtin_runtime_item_autostart,
    configure_builtin_runtime_job_schedule,
    delete_builtin_runtime_job,
    delete_builtin_runtime_queue_task,
    get_runtime_item_logs,
    reset_builtin_runtime_job_schedule,
    list_builtin_runtime_job_catalog,
    run_builtin_runtime_item_action,
    stop_builtin_runtime_item,
    stop_command_runtime_item,
    toggle_builtin_runtime_job,
    trigger_command_runtime_item,
    trigger_builtin_runtime_item,
    trigger_builtin_runtime_job,
)
from backend.core.runtime.system_metrics import get_system_metric_history
from backend.db import get_session
from backend.core.devices.device import BaseDevice


router = APIRouter()


class RuntimeJobToggleRequest(BaseModel):
    enabled: bool


class RuntimeJobScheduleRequest(BaseModel):
    schedule_policy: dict | None = None
    next_run_at: str | None = None


class RuntimeItemAutostartRequest(BaseModel):
    enabled: bool


@router.get("/jobs/catalog")
def list_runtime_job_catalog(
    _token_device: BaseDevice = Depends(verify_api_token),
    session: Session = Depends(get_session),
):
    return list_builtin_runtime_job_catalog(session)


@router.get("/status")
def get_runtime_status(
    token_device: BaseDevice = Depends(verify_api_token),
    session: Session = Depends(get_session),
):
    return build_runtime_status(session, token_device.id)


@router.get("/system-metrics")
def get_runtime_system_metrics(
    hours: int = 24,
    limit: int = 2000,
    token_device: BaseDevice = Depends(verify_api_token),
    session: Session = Depends(get_session),
):
    return get_system_metric_history(session, device_id=token_device.id, hours=hours, limit=limit)


@router.post("/jobs/{job_key}/trigger")
def trigger_runtime_job(
    job_key: str,
    _token_device: BaseDevice = Depends(verify_api_token),
    session: Session = Depends(get_session),
):
    return trigger_builtin_runtime_job(job_key, session)


@router.post("/jobs/{job_key}/add")
def add_runtime_job(
    job_key: str,
    _token_device: BaseDevice = Depends(verify_api_token),
):
    return add_builtin_runtime_job(job_key)


@router.post("/items/{source}/{item_key}/trigger")
def trigger_runtime_item(
    source: str,
    item_key: str,
    _token_device: BaseDevice = Depends(verify_api_token),
    session: Session = Depends(get_session),
):
    if source == "builtin":
        return trigger_builtin_runtime_item(item_key, session)
    if source == "command":
        return trigger_command_runtime_item(item_key, session)
    raise HTTPException(status_code=400, detail="不支持的运行单元来源")


@router.post("/items/{source}/{item_key}/stop")
def stop_runtime_item(
    source: str,
    item_key: str,
    _token_device: BaseDevice = Depends(verify_api_token),
    session: Session = Depends(get_session),
):
    if source == "builtin":
        return stop_builtin_runtime_item(item_key)
    if source == "command":
        return stop_command_runtime_item(item_key, session)
    raise HTTPException(status_code=400, detail="不支持的运行单元来源")


@router.post("/items/{source}/{item_key}/actions/{action_key}")
def run_runtime_item_action(
    source: str,
    item_key: str,
    action_key: str,
    _token_device: BaseDevice = Depends(verify_api_token),
):
    if source == "builtin":
        return run_builtin_runtime_item_action(item_key, action_key)
    raise HTTPException(status_code=400, detail="该运行单元不支持扩展动作")


@router.post("/items/{source}/{item_key}/autostart")
def configure_runtime_item_autostart(
    source: str,
    item_key: str,
    payload: RuntimeItemAutostartRequest,
    _token_device: BaseDevice = Depends(verify_api_token),
):
    if source == "builtin":
        return configure_builtin_runtime_item_autostart(item_key, payload.enabled)
    raise HTTPException(status_code=400, detail="该运行单元不支持开机自启配置")


@router.get("/items/{source}/{item_key}/logs")
def get_runtime_item_logs_route(
    source: str,
    item_key: str,
    n: int = 500,
    token_device: BaseDevice = Depends(verify_api_token),
):
    return get_runtime_item_logs(source, item_key, None, n, device_id=token_device.id)


@router.post("/jobs/{job_key}/toggle")
def toggle_runtime_job(
    job_key: str,
    payload: RuntimeJobToggleRequest,
    _token_device: BaseDevice = Depends(verify_api_token),
    session: Session = Depends(get_session),
):
    return toggle_builtin_runtime_job(job_key, payload.enabled, session)


@router.post("/jobs/{job_key}/schedule")
def configure_runtime_job_schedule(
    job_key: str,
    payload: RuntimeJobScheduleRequest,
    _token_device: BaseDevice = Depends(verify_api_token),
    session: Session = Depends(get_session),
):
    return configure_builtin_runtime_job_schedule(
        job_key,
        payload.schedule_policy,
        session,
        next_run_at=payload.next_run_at,
        next_run_at_provided="next_run_at" in payload.model_fields_set,
    )


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
