from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field, is_dataclass
import datetime as dt
import threading
import time
import traceback
import uuid
from typing import Any, Callable

DEFAULT_JOB_RESOURCE_LOCK = "resource:job-default"


@dataclass
class BackgroundTaskSnapshot:
    id: str
    name: str
    status: str
    queued_at: float
    started_at: float | None = None
    finished_at: float | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    result: Any = None


@dataclass
class _QueuedBackgroundTask:
    snapshot: BackgroundTaskSnapshot
    func: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


class BackgroundTaskQueue:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: deque[_QueuedBackgroundTask] = deque()
        self._running: BackgroundTaskSnapshot | None = None
        self._recent: deque[BackgroundTaskSnapshot] = deque(maxlen=20)
        self._worker: threading.Thread | None = None

    def enqueue(
        self,
        name: str,
        func: Callable[..., Any],
        *args: Any,
        metadata: dict[str, Any] | None = None,
        resource_lock: str | None = None,
        **kwargs: Any,
    ) -> str:
        task_metadata = self._build_metadata(metadata, resource_lock)
        snapshot = BackgroundTaskSnapshot(
            id=uuid.uuid4().hex,
            name=name,
            status="pending",
            queued_at=time.time(),
            metadata=task_metadata,
        )
        task = _QueuedBackgroundTask(snapshot=snapshot, func=func, args=args, kwargs=kwargs)
        with self._lock:
            self._pending.append(task)
            self._ensure_worker_locked()
        return snapshot.id

    def enqueue_once(
        self,
        name: str,
        func: Callable[..., Any],
        *args: Any,
        metadata: dict[str, Any] | None = None,
        resource_lock: str | None = None,
        **kwargs: Any,
    ) -> tuple[str, bool]:
        task_metadata = self._build_metadata(metadata, resource_lock)
        snapshot = BackgroundTaskSnapshot(
            id=uuid.uuid4().hex,
            name=name,
            status="pending",
            queued_at=time.time(),
            metadata=task_metadata,
        )
        task = _QueuedBackgroundTask(snapshot=snapshot, func=func, args=args, kwargs=kwargs)
        with self._lock:
            existing = self._find_active_by_name_locked(name)
            if existing is not None:
                return existing.id, False
            self._pending.append(task)
            self._ensure_worker_locked()
        return snapshot.id, True

    def active_snapshot_by_name(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            snapshot = self._find_active_by_name_locked(name)
            return self._serialize_snapshot(snapshot)

    def is_idle(self) -> bool:
        with self._lock:
            return self._running is None and not self._pending

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "is_idle": self._running is None and not self._pending,
                "running": self._serialize_snapshot(self._running),
                "pending": [self._serialize_snapshot(task.snapshot) for task in self._pending],
                "recent": [self._serialize_snapshot(item) for item in self._recent],
            }

    def delete(self, task_id: str) -> str:
        normalized_id = str(task_id or "").strip()
        if not normalized_id:
            return "missing"

        with self._lock:
            if self._running is not None and self._running.id == normalized_id:
                return "running"

            for index, task in enumerate(self._pending):
                if task.snapshot.id == normalized_id:
                    del self._pending[index]
                    return "deleted"

            for index, item in enumerate(self._recent):
                if item.id == normalized_id:
                    del self._recent[index]
                    return "deleted"

        return "missing"

    def delete_pending_by_name(self, name: str) -> int:
        normalized_name = str(name or "").strip()
        if not normalized_name:
            return 0

        deleted_count = 0
        with self._lock:
            kept: deque[_QueuedBackgroundTask] = deque()
            while self._pending:
                task = self._pending.popleft()
                if task.snapshot.name == normalized_name:
                    deleted_count += 1
                    continue
                kept.append(task)
            self._pending = kept
        return deleted_count

    def _find_active_by_name_locked(self, name: str) -> BackgroundTaskSnapshot | None:
        normalized_name = str(name or "").strip()
        if not normalized_name:
            return None
        if self._running is not None and self._running.name == normalized_name:
            return self._running
        for task in self._pending:
            if task.snapshot.name == normalized_name:
                return task.snapshot
        return None

    def reset_for_tests(self) -> None:
        with self._lock:
            self._pending.clear()
            self._running = None
            self._recent.clear()
            self._worker = None

    def _ensure_worker_locked(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._run_loop, name="codeyun-background-task-queue", daemon=True)
        self._worker.start()

    def _run_loop(self) -> None:
        while True:
            with self._lock:
                if not self._pending:
                    self._worker = None
                    return
                task = self._pending.popleft()
                task.snapshot.status = "running"
                task.snapshot.started_at = time.time()
                self._running = task.snapshot
            try:
                result = task.func(*task.args, **task.kwargs)
                task.snapshot.result = self._serialize_result(result)
                task.snapshot.status = "completed"
            except Exception as exc:  # pragma: no cover - queue must never kill the app.
                task.snapshot.status = "failed"
                task.snapshot.error_message = f"{exc}\n{traceback.format_exc(limit=5)}"
            finally:
                task.snapshot.finished_at = time.time()
                with self._lock:
                    self._recent.appendleft(task.snapshot)
                    if self._running and self._running.id == task.snapshot.id:
                        self._running = None

    @staticmethod
    def _build_metadata(metadata: dict[str, Any] | None, resource_lock: str | None) -> dict[str, Any]:
        result = dict(metadata or {})
        normalized_lock = str(resource_lock or result.get("resource_lock") or DEFAULT_JOB_RESOURCE_LOCK).strip()
        result.setdefault("resource_lock", normalized_lock)
        return result

    @staticmethod
    def _serialize_snapshot(snapshot: BackgroundTaskSnapshot | None) -> dict[str, Any] | None:
        if snapshot is None:
            return None
        metadata = dict(snapshot.metadata or {})
        return {
            "id": snapshot.id,
            "name": snapshot.name,
            "status": snapshot.status,
            "queued_at": snapshot.queued_at,
            "started_at": snapshot.started_at,
            "finished_at": snapshot.finished_at,
            "error_message": snapshot.error_message,
            "metadata": metadata,
            "resource_lock": metadata.get("resource_lock"),
            "result": snapshot.result,
        }

    @classmethod
    def _serialize_result(cls, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dt.datetime):
            return value.replace(microsecond=0).isoformat()
        if is_dataclass(value):
            return cls._serialize_result(asdict(value))
        if isinstance(value, dict):
            return {str(key): cls._serialize_result(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [cls._serialize_result(item) for item in value]
        return str(value)


background_task_queue = BackgroundTaskQueue()
