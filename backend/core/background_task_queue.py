from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import threading
import time
import traceback
import uuid
from typing import Any, Callable


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
        **kwargs: Any,
    ) -> str:
        snapshot = BackgroundTaskSnapshot(
            id=uuid.uuid4().hex,
            name=name,
            status="pending",
            queued_at=time.time(),
            metadata=dict(metadata or {}),
        )
        task = _QueuedBackgroundTask(snapshot=snapshot, func=func, args=args, kwargs=kwargs)
        with self._lock:
            self._pending.append(task)
            self._ensure_worker_locked()
        return snapshot.id

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
                task.func(*task.args, **task.kwargs)
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
    def _serialize_snapshot(snapshot: BackgroundTaskSnapshot | None) -> dict[str, Any] | None:
        if snapshot is None:
            return None
        return {
            "id": snapshot.id,
            "name": snapshot.name,
            "status": snapshot.status,
            "queued_at": snapshot.queued_at,
            "started_at": snapshot.started_at,
            "finished_at": snapshot.finished_at,
            "error_message": snapshot.error_message,
            "metadata": dict(snapshot.metadata or {}),
        }


background_task_queue = BackgroundTaskQueue()
