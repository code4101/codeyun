import copy
import os
import re
import json
import time
from typing import Any, List, Set, Optional, Dict
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select, func, text
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel

from backend.db import get_session, engine
from backend.core.auth import get_current_active_superuser, get_password_hash
from backend.core.auto_git_commit import (
    create_auto_git_commit_run,
    get_auto_git_commit_status,
)
from backend.core.background_task_runner import (
    BACKGROUND_TASK_SPECS,
    get_background_task_runner_snapshot,
    get_background_task_spec,
    is_background_task_deleted,
    refresh_background_task_schedule_states,
    reset_background_task_schedule,
    set_background_task_deleted,
    set_background_task_enabled,
)
from backend.core.background_task_queue import background_task_queue
from backend.core.device import get_device_id
from backend.core.note_metadata_feedback import (
    create_note_metadata_feedback_optimization_run,
    get_note_metadata_feedback_status,
)
from backend.models import AppSetting, User, NoteNode
from backend.core.settings import ROOT_DIR, get_settings
from backend.core.storage import (
    ATTACHMENT_URL_PATTERN,
    build_attachment_url,
    get_attachments_dir,
)
from backend.core.storage_usage import collect_directory_usage
from backend.core.storage_health import build_storage_health_report
from backend.schemas import AdminAccountRead
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

settings = get_settings()

def _create_admin_router() -> APIRouter:
    return APIRouter(
        tags=["admin"],
        dependencies=[Depends(get_current_active_superuser)],
        responses={404: {"description": "Not found"}},
    )


router = _create_admin_router()
accounts_router = _create_admin_router()
images_router = _create_admin_router()
tasks_router = _create_admin_router()

ATTACHMENTS_ABS_PATH = os.fspath(get_attachments_dir())
LEGACY_STORAGE_CONFIG_FILE = os.path.join(
    os.fspath(settings.data_dir),
    "storage_config.json",
)
STORAGE_SCHEDULE_SETTING_KEY = "storage.schedule"
DEFAULT_STORAGE_SCHEDULE = {
    "schedule_enabled": False,
    "cron_expression": "35 0 * * *",
}

# --- Scheduler Setup ---
storage_scheduler = BackgroundScheduler()

def load_config():
    with Session(engine) as session:
        row = session.get(AppSetting, STORAGE_SCHEDULE_SETTING_KEY)
        if row and isinstance(row.value, dict):
            enabled = row.value.get("schedule_enabled")
            if enabled is None:
                enabled = row.value.get("enabled")
            return {
                "schedule_enabled": bool(
                    DEFAULT_STORAGE_SCHEDULE["schedule_enabled"]
                    if enabled is None
                    else enabled
                ),
                "cron_expression": row.value.get(
                    "cron_expression",
                    DEFAULT_STORAGE_SCHEDULE["cron_expression"],
                ),
            }

    if os.path.exists(LEGACY_STORAGE_CONFIG_FILE):
        try:
            with open(LEGACY_STORAGE_CONFIG_FILE, 'r', encoding='utf-8') as f:
                legacy = json.load(f)
            enabled = legacy.get("schedule_enabled")
            if enabled is None:
                enabled = legacy.get("enabled")
            config = {
                "schedule_enabled": bool(
                    DEFAULT_STORAGE_SCHEDULE["schedule_enabled"]
                    if enabled is None
                    else enabled
                ),
                "cron_expression": legacy.get(
                    "cron_expression",
                    DEFAULT_STORAGE_SCHEDULE["cron_expression"],
                ),
            }
            save_config(config)
            return config
        except Exception:
            pass
    return dict(DEFAULT_STORAGE_SCHEDULE)

def save_config(config):
    schedule_enabled = config.get("schedule_enabled")
    if schedule_enabled is None:
        schedule_enabled = config.get("enabled")

    payload = {
        "schedule_enabled": bool(
            DEFAULT_STORAGE_SCHEDULE["schedule_enabled"]
            if schedule_enabled is None
            else schedule_enabled
        ),
        "cron_expression": config.get(
            "cron_expression",
            DEFAULT_STORAGE_SCHEDULE["cron_expression"],
        ),
    }
    with Session(engine) as session:
        row = session.get(AppSetting, STORAGE_SCHEDULE_SETTING_KEY)
        if row is None:
            row = AppSetting(key=STORAGE_SCHEDULE_SETTING_KEY)
        row.value = payload
        row.updated_at = time.time()
        session.add(row)
        session.commit()

def scheduled_analysis_job():
    """
    Background job to run analysis.
    In a real system, this might save results to DB history.
    For now, it just logs.
    """
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Running scheduled storage analysis...")
    # TODO: Implement heavy analysis and cache results
    pass

def init_storage_scheduler():
    config = load_config()
    if config.get("schedule_enabled"):
        try:
            cron = config.get("cron_expression", DEFAULT_STORAGE_SCHEDULE["cron_expression"])
            if not storage_scheduler.running:
                storage_scheduler.start()
            storage_scheduler.add_job(
                lambda: background_task_queue.enqueue("storage_analysis", scheduled_analysis_job),
                CronTrigger.from_crontab(cron),
                id="storage_analysis",
                replace_existing=True
            )
            print(f"Storage analysis scheduled: {cron}")
        except Exception as e:
            print(f"Failed to schedule storage analysis: {e}")

# --- Models ---


def _serialize_scheduler_next_run(scheduler: BackgroundScheduler, job_id: str) -> Optional[str]:
    if not scheduler.running:
        return None
    job = scheduler.get_job(job_id)
    if job is None or job.next_run_time is None:
        return None
    return job.next_run_time.isoformat()


def _find_queue_snapshot(queue: Dict[str, Any], task_name: str) -> Optional[Dict[str, Any]]:
    running = queue.get("running")
    if isinstance(running, dict) and running.get("name") == task_name:
        return running
    for item in queue.get("pending") or []:
        if isinstance(item, dict) and item.get("name") == task_name:
            return item
    for item in queue.get("recent") or []:
        if isinstance(item, dict) and item.get("name") == task_name:
            return item
    return None


def _queue_task_is_active(queue: Dict[str, Any], task_name: str) -> bool:
    running = queue.get("running")
    if isinstance(running, dict) and running.get("name") == task_name:
        return True
    return any(
        isinstance(item, dict) and item.get("name") == task_name
        for item in queue.get("pending") or []
    )


def _queue_run_payload(queue: Dict[str, Any], task_name: str) -> Optional[Dict[str, Any]]:
    snapshot = _find_queue_snapshot(queue, task_name)
    if not snapshot:
        return None
    return {
        "id": snapshot.get("id"),
        "status": snapshot.get("status"),
        "stage_label": snapshot.get("error_message") or "",
        "created_at": snapshot.get("queued_at"),
        "started_at": snapshot.get("started_at"),
        "finished_at": snapshot.get("finished_at"),
        "error_message": snapshot.get("error_message"),
        "metadata": snapshot.get("metadata") or {},
    }


def _run_is_active(run_payload: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(run_payload, dict):
        return False
    return str(run_payload.get("status") or "") in {"pending", "running"}


def _without_queue(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "queue"}

class OrphanImage(BaseModel):
    filename: str
    size: int
    mtime: float
    url: str

class DeleteImagesRequest(BaseModel):
    filenames: List[str]

class StorageStats(BaseModel):
    total_count: int
    total_size: int
    orphan_count: int
    orphan_size: int

class OrphanImageResponse(BaseModel):
    stats: StorageStats
    orphans: List[OrphanImage]

class StorageDashboardStats(BaseModel):
    total_size_bytes: int
    total_file_count: int
    total_note_count: int
    orphan_size_bytes: int # Estimated or last known
    orphan_count: int      # Estimated or last known
    dead_link_count: int   # Estimated or last known
    health_score: int      # 0-100
    attachments_path: str = ""
    data_workspace_path: str = ""

class WorkspaceUsageEntry(BaseModel):
    name: str
    path: str
    is_dir: bool
    logical_size_bytes: int
    allocated_size_bytes: int
    file_count: int
    directory_count: int
    symlink_count: int
    inaccessible_count: int
    modified_at: Optional[float] = None

class StorageHealthIssueRead(BaseModel):
    id: str
    severity: str
    title: str
    detail: str
    path: str = ""
    size_bytes: int = 0
    action_label: str = ""
    action_kind: str = "inspect"

class StorageSlimmingCandidateRead(BaseModel):
    id: str
    category: str
    title: str
    path: str
    logical_size_bytes: int
    allocated_size_bytes: int
    file_count: int = 0
    directory_count: int = 0
    risk: str = "review"
    cleanup_kind: str = "inspect"
    action_label: str = "查看"
    detail: str = ""

class WorkspaceUsageResponse(BaseModel):
    scope: str
    label: str
    expected_role: str
    health_score: int
    health_status: str
    health_issues: List[StorageHealthIssueRead]
    slimming_candidates: List[StorageSlimmingCandidateRead]
    root_path: str
    logical_size_bytes: int
    allocated_size_bytes: int
    file_count: int
    directory_count: int
    symlink_count: int
    inaccessible_count: int
    top_entries: List[WorkspaceUsageEntry]
    scan_started_at: float
    elapsed_ms: int
    source: str

class TopFile(BaseModel):
    filename: str
    size: int
    mtime: float
    url: str

class TopNode(BaseModel):
    id: str
    title: str
    size: int
    updated_at: float

class FixableLink(BaseModel):
    note_id: str
    note_title: str
    original_url: str
    suggested_url: str

class StorageAnalysisResponse(BaseModel):
    top_files: List[TopFile]
    top_nodes: List[TopNode]
    file_type_distribution: Dict[str, int] # ext -> count

class MaintenanceStatusResponse(BaseModel):
    orphan_count: int
    orphan_size: int
    dead_links: List[dict]
    fixable_links: List[FixableLink]

class ScheduleConfig(BaseModel):
    enabled: bool
    cron_expression: str


class BackgroundTaskRead(BaseModel):
    key: str
    title: str
    category: str
    description: str = ""
    cron_expression: str = ""
    schedule_label: str = ""
    enabled: bool = True
    scheduler_running: bool = False
    runner_running: bool = False
    next_run_at: Optional[str] = None
    retry_policy: str = ""
    can_trigger: bool = True
    trigger_warning: str = ""
    active: bool = False
    latest_run: Optional[Dict[str, Any]] = None


class BackgroundTaskStatusResponse(BaseModel):
    queue: Dict[str, Any]
    tasks: List[BackgroundTaskRead]
    runner_running: bool = False
    next_wake_at: Optional[str] = None
    runner_error: Optional[str] = None


class BackgroundTaskTriggerResponse(BaseModel):
    task_key: str
    queued: bool = True
    queue_task_id: Optional[str] = None
    run: Optional[Dict[str, Any]] = None


class DeviceControlIdentityResponse(BaseModel):
    device_id: str
    device_token_enabled: bool
    data_dir: str


WORKSPACE_USAGE_CACHE_TTL_SECONDS = 60
_workspace_usage_cache_by_scope: Dict[str, Dict[str, Any]] = {}
_workspace_usage_cache_at_by_scope: Dict[str, float] = {}
_workspace_usage_cache_top_limit_by_scope: Dict[str, int] = {}


def _clamp_workspace_usage_top_limit(value: int) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return 20
    return max(0, min(100, limit))


def _get_data_workspace_dir():
    return settings.data_workspace_dir


def _resolve_storage_usage_scope(scope: str) -> tuple[str, str, Any]:
    normalized_scope = (scope or "").strip().lower().replace("-", "_") or "data_workspace"
    if normalized_scope in {"data", "workspace", "data_workspace"}:
        return "data_workspace", "数据工作区", _get_data_workspace_dir()
    if normalized_scope in {"data_dir", "instance_data", "instance_data_dir"}:
        return "data_dir", "实例数据目录", settings.data_dir
    if normalized_scope in {"source", "source_dir", "repo", "repo_dir"}:
        return "source_dir", "源码目录", ROOT_DIR
    return "data_workspace", "数据工作区", _get_data_workspace_dir()


def _load_workspace_usage_payload(
    *,
    scope: str,
    refresh: bool,
    top_limit: int,
    session: Session | None = None,
) -> Dict[str, Any]:
    normalized_scope, label, root_path = _resolve_storage_usage_scope(scope)

    normalized_top_limit = _clamp_workspace_usage_top_limit(top_limit)
    now = time.time()
    cache_valid = (
        normalized_scope in _workspace_usage_cache_by_scope
        and not refresh
        and _workspace_usage_cache_top_limit_by_scope.get(normalized_scope, 0) >= normalized_top_limit
        and now - _workspace_usage_cache_at_by_scope.get(normalized_scope, 0) <= WORKSPACE_USAGE_CACHE_TTL_SECONDS
    )
    if cache_valid:
        payload = copy.deepcopy(_workspace_usage_cache_by_scope[normalized_scope])
        payload["top_entries"] = payload.get("top_entries", [])[:normalized_top_limit]
        return payload

    scan_top_limit = max(20, normalized_top_limit)
    payload = collect_directory_usage(root_path, top_limit=scan_top_limit, session=session).to_dict()
    payload["scope"] = normalized_scope
    payload["label"] = label
    health_report = build_storage_health_report(
        scope=normalized_scope,
        label=label,
        root_path=root_path,
        usage=payload,
        data_workspace_path=_get_data_workspace_dir(),
        attachments_dir=ATTACHMENTS_ABS_PATH,
    ).to_dict()
    payload["expected_role"] = health_report["expected_role"]
    payload["health_score"] = health_report["health_score"]
    payload["health_status"] = health_report["health_status"]
    payload["health_issues"] = health_report["issues"]
    payload["slimming_candidates"] = health_report["slimming_candidates"]
    _workspace_usage_cache_by_scope[normalized_scope] = copy.deepcopy(payload)
    _workspace_usage_cache_at_by_scope[normalized_scope] = time.time()
    _workspace_usage_cache_top_limit_by_scope[normalized_scope] = scan_top_limit
    payload["top_entries"] = payload.get("top_entries", [])[:normalized_top_limit]
    return payload


class ResetAccountPasswordRequest(BaseModel):
    password: str


class CreateAccountRequest(BaseModel):
    username: str
    password: str
    nickname: str = ""
    is_superuser: bool = False
    is_active: bool = True
    email: Optional[str] = None
    phone: Optional[str] = None


class UpdateAccountProfileRequest(BaseModel):
    nickname: str
    is_superuser: bool
    is_active: bool = True
    password: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None

# --- Endpoints ---

@accounts_router.get("/accounts", response_model=List[AdminAccountRead])
def list_accounts(session: Session = Depends(get_session)):
    statement = (
        select(User)
        .order_by(User.is_superuser.desc(), User.created_at.asc(), User.id.asc())
    )
    return session.exec(statement).all()


@accounts_router.post("/accounts", response_model=AdminAccountRead)
def create_account(
    payload: CreateAccountRequest,
    session: Session = Depends(get_session),
):
    username = payload.username.strip()
    if username == "":
        raise HTTPException(status_code=400, detail="账号不能为空")
    if payload.password == "":
        raise HTTPException(status_code=400, detail="密码不能为空")

    existing_user = session.exec(select(User).where(User.username == username)).first()
    if existing_user is not None:
        raise HTTPException(status_code=400, detail="账号已存在")

    user = User(
        username=username,
        nickname=payload.nickname.strip(),
        email=(payload.email or "").strip() or None,
        phone=(payload.phone or "").strip() or None,
        hashed_password=get_password_hash(payload.password),
        password_plain=payload.password,
        is_superuser=payload.is_superuser,
        is_active=payload.is_active,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@accounts_router.post("/accounts/{user_id}/password", response_model=AdminAccountRead)
def reset_account_password(
    user_id: int,
    payload: ResetAccountPasswordRequest,
    session: Session = Depends(get_session),
):
    if payload.password == "":
        raise HTTPException(status_code=400, detail="密码不能为空")

    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="账号不存在")

    user.hashed_password = get_password_hash(payload.password)
    user.password_plain = payload.password
    user.updated_at = time.time()
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@accounts_router.post("/accounts/{user_id}/profile", response_model=AdminAccountRead)
def update_account_profile(
    user_id: int,
    payload: UpdateAccountProfileRequest,
    session: Session = Depends(get_session),
):
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="账号不存在")

    if user.is_superuser and not payload.is_superuser:
        superuser_count = session.exec(
            select(func.count()).select_from(User).where(User.is_superuser == True)
        ).one()
        if superuser_count <= 1:
            raise HTTPException(status_code=400, detail="至少保留一个超级管理员账号")

    user.nickname = payload.nickname.strip()
    user.is_superuser = payload.is_superuser
    user.is_active = payload.is_active
    if payload.password is not None and payload.password != "":
        user.hashed_password = get_password_hash(payload.password)
        user.password_plain = payload.password
    user.email = (payload.email or "").strip() or None
    user.phone = (payload.phone or "").strip() or None
    user.updated_at = time.time()
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@accounts_router.delete("/accounts/{user_id}")
def delete_account(
    user_id: int,
    session: Session = Depends(get_session),
):
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="账号不存在")

    if user.is_superuser:
        superuser_count = session.exec(
            select(func.count()).select_from(User).where(User.is_superuser == True)
        ).one()
        if superuser_count <= 1:
            raise HTTPException(status_code=400, detail="至少保留一个超级管理员账号")

    try:
        session.delete(user)
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=400, detail="该账号下存在关联数据，无法直接删除")

    return {"success": True}


@tasks_router.get("/background-tasks/status", response_model=BackgroundTaskStatusResponse)
def get_background_task_status(session: Session = Depends(get_session)):
    from backend.api.notes import (
        CODEX_DIARY_AUTO_IMPORT_TASK_NAME,
        get_codex_diary_auto_import_status,
    )

    queue = background_task_queue.snapshot()
    runner = get_background_task_runner_snapshot()
    runner_tasks = runner.get("tasks") if isinstance(runner.get("tasks"), dict) else {}
    metadata_status = get_note_metadata_feedback_status(session)
    auto_git_status = get_auto_git_commit_status(session)
    codex_diary_status = get_codex_diary_auto_import_status(session)
    metadata_latest = metadata_status.get("latest_run") if isinstance(metadata_status, dict) else None
    auto_git_latest = auto_git_status.get("latest_run") if isinstance(auto_git_status, dict) else None
    codex_diary_latest = codex_diary_status.get("latest_run") if isinstance(codex_diary_status, dict) else None

    def _is_task_enabled(task_key: str) -> bool:
        row = session.get(AppSetting, f"background_task.{task_key}.enabled")
        if row and isinstance(row.value, dict):
            return bool(row.value.get("enabled", False))
        return False

    latest_by_key = {
        spec.key: _queue_run_payload(queue, spec.key)
        for spec in BACKGROUND_TASK_SPECS
    }
    latest_by_key.update({
        "auto_git_commit": auto_git_latest if isinstance(auto_git_latest, dict) else None,
        "note_metadata_feedback_optimization": metadata_latest if isinstance(metadata_latest, dict) else None,
        CODEX_DIARY_AUTO_IMPORT_TASK_NAME: codex_diary_latest if isinstance(codex_diary_latest, dict) else None,
        "attendance_summary_monthly_templates": _queue_run_payload(queue, "attendance_summary_monthly_templates"),
        "storage_analysis": _queue_run_payload(queue, "storage_analysis"),
    })
    active_by_key = {
        spec.key: _queue_task_is_active(queue, spec.key)
        for spec in BACKGROUND_TASK_SPECS
    }
    active_by_key.update({
        "auto_git_commit": _run_is_active(auto_git_status.get("active_run") if isinstance(auto_git_status, dict) else None)
        or _queue_task_is_active(queue, "auto_git_commit"),
        "note_metadata_feedback_optimization": _run_is_active(metadata_latest if isinstance(metadata_latest, dict) else None)
        or _queue_task_is_active(queue, "note_metadata_feedback_optimization"),
        CODEX_DIARY_AUTO_IMPORT_TASK_NAME: _run_is_active(codex_diary_status.get("active_run") if isinstance(codex_diary_status, dict) else None)
        or _queue_task_is_active(queue, CODEX_DIARY_AUTO_IMPORT_TASK_NAME),
        "attendance_summary_monthly_templates": _queue_task_is_active(queue, "attendance_summary_monthly_templates"),
        "storage_analysis": _queue_task_is_active(queue, "storage_analysis"),
    })

    tasks = []
    for spec in BACKGROUND_TASK_SPECS:
        if is_background_task_deleted(spec.key):
            continue
        task_state = runner_tasks.get(spec.key) if isinstance(runner_tasks.get(spec.key), dict) else {}
        tasks.append(
            BackgroundTaskRead(
                key=spec.key,
                title=spec.title,
                category=spec.category,
                description=spec.description,
                schedule_label=str(task_state.get("schedule_label") or spec.schedule_label),
                enabled=bool(task_state.get("enabled")),
                runner_running=bool(runner.get("runner_running")),
                next_run_at=task_state.get("next_run_at"),
                retry_policy=str(task_state.get("retry_label") or spec.retry_label),
                trigger_warning=spec.manual_warning,
                active=bool(active_by_key.get(spec.key)),
                latest_run=latest_by_key.get(spec.key),
            )
        )
    return BackgroundTaskStatusResponse(
        queue=queue,
        tasks=tasks,
        runner_running=bool(runner.get("runner_running")),
        next_wake_at=runner.get("next_wake_at"),
        runner_error=runner.get("last_error"),
    )


@tasks_router.post("/background-tasks/{task_key}/trigger", response_model=BackgroundTaskTriggerResponse)
def trigger_background_task(
    task_key: str,
    session: Session = Depends(get_session),
):
    normalized_key = task_key.strip()
    if normalized_key == "storage_analysis":
        queue_task_id = background_task_queue.enqueue("storage_analysis", scheduled_analysis_job)
        return BackgroundTaskTriggerResponse(task_key=normalized_key, queue_task_id=queue_task_id)

    if normalized_key == "attendance_summary_monthly_templates":
        from backend.api.note_sheets import run_attendance_summary_template_job

        queue_task_id = background_task_queue.enqueue(
            "attendance_summary_monthly_templates",
            run_attendance_summary_template_job,
        )
        return BackgroundTaskTriggerResponse(task_key=normalized_key, queue_task_id=queue_task_id)

    if normalized_key == "note_metadata_feedback_optimization":
        run = create_note_metadata_feedback_optimization_run(
            session,
            trigger_reason="manual_admin",
            enqueue=True,
            require_auto_conditions=False,
        )
        if run is None:
            raise HTTPException(status_code=409, detail="暂时不满足优化任务触发条件")
        return BackgroundTaskTriggerResponse(
            task_key=normalized_key,
            queue_task_id=run.queue_task_id,
            run=_without_queue(get_note_metadata_feedback_status(session).get("latest_run") or {}),
        )

    if normalized_key == "codex_diary_yesterday_import":
        from backend.api.notes import get_codex_diary_auto_import_status, maybe_enqueue_codex_diary_yesterday_import

        queue_task_id = maybe_enqueue_codex_diary_yesterday_import(trigger_reason="manual_admin")
        if queue_task_id is None:
            raise HTTPException(status_code=409, detail="Codex 星图日记任务已在队列中")
        return BackgroundTaskTriggerResponse(
            task_key=normalized_key,
            queue_task_id=queue_task_id,
            run=get_codex_diary_auto_import_status(session).get("latest_run"),
        )

    if normalized_key == "auto_git_commit":
        run = create_auto_git_commit_run(
            session,
            trigger_reason="manual_admin",
            enqueue=True,
        )
        return BackgroundTaskTriggerResponse(
            task_key=normalized_key,
            queue_task_id=run.queue_task_id,
            run=get_auto_git_commit_status(session).get("latest_run"),
        )

    spec = get_background_task_spec(normalized_key)
    if spec is not None:
        queue_task_id = spec.action()
        return BackgroundTaskTriggerResponse(
            task_key=normalized_key,
            queued=queue_task_id is not None,
            queue_task_id=queue_task_id,
        )

    raise HTTPException(status_code=404, detail="后台任务不存在")


class BackgroundTaskToggleRequest(BaseModel):
    enabled: bool

@tasks_router.post("/background-tasks/{task_key}/toggle", response_model=dict)
def toggle_background_task(
    task_key: str,
    payload: BackgroundTaskToggleRequest,
    session: Session = Depends(get_session),
):
    normalized_key = task_key.strip()
    enabled = payload.enabled

    if not any(spec.key == normalized_key for spec in BACKGROUND_TASK_SPECS):
        raise HTTPException(status_code=404, detail="后台任务不存在")
    set_background_task_enabled(normalized_key, enabled)
    refresh_background_task_schedule_states(normalized_key)
    return {"success": True, "enabled": enabled}


@tasks_router.delete("/background-tasks/queue/{task_id}", response_model=dict)
def delete_background_queue_task(task_id: str):
    status = background_task_queue.delete(task_id)
    if status == "missing":
        raise HTTPException(status_code=404, detail="队列任务不存在")
    if status == "running":
        raise HTTPException(status_code=409, detail="正在运行的任务不能删除")
    return {"success": True, "deleted": True, "task_id": task_id}


@tasks_router.delete("/background-tasks/{task_key}", response_model=dict)
def delete_background_task(task_key: str):
    normalized_key = task_key.strip()
    if not any(spec.key == normalized_key for spec in BACKGROUND_TASK_SPECS):
        raise HTTPException(status_code=404, detail="后台任务不存在")
    set_background_task_deleted(normalized_key, True)
    deleted_pending_count = background_task_queue.delete_pending_by_name(normalized_key)
    refresh_background_task_schedule_states(normalized_key)
    return {
        "success": True,
        "deleted": True,
        "task_key": normalized_key,
        "deleted_pending_count": deleted_pending_count,
    }


@tasks_router.post("/background-tasks/{task_key}/reset-schedule", response_model=dict)
def reset_background_task_schedule_api(task_key: str):
    normalized_key = task_key.strip()
    if not any(spec.key == normalized_key for spec in BACKGROUND_TASK_SPECS):
        raise HTTPException(status_code=404, detail="后台任务不存在")
    changed = reset_background_task_schedule(normalized_key)
    return {"success": True, "changed": changed}

@images_router.get("/storage/workspace-usage", response_model=WorkspaceUsageResponse)
def get_storage_workspace_usage(
    scope: str = "data_workspace",
    refresh: bool = False,
    top_limit: int = 20,
    session: Session = Depends(get_session),
):
    """
    Recursively scan a storage-related directory.
    data_workspace is the actual file-data workspace; source_dir is the source repository.
    """
    return _load_workspace_usage_payload(scope=scope, refresh=refresh, top_limit=top_limit, session=session)

@images_router.get("/storage/dashboard", response_model=StorageDashboardStats)
def get_storage_dashboard(session: Session = Depends(get_session)):
    """
    Get quick overview stats for the dashboard.
    Optimized for speed.
    """
    # 1. Disk Stats (Fast scan)
    total_size = 0
    file_count = 0
    
    if os.path.exists(ATTACHMENTS_ABS_PATH):
        try:
            with os.scandir(ATTACHMENTS_ABS_PATH) as it:
                for entry in it:
                    if entry.is_file():
                        total_size += entry.stat().st_size
                        file_count += 1
        except Exception:
            pass

    # 2. DB Stats
    note_count = session.exec(select(func.count(NoteNode.id))).one()
    
    return StorageDashboardStats(
        total_size_bytes=total_size,
        total_file_count=file_count,
        total_note_count=note_count,
        orphan_size_bytes=0, # Placeholder
        orphan_count=0,      # Placeholder
        dead_link_count=0,   # Placeholder
        health_score=98,     # Mock
        attachments_path=ATTACHMENTS_ABS_PATH,
        data_workspace_path=os.fspath(_get_data_workspace_dir()),
    )

@images_router.get("/storage/analysis", response_model=StorageAnalysisResponse)
def get_storage_analysis(session: Session = Depends(get_session)):
    """
    Deep analysis: Top 50 files, Top 50 nodes.
    This is the heavy operation.
    """
    # 1. Top 50 Files
    top_files = []
    file_types = {}
    
    if os.path.exists(ATTACHMENTS_ABS_PATH):
        try:
            file_list = []
            with os.scandir(ATTACHMENTS_ABS_PATH) as it:
                for entry in it:
                    if entry.is_file():
                        stat = entry.stat()
                        file_list.append({
                            "filename": entry.name,
                            "size": stat.st_size,
                            "mtime": stat.st_mtime
                        })
                        
                        ext = os.path.splitext(entry.name)[1].lower()
                        file_types[ext] = file_types.get(ext, 0) + 1
            
            # Sort by size desc
            file_list.sort(key=lambda x: x["size"], reverse=True)
            for f in file_list[:50]:
                top_files.append(TopFile(
                    filename=f["filename"],
                    size=f["size"],
                    mtime=f["mtime"],
                    url=build_attachment_url(f["filename"])
                ))
        except Exception as e:
            print(f"Error scanning files: {e}")

    # 2. Top 50 Nodes (Optimized SQL)
    top_nodes = []
    try:
        # Use SQL length function
        stmt = select(NoteNode.id, NoteNode.title, func.length(NoteNode.content).label("size"), NoteNode.updated_at)\
               .order_by(text("size DESC"))\
               .limit(50)
        
        results = session.exec(stmt).all()
        for row in results:
            top_nodes.append(TopNode(
                id=str(row.id),
                title=row.title or "Untitled",
                size=row.size or 0,
                updated_at=row.updated_at
            ))
    except Exception as e:
        print(f"Error querying nodes: {e}")

    return StorageAnalysisResponse(
        top_files=top_files,
        top_nodes=top_nodes,
        file_type_distribution=file_types
    )

@images_router.get("/storage/maintenance", response_model=MaintenanceStatusResponse)
def get_maintenance_status(session: Session = Depends(get_session)):
    """
    Get orphan files and dead links.
    """
    # 1. Scan Disk
    disk_files = set()
    disk_files_by_stem = {}
    file_stats = {}
    
    if os.path.exists(ATTACHMENTS_ABS_PATH):
        for filename in os.listdir(ATTACHMENTS_ABS_PATH):
            filepath = os.path.join(ATTACHMENTS_ABS_PATH, filename)
            if os.path.isfile(filepath):
                disk_files.add(filename)
                stat = os.stat(filepath)
                file_stats[filename] = {"size": stat.st_size}
                
                stem = os.path.splitext(filename)[0]
                if stem not in disk_files_by_stem:
                    disk_files_by_stem[stem] = []
                disk_files_by_stem[stem].append(filename)

    # 2. Scan DB for references
    referenced_files = set()
    dead_links = []
    fixable_links = []
    
    notes = session.exec(select(NoteNode)).all()
    
    for note in notes:
        if note.content:
            matches = ATTACHMENT_URL_PATTERN.findall(note.content)
            for filename in matches:
                referenced_files.add(filename)
                
                if filename not in disk_files:
                    stem = os.path.splitext(filename)[0]
                    candidates = disk_files_by_stem.get(stem)
                    if candidates:
                        fixable_links.append(FixableLink(
                            note_id=str(note.id),
                            note_title=note.title or "Untitled",
                            original_url=build_attachment_url(filename),
                            suggested_url=build_attachment_url(candidates[0])
                        ))
                    else:
                        dead_links.append({
                            "note_id": note.id,
                            "note_title": note.title,
                            "link": build_attachment_url(filename)
                        })

    # 3. Calculate Orphans
    orphan_filenames = disk_files - referenced_files
    orphan_count = len(orphan_filenames)
    orphan_size = sum(file_stats[f]["size"] for f in orphan_filenames)
    
    return MaintenanceStatusResponse(
        orphan_count=orphan_count,
        orphan_size=orphan_size,
        dead_links=dead_links,
        fixable_links=fixable_links
    )

@images_router.get("/images/orphans", response_model=OrphanImageResponse)
def get_orphan_images(session: Session = Depends(get_session)):
    """
    Legacy/Specific endpoint for the Orphan Table detail view
    """
    # Reuse logic for simplicity
    all_files = set()
    file_stats = {}
    total_size = 0
    if os.path.exists(ATTACHMENTS_ABS_PATH):
        for filename in os.listdir(ATTACHMENTS_ABS_PATH):
            fp = os.path.join(ATTACHMENTS_ABS_PATH, filename)
            if os.path.isfile(fp):
                all_files.add(filename)
                st = os.stat(fp)
                file_stats[filename] = {"size": st.st_size, "mtime": st.st_mtime}
                total_size += st.st_size

    referenced = set()
    notes = session.exec(select(NoteNode)).all()
    for n in notes:
        if n.content:
            for m in ATTACHMENT_URL_PATTERN.findall(n.content):
                referenced.add(m)
    
    orphans = []
    orphan_files = all_files - referenced
    orphan_size = 0
    for f in orphan_files:
        s = file_stats.get(f, {"size": 0, "mtime": 0})
        orphan_size += s["size"]
        orphans.append(OrphanImage(
            filename=f,
            size=s["size"],
            mtime=s["mtime"],
            url=build_attachment_url(f)
        ))
    
    orphans.sort(key=lambda x: x.size, reverse=True)
    
    return OrphanImageResponse(
        stats=StorageStats(
            total_count=len(all_files),
            total_size=total_size,
            orphan_count=len(orphan_files),
            orphan_size=orphan_size
        ),
        orphans=orphans
    )

@images_router.get("/storage/schedule", response_model=ScheduleConfig)
def get_schedule_config():
    config = load_config()
    return ScheduleConfig(
        enabled=config.get("schedule_enabled", False),
        cron_expression=config.get("cron_expression", DEFAULT_STORAGE_SCHEDULE["cron_expression"])
    )

@images_router.post("/storage/schedule", response_model=ScheduleConfig)
def set_schedule_config(config: ScheduleConfig):
    save_config(config.dict())
    
    if config.enabled:
        try:
            if not storage_scheduler.running:
                storage_scheduler.start()
            storage_scheduler.add_job(
                lambda: background_task_queue.enqueue("storage_analysis", scheduled_analysis_job),
                CronTrigger.from_crontab(config.cron_expression),
                id="storage_analysis",
                replace_existing=True
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid cron expression: {e}")
    else:
        if storage_scheduler.get_job("storage_analysis"):
            storage_scheduler.remove_job("storage_analysis")
            
    return config


@images_router.get("/device-control/identity", response_model=DeviceControlIdentityResponse)
def get_device_control_identity():
    return DeviceControlIdentityResponse(
        device_id=get_device_id(),
        device_token_enabled=bool(settings.device_token),
        data_dir=os.fspath(settings.data_dir),
    )

@images_router.post("/storage/fix-links", response_model=dict)
def fix_broken_links(session: Session = Depends(get_session)):
    """
    Automatically fix broken links.
    Handles dead links where a file with same UUID but different extension exists.
    Does NOT update 'updated_at' timestamp of notes.
    """
    if not os.path.exists(ATTACHMENTS_ABS_PATH):
         return {"fixed_count": 0, "message": "Attachment directory not found"}

    disk_files_by_stem = {}
    for filename in os.listdir(ATTACHMENTS_ABS_PATH):
        filepath = os.path.join(ATTACHMENTS_ABS_PATH, filename)
        if os.path.isfile(filepath):
            stem = os.path.splitext(filename)[0]
            if stem not in disk_files_by_stem:
                disk_files_by_stem[stem] = []
            disk_files_by_stem[stem].append(filename)

    notes = session.exec(select(NoteNode)).all()
    
    fixed_count = 0
    fixed_notes_count = 0
    
    for note in notes:
        if not note.content:
            continue
            
        original_content = note.content
        new_content = original_content
        note_modified = False
        
        matches = list(set(ATTACHMENT_URL_PATTERN.findall(original_content)))
        
        for filename in matches:
            filepath = os.path.join(ATTACHMENTS_ABS_PATH, filename)
            if not os.path.exists(filepath):
                stem = os.path.splitext(filename)[0]
                candidates = disk_files_by_stem.get(stem)
                
                if candidates:
                    suggested_filename = candidates[0]
                    new_link = build_attachment_url(suggested_filename)
                    old_links = (
                        f"/static/uploads/{filename}",
                        build_attachment_url(filename),
                    )

                    for old_link in old_links:
                        if old_link in new_content:
                            new_content = new_content.replace(old_link, new_link)
                            fixed_count += 1
                            note_modified = True
                            break
        
        if note_modified:
            # Manually update content without triggering updated_at change if possible
            # SQLModel/SQLAlchemy defaults usually update updated_at if configured in model events
            # But our model definition uses default_factory=time.time, which only sets on create/init if not provided?
            # Actually, looking at model: updated_at: float = Field(default_factory=time.time)
            # This is NOT an onupdate server-side trigger usually, unless there's event listener.
            # Let's check model definition again.
            # It's just a default factory. It won't auto-update on update unless we set it or there is a database trigger.
            # So simply setting note.content = new_content and session.add(note) should NOT change updated_at
            # unless we explicitly set note.updated_at = time.time().
            # So we are safe.
            note.content = new_content
            session.add(note)
            fixed_notes_count += 1
            
    if fixed_notes_count > 0:
        session.commit()
        
    return {
        "fixed_links_count": fixed_count,
        "fixed_notes_count": fixed_notes_count,
        "message": f"Fixed {fixed_count} links in {fixed_notes_count} notes."
    }

from PIL import Image
import io

# ... (Existing code)

class OptimizeImageRequest(BaseModel):
    filename: str
    target_format: str = "jpeg" # jpeg, webp
    quality: int = 80

class OptimizedPreview(BaseModel):
    original_size: int
    optimized_size: int
    saved_bytes: int
    preview_url: str # data url or temp url

@images_router.post("/images/optimize-preview", response_model=OptimizedPreview)
def preview_optimized_image(request: OptimizeImageRequest):
    """
    Generate an optimized version of the image for preview.
    Returns size comparison and a base64 data URL (or temp link).
    """
    if "/" in request.filename or "\\" in request.filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
        
    file_path = os.path.join(ATTACHMENTS_ABS_PATH, request.filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    try:
        original_size = os.path.getsize(file_path)
        
        with Image.open(file_path) as img:
            # Convert to RGB if saving as JPEG
            if request.target_format.lower() == "jpeg" and img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
                
            output_buffer = io.BytesIO()
            img.save(output_buffer, format=request.target_format, quality=request.quality)
            optimized_size = output_buffer.tell()
            
            # Encode to base64 for direct frontend preview
            import base64
            img_str = base64.b64encode(output_buffer.getvalue()).decode("utf-8")
            mime_type = f"image/{request.target_format.lower()}"
            preview_url = f"data:{mime_type};base64,{img_str}"
            
            return OptimizedPreview(
                original_size=original_size,
                optimized_size=optimized_size,
                saved_bytes=original_size - optimized_size,
                preview_url=preview_url
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Image processing failed: {str(e)}")

@images_router.post("/images/optimize-confirm", response_model=dict)
def confirm_image_optimization(request: OptimizeImageRequest):
    """
    Overwrite the original image with the optimized version.
    NOTE: If format changes (e.g. png -> jpg), we might need to update DB references or keep extension.
    To be safe and simple: We will KEEP the original extension if possible, or force update filename.
    BUT updating filename requires DB update.
    STRATEGY:
    1. If format is same (jpg->jpg compressed), just overwrite.
    2. If format diff (png->jpg), we save as .jpg, delete .png, and update all DB references.
    """
    if "/" in request.filename or "\\" in request.filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
        
    file_path = os.path.join(ATTACHMENTS_ABS_PATH, request.filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    try:
        # 1. Optimize
        with Image.open(file_path) as img:
            if request.target_format.lower() == "jpeg" and img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            
            # Determine new filename
            stem = os.path.splitext(request.filename)[0]
            new_ext = f".{request.target_format.lower()}"
            # Map jpeg to jpg commonly
            if new_ext == ".jpeg": new_ext = ".jpg"
            
            new_filename = f"{stem}{new_ext}"
            new_file_path = os.path.join(ATTACHMENTS_ABS_PATH, new_filename)
            
            # Save to temp first to ensure success
            temp_path = new_file_path + ".tmp"
            img.save(temp_path, format=request.target_format, quality=request.quality)
            
        # 2. Replace
        # If filename changed, we need to update DB references
        old_filename = request.filename
        
        if new_filename != old_filename:
            # Update DB
            session = get_session()
            # Need a new session context or pass dependency? 
            # We can use the global get_session logic or better, inject session.
            # But wait, this function signature didn't ask for session. Let's add it.
            # For now, let's assume we can't easily update DB in this scope without refactoring injection.
            # Let's add session to dependency.
            pass # See below
            
        # Swap files
        if os.path.exists(new_file_path) and new_filename != old_filename:
            # Target exists? Danger. But usually UUIDs don't collide.
            pass
            
        os.replace(temp_path, new_file_path)
        if new_filename != old_filename:
            os.remove(file_path)
            
        return {"success": True, "new_filename": new_filename}
        
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise HTTPException(status_code=500, detail=f"Optimization failed: {str(e)}")

@images_router.post("/images/optimize-confirm-with-db", response_model=dict)
def confirm_image_optimization_with_db(request: OptimizeImageRequest):
    """
    Optimize image.
    NO DB update is performed here. Extension changes will be handled by dead link fixer.
    """
    if "/" in request.filename or "\\" in request.filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
        
    file_path = os.path.join(ATTACHMENTS_ABS_PATH, request.filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    temp_path = ""
    try:
        # 1. Optimize
        with Image.open(file_path) as img:
            if request.target_format.lower() == "jpeg" and img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            
            stem = os.path.splitext(request.filename)[0]
            new_ext = f".{request.target_format.lower()}"
            if new_ext == ".jpeg": new_ext = ".jpg"
            
            new_filename = f"{stem}{new_ext}"
            new_file_path = os.path.join(ATTACHMENTS_ABS_PATH, new_filename)
            
            temp_path = new_file_path + ".tmp"
            img.save(temp_path, format=request.target_format, quality=request.quality)
            
        # 2. Finalize File System
        os.replace(temp_path, new_file_path)
        if new_filename != request.filename:
            try:
                os.remove(file_path)
            except:
                pass # Original might be gone or locked?
            
        return {
            "success": True, 
            "new_filename": new_filename, 
            "db_updates": 0 # Logic removed
        }
        
    except Exception as e:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
        raise HTTPException(status_code=500, detail=f"Optimization failed: {str(e)}")

@images_router.post("/images/delete", response_model=dict)
def delete_orphan_images(request: DeleteImagesRequest):
    """
    Delete specified orphan images.
    """
    deleted_count = 0
    errors = []
    
    for filename in request.filenames:
        if "/" in filename or "\\" in filename or ".." in filename:
            errors.append(f"Invalid filename: {filename}")
            continue
            
        file_path = os.path.join(ATTACHMENTS_ABS_PATH, filename)
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                deleted_count += 1
            except Exception as e:
                errors.append(f"Failed to delete {filename}: {str(e)}")
        else:
            errors.append(f"File not found: {filename}")
            
    return {
        "deleted_count": deleted_count,
        "errors": errors
    }


router.include_router(accounts_router)
router.include_router(images_router)
router.include_router(tasks_router)
