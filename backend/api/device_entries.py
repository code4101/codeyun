import shlex
import subprocess
import sys
import threading
import time
import uuid
from datetime import timedelta
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from sqlmodel import Session, select

from backend.api.codex_sessions import (
    CodexDailySummaryGenerateRequest,
    CodexDailySummaryRunRead,
    CodexDailySummaryRunRequest,
)
from backend.api.filesystem import (
    DEFAULT_MEDIA_SCAN_LIMIT,
    DeleteEntryRequest,
    DirectoryListRequest,
    DeviceFileScanRequest,
    DeviceFileSyncItemRequest,
    DeviceFileSyncRequest,
    DeviceFileWeightUpdateRequest,
    LabelmeRenameRequest,
    MediaListRequest,
    OcrPreviewRequest,
    RootScopedRequest,
    TextFileWriteRequest,
    build_ocr_preview_response,
    build_file_response,
    build_thumbnail_response,
    delete_scoped_entry,
    list_available_roots,
    list_directory_items,
    list_image_entries,
    list_media_entries,
    read_text_file,
    rename_labelme_annotation_pair,
    reveal_scoped_entry,
    resolve_request_path,
    scan_device_file_records,
    sync_device_file_records,
    update_device_file_weight_for_request,
    write_text_file,
)
from backend.api.git_tools import (
    GitToolCommitResponse,
    GitToolCommitRequest,
    GitToolContextRequest,
    GitToolContextResponse,
    GitToolHistoryStatsRequest,
    GitToolHistoryStatsResponse,
    GitToolFileDiffRequest,
    GitToolFileDiffResponse,
    GitToolGenerateAndCommitRequest,
    GitToolGenerateAndCommitResponse,
    GitToolGenerateMessageRequest,
    GitToolGenerateMessageResponse,
    GitToolInspectRequest,
    GitToolInspectResponse,
    GitToolReduceRequest,
    GitToolReduceAndCommitRequest,
    GitToolReduceAndCommitResponse,
    GitToolReduceResponse,
    GitToolReductionInputRequest,
    GitToolReductionInputResponse,
)
from backend.api.task_manager import CreateTaskRequest, UpdateTaskRequest, task_manager
from backend.core.ai_git_commit import (
    AiGitCommitError,
    generate_ai_git_commit_draft,
    resolve_ai_git_commit_runtime_config,
)
from backend.core.ai_chat import OllamaClientError
from backend.core.ai_git_reduction import generate_ai_git_commit_draft_hierarchical
from backend.core.auth import ALGORITHM, SECRET_KEY, create_access_token, get_current_user_from_token
from backend.core.codex_sessions import (
    build_codex_daily_summary,
    build_codex_overview,
    build_codex_thread_detail,
    build_codex_thread_message_images,
    build_codex_workload,
    cache_remote_codex_overview,
    cache_remote_codex_thread_detail,
    cache_remote_codex_workload,
    get_codex_daily_summary_latest_run,
    get_codex_daily_summary_latest_run_by_root_key,
    get_codex_daily_summary_run,
    paginate_codex_overview_payload,
    serialize_codex_daily_summary_run,
    start_codex_daily_summary_run,
)
from backend.core.codex_device_summary import (
    CODEX_REMOTE_READ_TIMEOUT_SECONDS,
    CODEX_REMOTE_WORKLOAD_TIMEOUT_SECONDS,
    REMOTE_DEVICE_DIRECT_PROXIES,
    build_multi_codex_summary_identity,
    codex_summary_entry_label,
    collect_multi_codex_daily_summary_source,
    collect_remote_codex_entry_daily_summary_source,
    ensure_local_codex_entry,
    snapshot_codex_summary_entries,
)
from backend.core.device import BaseDevice, device_manager, get_device_id
from backend.core.feature_access_guard import ensure_any_feature_access, ensure_feature_access
from backend.core.notebook_lab import (
    NotebookBindingUpdateRequest,
    NotebookLabError,
    NotebookRunResponse,
    NotebookState,
    RunCellRequest,
    RunCodeRequest,
    SaveNotebookRequest,
    UpdateCellRequest,
    get_notebook_state,
    interrupt_notebook_kernel,
    run_notebook_cell,
    run_temporary_code,
    update_notebook_binding,
    update_notebook_cell,
)
from backend.core.notebook_lab.service import save_notebook
from backend.core.device_file_cover import (
    DeviceFileMetadataSnapshot,
    resolve_device_cover_path,
    save_device_cover,
    upsert_device_file_metadata_batch,
)
from backend.core.device_files import update_device_file_weight
from backend.core.git_tools import (
    GitToolError,
    collect_git_history_stats,
    collect_git_commit_context,
    collect_git_file_diff,
    collect_git_reduction_source_units,
    create_git_commit,
    inspect_git_repository,
)
from backend.db import get_session
from backend.db import engine
from backend.models import CodexDailySummaryRun, DeviceFile
from backend.models import GitReductionRun
from backend.models import Task as TaskModel
from backend.models import User, UserDevice

router = APIRouter()
MEDIA_STREAM_TOKEN_SCOPE = "device-media-stream"
MEDIA_STREAM_TOKEN_EXPIRE_HOURS = 12
AI_NOTEBOOK_FEATURE_KEY = "tools.ai-notebook"
_GIT_REDUCTION_RUN_STATE: Dict[str, Dict[str, Any]] = {}
_GIT_REDUCTION_RUN_STATE_LOCK = threading.Lock()


class CodexDailySummaryMultiRunRequest(BaseModel):
    entry_ids: List[str] = Field(default_factory=list)
    date: str
    model: Optional[str] = None
    force: bool = False


def _get_entry_or_404(session: Session, current_user: User, entry_id: str) -> UserDevice:
    entry = session.get(UserDevice, entry_id)
    if not entry or entry.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Device entry not found")
    if not entry.is_active:
        raise HTTPException(status_code=400, detail="Device entry is inactive")
    return entry


def _get_entry_with_feature_or_404(
    session: Session,
    current_user: User,
    entry_id: str,
    feature_key: str,
) -> UserDevice:
    ensure_feature_access(
        session,
        feature_key=feature_key,
        current_user=current_user,
    )
    return _get_entry_or_404(session, current_user, entry_id)


def _get_entry_with_any_feature_or_404(
    session: Session,
    current_user: User,
    entry_id: str,
    *feature_keys: str,
) -> UserDevice:
    ensure_any_feature_access(
        session,
        feature_keys=tuple(feature_keys),
        current_user=current_user,
    )
    return _get_entry_or_404(session, current_user, entry_id)


def _ensure_local_entry(entry: UserDevice) -> None:
    ensure_local_codex_entry(entry)


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
    req: DirectoryListRequest | RootScopedRequest | MediaListRequest | DeleteEntryRequest | DeviceFileSyncRequest | DeviceFileScanRequest | TextFileWriteRequest | LabelmeRenameRequest | OcrPreviewRequest
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
            proxies=REMOTE_DEVICE_DIRECT_PROXIES.copy(),
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
    timeout: int = 10,
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
            timeout=timeout,
            stream=stream_response,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Failed to reach remote device: {exc}") from exc
    return _proxy_response(resp, stream_response=stream_response)


def _raise_codex_http_error(exc: Exception) -> None:
    if isinstance(exc, FileNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, NotADirectoryError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, KeyError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (ValueError, OllamaClientError)):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=500, detail=f"读取 Codex 会话失败：{exc}") from exc


def _raise_notebook_http_error(exc: NotebookLabError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _get_ai_notebook_entry(
    session: Session,
    current_user: User,
    entry_id: str,
) -> UserDevice:
    entry = _get_entry_with_feature_or_404(
        session,
        current_user,
        entry_id,
        AI_NOTEBOOK_FEATURE_KEY,
    )
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="只有管理员可以使用 AI 协作 Notebook")
    return entry


def _attach_ai_notebook_entry_metadata(entry: UserDevice, payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    updated = dict(payload)
    updated["entry_id"] = entry.entry_id
    updated["device_id"] = entry.device_id
    binding = updated.get("binding")
    if isinstance(binding, dict):
        updated["binding"] = {
            **binding,
            "entry_id": entry.entry_id,
            "device_id": entry.device_id,
        }
    state = updated.get("state")
    if isinstance(state, dict):
        updated["state"] = _attach_ai_notebook_entry_metadata(entry, state)
    return updated


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
                content_hash=item.get("content_hash"),
                hash_algorithm=item.get("hash_algorithm") or "sha256",
                visual_hash=item.get("visual_hash"),
                visual_hash_algorithm=item.get("visual_hash_algorithm") or "dhash-8",
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
                visual_hash=item.get("visual_hash"),
                visual_hash_algorithm=item.get("visual_hash_algorithm") or "dhash-8",
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


class GitToolStartReductionRunRequest(BaseModel):
    cwd: str
    provider: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    style: str = "summary"
    include_body: bool = True
    branch_factor: int = Field(default=10, ge=2, le=20)
    auto_commit: bool = False
    add_all: bool = True


class GitReductionRunRead(BaseModel):
    id: str
    entry_id: str
    cwd: str
    provider: str
    model: str
    style: str
    include_body: bool
    branch_factor: int
    auto_commit: bool
    add_all: bool
    status: str
    repo_root: str = ""
    branch: str = ""
    source_unit_count: int = 0
    source_unit_truncated_count: int = 0
    estimated_level_count: int = 0
    current_level_index: int = 0
    current_level_chunk_count: int = 0
    current_level_completed_chunk_count: int = 0
    completed_chunk_count: int = 0
    level_count: int = 0
    node_count: int = 0
    error_message: Optional[str] = None
    result: Optional[GitToolReduceResponse] = None
    commit: Optional[GitToolCommitResponse] = None
    created_at: float
    finished_at: Optional[float] = None
    updated_at: float


def _serialize_git_reduction_run(run: GitReductionRun) -> GitReductionRunRead:
    result_payload = dict(run.result_json or {})
    commit_payload = dict(run.commit_json or {})
    return GitReductionRunRead(
        id=run.id,
        entry_id=run.entry_id,
        cwd=run.cwd,
        provider=run.provider,
        model=run.model,
        style=run.style,
        include_body=bool(run.include_body),
        branch_factor=int(run.branch_factor or 10),
        auto_commit=bool(run.auto_commit),
        add_all=bool(run.add_all),
        status=run.status,
        repo_root=run.repo_root or "",
        branch=run.branch or "",
        source_unit_count=int(run.source_unit_count or 0),
        source_unit_truncated_count=int(run.source_unit_truncated_count or 0),
        estimated_level_count=int(run.estimated_level_count or 0),
        current_level_index=int(run.current_level_index or 0),
        current_level_chunk_count=int(run.current_level_chunk_count or 0),
        current_level_completed_chunk_count=int(run.current_level_completed_chunk_count or 0),
        completed_chunk_count=int(run.completed_chunk_count or 0),
        level_count=int(run.level_count or 0),
        node_count=int(run.node_count or 0),
        error_message=run.error_message,
        result=GitToolReduceResponse.model_validate(result_payload) if result_payload else None,
        commit=GitToolCommitResponse.model_validate(commit_payload) if commit_payload else None,
        created_at=run.created_at,
        finished_at=run.finished_at,
        updated_at=run.updated_at,
    )


def _store_git_reduction_run_state(user_id: int, run_payload: Dict[str, Any]) -> None:
    payload = dict(run_payload)
    payload["_user_id"] = user_id
    with _GIT_REDUCTION_RUN_STATE_LOCK:
        _GIT_REDUCTION_RUN_STATE[str(payload["id"])] = payload


def _load_git_reduction_run_state(user_id: int, entry_id: str, run_id: str) -> Optional[GitReductionRunRead]:
    with _GIT_REDUCTION_RUN_STATE_LOCK:
        payload = dict(_GIT_REDUCTION_RUN_STATE.get(run_id) or {})
    if not payload:
        return None
    if int(payload.pop("_user_id", 0) or 0) != int(user_id):
        return None
    if str(payload.get("entry_id") or "") != str(entry_id):
        return None
    return GitReductionRunRead.model_validate(payload)


def _get_git_reduction_run_or_404(
    session: Session,
    current_user: User,
    entry_id: str,
    run_id: str,
) -> GitReductionRun:
    run = session.get(GitReductionRun, run_id)
    if not run or run.user_id != current_user.id or run.entry_id != entry_id:
        raise HTTPException(status_code=404, detail="Git reduction run not found")
    return run


def _load_git_reduction_input(entry: UserDevice, cwd: str) -> dict[str, Any]:
    if entry.mode == "local":
        return collect_git_reduction_source_units(cwd)
    payload, error_response = _fetch_remote_json(
        entry,
        "POST",
        "/git-tools/reduction-input",
        json_body=GitToolReductionInputRequest(cwd=cwd).model_dump(),
        timeout=60,
    )
    if error_response is not None:
        _raise_remote_json_error(error_response)
    return GitToolReductionInputResponse.model_validate(payload).model_dump()


def _run_git_reduction_worker(
    *,
    user_id: int,
    run_id: str,
    entry_snapshot: dict[str, Any],
    initial_run_payload: dict[str, Any],
    provider_id: str,
    base_url: Optional[str],
    api_key: Optional[str],
    model: Optional[str],
    extra_providers: tuple[Any, ...],
) -> None:
    with Session(engine) as session:
        run = session.get(GitReductionRun, run_id)
        entry = UserDevice.model_validate(entry_snapshot)
        state = dict(initial_run_payload)

        def publish_state() -> None:
            _store_git_reduction_run_state(user_id, state)

        def sync_row() -> None:
            if not run:
                return
            run.provider = str(state.get("provider") or run.provider or "")
            run.model = str(state.get("model") or run.model or "")
            run.style = str(state.get("style") or run.style or "summary")
            run.include_body = bool(state.get("include_body", run.include_body))
            run.branch_factor = int(state.get("branch_factor") or run.branch_factor or 10)
            run.auto_commit = bool(state.get("auto_commit", run.auto_commit))
            run.add_all = bool(state.get("add_all", run.add_all))
            run.status = str(state.get("status") or run.status or "running")
            run.repo_root = str(state.get("repo_root") or "")
            run.branch = str(state.get("branch") or "")
            run.source_unit_count = int(state.get("source_unit_count") or 0)
            run.source_unit_truncated_count = int(state.get("source_unit_truncated_count") or 0)
            run.estimated_level_count = int(state.get("estimated_level_count") or 0)
            run.current_level_index = int(state.get("current_level_index") or 0)
            run.current_level_chunk_count = int(state.get("current_level_chunk_count") or 0)
            run.current_level_completed_chunk_count = int(state.get("current_level_completed_chunk_count") or 0)
            run.completed_chunk_count = int(state.get("completed_chunk_count") or 0)
            run.level_count = int(state.get("level_count") or 0)
            run.node_count = int(state.get("node_count") or 0)
            run.error_message = state.get("error_message")
            run.result_json = dict(state.get("result") or {})
            run.commit_json = dict(state.get("commit") or {})
            run.updated_at = float(state.get("updated_at") or time.time())
            run.finished_at = state.get("finished_at")
            session.add(run)
            session.commit()

        def update_progress(event: dict[str, Any]) -> None:
            event_type = str(event.get("event") or "").strip()
            if event_type == "prepared":
                state["source_unit_count"] = int(event.get("source_unit_count") or 0)
                state["estimated_level_count"] = int(event.get("estimated_level_count") or 0)
                state["source_unit_truncated_count"] = int(event.get("source_unit_truncated_count") or 0)
            elif event_type in {"level_started", "chunk_completed"}:
                state["current_level_index"] = int(event.get("level") or 0)
                state["current_level_chunk_count"] = int(event.get("chunk_count") or 0)
                state["completed_chunk_count"] = int(event.get("completed_chunk_count") or 0)
                state["current_level_completed_chunk_count"] = int(event.get("completed_level_chunk_count") or 0)
            elif event_type == "completed":
                state["completed_chunk_count"] = int(event.get("completed_chunk_count") or state.get("completed_chunk_count") or 0)
                state["level_count"] = int(event.get("level_count") or state.get("level_count") or 0)
            state["updated_at"] = time.time()
            publish_state()
            sync_row()

        try:
            reduction_input = _load_git_reduction_input(entry, str(state["cwd"]))
            state["repo_root"] = str(reduction_input.get("repo_root") or "")
            state["branch"] = str(reduction_input.get("branch") or "")
            state["source_unit_count"] = int(reduction_input.get("source_unit_count") or 0)
            state["source_unit_truncated_count"] = int(reduction_input.get("source_unit_truncated_count") or 0)
            state["updated_at"] = time.time()
            publish_state()
            sync_row()

            reduction_payload = generate_ai_git_commit_draft_hierarchical(
                cwd=str(state["cwd"]) if entry.mode == "local" else None,
                provider_id=provider_id,
                base_url=base_url,
                api_key=api_key,
                model=model,
                style=str(state.get("style") or "summary"),
                include_body=bool(state.get("include_body")),
                extra_providers=extra_providers,
                branch_factor=int(state.get("branch_factor") or 10),
                reduction_input=reduction_input,
                progress_callback=update_progress,
            )
            reduction_result = GitToolReduceResponse.model_validate(reduction_payload)
            state["result"] = reduction_result.model_dump(mode="json")
            state["repo_root"] = reduction_result.inspect.repo_root
            state["branch"] = reduction_result.inspect.branch
            state["source_unit_count"] = reduction_result.reduction.source_unit_count
            state["source_unit_truncated_count"] = reduction_result.reduction.source_unit_truncated_count
            state["level_count"] = reduction_result.reduction.level_count
            state["node_count"] = reduction_result.reduction.node_count
            state["model"] = reduction_result.model or str(state.get("model") or "")
            state["current_level_completed_chunk_count"] = int(state.get("current_level_chunk_count") or 0)

            if bool(state.get("auto_commit")):
                if entry.mode == "local":
                    commit_payload = create_git_commit(
                        str(state["cwd"]),
                        subject=reduction_result.subject,
                        body=list(reduction_result.body),
                        add_all=bool(state.get("add_all")),
                    )
                else:
                    payload, error_response = _fetch_remote_json(
                        entry,
                        "POST",
                        "/git-tools/commit",
                        json_body=GitToolCommitRequest(
                            cwd=str(state["cwd"]),
                            subject=reduction_result.subject,
                            body=list(reduction_result.body),
                            add_all=bool(state.get("add_all")),
                        ).model_dump(),
                        timeout=30,
                    )
                    if error_response is not None:
                        _raise_remote_json_error(error_response)
                    commit_payload = GitToolCommitResponse.model_validate(payload).model_dump()
                state["commit"] = GitToolCommitResponse.model_validate(commit_payload).model_dump(mode="json")

            state["status"] = "completed"
            state["finished_at"] = time.time()
            state["updated_at"] = state["finished_at"]
            publish_state()
            sync_row()
        except Exception as exc:
            state["status"] = "failed"
            state["error_message"] = str(exc)
            state["finished_at"] = time.time()
            state["updated_at"] = state["finished_at"]
            publish_state()
            sync_row()


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


@router.post("/{entry_id}/git/history-stats", response_model=GitToolHistoryStatsResponse)
def collect_git_history_stats_for_entry(
    entry_id: str,
    req: GitToolHistoryStatsRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    try:
        if entry.mode == "local":
            return collect_git_history_stats(req.cwd, days=req.days)
    except GitToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload, error_response = _fetch_remote_json(
        entry,
        "POST",
        "/git-tools/history-stats",
        json_body=req.model_dump(),
        timeout=60,
    )
    if error_response is not None:
        _raise_remote_json_error(error_response)
    return GitToolHistoryStatsResponse.model_validate(payload)


@router.post("/{entry_id}/git/file-diff", response_model=GitToolFileDiffResponse)
def collect_git_file_diff_for_entry(
    entry_id: str,
    req: GitToolFileDiffRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    try:
        if entry.mode == "local":
            return collect_git_file_diff(req.cwd, req.path)
    except GitToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload, error_response = _fetch_remote_json(
        entry,
        "POST",
        "/git-tools/file-diff",
        json_body=req.model_dump(),
        timeout=30,
    )
    if error_response is not None:
        _raise_remote_json_error(error_response)
    return GitToolFileDiffResponse.model_validate(payload)


@router.post("/{entry_id}/git/reduce-runs", response_model=GitReductionRunRead)
def start_git_reduction_run_for_entry(
    entry_id: str,
    req: GitToolStartReductionRunRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)

    try:
        provider_id, base_url, api_key, resolved_model, extra_providers = resolve_ai_git_commit_runtime_config(
            session=session,
            current_user=current_user,
            provider=req.provider,
            base_url=req.base_url,
            api_key=req.api_key,
            model=req.model,
        )
    except AiGitCommitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    run = GitReductionRun(
        user_id=current_user.id,
        entry_id=entry.entry_id,
        cwd=req.cwd,
        provider=provider_id,
        model=(resolved_model or "").strip(),
        style=req.style,
        include_body=req.include_body,
        branch_factor=req.branch_factor,
        auto_commit=req.auto_commit,
        add_all=req.add_all,
        status="running",
        created_at=time.time(),
        updated_at=time.time(),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    serialized_run = _serialize_git_reduction_run(run)
    _store_git_reduction_run_state(current_user.id, serialized_run.model_dump(mode="json"))

    entry_snapshot = {
        "entry_id": entry.entry_id,
        "user_id": entry.user_id,
        "device_id": entry.device_id,
        "name": entry.name,
        "mode": entry.mode,
        "server_url": entry.server_url,
        "token": entry.token,
        "is_active": entry.is_active,
        "order_index": entry.order_index,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }
    worker = threading.Thread(
        target=_run_git_reduction_worker,
        kwargs={
            "user_id": current_user.id,
            "run_id": run.id,
            "entry_snapshot": entry_snapshot,
            "initial_run_payload": serialized_run.model_dump(mode="json"),
            "provider_id": provider_id,
            "base_url": base_url,
            "api_key": api_key,
            "model": resolved_model,
            "extra_providers": extra_providers,
        },
        daemon=True,
    )
    worker.start()
    return serialized_run


@router.get("/{entry_id}/git/reduce-runs/{run_id}", response_model=GitReductionRunRead)
def get_git_reduction_run_for_entry(
    entry_id: str,
    run_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    _get_entry_or_404(session, current_user, entry_id)
    cached = _load_git_reduction_run_state(current_user.id, entry_id, run_id)
    if cached is not None:
        return cached
    run = _get_git_reduction_run_or_404(session, current_user, entry_id, run_id)
    return _serialize_git_reduction_run(run)


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

        provider_id, base_url, api_key, resolved_model, extra_providers = resolve_ai_git_commit_runtime_config(
            session=session,
            current_user=current_user,
            provider=req.provider,
            base_url=req.base_url,
            api_key=req.api_key,
            model=req.model,
        )
        draft = generate_ai_git_commit_draft(
            context_text=str(context_payload["prompt_context"]),
            provider_id=provider_id,
            base_url=base_url,
            api_key=api_key,
            model=resolved_model,
            style=req.style,
            include_body=req.include_body,
            force_split_reason=str(context_payload.get("split_reason") or "").strip() or None,
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


@router.post("/{entry_id}/git/reduce", response_model=GitToolReduceResponse)
def reduce_git_message_for_entry(
    entry_id: str,
    req: GitToolReduceRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)

    try:
        if entry.mode == "local":
            reduction_input = None
        else:
            payload, error_response = _fetch_remote_json(
                entry,
                "POST",
                "/git-tools/reduction-input",
                json_body=GitToolReductionInputRequest(cwd=req.cwd).model_dump(),
                timeout=60,
            )
            if error_response is not None:
                _raise_remote_json_error(error_response)
            reduction_input = GitToolReductionInputResponse.model_validate(payload).model_dump()

        provider_id, base_url, api_key, resolved_model, extra_providers = resolve_ai_git_commit_runtime_config(
            session=session,
            current_user=current_user,
            provider=req.provider,
            base_url=req.base_url,
            api_key=req.api_key,
            model=req.model,
        )
        reduction_payload = generate_ai_git_commit_draft_hierarchical(
            cwd=req.cwd if entry.mode == "local" else None,
            provider_id=provider_id,
            base_url=base_url,
            api_key=api_key,
            model=resolved_model,
            style=req.style,
            include_body=req.include_body,
            extra_providers=extra_providers,
            branch_factor=req.branch_factor,
            reduction_input=reduction_input,
        )
    except GitToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AiGitCommitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return GitToolReduceResponse.model_validate(reduction_payload)


@router.post("/{entry_id}/git/reduce-and-commit", response_model=GitToolReduceAndCommitResponse)
def reduce_and_commit_git_for_entry(
    entry_id: str,
    req: GitToolReduceAndCommitRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)

    try:
        if entry.mode == "local":
            reduction_input = None
        else:
            payload, error_response = _fetch_remote_json(
                entry,
                "POST",
                "/git-tools/reduction-input",
                json_body=GitToolReductionInputRequest(cwd=req.cwd).model_dump(),
                timeout=60,
            )
            if error_response is not None:
                _raise_remote_json_error(error_response)
            reduction_input = GitToolReductionInputResponse.model_validate(payload).model_dump()

        provider_id, base_url, api_key, resolved_model, extra_providers = resolve_ai_git_commit_runtime_config(
            session=session,
            current_user=current_user,
            provider=req.provider,
            base_url=req.base_url,
            api_key=req.api_key,
            model=req.model,
        )
        reduction_payload = generate_ai_git_commit_draft_hierarchical(
            cwd=req.cwd if entry.mode == "local" else None,
            provider_id=provider_id,
            base_url=base_url,
            api_key=api_key,
            model=resolved_model,
            style=req.style,
            include_body=req.include_body,
            extra_providers=extra_providers,
            branch_factor=req.branch_factor,
            reduction_input=reduction_input,
        )

        if entry.mode == "local":
            commit_payload = create_git_commit(
                req.cwd,
                subject=str(reduction_payload["subject"]),
                body=[str(item) for item in reduction_payload["body"]],
                add_all=req.add_all,
            )
        else:
            payload, error_response = _fetch_remote_json(
                entry,
                "POST",
                "/git-tools/commit",
                json_body=GitToolCommitRequest(
                    cwd=req.cwd,
                    subject=str(reduction_payload["subject"]),
                    body=[str(item) for item in reduction_payload["body"]],
                    add_all=req.add_all,
                ).model_dump(),
                timeout=30,
            )
            if error_response is not None:
                _raise_remote_json_error(error_response)
            commit_payload = GitToolCommitResponse.model_validate(payload).model_dump()
    except GitToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AiGitCommitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return GitToolReduceAndCommitResponse(
        commit=GitToolCommitResponse.model_validate(commit_payload),
        **GitToolReduceResponse.model_validate(reduction_payload).model_dump(),
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


@router.post("/{entry_id}/git/generate-and-commit", response_model=GitToolGenerateAndCommitResponse)
def generate_and_commit_git_for_entry(
    entry_id: str,
    req: GitToolGenerateAndCommitRequest,
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

        provider_id, base_url, api_key, resolved_model, extra_providers = resolve_ai_git_commit_runtime_config(
            session=session,
            current_user=current_user,
            provider=req.provider,
            base_url=req.base_url,
            api_key=req.api_key,
            model=req.model,
        )
        draft = generate_ai_git_commit_draft(
            context_text=str(context_payload["prompt_context"]),
            provider_id=provider_id,
            base_url=base_url,
            api_key=api_key,
            model=resolved_model,
            style=req.style,
            include_body=req.include_body,
            force_split_reason=str(context_payload.get("split_reason") or "").strip() or None,
            extra_providers=extra_providers,
        )

        if entry.mode == "local":
            commit_payload = create_git_commit(
                req.cwd,
                subject=str(draft["subject"]),
                body=[str(item) for item in draft["body"]],
                add_all=req.add_all,
            )
        else:
            payload, error_response = _fetch_remote_json(
                entry,
                "POST",
                "/git-tools/commit",
                json_body=GitToolCommitRequest(
                    cwd=req.cwd,
                    subject=str(draft["subject"]),
                    body=[str(item) for item in draft["body"]],
                    add_all=req.add_all,
                ).model_dump(),
                timeout=30,
            )
            if error_response is not None:
                _raise_remote_json_error(error_response)
            commit_payload = GitToolCommitResponse.model_validate(payload).model_dump()
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
    return GitToolGenerateAndCommitResponse(
        inspect=inspect_payload,
        commit=GitToolCommitResponse.model_validate(commit_payload),
        **draft,
    )


@router.get("/{entry_id}/codex/overview")
def get_codex_overview_for_entry(
    entry_id: str,
    root_dir: str | None = Query(default=None),
    thread_offset: int = Query(default=0, ge=0),
    thread_limit: int | None = Query(default=None, ge=1, le=2000),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        try:
            return build_codex_overview(
                root_dir,
                session=session,
                thread_offset=thread_offset,
                thread_limit=thread_limit,
            )
        except Exception as exc:  # pragma: no cover - translated for HTTP callers
            _raise_codex_http_error(exc)

    params = {
        **({"root_dir": root_dir} if root_dir else {}),
        **({"thread_offset": thread_offset} if thread_offset else {}),
        **({"thread_limit": thread_limit} if thread_limit is not None else {}),
    } or None
    payload, error_response = _fetch_remote_json(
        entry,
        "GET",
        "/codex/overview",
        params=params,
        timeout=CODEX_REMOTE_READ_TIMEOUT_SECONDS,
    )
    if error_response is not None:
        return _proxy_response(error_response)
    try:
        cache_remote_codex_overview(entry.entry_id, payload, session=session)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"写入远端 Codex 缓存失败：{exc}") from exc
    if "thread_offset" in payload:
        return payload
    return paginate_codex_overview_payload(payload, thread_offset=thread_offset, thread_limit=thread_limit)


@router.get("/{entry_id}/codex/threads/{thread_id}")
def get_codex_thread_detail_for_entry(
    entry_id: str,
    thread_id: str,
    root_dir: str | None = Query(default=None),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        try:
            return build_codex_thread_detail(root_dir, thread_id, session=session)
        except Exception as exc:  # pragma: no cover - translated for HTTP callers
            _raise_codex_http_error(exc)

    params = {"root_dir": root_dir} if root_dir else None
    payload, error_response = _fetch_remote_json(
        entry,
        "GET",
        f"/codex/threads/{thread_id}",
        params=params,
        timeout=CODEX_REMOTE_READ_TIMEOUT_SECONDS,
    )
    if error_response is not None:
        return _proxy_response(error_response)
    try:
        cache_remote_codex_thread_detail(entry.entry_id, payload, session=session)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"写入远端 Codex 缓存失败：{exc}") from exc
    return payload


@router.get("/{entry_id}/codex/threads/{thread_id}/messages/{message_seq}/images")
def get_codex_thread_message_images_for_entry(
    entry_id: str,
    thread_id: str,
    message_seq: int,
    root_dir: str | None = Query(default=None),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        try:
            return build_codex_thread_message_images(root_dir, thread_id, message_seq, session=session)
        except Exception as exc:  # pragma: no cover - translated for HTTP callers
            _raise_codex_http_error(exc)

    params = {"root_dir": root_dir} if root_dir else None
    return _proxy_request(
        entry,
        "GET",
        f"/codex/threads/{thread_id}/messages/{message_seq}/images",
        params=params,
        timeout=CODEX_REMOTE_READ_TIMEOUT_SECONDS,
    )


@router.get("/{entry_id}/codex/workload")
def get_codex_workload_for_entry(
    entry_id: str,
    root_dir: str | None = Query(default=None),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        try:
            return build_codex_workload(root_dir, session=session)
        except Exception as exc:  # pragma: no cover - translated for HTTP callers
            _raise_codex_http_error(exc)

    params = {"root_dir": root_dir} if root_dir else None
    payload, error_response = _fetch_remote_json(
        entry,
        "GET",
        "/codex/workload",
        params=params,
        timeout=CODEX_REMOTE_WORKLOAD_TIMEOUT_SECONDS,
    )
    if error_response is not None:
        return _proxy_response(error_response)
    try:
        cache_remote_codex_workload(entry.entry_id, payload, session=session)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"写入远端 Codex 缓存失败：{exc}") from exc
    return payload


def _parse_daily_summary_entry_ids(entry_ids: str | None) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for part in str(entry_ids or "").split(","):
        entry_id = part.strip()
        if not entry_id or entry_id in seen:
            continue
        ids.append(entry_id)
        seen.add(entry_id)
    return ids


def _get_daily_summary_entries(
    session: Session,
    current_user: User,
    entry_ids: list[str],
) -> list[UserDevice]:
    if not entry_ids:
        raise HTTPException(status_code=400, detail="请选择至少一个设备")

    rows = session.exec(
        select(UserDevice).where(
            UserDevice.user_id == current_user.id,
            UserDevice.entry_id.in_(entry_ids),
            UserDevice.is_active == True,  # noqa: E712
        )
    ).all()
    row_map = {row.entry_id: row for row in rows}
    missing_ids = [entry_id for entry_id in entry_ids if entry_id not in row_map]
    if missing_ids:
        raise HTTPException(status_code=404, detail=f"设备不存在或不可用：{', '.join(missing_ids)}")
    return [row_map[entry_id] for entry_id in entry_ids]


def _daily_summary_entry_label(entry: UserDevice | dict[str, Any]) -> str:
    return codex_summary_entry_label(entry)


def _snapshot_daily_summary_entries(entries: list[UserDevice]) -> list[dict[str, Any]]:
    return snapshot_codex_summary_entries(entries)


def _build_multi_daily_summary_identity(entry_specs: list[dict[str, Any]]) -> tuple[str, dict[str, str]]:
    return build_multi_codex_summary_identity(entry_specs)


def _collect_remote_entry_daily_summary_source(
    entry_spec: dict[str, Any],
    target_date_text: str,
    *,
    user_id: int | None,
    session: Session,
) -> dict[str, Any]:
    return collect_remote_codex_entry_daily_summary_source(
        entry_spec,
        target_date_text,
        user_id=user_id,
        session=session,
    )


def _collect_multi_daily_summary_source(
    entry_specs: list[dict[str, Any]],
    root_identity: dict[str, str],
    target_date_text: str,
    *,
    user_id: int | None,
    session: Session,
) -> dict[str, Any]:
    return collect_multi_codex_daily_summary_source(
        entry_specs,
        root_identity,
        target_date_text=target_date_text,
        user_id=user_id,
        session=session,
    )


@router.get("/codex/daily-summary/latest", response_model=CodexDailySummaryRunRead)
def get_latest_codex_daily_summary_for_entries(
    entry_ids: str = Query(default=""),
    date: str = Query(default=""),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entries = _get_daily_summary_entries(
        session,
        current_user,
        _parse_daily_summary_entry_ids(entry_ids),
    )
    entry_specs = _snapshot_daily_summary_entries(entries)
    scope_key, root_identity = _build_multi_daily_summary_identity(entry_specs)
    try:
        return get_codex_daily_summary_latest_run_by_root_key(
            scope_key,
            root_identity["root_key"],
            date,
            session=session,
        )
    except Exception as exc:  # pragma: no cover - translated for HTTP callers
        _raise_codex_http_error(exc)


@router.post("/codex/daily-summary/runs", response_model=CodexDailySummaryRunRead)
def create_codex_daily_summary_run_for_entries(
    req: CodexDailySummaryMultiRunRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entries = _get_daily_summary_entries(session, current_user, list(req.entry_ids or []))
    entry_specs = _snapshot_daily_summary_entries(entries)
    scope_key, root_identity = _build_multi_daily_summary_identity(entry_specs)
    user_id = current_user.id

    def source_loader(active_session: Session) -> dict[str, Any]:
        return _collect_multi_daily_summary_source(
            entry_specs,
            root_identity,
            req.date,
            user_id=user_id,
            session=active_session,
        )

    try:
        return start_codex_daily_summary_run(
            scope_key,
            None,
            req.date,
            model=req.model,
            user_id=user_id,
            force=req.force,
            session=session,
            root_identity=root_identity,
            source_loader=source_loader,
        )
    except Exception as exc:  # pragma: no cover - translated for HTTP callers
        _raise_codex_http_error(exc)


@router.get("/codex/daily-summary/runs/{run_id}", response_model=CodexDailySummaryRunRead)
def get_codex_daily_summary_run_for_entries(
    run_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    run = session.get(CodexDailySummaryRun, run_id)
    if (
        run is None
        or str(run.scope_key or "").split(":", 1)[0] != "entries"
        or (run.user_id is not None and run.user_id != current_user.id)
    ):
        raise HTTPException(status_code=404, detail="Codex daily summary run not found")
    return serialize_codex_daily_summary_run(run)


@router.post("/{entry_id}/codex/daily-summary/generate")
def generate_codex_daily_summary_for_entry(
    entry_id: str,
    req: CodexDailySummaryGenerateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        try:
            return build_codex_daily_summary(
                req.root_dir,
                req.date,
                model=req.model,
                user_id=current_user.id,
                session=session,
            )
        except Exception as exc:  # pragma: no cover - translated for HTTP callers
            _raise_codex_http_error(exc)

    return _proxy_request(
        entry,
        "POST",
        "/codex/daily-summary/generate",
        json_body=req.model_dump(),
        timeout=15 * 60,
    )


@router.get("/{entry_id}/codex/daily-summary/latest", response_model=CodexDailySummaryRunRead)
def get_latest_codex_daily_summary_for_entry(
    entry_id: str,
    date: str,
    root_dir: str | None = Query(default=None),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        try:
            return get_codex_daily_summary_latest_run(
                f"entry:{entry.entry_id}",
                root_dir,
                date,
                session=session,
            )
        except Exception as exc:  # pragma: no cover - translated for HTTP callers
            _raise_codex_http_error(exc)

    params = {"date": date}
    if root_dir:
        params["root_dir"] = root_dir
    return _proxy_request(entry, "GET", "/codex/daily-summary/latest", params=params, timeout=20)


@router.post("/{entry_id}/codex/daily-summary/runs", response_model=CodexDailySummaryRunRead)
def create_codex_daily_summary_run_for_entry(
    entry_id: str,
    req: CodexDailySummaryRunRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        try:
            return start_codex_daily_summary_run(
                f"entry:{entry.entry_id}",
                req.root_dir,
                req.date,
                model=req.model,
                user_id=current_user.id,
                force=req.force,
                session=session,
            )
        except Exception as exc:  # pragma: no cover - translated for HTTP callers
            _raise_codex_http_error(exc)

    return _proxy_request(
        entry,
        "POST",
        "/codex/daily-summary/runs",
        json_body=req.model_dump(),
        timeout=20,
    )


@router.get("/{entry_id}/codex/daily-summary/runs/{run_id}", response_model=CodexDailySummaryRunRead)
def get_codex_daily_summary_run_for_entry(
    entry_id: str,
    run_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        try:
            return get_codex_daily_summary_run(
                f"entry:{entry.entry_id}",
                run_id,
                session=session,
            )
        except Exception as exc:  # pragma: no cover - translated for HTTP callers
            _raise_codex_http_error(exc)

    return _proxy_request(
        entry,
        "GET",
        f"/codex/daily-summary/runs/{run_id}",
        timeout=20,
    )


@router.get("/{entry_id}/ai-notebook/state", response_model=NotebookState)
def get_ai_notebook_state_for_entry(
    entry_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_ai_notebook_entry(session, current_user, entry_id)
    if entry.mode == "local":
        try:
            return get_notebook_state(session, entry_id=entry.entry_id, device_id=entry.device_id)
        except NotebookLabError as exc:
            _raise_notebook_http_error(exc)

    payload, error_response = _fetch_remote_json(entry, "GET", "/ai-notebook/state", timeout=20)
    if error_response is not None:
        return _proxy_response(error_response)
    return _attach_ai_notebook_entry_metadata(entry, payload)


@router.put("/{entry_id}/ai-notebook/binding", response_model=NotebookState)
def put_ai_notebook_binding_for_entry(
    entry_id: str,
    payload: NotebookBindingUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_ai_notebook_entry(session, current_user, entry_id)
    if entry.mode == "local":
        try:
            return update_notebook_binding(
                session,
                entry_id=entry.entry_id,
                device_id=entry.device_id,
                notebook_path=payload.notebook_path,
            )
        except NotebookLabError as exc:
            _raise_notebook_http_error(exc)

    remote_payload, error_response = _fetch_remote_json(
        entry,
        "PUT",
        "/ai-notebook/binding",
        json_body=payload.model_dump(),
        timeout=20,
    )
    if error_response is not None:
        return _proxy_response(error_response)
    return _attach_ai_notebook_entry_metadata(entry, remote_payload)


@router.put("/{entry_id}/ai-notebook/cells/{cell_id}", response_model=NotebookState)
def put_ai_notebook_cell_for_entry(
    entry_id: str,
    cell_id: str,
    payload: UpdateCellRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_ai_notebook_entry(session, current_user, entry_id)
    if entry.mode == "local":
        try:
            return update_notebook_cell(
                session,
                entry_id=entry.entry_id,
                device_id=entry.device_id,
                cell_id=cell_id,
                notebook_hash=payload.notebook_hash,
                source=payload.source,
            )
        except NotebookLabError as exc:
            _raise_notebook_http_error(exc)

    remote_payload, error_response = _fetch_remote_json(
        entry,
        "PUT",
        f"/ai-notebook/cells/{cell_id}",
        json_body=payload.model_dump(),
        timeout=20,
    )
    if error_response is not None:
        return _proxy_response(error_response)
    return _attach_ai_notebook_entry_metadata(entry, remote_payload)


@router.post("/{entry_id}/ai-notebook/save", response_model=NotebookState)
def post_ai_notebook_save_for_entry(
    entry_id: str,
    payload: SaveNotebookRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_ai_notebook_entry(session, current_user, entry_id)
    if entry.mode == "local":
        try:
            return save_notebook(
                session,
                entry_id=entry.entry_id,
                device_id=entry.device_id,
                notebook_hash=payload.notebook_hash,
            )
        except NotebookLabError as exc:
            _raise_notebook_http_error(exc)

    remote_payload, error_response = _fetch_remote_json(
        entry,
        "POST",
        "/ai-notebook/save",
        json_body=payload.model_dump(),
        timeout=20,
    )
    if error_response is not None:
        return _proxy_response(error_response)
    return _attach_ai_notebook_entry_metadata(entry, remote_payload)


@router.post("/{entry_id}/ai-notebook/run-cell", response_model=NotebookRunResponse)
def post_ai_notebook_run_cell_for_entry(
    entry_id: str,
    payload: RunCellRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_ai_notebook_entry(session, current_user, entry_id)
    if entry.mode == "local":
        try:
            return run_notebook_cell(
                session,
                entry_id=entry.entry_id,
                device_id=entry.device_id,
                notebook_hash=payload.notebook_hash,
                cell_id=payload.cell_id,
            )
        except NotebookLabError as exc:
            _raise_notebook_http_error(exc)

    remote_payload, error_response = _fetch_remote_json(
        entry,
        "POST",
        "/ai-notebook/run-cell",
        json_body=payload.model_dump(),
        timeout=180,
    )
    if error_response is not None:
        return _proxy_response(error_response)
    return _attach_ai_notebook_entry_metadata(entry, remote_payload)


@router.post("/{entry_id}/ai-notebook/run-code", response_model=NotebookRunResponse)
def post_ai_notebook_run_code_for_entry(
    entry_id: str,
    payload: RunCodeRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_ai_notebook_entry(session, current_user, entry_id)
    if entry.mode == "local":
        try:
            return run_temporary_code(
                session,
                entry_id=entry.entry_id,
                device_id=entry.device_id,
                code=payload.code,
            )
        except NotebookLabError as exc:
            _raise_notebook_http_error(exc)

    remote_payload, error_response = _fetch_remote_json(
        entry,
        "POST",
        "/ai-notebook/run-code",
        json_body=payload.model_dump(),
        timeout=180,
    )
    if error_response is not None:
        return _proxy_response(error_response)
    return _attach_ai_notebook_entry_metadata(entry, remote_payload)


@router.post("/{entry_id}/ai-notebook/interrupt", response_model=NotebookState)
def post_ai_notebook_interrupt_for_entry(
    entry_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_ai_notebook_entry(session, current_user, entry_id)
    if entry.mode == "local":
        try:
            return interrupt_notebook_kernel(session, entry_id=entry.entry_id, device_id=entry.device_id)
        except NotebookLabError as exc:
            _raise_notebook_http_error(exc)

    remote_payload, error_response = _fetch_remote_json(
        entry,
        "POST",
        "/ai-notebook/interrupt",
        timeout=20,
    )
    if error_response is not None:
        return _proxy_response(error_response)
    return _attach_ai_notebook_entry_metadata(entry, remote_payload)


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
    req: DirectoryListRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return list_directory_items(
            req.root,
            req.path,
            absolute_path=req.absolute_path,
            sort_program=req.sort_program,
            session=session,
        )
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


@router.post("/{entry_id}/files/labelme/rename")
def rename_labelme_file_for_entry(
    entry_id: str,
    req: LabelmeRenameRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return rename_labelme_annotation_pair(req)
    return _proxy_request(entry, "POST", "/fs/labelme/rename", json_body=_filesystem_payload(req))


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


@router.get("/{entry_id}/files/text")
def get_file_text_for_entry(
    entry_id: str,
    root: Optional[str] = Query(None),
    path: str = Query(""),
    absolute_path: str = Query(""),
    encoding: str = Query("utf-8"),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return read_text_file(root, path, absolute_path=absolute_path, encoding=encoding)

    params = {"path": path, "encoding": encoding}
    if root:
        params["root"] = root
    if absolute_path:
        params["absolute_path"] = absolute_path

    payload, error_response = _fetch_remote_json(
        entry,
        "GET",
        "/fs/text",
        params=params,
    )
    if error_response is not None:
        return _proxy_response(error_response)
    return payload


@router.post("/{entry_id}/files/text")
def save_file_text_for_entry(
    entry_id: str,
    req: TextFileWriteRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return write_text_file(
            req.root,
            req.path,
            absolute_path=req.absolute_path,
            text=req.text,
            encoding=req.encoding,
        )

    payload, error_response = _fetch_remote_json(
        entry,
        "POST",
        "/fs/text",
        json_body=_filesystem_payload(req),
    )
    if error_response is not None:
        return _proxy_response(error_response)
    return payload


@router.post("/{entry_id}/files/ocr")
def preview_file_ocr_for_entry(
    entry_id: str,
    req: OcrPreviewRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return build_ocr_preview_response(
            req.root,
            req.path,
            absolute_path=req.absolute_path,
            shape_type=req.shape_type,
        )

    payload, error_response = _fetch_remote_json(
        entry,
        "POST",
        "/fs/ocr",
        json_body=_filesystem_payload(req),
    )
    if error_response is not None:
        return _proxy_response(error_response)
    return payload


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
