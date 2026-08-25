from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from backend.core.fanxiu.data_annotation.state import write_data_annotation_json
from backend.core.fanxiu.behavior_tree.runtime import (
    fanxiu_data_annotation_scheduler_state_path,
)


_ENVIRONMENT_MISMATCH_PATTERN = re.compile(
    r"full_frame_similar_identity_mismatch[^#]*#(?P<scene_id>\d+)",
    re.IGNORECASE,
)


def _incident_environment_signature(incident: Mapping[str, Any]) -> dict[str, Any] | None:
    """Extract a conservative shared-environment signature from an incident.

    Only the unknown-recovery classification that explicitly says the live
    frame is highly similar to an existing reference while its identity rules
    disagree is eligible.  Ordinary task failures, known scenes, and repeated
    failures from one task must never become a global Scheduler blocker.
    """

    if str(incident.get("kind") or "") != "attempt_failed":
        return None
    schedule = incident.get("schedule") if isinstance(incident.get("schedule"), Mapping) else {}
    evidence = incident.get("evidence") if isinstance(incident.get("evidence"), Mapping) else {}
    raw_incident = evidence.get("incident") if isinstance(evidence.get("incident"), Mapping) else {}
    reason = str(
        raw_incident.get("reason")
        or schedule.get("reason")
        or ""
    )
    match = _ENVIRONMENT_MISMATCH_PATTERN.search(reason)
    if match is None:
        return None
    return {
        "kind": "full_frame_similar_identity_mismatch",
        "scene_id": int(match.group("scene_id")),
    }


def detect_scheduler_environment_circuit(
    incidents: list[Mapping[str, Any]],
    *,
    now: datetime | None = None,
    minimum_distinct_tasks: int = 2,
    cluster_window_seconds: float = 10 * 60,
) -> dict[str, Any] | None:
    """Find today's newest cross-task stable-environment failure cluster.

    The result is derived from immutable Scheduler incidents and is not a
    second persisted dispatch switch.  Callers must still re-observe the live
    frame before withholding a due Cell; this function alone is historical
    evidence and never authorizes a click or a task schedule mutation.
    """

    current = now or datetime.now()
    rows: list[dict[str, Any]] = []
    for incident in incidents:
        if not isinstance(incident, Mapping):
            continue
        signature = _incident_environment_signature(incident)
        if signature is None:
            continue
        try:
            occurred_at = datetime.strptime(
                str(incident.get("occurred_at") or ""),
                "%Y-%m-%d %H:%M:%S",
            )
        except ValueError:
            continue
        if occurred_at.date() != current.date():
            continue
        task = incident.get("task") if isinstance(incident.get("task"), Mapping) else {}
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            continue
        rows.append({
            **signature,
            "occurred_at": occurred_at,
            "task_id": task_id,
            "incident_id": str(incident.get("id") or ""),
        })

    required_tasks = max(2, int(minimum_distinct_tasks or 2))
    max_span = max(1.0, float(cluster_window_seconds or 1.0))
    candidates: list[dict[str, Any]] = []
    signatures = {
        (str(row["kind"]), int(row["scene_id"]))
        for row in rows
    }
    for kind, scene_id in signatures:
        grouped = sorted(
            (
                row
                for row in rows
                if str(row["kind"]) == kind and int(row["scene_id"]) == scene_id
            ),
            key=lambda row: row["occurred_at"],
        )
        for start_index, first in enumerate(grouped):
            cluster = [first]
            for row in grouped[start_index + 1 :]:
                if (row["occurred_at"] - first["occurred_at"]).total_seconds() > max_span:
                    break
                cluster.append(row)
            task_ids = sorted({str(row["task_id"]) for row in cluster})
            if len(task_ids) < required_tasks:
                continue
            last = cluster[-1]
            candidates.append({
                "kind": "repeated_environment_failure",
                "signature_kind": kind,
                "scene_id": scene_id,
                "first_occurred_at": first["occurred_at"].strftime("%Y-%m-%d %H:%M:%S"),
                "last_occurred_at": last["occurred_at"].strftime("%Y-%m-%d %H:%M:%S"),
                "task_ids": task_ids,
                "incident_ids": [
                    str(row["incident_id"])
                    for row in cluster
                    if str(row["incident_id"])
                ],
                "failure_count": len(cluster),
                "distinct_task_count": len(task_ids),
            })
    if not candidates:
        return None
    return max(candidates, key=lambda item: str(item["last_occurred_at"]))


def scheduler_incidents_dir(
    scheduler_state_path: Path | None = None,
) -> Path:
    state_path = scheduler_state_path or fanxiu_data_annotation_scheduler_state_path()
    return state_path.parent / "scheduler-incidents"


def _safe_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return json.loads(json.dumps(dict(value), ensure_ascii=False, default=str))


def _task_snapshot(task: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: task.get(key)
        for key in (
            "id",
            "task_type",
            "label",
            "trigger_description",
            "dispatch_level",
            "dispatch_order",
            "next_time",
            "last_run_at",
            "last_result",
            "last_message",
            "started_at",
            "finished_at",
            "attempt_id",
            "attempt_kernel_generation",
        )
    }


def _cycle_date(original_next_time: Any, occurred_at: datetime) -> str:
    try:
        return datetime.strptime(
            str(original_next_time or ""),
            "%Y-%m-%d %H:%M:%S",
        ).date().isoformat()
    except ValueError:
        return occurred_at.date().isoformat()


def _attempt_logs(
    logs: list[Any],
    *,
    started_at: Any,
    occurred_at: datetime,
) -> list[dict[str, Any]]:
    """Keep only log rows that can belong to this attempt's time window."""

    try:
        started = datetime.strptime(str(started_at or ""), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return [
            _safe_mapping(item)
            for item in logs[-100:]
            if isinstance(item, Mapping)
        ]
    selected: list[dict[str, Any]] = []
    crosses_midnight = started.date() != occurred_at.date()
    for item in logs:
        if not isinstance(item, Mapping):
            continue
        try:
            logged_time = datetime.strptime(
                str(item.get("time") or ""), "%H:%M:%S"
            ).time()
        except ValueError:
            continue
        if crosses_midnight:
            in_window = (
                logged_time >= started.time()
                or logged_time <= occurred_at.time()
            )
        else:
            in_window = started.time() <= logged_time <= occurred_at.time()
        if in_window:
            selected.append(_safe_mapping(item))
    return selected[-100:]


def record_scheduler_incident(
    *,
    task: Mapping[str, Any],
    original_next_time: Any,
    next_time: Any,
    incident: Mapping[str, Any],
    attempt_id: str,
    entry_id: str,
    occurred_at: datetime,
    runtime_status: Mapping[str, Any] | None = None,
    scheduler_state_path: Path | None = None,
) -> dict[str, Any]:
    """Persist one immutable Scheduler incident for later AI diagnosis."""

    task_id = str(task.get("id") or "").strip()
    kind = str(incident.get("kind") or "scheduler_incident").strip()
    dedupe_source = "|".join(
        (
            task_id,
            kind,
            str(original_next_time or ""),
            str(attempt_id or ""),
        )
    )
    digest = hashlib.sha256(dedupe_source.encode("utf-8")).hexdigest()[:16]
    incident_id = f"scheduler-{kind}-{digest}"
    root = scheduler_incidents_dir(scheduler_state_path)
    path = root / f"{incident_id}.json"
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None
        if isinstance(existing, dict):
            return existing

    runtime = _safe_mapping(runtime_status)
    logs = runtime.get("logs") if isinstance(runtime.get("logs"), list) else []
    payload = {
        "version": 1,
        "id": incident_id,
        "kind": kind,
        "status": "open",
        "review_status": "pending",
        "analysis_status": "pending",
        "occurred_at": occurred_at.strftime("%Y-%m-%d %H:%M:%S"),
        "cycle_kind": str(incident.get("cycle_kind") or "daily"),
        "cycle_date": str(
            incident.get("cycle_date")
            or _cycle_date(original_next_time, occurred_at)
        ),
        "task": _task_snapshot(task),
        "schedule": {
            "original_next_time": original_next_time,
            "next_time": next_time,
            "window": incident.get("window"),
            "reason": incident.get("reason"),
        },
        "attempt": {
            "id": str(attempt_id or ""),
            "entry_id": str(entry_id or ""),
            "kernel_generation": task.get("attempt_kernel_generation"),
            "started_at": task.get("started_at"),
            "finished_at": occurred_at.strftime("%Y-%m-%d %H:%M:%S"),
            "last_result": task.get("last_result"),
            "last_message": task.get("last_message"),
        },
        "evidence": {
            "incident": _safe_mapping(incident),
            "runtime": {
                key: runtime.get(key)
                for key in (
                    "status",
                    "phase",
                    "message",
                    "error",
                    "current_scene",
                    "started_at",
                    "finished_at",
                )
            },
            "recent_logs": _attempt_logs(
                logs,
                started_at=task.get("started_at"),
                occurred_at=occurred_at,
            ),
        },
        "ai_handoff": {
            "goal": "分析该日窗口为什么未按时完成，并提出可验证的调度或业务优化",
            "questions": [
                "原定时间是否成功派发 Cell？",
                "失败属于调度、Kernel、Runtime、识别、输入、等待还是业务判定？",
                "怎样避免下一日重复发生，同时不跨日补跑？",
            ],
        },
        "analysis": None,
    }
    write_data_annotation_json(path, payload)
    return payload


def list_scheduler_incidents(
    *,
    scheduler_state_path: Path | None = None,
    analysis_status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    root = scheduler_incidents_dir(scheduler_state_path)
    if not root.is_dir():
        return []
    result: list[dict[str, Any]] = []
    for path in sorted(
        root.glob("scheduler-*.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    ):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if analysis_status and str(payload.get("analysis_status") or "") != analysis_status:
            continue
        result.append(payload)
        if len(result) >= max(1, int(limit)):
            break
    return result
