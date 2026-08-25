from __future__ import annotations

import threading
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable

@dataclass(frozen=True)
class DataAnnotationTaskCellDefinition:
    task_type: str
    label: str
    handler: Callable[[Any, dict[str, Any], dict[str, Any], threading.Event], Any]
    scheduler_supported: bool = False
    interruptible: bool = True
    admission: Callable[[Any, dict[str, Any]], dict[str, Any] | None] | None = None
    normalize_payload: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    standard_job: bool = False
    standard_job_id: str = ""
    standard_job_description: str = "手动"
    standard_job_payload: dict[str, Any] = field(default_factory=dict)


_DATA_ANNOTATION_TASK_CELL_REGISTRY: dict[str, DataAnnotationTaskCellDefinition] = {}
_DEPRECATED_DATA_ANNOTATION_JOB_TYPES = {
    "daily_yihuo",
}

MAIL_SELECTIVE_CLAIM_TASK_TYPE = "mail_selective_claim"
_LEGACY_DATA_ANNOTATION_JOB_TYPE_ALIASES = {
    "mail_cleanup": MAIL_SELECTIVE_CLAIM_TASK_TYPE,
    "mail_claim_check": MAIL_SELECTIVE_CLAIM_TASK_TYPE,
}


def canonical_fanxiu_data_annotation_task_type(task_type: str) -> str:
    """Translate legacy task names at the boundary; new code only sees canonical names."""
    normalized = str(task_type or "").strip()
    return _LEGACY_DATA_ANNOTATION_JOB_TYPE_ALIASES.get(normalized, normalized)


def is_deprecated_data_annotation_job_type(task_type: str) -> bool:
    return str(task_type or "").strip() in _DEPRECATED_DATA_ANNOTATION_JOB_TYPES


def register_fanxiu_data_annotation_task_cell(
    task_type: str,
    label: str,
    *,
    scheduler_supported: bool = False,
    interruptible: bool = True,
    admission: Callable[[Any, dict[str, Any]], dict[str, Any] | None] | None = None,
    normalize_payload: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    standard_job: bool = False,
    standard_job_id: str = "",
    standard_job_description: str = "手动",
    standard_job_payload: dict[str, Any] | None = None,
):
    """Register a task callable by ``run_task`` inside the Jupyter kernel."""
    task_type = str(task_type or "").strip()
    if not task_type:
        raise ValueError("task cell task_type is required")

    def decorator(handler: Callable[[Any, dict[str, Any], dict[str, Any], threading.Event], Any]):
        if is_deprecated_data_annotation_job_type(task_type):
            return handler
        _DATA_ANNOTATION_TASK_CELL_REGISTRY[task_type] = DataAnnotationTaskCellDefinition(
            task_type=task_type,
            label=str(label or task_type),
            handler=handler,
            scheduler_supported=bool(scheduler_supported),
            interruptible=bool(interruptible),
            admission=admission,
            normalize_payload=normalize_payload,
            standard_job=bool(standard_job),
            standard_job_id=str(standard_job_id or "").strip(),
            standard_job_description=str(standard_job_description or "手动").strip() or "手动",
            standard_job_payload=deepcopy(standard_job_payload or {}),
        )
        return handler

    return decorator


def get_fanxiu_data_annotation_task_cell_definition(task_type: str) -> DataAnnotationTaskCellDefinition | None:
    normalized = canonical_fanxiu_data_annotation_task_type(task_type)
    if is_deprecated_data_annotation_job_type(normalized):
        return None
    return _DATA_ANNOTATION_TASK_CELL_REGISTRY.get(normalized)


def list_fanxiu_data_annotation_task_cell_definitions() -> list[DataAnnotationTaskCellDefinition]:
    return [
        _DATA_ANNOTATION_TASK_CELL_REGISTRY[key]
        for key in sorted(_DATA_ANNOTATION_TASK_CELL_REGISTRY)
        if not is_deprecated_data_annotation_job_type(key)
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
    normalized = {**payload, "target_scene_id": target_scene_id}
    layer0_wait = payload.get("layer0_wait_seconds")
    if layer0_wait is None:
        layer0_wait = payload.get("wait_seconds")
    if layer0_wait is None:
        layer0_wait = payload.get("wait_time")
    if layer0_wait is not None:
        normalized["layer0_wait_seconds"] = max(0.0, float(layer0_wait))
    return normalized


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
        "timeout_seconds": max(30, min(21600, int(payload.get("timeout_seconds") or payload.get("max_runtime_seconds") or 120))),
    }



