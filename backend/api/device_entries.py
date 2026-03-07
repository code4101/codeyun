import shlex
import subprocess
import sys
import time
import uuid
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response
from sqlmodel import Session, select

from backend.api.task_manager import CreateTaskRequest, UpdateTaskRequest, task_manager
from backend.core.auth import get_current_user_from_token
from backend.core.device import BaseDevice, device_manager, get_device_id
from backend.db import get_session
from backend.models import Task as TaskModel
from backend.models import User, UserDevice

router = APIRouter()


def _get_entry_or_404(session: Session, current_user: User, entry_id: str) -> UserDevice:
    entry = session.get(UserDevice, entry_id)
    if not entry or entry.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Device entry not found")
    if not entry.is_active:
        raise HTTPException(status_code=400, detail="Device entry is inactive")
    return entry


def _ensure_local_entry(entry: UserDevice) -> None:
    if entry.mode != "local":
        raise HTTPException(status_code=400, detail="This entry is not a local entry")

    local_device_id = get_device_id()
    if entry.device_id != local_device_id:
        raise HTTPException(status_code=409, detail="Local entry device_id does not match current node")


def _remote_base_url(entry: UserDevice) -> str:
    if entry.mode != "remote":
        raise HTTPException(status_code=400, detail="This entry is not a remote entry")
    if not entry.server_url:
        raise HTTPException(status_code=400, detail="Remote entry has no server_url configured")
    return entry.server_url.rstrip("/")


def _proxy_headers(entry: UserDevice) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {entry.token}",
        "X-Device-Token": entry.token,
    }


def _proxy_response(resp: requests.Response) -> Response:
    content_type = resp.headers.get("content-type", "")
    if "application/json" in content_type.lower():
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=content_type or None,
    )


def _proxy_request(
    entry: UserDevice,
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Any] = None,
) -> Response:
    target_url = f"{_remote_base_url(entry)}/api{path}"
    try:
        resp = requests.request(
            method=method,
            url=target_url,
            headers=_proxy_headers(entry),
            params=params,
            json=json_body,
            timeout=10,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Failed to reach remote device: {exc}") from exc
    return _proxy_response(resp)


def _get_scoped_task(session: Session, task_id: str, device_id: str) -> TaskModel:
    task = session.get(TaskModel, task_id)
    if not task or task.device_id != device_id:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


def _get_local_device(entry: UserDevice) -> BaseDevice:
    _ensure_local_entry(entry)
    device = device_manager.get_device(entry.device_id)
    if not device:
        raise HTTPException(status_code=500, detail="Local device unavailable")
    return device


def _list_local_tasks(session: Session, entry: UserDevice) -> List[Dict[str, Any]]:
    device = _get_local_device(entry)
    stmt = (
        select(TaskModel)
        .where(TaskModel.device_id == entry.device_id)
        .order_by(TaskModel.order, TaskModel.created_at)
    )
    tasks = session.exec(stmt).all()
    device.scan_running_tasks(tasks)

    results = []
    for task in tasks:
        status = device.get_task_status(task.id)
        task_dict = task.model_dump()
        task_dict["status"] = status.model_dump()
        results.append(task_dict)
    return results


def _create_local_task(session: Session, entry: UserDevice, req: CreateTaskRequest) -> Dict[str, Any]:
    _get_local_device(entry)
    last_task = session.exec(
        select(TaskModel)
        .where(TaskModel.device_id == entry.device_id)
        .order_by(TaskModel.order.desc(), TaskModel.created_at.desc())
    ).first()
    next_order = 0 if not last_task or last_task.order is None else last_task.order + 1

    new_task = TaskModel(
        id=str(uuid.uuid4()),
        name=req.name,
        command=req.command,
        cwd=req.cwd,
        description=req.description,
        device_id=entry.device_id,
        schedule=req.schedule,
        timeout=req.timeout,
        created_at=time.time(),
        order=next_order,
    )
    session.add(new_task)
    session.commit()
    session.refresh(new_task)

    if req.schedule:
        task_manager.update_schedule(new_task.id, req.schedule)

    return new_task.model_dump()


def _delete_local_task(session: Session, entry: UserDevice, task_id: str) -> Dict[str, str]:
    device = _get_local_device(entry)
    task = _get_scoped_task(session, task_id, entry.device_id)
    task_manager.update_schedule(task_id, None)
    device.stop_task(task_id)
    session.delete(task)
    session.commit()
    return {"status": "deleted"}


def _start_local_task(session: Session, entry: UserDevice, task_id: str) -> Dict[str, Any]:
    device = _get_local_device(entry)
    task = _get_scoped_task(session, task_id, entry.device_id)
    try:
        return device.start_task(task.id, task.command, task.cwd, env={}, timeout=task.timeout)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _stop_local_task(session: Session, entry: UserDevice, task_id: str) -> Dict[str, Any]:
    device = _get_local_device(entry)
    _get_scoped_task(session, task_id, entry.device_id)
    try:
        return device.stop_task(task_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _update_local_task(session: Session, entry: UserDevice, task_id: str, req: UpdateTaskRequest) -> Dict[str, Any]:
    _get_local_device(entry)
    task = _get_scoped_task(session, task_id, entry.device_id)
    if req.name is not None:
        task.name = req.name
    if req.command is not None:
        task.command = req.command
    if req.cwd is not None:
        task.cwd = req.cwd
    if req.description is not None:
        task.description = req.description
    if req.schedule is not None:
        task.schedule = req.schedule
        task_manager.update_schedule(task_id, req.schedule)
    if req.timeout is not None:
        task.timeout = req.timeout

    session.add(task)
    session.commit()
    session.refresh(task)
    return task.model_dump()


def _reorder_local_tasks(session: Session, entry: UserDevice, task_ids: List[str]) -> Dict[str, str]:
    _get_local_device(entry)
    tasks = session.exec(
        select(TaskModel).where(TaskModel.device_id == entry.device_id)
    ).all()
    task_by_id = {task.id: task for task in tasks}

    for index, task_id in enumerate(task_ids):
        task = task_by_id.get(task_id)
        if task:
            task.order = index
            session.add(task)
    session.commit()
    return {"status": "reordered"}


def _get_local_task_details(session: Session, entry: UserDevice, task_id: str) -> Dict[str, Any]:
    device = _get_local_device(entry)
    task = _get_scoped_task(session, task_id, entry.device_id)
    status = device.get_task_status(task_id)
    return {
        **task.model_dump(),
        "status": status.model_dump(),
    }


def _get_local_task_logs(session: Session, entry: UserDevice, task_id: str, lines: int) -> Dict[str, List[str]]:
    device = _get_local_device(entry)
    _get_scoped_task(session, task_id, entry.device_id)
    return {"logs": device.get_logs(task_id, lines)}


def _get_local_related_processes(session: Session, entry: UserDevice, task_id: str) -> List[Dict[str, Any]]:
    device = _get_local_device(entry)
    task = _get_scoped_task(session, task_id, entry.device_id)
    return device.find_related_processes(task.command)


def _kill_local_process(entry: UserDevice, pid: int) -> Dict[str, str]:
    device = _get_local_device(entry)
    if device.kill_process_by_pid(pid):
        return {"status": "killed"}
    raise HTTPException(status_code=500, detail="Failed to kill process")


def _associate_local_process(session: Session, entry: UserDevice, task_id: str, pid: int) -> Dict[str, Any]:
    device = _get_local_device(entry)
    task = _get_scoped_task(session, task_id, entry.device_id)
    result = device.associate_process(task_id, pid)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message"))

    cmd_list = result.get("cmdline")
    if cmd_list:
        if sys.platform == "win32":
            task.command = subprocess.list2cmdline(cmd_list)
        else:
            task.command = shlex.join(cmd_list)

    if "cwd" in result:
        task.cwd = result["cwd"] or "Unknown"

    session.add(task)
    session.commit()
    session.refresh(task)
    return result


@router.get("/{entry_id}/task/")
def list_tasks_for_entry(
    entry_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return _list_local_tasks(session, entry)
    return _proxy_request(entry, "GET", "/task/")


@router.post("/{entry_id}/task/create")
def create_task_for_entry(
    entry_id: str,
    req: CreateTaskRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return _create_local_task(session, entry, req)
    return _proxy_request(entry, "POST", "/task/create", json_body=req.model_dump())


@router.delete("/{entry_id}/task/{task_id}")
def delete_task_for_entry(
    entry_id: str,
    task_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return _delete_local_task(session, entry, task_id)
    return _proxy_request(entry, "DELETE", f"/task/{task_id}")


@router.post("/{entry_id}/task/{task_id}/start")
def start_task_for_entry(
    entry_id: str,
    task_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return _start_local_task(session, entry, task_id)
    return _proxy_request(entry, "POST", f"/task/{task_id}/start")


@router.post("/{entry_id}/task/{task_id}/stop")
def stop_task_for_entry(
    entry_id: str,
    task_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return _stop_local_task(session, entry, task_id)
    return _proxy_request(entry, "POST", f"/task/{task_id}/stop")


@router.post("/{entry_id}/task/{task_id}/update")
def update_task_for_entry(
    entry_id: str,
    task_id: str,
    req: UpdateTaskRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return _update_local_task(session, entry, task_id, req)
    return _proxy_request(entry, "POST", f"/task/{task_id}/update", json_body=req.model_dump(exclude_none=True))


@router.post("/{entry_id}/task/reorder")
def reorder_tasks_for_entry(
    entry_id: str,
    task_ids: List[str],
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return _reorder_local_tasks(session, entry, task_ids)
    return _proxy_request(entry, "POST", "/task/reorder", json_body=task_ids)


@router.get("/{entry_id}/task/{task_id}")
def get_task_for_entry(
    entry_id: str,
    task_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return _get_local_task_details(session, entry, task_id)
    return _proxy_request(entry, "GET", f"/task/{task_id}")


@router.get("/{entry_id}/task/{task_id}/logs")
def get_task_logs_for_entry(
    entry_id: str,
    task_id: str,
    n: int = 500,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return _get_local_task_logs(session, entry, task_id, n)
    return _proxy_request(entry, "GET", f"/task/{task_id}/logs", params={"n": n})


@router.get("/{entry_id}/task/{task_id}/related_processes")
def get_related_processes_for_entry(
    entry_id: str,
    task_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return _get_local_related_processes(session, entry, task_id)
    return _proxy_request(entry, "GET", f"/task/{task_id}/related_processes")


@router.post("/{entry_id}/task/process/kill")
def kill_process_for_entry(
    entry_id: str,
    req: Dict[str, int],
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    pid = req.get("pid")
    if not pid:
        raise HTTPException(status_code=400, detail="PID required")

    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return _kill_local_process(entry, pid)
    return _proxy_request(entry, "POST", "/task/process/kill", json_body=req)


@router.post("/{entry_id}/task/{task_id}/associate")
def associate_process_for_entry(
    entry_id: str,
    task_id: str,
    req: Dict[str, int],
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    pid = req.get("pid")
    if not pid:
        raise HTTPException(status_code=400, detail="PID required")

    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return _associate_local_process(session, entry, task_id, pid)
    return _proxy_request(entry, "POST", f"/task/{task_id}/associate", json_body=req)
