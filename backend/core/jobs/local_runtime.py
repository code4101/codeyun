from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from filelock import FileLock, Timeout
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, Session, select

from backend.core.services.launcher import popen_python_module_background
from backend.core.settings import get_settings
from backend.core.temp_paths import codeyun_temp_root
from backend.db import engine
from backend.models import LocalJobRun


ACTIVE_LOCAL_JOB_STATUSES = frozenset({"queued", "running"})
TERMINAL_LOCAL_JOB_STATUSES = frozenset({"succeeded", "failed", "cancelled", "interrupted"})
MAX_LOCAL_JOB_JSON_BYTES = 2 * 1024 * 1024


class LocalJobCancelledError(RuntimeError):
    pass


LocalJobHandler = Callable[["LocalJobContext", dict[str, Any]], Any]
ResourceKeyFactory = Callable[[dict[str, Any]], str]


@dataclass(frozen=True, slots=True)
class LocalJobSpec:
    job_type: str
    handler: LocalJobHandler
    resource_key: str | ResourceKeyFactory = ""
    title: str = ""
    cancellable: bool = True
    user_submittable: bool = False


_JOB_SPECS: dict[str, LocalJobSpec] = {}
_JOB_SPECS_LOCK = threading.Lock()


def register_local_job(spec: LocalJobSpec) -> None:
    normalized = str(spec.job_type or "").strip()
    if not normalized or normalized != spec.job_type:
        raise ValueError("Local Job 类型不能为空或包含首尾空白。")
    with _JOB_SPECS_LOCK:
        existing = _JOB_SPECS.get(normalized)
        if existing is not None and existing != spec:
            raise ValueError(f"Local Job 类型重复注册：{normalized}")
        _JOB_SPECS[normalized] = spec


def get_local_job_spec(job_type: str) -> LocalJobSpec:
    _load_builtin_specs()
    normalized = str(job_type or "").strip()
    with _JOB_SPECS_LOCK:
        spec = _JOB_SPECS.get(normalized)
    if spec is None:
        raise ValueError(f"未注册的 Local Job 类型：{normalized or '<empty>'}")
    return spec


def list_local_job_specs(*, user_submittable_only: bool = False) -> list[dict[str, Any]]:
    _load_builtin_specs()
    with _JOB_SPECS_LOCK:
        specs = [
            spec
            for spec in _JOB_SPECS.values()
            if not user_submittable_only or spec.user_submittable
        ]
    return [
        {
            "job_type": spec.job_type,
            "title": spec.title or spec.job_type,
            "cancellable": spec.cancellable,
            "user_submittable": spec.user_submittable,
        }
        for spec in sorted(specs, key=lambda item: item.job_type)
    ]


def _load_builtin_specs() -> None:
    from backend.core.jobs import local_handlers  # noqa: F401


def ensure_local_job_schema(db_engine: Engine = engine) -> None:
    SQLModel.metadata.create_all(db_engine, tables=[LocalJobRun.__table__])


def _json_object(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    encoded = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
    if len(encoded) > MAX_LOCAL_JOB_JSON_BYTES:
        raise ValueError(f"Local Job JSON 超过 {MAX_LOCAL_JOB_JSON_BYTES} 字节限制。")
    serialized = json.loads(encoded.decode("utf-8"))
    return serialized if isinstance(serialized, dict) else {"value": serialized}


def _resolve_resource_key(spec: LocalJobSpec, payload: dict[str, Any], run_id: str) -> str:
    raw = spec.resource_key(payload) if callable(spec.resource_key) else spec.resource_key
    return str(raw or f"local-job:{run_id}").strip()


def _resource_lock_path(resource_key: str, *, root: Path | None = None) -> Path:
    lock_root = root or (get_settings().data_dir / "local-jobs" / "locks")
    lock_root.mkdir(parents=True, exist_ok=True)
    readable = "".join(char if char.isalnum() or char in "._-" else "_" for char in resource_key)
    readable = readable.strip("._-")[:48] or "job"
    digest = hashlib.sha256(resource_key.encode("utf-8")).hexdigest()[:12]
    return lock_root / f"{readable}-{digest}.lock"


def serialize_local_job_run(run: LocalJobRun) -> dict[str, Any]:
    result_json = copy.deepcopy(run.result_json or {})
    progress = result_json.pop("__progress__", {}) if isinstance(result_json, dict) else {}
    if not isinstance(progress, dict):
        progress = {}
    return {
        "id": run.id,
        "user_id": run.user_id,
        "job_type": run.job_type,
        "resource_key": run.resource_key,
        "status": run.status,
        "running": run.status in ACTIVE_LOCAL_JOB_STATUSES,
        "stage": run.stage,
        "message": run.message,
        "input": copy.deepcopy(run.input_json or {}),
        "result": result_json,
        "progress_current": progress.get("current"),
        "progress_total": progress.get("total"),
        "error": run.error_message,
        "worker_pid": run.worker_pid,
        "worker_started_at": run.worker_started_at,
        "heartbeat_at": run.heartbeat_at,
        "cancel_requested_at": run.cancel_requested_at,
        "stdout_path": run.stdout_path,
        "stderr_path": run.stderr_path,
        "attempt_count": run.attempt_count,
        "queued_at": run.queued_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "updated_at": run.updated_at,
    }


def create_local_job_run(
    *,
    job_type: str,
    payload: dict[str, Any] | None = None,
    user_id: int | None = None,
    resource_key: str | None = None,
    db_engine: Engine = engine,
) -> LocalJobRun:
    ensure_local_job_schema(db_engine)
    spec = get_local_job_spec(job_type)
    input_json = _json_object(payload)
    run = LocalJobRun(
        user_id=user_id,
        job_type=spec.job_type,
        input_json=input_json,
        queued_at=time.time(),
        updated_at=time.time(),
    )
    run.resource_key = str(resource_key or _resolve_resource_key(spec, input_json, run.id))
    with Session(db_engine) as session:
        session.add(run)
        session.commit()
        session.refresh(run)
        session.expunge(run)
    return run


def launch_local_job_worker(run_id: str, *, db_engine: Engine = engine) -> LocalJobRun:
    ensure_local_job_schema(db_engine)
    if db_engine is not engine:
        raise ValueError("独立 Worker 只能使用 CodeYun 已配置的持久数据库。")
    with Session(db_engine) as session:
        run = session.get(LocalJobRun, run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status != "queued":
            raise RuntimeError(f"Local Job 当前不是 queued：{run.status}")
        log_root = codeyun_temp_root("local-jobs", run.job_type.replace(".", "-"))
        log_root.mkdir(parents=True, exist_ok=True)
        stdout_path = log_root / f"{run.id}.stdout.log"
        stderr_path = log_root / f"{run.id}.stderr.log"
        with stdout_path.open("ab") as stdout_file, stderr_path.open("ab") as stderr_file:
            process = popen_python_module_background(
                "backend.core.jobs.local_worker",
                "--run-id",
                run.id,
                preferred_root=Path(__file__).resolve().parents[3],
                cwd=Path(__file__).resolve().parents[3],
                stdout=stdout_file,
                stderr=stderr_file,
            )
        now = time.time()
        run.worker_pid = int(process.pid)
        run.stdout_path = str(stdout_path)
        run.stderr_path = str(stderr_path)
        run.updated_at = now
        session.add(run)
        session.commit()
        session.refresh(run)
        session.expunge(run)
        return run


def submit_local_job(
    *,
    job_type: str,
    payload: dict[str, Any] | None = None,
    user_id: int | None = None,
    resource_key: str | None = None,
) -> LocalJobRun:
    run = create_local_job_run(
        job_type=job_type,
        payload=payload,
        user_id=user_id,
        resource_key=resource_key,
    )
    return launch_local_job_worker(run.id)


def submit_local_job_once(
    *,
    job_type: str,
    payload: dict[str, Any] | None = None,
    user_id: int | None = None,
    resource_key: str | None = None,
    dedup_key: str | None = None,
) -> tuple[LocalJobRun, bool]:
    """Return an active matching run or atomically submit a new detached run.

    The short-lived file lock closes the cross-process race between the active
    lookup and run creation.  The job's own resource lock still controls actual
    execution and may intentionally be shared by several different job types.
    """

    normalized_job_type = str(job_type or "").strip()
    key = str(dedup_key or normalized_job_type).strip()
    if not normalized_job_type or not key:
        raise ValueError("Local Job 去重类型和键不能为空。")
    input_json = _json_object(payload)
    spec = get_local_job_spec(normalized_job_type)
    expected_resource_key = str(resource_key or _resolve_resource_key(spec, input_json, "submit-once"))
    lock = FileLock(str(_resource_lock_path(f"submit-once:{key}")))
    with lock:
        active = find_active_local_job_run(
            normalized_job_type,
            resource_key=expected_resource_key,
        )
        if active is not None:
            return active, False
        return (
            submit_local_job(
                job_type=normalized_job_type,
                payload=input_json,
                user_id=user_id,
                resource_key=resource_key,
            ),
            True,
        )


def request_local_job_cancel(run_id: str, *, db_engine: Engine = engine) -> LocalJobRun:
    ensure_local_job_schema(db_engine)
    with Session(db_engine) as session:
        run = session.get(LocalJobRun, run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status not in ACTIVE_LOCAL_JOB_STATUSES:
            raise RuntimeError(f"Local Job 已结束：{run.status}")
        spec = get_local_job_spec(run.job_type)
        if not spec.cancellable:
            raise RuntimeError(f"Local Job 不支持取消：{run.job_type}")
        now = time.time()
        run.cancel_requested_at = run.cancel_requested_at or now
        run.message = "已请求取消，等待 Worker 安全退出"
        run.updated_at = now
        session.add(run)
        session.commit()
        session.refresh(run)
        session.expunge(run)
        return run


class LocalJobContext:
    def __init__(self, run_id: str, db_engine: Engine):
        self.run_id = run_id
        self.task_id = run_id
        self.db_engine = db_engine

    @property
    def job_type(self) -> str:
        with Session(self.db_engine) as session:
            run = session.get(LocalJobRun, self.run_id)
            return str(run.job_type) if run is not None else ""

    def heartbeat(
        self,
        *,
        stage: str | None = None,
        message: str | None = None,
        progress_current: int | None = None,
        progress_total: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.raise_if_cancelled()
        with Session(self.db_engine) as session:
            run = session.get(LocalJobRun, self.run_id)
            if run is None:
                raise KeyError(self.run_id)
            now = time.time()
            if stage is not None:
                run.stage = stage
            if message is not None:
                run.message = message
            if progress_current is not None or progress_total is not None or metadata is not None:
                result = copy.deepcopy(run.result_json or {})
                current_progress = result.get("__progress__")
                progress = dict(current_progress) if isinstance(current_progress, dict) else {}
                if progress_current is not None:
                    progress["current"] = int(progress_current)
                if progress_total is not None:
                    progress["total"] = int(progress_total)
                if metadata is not None:
                    progress["metadata"] = _json_object(metadata)
                result["__progress__"] = progress
                run.result_json = _json_object(result)
            run.heartbeat_at = now
            run.updated_at = now
            session.add(run)
            session.commit()

    def raise_if_cancelled(self) -> None:
        with Session(self.db_engine) as session:
            run = session.get(LocalJobRun, self.run_id)
            if run is None:
                raise KeyError(self.run_id)
            if run.cancel_requested_at is not None:
                raise LocalJobCancelledError("用户请求取消 Local Job。")


def _worker_process_started_at() -> float:
    try:
        import psutil

        return float(psutil.Process(os.getpid()).create_time())
    except Exception:
        return time.time()


def _set_terminal_state(
    run_id: str,
    *,
    status: str,
    stage: str,
    message: str,
    result: Any = None,
    error: str | None = None,
    db_engine: Engine,
) -> None:
    with Session(db_engine) as session:
        run = session.get(LocalJobRun, run_id)
        if run is None:
            return
        now = time.time()
        run.status = status
        run.stage = stage
        run.message = message
        run.result_json = _json_object(result)
        run.error_message = error
        run.heartbeat_at = now
        run.finished_at = now
        run.updated_at = now
        session.add(run)
        session.commit()


def run_local_job(
    run_id: str,
    *,
    db_engine: Engine = engine,
    lock_root: Path | None = None,
) -> int:
    ensure_local_job_schema(db_engine)
    with Session(db_engine) as session:
        run = session.get(LocalJobRun, run_id)
        if run is None:
            return 2
        spec = get_local_job_spec(run.job_type)
        payload = copy.deepcopy(run.input_json or {})
        resource_key = run.resource_key

    context = LocalJobContext(run_id, db_engine)
    lock = FileLock(str(_resource_lock_path(resource_key, root=lock_root)))
    while True:
        try:
            context.raise_if_cancelled()
        except LocalJobCancelledError as exc:
            _set_terminal_state(
                run_id,
                status="cancelled",
                stage="cancelled",
                message="本地任务已取消",
                error=str(exc),
                db_engine=db_engine,
            )
            return 0
        try:
            lock.acquire(timeout=0.5)
            break
        except Timeout:
            context.heartbeat(stage="waiting-resource", message=f"等待资源：{resource_key}")

    stop_heartbeat = threading.Event()

    def heartbeat_loop() -> None:
        while not stop_heartbeat.wait(5):
            try:
                context.heartbeat()
            except Exception:
                return

    try:
        with Session(db_engine) as session:
            run = session.get(LocalJobRun, run_id)
            if run is None:
                return 2
            if run.status != "queued":
                return 2
            now = time.time()
            run.status = "running"
            run.stage = "running"
            run.message = spec.title or spec.job_type
            run.worker_pid = os.getpid()
            run.worker_started_at = _worker_process_started_at()
            run.heartbeat_at = now
            run.started_at = run.started_at or now
            run.attempt_count = int(run.attempt_count or 0) + 1
            run.updated_at = now
            session.add(run)
            session.commit()
        heartbeat_thread = threading.Thread(
            target=heartbeat_loop,
            name=f"local-job-heartbeat-{run_id[:8]}",
            daemon=True,
        )
        heartbeat_thread.start()
        context.raise_if_cancelled()
        result = spec.handler(context, payload)
        context.raise_if_cancelled()
        _set_terminal_state(
            run_id,
            status="succeeded",
            stage="completed",
            message="本地任务执行完成",
            result=result,
            db_engine=db_engine,
        )
        return 0
    except LocalJobCancelledError as exc:
        _set_terminal_state(
            run_id,
            status="cancelled",
            stage="cancelled",
            message="本地任务已取消",
            error=str(exc),
            db_engine=db_engine,
        )
        return 0
    except Exception as exc:
        _set_terminal_state(
            run_id,
            status="failed",
            stage="failed",
            message="本地任务执行失败",
            error=f"{type(exc).__name__}: {exc}",
            db_engine=db_engine,
        )
        return 1
    finally:
        stop_heartbeat.set()
        if lock.is_locked:
            lock.release()


def get_local_job_run(run_id: str, *, db_engine: Engine = engine) -> LocalJobRun | None:
    ensure_local_job_schema(db_engine)
    reconcile_local_job_run(run_id, db_engine=db_engine)
    with Session(db_engine) as session:
        run = session.get(LocalJobRun, run_id)
        if run is None:
            return None
        session.expunge(run)
        return run


def _worker_identity_matches(pid: int | None, started_at: float | None) -> bool:
    if not pid:
        return False
    try:
        import psutil

        process = psutil.Process(int(pid))
        if not process.is_running():
            return False
        if started_at is None:
            return True
        return abs(float(process.create_time()) - float(started_at)) <= 2
    except Exception:
        return False


def reconcile_local_job_run(
    run_id: str,
    *,
    db_engine: Engine = engine,
    launch_grace_seconds: float = 10.0,
) -> bool:
    """Project a vanished Worker as infrastructure interruption, not business failure."""

    ensure_local_job_schema(db_engine)
    with Session(db_engine) as session:
        run = session.get(LocalJobRun, run_id)
        if run is None or run.status not in ACTIVE_LOCAL_JOB_STATUSES:
            return False
        now = time.time()
        within_launch_grace = (
            run.status == "queued"
            and run.worker_started_at is None
            and now - float(run.queued_at or now) <= max(float(launch_grace_seconds), 0)
        )
        if within_launch_grace or _worker_identity_matches(run.worker_pid, run.worker_started_at):
            return False
        run.status = "interrupted"
        run.stage = "worker-interrupted"
        run.message = "本地 Worker 已退出，执行通道中断"
        run.error_message = "Worker 进程不存在或 PID 身份不匹配；未将其记为业务失败。"
        run.finished_at = now
        run.updated_at = now
        session.add(run)
        session.commit()
        return True


def list_local_job_runs(
    *,
    user_id: int | None = None,
    limit: int = 50,
    db_engine: Engine = engine,
) -> list[LocalJobRun]:
    ensure_local_job_schema(db_engine)
    statement = select(LocalJobRun)
    if user_id is not None:
        statement = statement.where(LocalJobRun.user_id == int(user_id))
    statement = statement.order_by(LocalJobRun.queued_at.desc()).limit(max(1, min(int(limit), 200)))
    with Session(db_engine) as session:
        run_ids = [run.id for run in session.exec(statement).all()]
    result: list[LocalJobRun] = []
    for run_id in run_ids:
        run = get_local_job_run(run_id, db_engine=db_engine)
        if run is not None:
            result.append(run)
    return result


def find_active_local_job_run(
    *job_types: str,
    resource_key: str | None = None,
    db_engine: Engine = engine,
) -> LocalJobRun | None:
    """Return the newest live run for one of the explicitly named job types."""

    normalized = tuple(dict.fromkeys(str(item or "").strip() for item in job_types if str(item or "").strip()))
    if not normalized:
        return None
    ensure_local_job_schema(db_engine)
    statement = (
        select(LocalJobRun)
        .where(LocalJobRun.job_type.in_(normalized))
        .where(LocalJobRun.status.in_(ACTIVE_LOCAL_JOB_STATUSES))
        .order_by(LocalJobRun.queued_at.desc())
    )
    if resource_key is not None:
        statement = statement.where(LocalJobRun.resource_key == str(resource_key))
    with Session(db_engine) as session:
        run_ids = [run.id for run in session.exec(statement).all()]
    for run_id in run_ids:
        run = get_local_job_run(run_id, db_engine=db_engine)
        if run is not None and run.status in ACTIVE_LOCAL_JOB_STATUSES:
            return run
    return None
