from __future__ import annotations

"""User-authorized daily activity -> Scheduler Job bindings.

Runtime discovery supplies occurrence facts. This registry is the separate
execution authority: an activity can only change a Job ``next_time`` when a
binding is explicitly declared here and covered by contract tests.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
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
    activity_names: frozenset[str]
    task_id: str
    task_label: str
    trigger_at: time


# Every entry is a user-approved execution relationship. Do not infer or append
# entries from Runtime names, page families, follow items, or similarity.
# Activity-list synchronization owns only these explicitly authorized top-level
# activity Jobs. Ranking child Jobs remain owned by their ranking parents.
AUTHORIZED_ACTIVITY_JOB_BINDINGS: tuple[AuthorizedActivityJobBinding, ...] = (
    AuthorizedActivityJobBinding(
        binding_id="penglai-xianzang-config-from-daily-list",
        activity_names=frozenset({"蓬莱仙藏"}),
        task_id="penglai-xianzang-config",
        task_label="蓬莱仙藏_配置",
        trigger_at=time(0, 5),
    ),
    AuthorizedActivityJobBinding(
        binding_id="penglai-xianzang-lottery-from-daily-list",
        activity_names=frozenset({"蓬莱仙藏"}),
        task_id="penglai-xianzang-lottery",
        task_label="蓬莱仙藏_抽奖",
        trigger_at=time(21, 10),
    ),
    AuthorizedActivityJobBinding(
        binding_id="kunlun-secret-config-from-daily-list",
        activity_names=frozenset({"昆仑秘藏"}),
        task_id="kunlun-secret-config",
        task_label="昆仑秘藏_配置",
        trigger_at=time(0, 5),
    ),
    AuthorizedActivityJobBinding(
        binding_id="kunlun-secret-lottery-from-daily-list",
        activity_names=frozenset({"昆仑秘藏"}),
        task_id="kunlun-secret-lottery",
        task_label="昆仑秘藏_抽奖",
        trigger_at=time(21, 10),
    ),
)


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


def _complete_activity_observation_names(plan: Mapping[str, Any]) -> set[str] | None:
    source = dict(plan.get("source_evidence") or {}).get(
        "supplemental_activity_observation"
    )
    if not isinstance(source, Mapping) or source.get("complete") is not True:
        return None
    activity_ids_by_name: dict[str, set[int]] = {}
    for raw in plan.get("activity_observations") or []:
        if (
            not isinstance(raw, Mapping)
            or raw.get("is_schedule_occurrence") is not False
        ):
            continue
        name = str(raw.get("name") or "").strip()
        activity_id = _as_int(raw.get("activity_id"))
        if name and activity_id is not None and activity_id > 0:
            activity_ids_by_name.setdefault(name, set()).add(activity_id)
    conflicts = sorted(
        name
        for name, activity_ids in activity_ids_by_name.items()
        if len(activity_ids) > 1
    )
    if conflicts:
        raise ValueError(
            "活动清单同名 observation 对应多个 activity_id：" + ", ".join(conflicts)
        )
    return set(activity_ids_by_name)


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
    observed_names = _complete_activity_observation_names(plan)
    if observed_names is None:
        return {
            "target_date": target.isoformat(),
            "desired_next_times": {},
            "decisions": [],
            "status": "observation_unavailable",
            "reason": "Runtime 活动清单 observation 不完整，保留现有活动 Job next_time",
        }
    desired: dict[str, str | None] = {
        binding.task_id: None for binding in AUTHORIZED_ACTIVITY_JOB_BINDINGS
    }
    decisions: list[dict[str, Any]] = []
    for binding in AUTHORIZED_ACTIVITY_JOB_BINDINGS:
        matched_names = sorted(binding.activity_names.intersection(observed_names))
        if not matched_names:
            continue
        trigger_at = datetime.combine(target, binding.trigger_at, tzinfo=timezone)
        next_time = trigger_at.strftime("%Y-%m-%d %H:%M:%S")
        desired[binding.task_id] = next_time
        decisions.append(
            {
                "binding_id": binding.binding_id,
                "task_id": binding.task_id,
                "task_label": binding.task_label,
                "next_time": next_time,
                "activity_name": matched_names[0],
            }
        )
    return {
        "target_date": target.isoformat(),
        "desired_next_times": desired,
        "decisions": decisions,
        "status": "ready",
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
