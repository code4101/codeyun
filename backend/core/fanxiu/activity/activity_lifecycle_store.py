from __future__ import annotations

"""Durable business facts for one-activity/one-Job lifecycle completion."""

from datetime import datetime
from typing import Any, Mapping

from sqlmodel import Session, select

from backend.models import FanxiuPacketBusinessRecord


ACTIVITY_LIFECYCLE_COMPLETION_DOMAIN = "activity_job_lifecycle_completion"
ACTIVITY_LIFECYCLE_COMPLETION_PROTOCOL = "activity_job_lifecycle_v1"
_ONE_SHOT_TRIGGERS = frozenset({"instance_activation", "authoritative_end_tail"})


def _completed_at(state: Mapping[str, Any]) -> datetime:
    text = str(state.get("completed_at") or "").strip()
    try:
        value = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("活动生命周期完成状态缺少有效 completed_at") from exc
    if value.tzinfo is None:
        raise ValueError("活动生命周期 completed_at 必须带时区")
    return value


def _normalized_state(state: Mapping[str, Any]) -> dict[str, Any]:
    task_id = str(state.get("task_id") or "").strip()
    instance_key = str(state.get("instance_key") or "").strip()
    try:
        activity_id = int(state.get("activity_id"))
    except (TypeError, ValueError) as exc:
        raise ValueError("活动生命周期完成状态缺少 activity_id") from exc
    if not task_id or not instance_key or activity_id <= 0:
        raise ValueError("活动生命周期完成状态身份不完整")
    triggers = {
        str(value or "").strip()
        for value in state.get("completed_triggers") or []
        if str(value or "").strip()
    }
    if not triggers <= _ONE_SHOT_TRIGGERS:
        raise ValueError("活动生命周期完成状态含未知 one-shot trigger")
    resource = state.get("resource_count")
    if resource is not None:
        if isinstance(resource, bool):
            raise ValueError("活动生命周期 resource_count 无效")
        resource = int(resource)
        if resource < 0:
            raise ValueError("活动生命周期 resource_count 不能为负数")
    completed = _completed_at(state)
    return {
        "version": 1,
        "task_id": task_id,
        "activity_id": activity_id,
        "instance_key": instance_key,
        "completed_triggers": sorted(triggers),
        "resource_count": resource,
        "completed_at": completed.isoformat(timespec="seconds"),
    }


def read_activity_lifecycle_completion(
    session: Session,
    *,
    task_id: str,
) -> dict[str, Any] | None:
    identity = str(task_id or "").strip()
    if not identity:
        raise ValueError("读取活动生命周期状态缺少 task_id")
    row = session.exec(
        select(FanxiuPacketBusinessRecord).where(
            FanxiuPacketBusinessRecord.domain
            == ACTIVITY_LIFECYCLE_COMPLETION_DOMAIN,
            FanxiuPacketBusinessRecord.record_key == identity,
        )
    ).first()
    if row is None:
        return None
    if row.protocol != ACTIVITY_LIFECYCLE_COMPLETION_PROTOCOL:
        raise ValueError(f"活动生命周期 completion protocol 不兼容：{row.protocol}")
    return _normalized_state(dict(row.payload or {}))


def persist_activity_lifecycle_completion(
    session: Session,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    """Upsert a monotonic completion fact after a successful Job only."""

    normalized = _normalized_state(state)
    task_id = normalized["task_id"]
    row = session.exec(
        select(FanxiuPacketBusinessRecord).where(
            FanxiuPacketBusinessRecord.domain
            == ACTIVITY_LIFECYCLE_COMPLETION_DOMAIN,
            FanxiuPacketBusinessRecord.record_key == task_id,
        )
    ).first()
    if row is not None:
        if row.protocol != ACTIVITY_LIFECYCLE_COMPLETION_PROTOCOL:
            raise ValueError(
                f"活动生命周期 completion protocol 不兼容：{row.protocol}"
            )
        previous = _normalized_state(dict(row.payload or {}))
        if _completed_at(normalized) < _completed_at(previous):
            raise ValueError("活动生命周期 completion 状态发生时间倒退")
        if previous["instance_key"] == normalized["instance_key"]:
            if previous["activity_id"] != normalized["activity_id"]:
                raise ValueError("同一活动实例的 activity_id 发生变化")
            old_triggers = set(previous["completed_triggers"])
            new_triggers = set(normalized["completed_triggers"])
            if not old_triggers <= new_triggers:
                raise ValueError("同一活动实例的 one-shot 完成事实发生倒退")
        row.entity_id = normalized["instance_key"]
        row.captured_at = normalized["completed_at"]
        row.captured_date = normalized["completed_at"][:10]
        row.payload = normalized
        row.evidence = {
            "write_boundary": "after_successful_activity_job",
            "scheduler_trigger_fields": ["next_time"],
        }
        row.updated_at = datetime.now().timestamp()
    else:
        row = FanxiuPacketBusinessRecord(
            domain=ACTIVITY_LIFECYCLE_COMPLETION_DOMAIN,
            record_key=task_id,
            protocol=ACTIVITY_LIFECYCLE_COMPLETION_PROTOCOL,
            source_kind="activity_job_success_completion",
            entity_id=normalized["instance_key"],
            entity_name=task_id,
            captured_at=normalized["completed_at"],
            captured_date=normalized["completed_at"][:10],
            payload=normalized,
            evidence={
                "write_boundary": "after_successful_activity_job",
                "scheduler_trigger_fields": ["next_time"],
            },
        )
        session.add(row)
    session.commit()
    return normalized


__all__ = [
    "ACTIVITY_LIFECYCLE_COMPLETION_DOMAIN",
    "ACTIVITY_LIFECYCLE_COMPLETION_PROTOCOL",
    "persist_activity_lifecycle_completion",
    "read_activity_lifecycle_completion",
]
