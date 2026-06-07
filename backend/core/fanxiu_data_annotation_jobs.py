from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from backend.core.fanxiu_data_annotation_state import normalize_data_annotation_manual_job


@dataclass(frozen=True)
class DataAnnotationManualJobDefinition:
    task_type: str
    label: str
    handler: Callable[[Any, dict[str, Any], dict[str, Any], threading.Event], Any]
    scheduler_supported: bool = False
    interruptible: bool = True
    normalize_payload: Callable[[dict[str, Any]], dict[str, Any]] | None = None


_DATA_ANNOTATION_MANUAL_JOB_REGISTRY: dict[str, DataAnnotationManualJobDefinition] = {}
_DATA_ANNOTATION_JOB_GROUP_ORDER = {"guard": 10, "manual_job": 50, "job": 100}


def register_fanxiu_data_annotation_manual_job(
    task_type: str,
    label: str,
    *,
    scheduler_supported: bool = False,
    interruptible: bool = True,
    normalize_payload: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
):
    """Register a backend-defined data-annotation job consumed by the resident behavior tree."""
    task_type = str(task_type or "").strip()
    if not task_type:
        raise ValueError("manual job task_type is required")

    def decorator(handler: Callable[[Any, dict[str, Any], dict[str, Any], threading.Event], Any]):
        _DATA_ANNOTATION_MANUAL_JOB_REGISTRY[task_type] = DataAnnotationManualJobDefinition(
            task_type=task_type,
            label=str(label or task_type),
            handler=handler,
            scheduler_supported=bool(scheduler_supported),
            interruptible=bool(interruptible),
            normalize_payload=normalize_payload,
        )
        return handler

    return decorator


def get_fanxiu_data_annotation_manual_job_definition(task_type: str) -> DataAnnotationManualJobDefinition | None:
    return _DATA_ANNOTATION_MANUAL_JOB_REGISTRY.get(str(task_type or "").strip())


def read_data_annotation_manual_jobs(raw: Any) -> list[dict[str, Any]]:
    source = raw if isinstance(raw, list) else []
    return [
        job
        for item in source
        if (job := normalize_data_annotation_manual_job(item))
        and job.get("status") in {"pending", "running", "queued"}
    ][-100:]


def data_annotation_manual_jobs_state(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return jobs[-100:]


def requeue_running_data_annotation_manual_jobs(
    jobs: list[dict[str, Any]],
    *,
    now: float | None = None,
) -> tuple[list[dict[str, Any]], int]:
    current_time = time.time() if now is None else now
    updated: list[dict[str, Any]] = []
    changed_count = 0
    for job in jobs:
        if str(job.get("status") or "") != "running":
            updated.append(job)
            continue
        changed_count += 1
        # Most manual jobs can perform non-idempotent actions, so replaying a
        # claimed job after backend reload is riskier than requiring a fresh
        # submission. Mail cleanup is different: it scans from the current game
        # list and only deletes after UI confirmation, so restarting from the
        # top is the safer way to survive dev/backend reloads.
        if str(job.get("task_type") or "") == "mail_claim_check":
            updated.append({
                **job,
                "status": "queued",
                "updated_at": current_time,
                "last_requeue_reason": "backend_reload",
            })
    return updated, changed_count


def create_data_annotation_manual_job(
    task_type: str,
    payload: dict[str, Any] | None = None,
    *,
    label: str = "",
    interruptible: bool | None = None,
    definition: DataAnnotationManualJobDefinition | None = None,
    task_label: Callable[[str, dict[str, Any]], str] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    current_time = time.time() if now is None else now
    task_type = str(task_type or "detect_scene").strip() or "detect_scene"
    normalized_payload = dict(payload or {})
    if definition is not None and definition.normalize_payload is not None:
        normalized_payload = definition.normalize_payload(normalized_payload)
    resolved_label = label
    if not resolved_label and task_label is not None:
        resolved_label = task_label(task_type, normalized_payload)
    return {
        "id": f"manual-{int(current_time * 1000)}-{uuid.uuid4().hex[:8]}",
        "task_type": task_type,
        "label": resolved_label or task_type,
        "group": "manual_job",
        "status": "pending",
        "interruptible": bool(interruptible if interruptible is not None else (definition.interruptible if definition is not None else True)),
        "payload": normalized_payload,
        "created_at": current_time,
        "updated_at": current_time,
    }


def pop_next_data_annotation_manual_job(jobs: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    runnable = [job for job in jobs if str(job.get("status") or "") in {"pending", "queued"}]
    if not runnable:
        return None, jobs
    runnable.sort(
        key=lambda item: (
            _DATA_ANNOTATION_JOB_GROUP_ORDER.get(str(item.get("group") or "manual_job"), 1000),
            float(item.get("created_at") or 0),
        )
    )
    selected_id = str(runnable[0].get("id") or "")
    updated: list[dict[str, Any]] = []
    selected: dict[str, Any] | None = None
    current_time = time.time()
    for job in jobs:
        if str(job.get("id") or "") == selected_id:
            job = {**job, "status": "running", "updated_at": current_time}
            selected = job
            continue
        updated.append(job)
    return selected, updated
