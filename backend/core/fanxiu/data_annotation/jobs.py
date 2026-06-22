from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable

from pyxllib.prog import create_job_record, job_queue_state, pop_next_job, read_job_queue, requeue_running_jobs

from backend.core.fanxiu.data_annotation.state import normalize_data_annotation_manual_job


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


def list_fanxiu_data_annotation_manual_job_definitions() -> list[DataAnnotationManualJobDefinition]:
    return [
        _DATA_ANNOTATION_MANUAL_JOB_REGISTRY[key]
        for key in sorted(_DATA_ANNOTATION_MANUAL_JOB_REGISTRY)
    ]


def parse_data_annotation_scene_id(value: Any, *, default: int = 49) -> int:
    text = str(value or "").strip()
    if text.startswith("#"):
        text = text[1:].strip()
    if not text:
        return int(default)
    return int(text)


def normalize_data_annotation_go_scene_payload(payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(payload or {})
    target_scene_id = parse_data_annotation_scene_id(payload.get("target_scene_id") or payload.get("target") or 49)
    return {**payload, "target_scene_id": target_scene_id}


def normalize_data_annotation_debug_eval_payload(payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(payload or {})
    code = str(payload.get("code") or payload.get("source") or "").strip()
    if not code:
        raise ValueError("debug_eval 需要 payload.code")
    mode = str(payload.get("mode") or "readonly").strip().lower()
    if mode not in {"readonly", "act"}:
        raise ValueError("debug_eval mode 只支持 readonly/act")
    return {
        **payload,
        "code": code,
        "mode": mode,
        "call_task": bool(payload.get("call_task", True)),
        "max_output_chars": max(200, min(20000, int(payload.get("max_output_chars") or 4000))),
        "timeout_seconds": max(30, min(3600, int(payload.get("timeout_seconds") or payload.get("max_runtime_seconds") or 120))),
    }


def read_data_annotation_manual_jobs(raw: Any) -> list[dict[str, Any]]:
    return read_job_queue(raw, normalizer=normalize_data_annotation_manual_job)


def data_annotation_manual_jobs_state(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return job_queue_state(jobs)


def requeue_running_data_annotation_manual_jobs(
    jobs: list[dict[str, Any]],
    *,
    now: float | None = None,
) -> tuple[list[dict[str, Any]], int]:
    def should_requeue(job: dict[str, Any]) -> bool:
        # Current non-mail manual jobs can perform non-idempotent actions, so
        # do not replay them after a reload. Legacy running records may lack a
        # group marker; keep requeueing those so older local state can recover.
        return str(job.get("task_type") or "") in {"mail_cleanup", "mail_claim_check", "detect_scene", "manual_tick"}

    return requeue_running_jobs(jobs, keep_running_job=should_requeue, now=now)


def create_data_annotation_manual_job(
    task_type: str,
    payload: dict[str, Any] | None = None,
    *,
    label: str = "",
    group: str = "manual_job",
    interruptible: bool | None = None,
    definition: DataAnnotationManualJobDefinition | None = None,
    task_label: Callable[[str, dict[str, Any]], str] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    task_type = str(task_type or "detect_scene").strip() or "detect_scene"
    normalized_payload = dict(payload or {})
    if definition is not None and definition.normalize_payload is not None:
        normalized_payload = definition.normalize_payload(normalized_payload)
    resolved_label = label
    if not resolved_label and task_label is not None:
        resolved_label = task_label(task_type, normalized_payload)
    return create_job_record(
        task_type,
        normalized_payload,
        label=resolved_label or task_type,
        group=group or "manual_job",
        status="pending",
        interruptible=bool(interruptible if interruptible is not None else (definition.interruptible if definition is not None else True)),
        id_prefix="manual",
        now=now,
    )


def pop_next_data_annotation_manual_job(jobs: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    return pop_next_job(jobs, group_order=_DATA_ANNOTATION_JOB_GROUP_ORDER)

