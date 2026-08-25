from __future__ import annotations

"""User-authorized daily activity -> Scheduler Job bindings.

Runtime discovery supplies occurrence facts. This registry is the separate
execution authority: an activity can only change a Job ``next_time`` when a
binding is explicitly declared here and covered by contract tests.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from backend.core.fanxiu.activity.daily_activity_discovery import DEFAULT_TIMEZONE


XIANMENG_CHALLENGE_TASK_ID = "legacy-daily-xianmeng"
XIANMENG_CHALLENGE_LABEL = "仙盟_挑战"
XIANMENG_FORMAL_CHILD_BASE_IDS = frozenset({28100, 28200})
XIANMENG_FORMAL_BASE_IDS = frozenset({28000, *XIANMENG_FORMAL_CHILD_BASE_IDS})
XIANMENG_STAMINA_SWEEPS = ((21, 10), (21, 50))
XIANMENG_TRIPLE_DISABLE_AT = (21, 30)
XIANMENG_FINAL_SWEEP = (21, 50)


@dataclass(frozen=True)
class AuthorizedActivityJobBinding:
    binding_id: str
    child_base_ids: frozenset[int]
    activity_types: frozenset[int]
    task_id: str
    task_label: str
    day_relation: str = "starts_today"
    trigger_mode: str = "activity_start"


# Every entry is a user-approved execution relationship. Do not infer or append
# entries from Runtime names, page families, follow items, or similarity.
# Activity-list synchronization no longer owns any ranking child Job.  Xianmeng
# is discovered and checkpointed by the gameplay-ranking parent; this empty
# tuple is intentional and prevents a second next_time owner from reappearing.
AUTHORIZED_ACTIVITY_JOB_BINDINGS: tuple[AuthorizedActivityJobBinding, ...] = ()


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _target_date(plan: Mapping[str, Any]) -> date:
    text = str(plan.get("target_date") or "").strip()
    if not text:
        raise ValueError("活动触发计划缺少 target_date")
    return date.fromisoformat(text)


def _occurrence_binding(
    occurrence: Mapping[str, Any],
) -> AuthorizedActivityJobBinding | None:
    base_id = _as_int(occurrence.get("base_id"))
    raw = occurrence.get("raw")
    if base_id is None and isinstance(raw, Mapping):
        base_id = _as_int(raw.get("baseId"))
    activity_type = _as_int(occurrence.get("activity_type"))
    for binding in AUTHORIZED_ACTIVITY_JOB_BINDINGS:
        if base_id in binding.child_base_ids and activity_type in binding.activity_types:
            return binding
    return None


def _day_relation_matches(
    binding: AuthorizedActivityJobBinding,
    occurrence: Mapping[str, Any],
) -> bool:
    relation = str(occurrence.get("day_relation") or "")
    if binding.day_relation == "starts_or_final_today":
        return relation == "starts_today" or (
            relation == "continues_today"
            and occurrence.get("ends_today") is True
        )
    return relation == binding.day_relation


def build_authorized_daily_activity_job_schedule(
    plan: Mapping[str, Any],
    *,
    now: datetime | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    """Project one ready Runtime plan into the complete managed Job state."""

    if str(plan.get("status") or "") != "ready":
        raise ValueError("只有完整 ready 的 Runtime 日程才能配置活动 Job")
    target = _target_date(plan)
    timezone = ZoneInfo(timezone_name)
    current = now or datetime.now(timezone)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone)
    current = current.astimezone(timezone)
    desired: dict[str, str | None] = {
        binding.task_id: None for binding in AUTHORIZED_ACTIVITY_JOB_BINDINGS
    }
    decisions: list[dict[str, Any]] = []
    plan_occurrences = plan.get("occurrences")
    if isinstance(plan_occurrences, list):
        occurrences = plan_occurrences
    else:
        # Compatibility for older callers/tests that only carry sync deltas.
        occurrences = [
            operation.get("occurrence")
            for operation in plan.get("operations") or []
            if isinstance(operation, Mapping)
        ]
    for occurrence in occurrences:
        if not isinstance(occurrence, Mapping):
            continue
        binding = _occurrence_binding(occurrence)
        if binding is None:
            continue
        if (
            not bool(occurrence.get("identity_complete"))
            or str(occurrence.get("catalog_status") or "") != "known"
            or (_as_int(occurrence.get("schedule_id")) or 0) <= 0
            or not _day_relation_matches(binding, occurrence)
        ):
            continue
        start_text = str(occurrence.get("start_at") or "").strip()
        start_at = datetime.fromisoformat(start_text) if start_text else None
        if binding.trigger_mode == "activity_start":
            if start_at is None or start_at.tzinfo is None:
                continue
            local_start = start_at.astimezone(timezone)
            relation = str(occurrence.get("day_relation") or "")
            if relation == "starts_today":
                if local_start.date() != target:
                    continue
                trigger_at = local_start
            elif (
                binding.day_relation == "starts_or_final_today"
                and relation == "continues_today"
                and occurrence.get("ends_today") is True
            ):
                # The Runtime exposes Xianmeng qualifying/final as one
                # multi-day root occurrence.  Its final day is a fresh daily
                # challenge cycle, but keeps the root's original start_at.
                # Reuse only the authoritative formal-start clock (10:00)
                # on the target final date; do not authorize intermediate
                # continuation days.
                trigger_at = local_start.replace(
                    year=target.year,
                    month=target.month,
                    day=target.day,
                )
            else:
                continue
        elif binding.trigger_mode == "now_plus_five_minutes":
            end_text = str(occurrence.get("end_at") or "").strip()
            close_text = str(occurrence.get("close_panel_at") or "").strip()
            if not end_text or not close_text or current.date() != target:
                continue
            end_at = datetime.fromisoformat(end_text).astimezone(timezone)
            close_at = datetime.fromisoformat(close_text).astimezone(timezone)
            if not end_at <= current < close_at:
                continue
            trigger_at = (current + timedelta(minutes=5)).replace(microsecond=0)
        else:
            raise ValueError(f"未知活动 Job 触发模式：{binding.trigger_mode}")
        next_time = trigger_at.strftime("%Y-%m-%d %H:%M:%S")
        previous = desired[binding.task_id]
        if previous is not None and previous != next_time:
            raise ValueError(
                f"授权活动对 {binding.task_label} 产生冲突时间：{previous} / {next_time}"
            )
        desired[binding.task_id] = next_time
        raw = occurrence.get("raw")
        decisions.append(
            {
                "binding_id": binding.binding_id,
                "task_id": binding.task_id,
                "task_label": binding.task_label,
                "next_time": next_time,
                "activity_id": _as_int(occurrence.get("activity_id")),
                "base_id": _as_int(occurrence.get("base_id"))
                or (_as_int(raw.get("baseId")) if isinstance(raw, Mapping) else None),
                "schedule_id": _as_int(occurrence.get("schedule_id")),
                "start_at": start_at.isoformat(timespec="seconds") if start_at else "",
                "end_at": str(occurrence.get("end_at") or ""),
                "close_panel_at": str(occurrence.get("close_panel_at") or ""),
            }
        )
    return {
        "target_date": target.isoformat(),
        "desired_next_times": desired,
        "decisions": decisions,
    }


def next_xianmeng_challenge_tail_time(
    plan: Mapping[str, Any],
    *,
    now: datetime | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> str | None:
    """Return the next 21:10/21:50 stamina sweep for an active Xianmeng day."""

    timezone = ZoneInfo(timezone_name)
    current = now or datetime.now(timezone)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone)
    current = current.astimezone(timezone)
    schedule = build_authorized_daily_activity_job_schedule(plan)
    if schedule["target_date"] != current.date().isoformat():
        return None
    if schedule["desired_next_times"].get(XIANMENG_CHALLENGE_TASK_ID) is None:
        return None
    future_sweeps = [
        current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        for hour, minute in XIANMENG_STAMINA_SWEEPS
        if current.replace(hour=hour, minute=minute, second=0, microsecond=0) > current
    ]
    if not future_sweeps:
        return None
    next_sweep = min(future_sweeps)
    for decision in schedule["decisions"]:
        if decision["task_id"] != XIANMENG_CHALLENGE_TASK_ID:
            continue
        boundaries: list[datetime] = []
        for key in ("end_at", "close_panel_at"):
            text = str(decision.get(key) or "").strip()
            if text:
                boundary = datetime.fromisoformat(text)
                if boundary.tzinfo is not None:
                    boundaries.append(boundary.astimezone(timezone))
        if boundaries and next_sweep >= min(boundaries):
            return None
    return next_sweep.strftime("%Y-%m-%d %H:%M:%S")


__all__ = [
    "AUTHORIZED_ACTIVITY_JOB_BINDINGS",
    "XIANMENG_CHALLENGE_LABEL",
    "XIANMENG_CHALLENGE_TASK_ID",
    "XIANMENG_FINAL_SWEEP",
    "XIANMENG_STAMINA_SWEEPS",
    "XIANMENG_TRIPLE_DISABLE_AT",
    "XIANMENG_FORMAL_BASE_IDS",
    "XIANMENG_FORMAL_CHILD_BASE_IDS",
    "AuthorizedActivityJobBinding",
    "build_authorized_daily_activity_job_schedule",
    "next_xianmeng_challenge_tail_time",
]
