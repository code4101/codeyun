from __future__ import annotations

import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Callable
from uuid import uuid4

from fastapi import HTTPException

LongTaskStatus = str


@dataclass(slots=True)
class LongTaskRecord:
    task_id: str
    kind: str
    status: LongTaskStatus
    stage: str
    message: str
    created_at: float
    started_at: float | None = None
    updated_at: float = 0.0
    finished_at: float | None = None
    progress_current: int | None = None
    progress_total: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: str | None = None
    error_status_code: int | None = None
    traceback_text: str | None = None


class LongTaskContext:
    def __init__(self, manager: "LongTaskManager", task_id: str):
        self._manager = manager
        self.task_id = task_id

    def heartbeat(
        self,
        *,
        stage: str | None = None,
        message: str | None = None,
        progress_current: int | None = None,
        progress_total: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._manager.update(
            self.task_id,
            stage=stage,
            message=message,
            progress_current=progress_current,
            progress_total=progress_total,
            metadata=metadata,
        )


def make_long_task_progress_heartbeat(context: LongTaskContext) -> Callable[[dict[str, Any]], None]:
    def heartbeat(progress: dict[str, Any]) -> None:
        context.heartbeat(
            stage=str(progress.get("stage") or "running"),
            message=str(progress.get("message") or "运行中"),
            progress_current=progress.get("progress_current"),
            progress_total=progress.get("progress_total"),
        )

    return heartbeat


class LongTaskNotFoundError(KeyError):
    pass


class LongTaskManager:
    def __init__(
        self,
        kind: str,
        *,
        max_workers: int = 2,
        max_records: int = 64,
        record_ttl_seconds: float = 60 * 60,
    ):
        self.kind = kind
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=f"{kind}-task")
        self._max_records = max(1, int(max_records))
        self._record_ttl_seconds = max(60.0, float(record_ttl_seconds))
        self._records: dict[str, LongTaskRecord] = {}
        self._lock = RLock()

    def start(
        self,
        run: Callable[[LongTaskContext], Any],
        *,
        stage: str = "queued",
        message: str = "等待执行",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        task_id = uuid4().hex
        record = LongTaskRecord(
            task_id=task_id,
            kind=self.kind,
            status="queued",
            stage=stage,
            message=message,
            created_at=now,
            updated_at=now,
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self._records[task_id] = record
            self._prune_locked(now)

        self._executor.submit(self._run_task, task_id, run)
        return self.serialize(record, include_result=False)

    def get(self, task_id: str) -> LongTaskRecord:
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            record = self._records.get(task_id)
            if record is None:
                raise LongTaskNotFoundError(task_id)
            return self._copy_record(record)

    def update(
        self,
        task_id: str,
        *,
        stage: str | None = None,
        message: str | None = None,
        progress_current: int | None = None,
        progress_total: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = time.time()
        with self._lock:
            record = self._records.get(task_id)
            if record is None or record.status not in {"queued", "running"}:
                return
            if stage is not None:
                record.stage = stage
            if message is not None:
                record.message = message
            if progress_current is not None:
                record.progress_current = int(progress_current)
            if progress_total is not None:
                record.progress_total = int(progress_total)
            if metadata:
                record.metadata.update(metadata)
            record.updated_at = now

    def serialize(self, record: LongTaskRecord, *, include_result: bool = True) -> dict[str, Any]:
        running = record.status in {"queued", "running"}
        payload = {
            "task_id": record.task_id,
            "kind": record.kind,
            "status": record.status,
            "running": running,
            "stage": record.stage,
            "message": record.message,
            "created_at": record.created_at,
            "started_at": record.started_at,
            "updated_at": record.updated_at,
            "finished_at": record.finished_at,
            "progress_current": record.progress_current,
            "progress_total": record.progress_total,
            "metadata": dict(record.metadata),
            "error": record.error,
            "error_status_code": record.error_status_code,
            "elapsed_ms": int(round(((record.finished_at or time.time()) - record.created_at) * 1000)),
        }
        if include_result and record.status == "completed":
            payload["result"] = record.result
        return payload

    def serialize_task(self, task_id: str, *, include_result: bool = True) -> dict[str, Any]:
        return self.serialize(self.get(task_id), include_result=include_result)

    def _run_task(self, task_id: str, run: Callable[[LongTaskContext], Any]) -> None:
        self._mark_running(task_id)
        context = LongTaskContext(self, task_id)
        try:
            result = run(context)
        except HTTPException as exc:
            self._mark_failed(task_id, str(exc.detail), status_code=exc.status_code)
        except Exception as exc:
            self._mark_failed(task_id, str(exc), traceback_text=traceback.format_exc())
        else:
            self._mark_completed(task_id, result)

    def _mark_running(self, task_id: str) -> None:
        now = time.time()
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return
            record.status = "running"
            record.stage = "running"
            record.message = "运行中"
            record.started_at = now
            record.updated_at = now

    def _mark_completed(self, task_id: str, result: Any) -> None:
        now = time.time()
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return
            record.status = "completed"
            record.stage = "completed"
            record.message = "完成"
            record.result = result
            record.updated_at = now
            record.finished_at = now

    def _mark_failed(
        self,
        task_id: str,
        error: str,
        *,
        status_code: int | None = None,
        traceback_text: str | None = None,
    ) -> None:
        now = time.time()
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return
            record.status = "failed"
            record.stage = "failed"
            record.message = error or "执行失败"
            record.error = error or "执行失败"
            record.error_status_code = status_code
            record.traceback_text = traceback_text
            record.updated_at = now
            record.finished_at = now

    def _copy_record(self, record: LongTaskRecord) -> LongTaskRecord:
        return LongTaskRecord(
            task_id=record.task_id,
            kind=record.kind,
            status=record.status,
            stage=record.stage,
            message=record.message,
            created_at=record.created_at,
            started_at=record.started_at,
            updated_at=record.updated_at,
            finished_at=record.finished_at,
            progress_current=record.progress_current,
            progress_total=record.progress_total,
            metadata=dict(record.metadata),
            result=record.result,
            error=record.error,
            error_status_code=record.error_status_code,
            traceback_text=record.traceback_text,
        )

    def _prune_locked(self, now: float) -> None:
        expired_ids = [
            task_id
            for task_id, record in self._records.items()
            if record.status not in {"queued", "running"} and now - record.updated_at > self._record_ttl_seconds
        ]
        for task_id in expired_ids:
            self._records.pop(task_id, None)

        if len(self._records) <= self._max_records:
            return
        removable = sorted(
            (
                record
                for record in self._records.values()
                if record.status not in {"queued", "running"}
            ),
            key=lambda item: item.updated_at,
        )
        for record in removable:
            if len(self._records) <= self._max_records:
                break
            self._records.pop(record.task_id, None)
