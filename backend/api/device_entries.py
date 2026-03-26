import shlex
import subprocess
import sys
import time
import uuid
from datetime import timedelta
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from jose import JWTError, jwt
from starlette.background import BackgroundTask
from sqlmodel import Session, select

from backend.api.filesystem import (
    DEFAULT_MEDIA_SCAN_LIMIT,
    DeleteEntryRequest,
    DeviceFileScanRequest,
    DeviceFileSyncItemRequest,
    DeviceFileSyncRequest,
    DeviceFileWeightUpdateRequest,
    MediaListRequest,
    RootScopedRequest,
    build_file_response,
    build_thumbnail_response,
    delete_scoped_entry,
    list_available_roots,
    list_directory_items,
    list_image_entries,
    list_media_entries,
    reveal_scoped_entry,
    resolve_request_path,
    scan_device_file_records,
    sync_device_file_records,
    update_device_file_weight_for_request,
)
from backend.api.git_tools import (
    GitToolCommitResponse,
    GitToolCommitRequest,
    GitToolContextRequest,
    GitToolContextResponse,
    GitToolGenerateMessageRequest,
    GitToolGenerateMessageResponse,
    GitToolInspectRequest,
    GitToolInspectResponse,
)
from backend.api.task_manager import CreateTaskRequest, UpdateTaskRequest, task_manager
from backend.core.ai_git_commit import (
    AiGitCommitError,
    generate_ai_git_commit_draft,
    resolve_ai_runtime_config,
)
from backend.core.auth import ALGORITHM, SECRET_KEY, create_access_token, get_current_user_from_token
from backend.core.device import BaseDevice, device_manager, get_device_id
from backend.core.device_file_cover import (
    DeviceFileMetadataSnapshot,
    resolve_device_cover_path,
    save_device_cover,
    upsert_device_file_metadata_batch,
)
from backend.core.device_files import update_device_file_weight
from backend.core.git_tools import GitToolError, collect_git_commit_context, create_git_commit, inspect_git_repository
from backend.db import get_session
from backend.models import DeviceFile
from backend.models import Task as TaskModel
from backend.models import User, UserDevice

router = APIRouter()
MEDIA_STREAM_TOKEN_SCOPE = "device-media-stream"
MEDIA_STREAM_TOKEN_EXPIRE_HOURS = 12


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


def _copy_proxy_response_headers(resp: requests.Response) -> Dict[str, str]:
    allowed_headers = {
        "accept-ranges",
        "cache-control",
        "content-disposition",
        "content-length",
        "content-range",
        "etag",
        "last-modified",
    }
    return {
        key: value
        for key, value in resp.headers.items()
        if key.lower() in allowed_headers
    }


def _proxy_response(resp: requests.Response, *, stream_response: bool = False) -> Response:
    content_type = resp.headers.get("content-type", "")
    if "application/json" in content_type.lower() and not stream_response:
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    if stream_response:
        return StreamingResponse(
            resp.iter_content(chunk_size=64 * 1024),
            status_code=resp.status_code,
            media_type=content_type or None,
            headers=_copy_proxy_response_headers(resp),
            background=BackgroundTask(resp.close),
        )
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=content_type or None,
        headers=_copy_proxy_response_headers(resp),
    )


def _filesystem_payload(
    req: RootScopedRequest | MediaListRequest | DeleteEntryRequest | DeviceFileSyncRequest | DeviceFileScanRequest
) -> Dict[str, Any]:
    payload = req.model_dump(exclude_none=True)
    if not payload.get("absolute_path"):
        payload.pop("absolute_path", None)
    if payload.get("recursive") is False:
        payload.pop("recursive", None)
    if not payload.get("sort_program", {}).get("rules"):
        payload.pop("sort_program", None)
    if payload.get("sort_mode") == "path":
        payload.pop("sort_mode", None)
    if not payload.get("snapshot_id"):
        payload.pop("snapshot_id", None)
    if int(payload.get("scan_limit") or DEFAULT_MEDIA_SCAN_LIMIT) == DEFAULT_MEDIA_SCAN_LIMIT:
        payload.pop("scan_limit", None)
    if int(payload.get("offset") or 0) <= 0:
        payload.pop("offset", None)
    if int(payload.get("limit") or 0) <= 0:
        payload.pop("limit", None)
    if payload.get("layout_mode") in {None, "", "none"}:
        payload.pop("layout_mode", None)
    if int(payload.get("layout_columns") or 0) <= 0:
        payload.pop("layout_columns", None)
    if int(payload.get("layout_column_width") or 0) <= 0:
        payload.pop("layout_column_width", None)
    if int(payload.get("layout_gap") or 0) <= 0:
        payload.pop("layout_gap", None)
    if not payload.get("layout_column_heights"):
        payload.pop("layout_column_heights", None)
    return payload


def _create_media_stream_token(current_user: User, entry_id: str, payload: Dict[str, Any]) -> str:
    return create_access_token(
        {
            "sub": MEDIA_STREAM_TOKEN_SCOPE,
            "scope": MEDIA_STREAM_TOKEN_SCOPE,
            "username": current_user.username,
            "entry_id": entry_id,
            "root": payload.get("root"),
            "path": payload.get("path", ""),
            "absolute_path": payload.get("absolute_path", ""),
        },
        expires_delta=timedelta(hours=MEDIA_STREAM_TOKEN_EXPIRE_HOURS),
    )


def _decode_media_stream_token(session: Session, entry_id: str, token: str) -> tuple[UserDevice, Dict[str, Any]]:
    credentials_exception = HTTPException(
        status_code=401,
        detail="Invalid media stream token",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise credentials_exception from exc

    if payload.get("scope") != MEDIA_STREAM_TOKEN_SCOPE or payload.get("sub") != MEDIA_STREAM_TOKEN_SCOPE:
        raise credentials_exception

    username = payload.get("username")
    if payload.get("entry_id") != entry_id or not username:
        raise credentials_exception

    user = session.exec(select(User).where(User.username == username)).first()
    if not user:
        raise credentials_exception

    entry = _get_entry_or_404(session, user, entry_id)
    return entry, {
        "root": payload.get("root"),
        "path": payload.get("path", ""),
        "absolute_path": payload.get("absolute_path", ""),
    }


def _normalize_scoped_path(path: str) -> str:
    return (path or "").strip().replace("\\", "/").lstrip("/")


def _resolve_device_file_identity(
    entry: UserDevice,
    root: Optional[str],
    path: str,
    absolute_path: str,
) -> str:
    normalized_absolute = (absolute_path or "").strip()
    if normalized_absolute:
        return normalized_absolute

    if entry.mode == "local":
        target_path, _ = resolve_request_path(root, path, absolute_path="")
        return str(target_path)

    normalized_path = _normalize_scoped_path(path)
    if root:
        return f"root://{root}/{normalized_path}" if normalized_path else f"root://{root}"
    if normalized_path:
        return normalized_path
    raise HTTPException(status_code=400, detail="Either absolute_path or root/path is required")


def _get_cached_cover_response(
    session: Session,
    device_id: str,
    absolute_path: str,
) -> Response | None:
    record = session.exec(
        select(DeviceFile).where(
            DeviceFile.device_id == device_id,
            DeviceFile.absolute_path == absolute_path,
        )
    ).first()
    if not record or not record.cover_path:
        return None

    cover_path = resolve_device_cover_path(record.cover_path)
    if not cover_path or not cover_path.exists():
        record.cover_path = None
        record.cover_mime_type = None
        record.cover_source = None
        record.cover_updated_at = None
        record.updated_at = time.time()
        session.add(record)
        session.commit()
        return None

    return FileResponse(
        path=str(cover_path),
        media_type=record.cover_mime_type or None,
        filename=cover_path.name,
        headers={"Cache-Control": "private, no-store"},
    )


def _fetch_remote_thumbnail(entry: UserDevice, params: Dict[str, Any]) -> requests.Response:
    target_url = f"{_remote_base_url(entry)}/api/fs/thumbnail"
    try:
        return requests.request(
            method="GET",
            url=target_url,
            headers=_proxy_headers(entry),
            params=params,
            timeout=20,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Failed to reach remote device: {exc}") from exc


def _fetch_remote_json(
    entry: UserDevice,
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Any] = None,
    timeout: int = 10,
) -> tuple[Dict[str, Any] | List[Any], requests.Response | None]:
    target_url = f"{_remote_base_url(entry)}/api{path}"
    try:
        resp = requests.request(
            method=method,
            url=target_url,
            headers=_proxy_headers(entry),
            params=params,
            json=json_body,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Failed to reach remote device: {exc}") from exc

    if resp.status_code >= 400:
        return {}, resp

    content_type = resp.headers.get("content-type", "")
    if "application/json" not in content_type.lower():
        return {}, resp

    return resp.json(), None


def _raise_remote_json_error(resp: requests.Response) -> None:
    detail = None
    try:
        payload = resp.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        raw_detail = payload.get("detail") or payload.get("message") or payload.get("error")
        if isinstance(raw_detail, str) and raw_detail.strip():
            detail = raw_detail.strip()

    if not detail:
        detail = resp.text.strip() or f"Remote request failed with HTTP {resp.status_code}"

    raise HTTPException(status_code=resp.status_code, detail=detail)


def _proxy_request(
    entry: UserDevice,
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Any] = None,
    forwarded_headers: Optional[Dict[str, str]] = None,
    stream_response: bool = False,
) -> Response:
    target_url = f"{_remote_base_url(entry)}/api{path}"
    headers = _proxy_headers(entry)
    if forwarded_headers:
        headers.update({key: value for key, value in forwarded_headers.items() if value})
    try:
        resp = requests.request(
            method=method,
            url=target_url,
            headers=headers,
            params=params,
            json=json_body,
            timeout=10,
            stream=stream_response,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Failed to reach remote device: {exc}") from exc
    return _proxy_response(resp, stream_response=stream_response)


def _index_device_media_payload(
    session: Session,
    entry: UserDevice,
    root: Optional[str],
    payload: Dict[str, Any],
    *,
    response_key: str,
) -> None:
    raw_items = payload.get(response_key)
    if not isinstance(raw_items, list):
        return

    snapshots: list[DeviceFileMetadataSnapshot] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue

        item_path = str(item.get("path") or "")
        item_absolute_path = str(item.get("absolute_path") or "")
        try:
            file_identity = _resolve_device_file_identity(entry, root, item_path, item_absolute_path)
        except HTTPException:
            continue

        snapshots.append(
            DeviceFileMetadataSnapshot(
                absolute_path=file_identity,
                last_known_path=file_identity,
                file_size=item.get("size"),
                modified_at_ms=item.get("modified_at"),
                duration_ms=item.get("duration_ms"),
                width_px=item.get("width"),
                height_px=item.get("height"),
                media_kind=item.get("kind"),
                mime_type=item.get("mime_type"),
            )
        )

    if snapshots:
        upsert_device_file_metadata_batch(session, entry.device_id, snapshots)


def _mirror_scanned_device_files_to_cache(
    session: Session,
    entry: UserDevice,
    req: DeviceFileScanRequest,
    payload: Dict[str, Any],
) -> None:
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        return

    sync_items = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue

        item_path = str(item.get("path") or "")
        item_absolute_path = str(item.get("absolute_path") or "")
        try:
            file_identity = _resolve_device_file_identity(entry, req.root, item_path, item_absolute_path)
        except HTTPException:
            continue

        sync_items.append(
            DeviceFileSyncItemRequest(
                absolute_path=file_identity,
                last_known_path=file_identity,
                content_hash=item.get("content_hash"),
                hash_algorithm=item.get("hash_algorithm") or "sha256",
                file_size=item.get("size"),
                modified_at_ms=item.get("modified_at"),
                width_px=item.get("width_px"),
                height_px=item.get("height_px"),
                media_kind=item.get("media_kind"),
                mime_type=item.get("mime_type"),
                weight=item.get("weight"),
            )
        )

    scope_prefixes: list[str] = []
    if req.mark_missing_as_dangling:
        try:
            scope_prefixes.append(
                _resolve_device_file_identity(entry, req.root, req.path, req.absolute_path)
            )
        except HTTPException:
            return

    if not sync_items and not scope_prefixes:
        return

    sync_device_file_records(
        DeviceFileSyncRequest(
            items=sync_items,
            mark_missing_as_dangling=req.mark_missing_as_dangling,
            scope_prefixes=scope_prefixes,
        ),
        session,
        device_id=entry.device_id,
    )


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


@router.post("/{entry_id}/git/inspect", response_model=GitToolInspectResponse)
def inspect_git_for_entry(
    entry_id: str,
    req: GitToolInspectRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    try:
        if entry.mode == "local":
            return inspect_git_repository(req.cwd)
    except GitToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload, error_response = _fetch_remote_json(
        entry,
        "POST",
        "/git-tools/inspect",
        json_body=req.model_dump(),
        timeout=20,
    )
    if error_response is not None:
        _raise_remote_json_error(error_response)
    return GitToolInspectResponse.model_validate(payload)


@router.post("/{entry_id}/git/generate-message", response_model=GitToolGenerateMessageResponse)
def generate_git_message_for_entry(
    entry_id: str,
    req: GitToolGenerateMessageRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)

    try:
        if entry.mode == "local":
            context_payload = collect_git_commit_context(req.cwd, max_files=req.max_files)
        else:
            payload, error_response = _fetch_remote_json(
                entry,
                "POST",
                "/git-tools/context",
                json_body=GitToolContextRequest(
                    cwd=req.cwd,
                    max_files=req.max_files,
                ).model_dump(),
                timeout=30,
            )
            if error_response is not None:
                _raise_remote_json_error(error_response)
            context_payload = GitToolContextResponse.model_validate(payload).model_dump()

        provider_id, base_url, api_key, extra_providers = resolve_ai_runtime_config(
            session=session,
            current_user=current_user,
            provider=req.provider,
            base_url=req.base_url,
            api_key=req.api_key,
        )
        draft = generate_ai_git_commit_draft(
            context_text=str(context_payload["prompt_context"]),
            provider_id=provider_id,
            base_url=base_url,
            api_key=api_key,
            model=req.model,
            style=req.style,
            include_body=req.include_body,
            extra_providers=extra_providers,
        )
    except GitToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AiGitCommitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    inspect_payload = GitToolInspectResponse.model_validate(
        {
            key: value
            for key, value in context_payload.items()
            if key in GitToolInspectResponse.model_fields
        }
    )
    return GitToolGenerateMessageResponse(
        inspect=inspect_payload,
        **draft,
    )


@router.post("/{entry_id}/git/commit", response_model=GitToolCommitResponse)
def commit_git_for_entry(
    entry_id: str,
    req: GitToolCommitRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)

    try:
        if entry.mode == "local":
            return create_git_commit(
                req.cwd,
                subject=req.subject,
                body=req.body,
                add_all=req.add_all,
            )
    except GitToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload, error_response = _fetch_remote_json(
        entry,
        "POST",
        "/git-tools/commit",
        json_body=req.model_dump(),
        timeout=30,
    )
    if error_response is not None:
        _raise_remote_json_error(error_response)
    return GitToolCommitResponse.model_validate(payload)


@router.get("/{entry_id}/files/roots")
def get_filesystem_roots_for_entry(
    entry_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return {"roots": list_available_roots()}
    return _proxy_request(entry, "GET", "/fs/roots")


@router.post("/{entry_id}/files/list_dir")
def list_directory_for_entry(
    entry_id: str,
    req: RootScopedRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return list_directory_items(req.root, req.path, absolute_path=req.absolute_path)
    return _proxy_request(entry, "POST", "/fs/scoped/list_dir", json_body=_filesystem_payload(req))


@router.post("/{entry_id}/files/images/list")
def list_images_for_entry(
    entry_id: str,
    req: RootScopedRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return list_image_entries(req.root, req.path, absolute_path=req.absolute_path, session=session)

    payload, error_response = _fetch_remote_json(
        entry,
        "POST",
        "/fs/images/list",
        json_body=_filesystem_payload(req),
    )
    if error_response is not None:
        return _proxy_response(error_response)
    assert isinstance(payload, dict)
    _index_device_media_payload(session, entry, req.root, payload, response_key="images")
    return payload


@router.post("/{entry_id}/files/media/list")
def list_media_for_entry(
    entry_id: str,
    req: MediaListRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return list_media_entries(
            req.root,
            req.path,
            absolute_path=req.absolute_path,
            recursive=req.recursive,
            scan_limit=req.scan_limit,
            session=session,
            sort_mode=req.sort_mode,
            sort_program=req.sort_program,
            snapshot_id=req.snapshot_id,
            offset=req.offset,
            limit=req.limit,
            layout_mode=req.layout_mode,
            layout_columns=req.layout_columns,
            layout_column_width=req.layout_column_width,
            layout_gap=req.layout_gap,
            layout_column_heights=req.layout_column_heights,
        )

    payload, error_response = _fetch_remote_json(
        entry,
        "POST",
        "/fs/media/list",
        json_body=_filesystem_payload(req),
    )
    if error_response is not None:
        return _proxy_response(error_response)
    assert isinstance(payload, dict)
    _index_device_media_payload(session, entry, req.root, payload, response_key="media")
    return payload


@router.post("/{entry_id}/files/weight")
def update_file_weight_for_entry(
    entry_id: str,
    req: DeviceFileWeightUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return update_device_file_weight_for_request(req, session, device_id=entry.device_id)

    payload, error_response = _fetch_remote_json(
        entry,
        "POST",
        "/fs/weight",
        json_body=_filesystem_payload(req),
    )
    if error_response is not None:
        return _proxy_response(error_response)

    assert isinstance(payload, dict)
    try:
        file_identity = _resolve_device_file_identity(entry, req.root, req.path, req.absolute_path)
        update_device_file_weight(
            session,
            entry.device_id,
            file_identity,
            weight=int(payload.get("weight", req.weight)),
        )
    except (HTTPException, ValueError):
        pass
    return payload


@router.post("/{entry_id}/files/delete")
def delete_file_for_entry(
    entry_id: str,
    req: DeleteEntryRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return delete_scoped_entry(
            req.root,
            req.path,
            absolute_path=req.absolute_path,
            recursive=req.recursive,
    )
    return _proxy_request(entry, "POST", "/fs/delete", json_body=_filesystem_payload(req))


@router.post("/{entry_id}/files/reveal")
def reveal_file_for_entry(
    entry_id: str,
    req: RootScopedRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return reveal_scoped_entry(
            req.root,
            req.path,
            absolute_path=req.absolute_path,
        )
    return _proxy_request(entry, "POST", "/fs/reveal", json_body=_filesystem_payload(req))


@router.post("/{entry_id}/files/sync")
def sync_device_files_for_entry(
    entry_id: str,
    req: DeviceFileSyncRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return sync_device_file_records(req, session, device_id=entry.device_id)

    payload, error_response = _fetch_remote_json(
        entry,
        "POST",
        "/fs/device-files/sync",
        json_body=_filesystem_payload(req),
    )
    if error_response is not None:
        return _proxy_response(error_response)

    sync_device_file_records(req, session, device_id=entry.device_id)
    return payload


@router.post("/{entry_id}/files/scan")
def scan_device_files_for_entry(
    entry_id: str,
    req: DeviceFileScanRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return scan_device_file_records(req, session, device_id=entry.device_id)

    payload, error_response = _fetch_remote_json(
        entry,
        "POST",
        "/fs/device-files/scan",
        json_body=_filesystem_payload(req),
        timeout=30,
    )
    if error_response is not None:
        return _proxy_response(error_response)

    assert isinstance(payload, dict)
    _mirror_scanned_device_files_to_cache(session, entry, req, payload)
    return payload


@router.get("/{entry_id}/files/content")
def get_file_content_for_entry(
    entry_id: str,
    request: Request,
    root: Optional[str] = Query(None),
    path: str = Query(""),
    absolute_path: str = Query(""),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return build_file_response(root, path, absolute_path=absolute_path)

    params = {"path": path}
    if root:
        params["root"] = root
    if absolute_path:
        params["absolute_path"] = absolute_path
    return _proxy_request(
        entry,
        "GET",
        "/fs/content",
        params=params,
        forwarded_headers={
            "Range": request.headers.get("range"),
            "If-Range": request.headers.get("if-range"),
        },
        stream_response=True,
    )


@router.post("/{entry_id}/files/stream-url")
def get_file_stream_url_for_entry(
    entry_id: str,
    req: RootScopedRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    _get_entry_or_404(session, current_user, entry_id)
    payload = _filesystem_payload(req)
    token = _create_media_stream_token(current_user, entry_id, payload)
    return {
        "url": f"/api/device-entries/{entry_id}/files/stream?token={token}",
        "expires_in": MEDIA_STREAM_TOKEN_EXPIRE_HOURS * 60 * 60,
    }


@router.get("/{entry_id}/files/stream")
def stream_file_for_entry(
    entry_id: str,
    request: Request,
    token: str = Query(...),
    session: Session = Depends(get_session),
):
    entry, payload = _decode_media_stream_token(session, entry_id, token)

    root = payload.get("root")
    path = payload.get("path", "")
    absolute_path = payload.get("absolute_path", "")

    if entry.mode == "local":
        return build_file_response(root, path, absolute_path=absolute_path)

    params = {"path": path}
    if root:
        params["root"] = root
    if absolute_path:
        params["absolute_path"] = absolute_path
    return _proxy_request(
        entry,
        "GET",
        "/fs/content",
        params=params,
        forwarded_headers={
            "Range": request.headers.get("range"),
            "If-Range": request.headers.get("if-range"),
        },
        stream_response=True,
    )


@router.get("/{entry_id}/files/thumbnail")
def get_file_thumbnail_for_entry(
    entry_id: str,
    root: Optional[str] = Query(None),
    path: str = Query(""),
    absolute_path: str = Query(""),
    max_edge: int = Query(360, ge=64, le=2048),
    quality: int = Query(82, ge=40, le=95),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    file_identity = _resolve_device_file_identity(entry, root, path, absolute_path)
    cached_response = _get_cached_cover_response(session, entry.device_id, file_identity)
    if cached_response:
        return cached_response

    if entry.mode == "local":
        generated_response = build_thumbnail_response(
            root,
            path,
            absolute_path=absolute_path,
            max_edge=max_edge,
            quality=quality,
        )
        cover_bytes = generated_response.body
        media_type = generated_response.media_type or "image/jpeg"
    else:
        params = {"path": path, "max_edge": max_edge, "quality": quality}
        if root:
            params["root"] = root
        if absolute_path:
            params["absolute_path"] = absolute_path
        remote_response = _fetch_remote_thumbnail(entry, params)
        if remote_response.status_code >= 400:
            return _proxy_response(remote_response)
        cover_bytes = remote_response.content
        media_type = remote_response.headers.get("content-type", "image/jpeg")

    try:
        record = save_device_cover(
            session,
            entry.device_id,
            file_identity,
            cover_bytes,
            source="auto",
        )
    except (OSError, ValueError):
        record = None

    if record:
        cover_path = resolve_device_cover_path(record.cover_path)
        if cover_path and cover_path.exists():
            return FileResponse(
                path=str(cover_path),
                media_type=record.cover_mime_type or media_type,
                filename=cover_path.name,
                headers={"Cache-Control": "private, no-store"},
            )

    return Response(
        content=cover_bytes,
        media_type=media_type,
        headers={"Cache-Control": "private, no-store"},
    )


@router.post("/{entry_id}/files/cover")
async def set_file_cover_for_entry(
    entry_id: str,
    root: Optional[str] = Form(None),
    path: str = Form(""),
    absolute_path: str = Form(""),
    cover: UploadFile = File(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if cover.content_type and not cover.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Cover must be an image")

    image_bytes = await cover.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Cover image is empty")

    file_identity = _resolve_device_file_identity(entry, root, path, absolute_path)
    try:
        record = save_device_cover(
            session,
            entry.device_id,
            file_identity,
            image_bytes,
            source="manual",
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Cover must be a valid image") from exc
    return {
        "ok": True,
        "cover_source": record.cover_source,
        "cover_updated_at": record.cover_updated_at,
        "absolute_path": file_identity,
    }
