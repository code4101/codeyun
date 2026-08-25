from __future__ import annotations

"""Pure one-activity/one-Job lifecycle scheduling decisions.

Discovery facts do not write Scheduler state.  This module reduces one
authoritative activity instance, its last successfully completed Job state and
its current resource count to at most one absolute ``next_time``.  The caller
must persist that single timestamp through the framework's atomic Scheduler
field update and must persist completion state only after a successful Job.
"""

from datetime import datetime, time, timedelta
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from backend.core.fanxiu.activity.daily_activity_discovery import DEFAULT_TIMEZONE


TRUSTED_ACTIVITY_END_SOURCE_KINDS = frozenset(
    {
        "worldline_activity_runtime_memory",
        "revenue_activity_period_runtime_memory",
    }
)
END_DAY_TAIL_TIME = time(21, 10)
DEFAULT_TEN_DRAW_THRESHOLD = 10


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _aware_datetime(value: Any, timezone: ZoneInfo) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone)


def _blocked(
    *, task_id: str, activity_id: int | None, reason: str
) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": reason,
        "task_id": task_id,
        "activity_id": activity_id,
        "instance_key": "",
        "trigger": "none",
        "next_time": None,
        "completion_token": None,
    }


def _authoritative_tail_at(end_at: datetime) -> datetime:
    # A period ending exactly at 00:00 uses an exclusive upper boundary; its
    # last playable calendar day is therefore the preceding day.
    last_day = end_at.date()
    if end_at.timetz().replace(tzinfo=None) == time.min:
        last_day -= timedelta(days=1)
    return datetime.combine(last_day, END_DAY_TAIL_TIME, end_at.tzinfo)


def plan_activity_job_lifecycle(
    *,
    task_id: str,
    observation: Mapping[str, Any],
    end_authority: Mapping[str, Any] | None,
    previous_completion: Mapping[str, Any] | None = None,
    resource_count: int | None = None,
    ten_draw_threshold: int = DEFAULT_TEN_DRAW_THRESHOLD,
    activation_earliest_at: datetime | str | None = None,
    activation_delay_minutes: int = 0,
    now: datetime | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    """Choose the sole trigger for one activity Job, or fail closed.

    ``end_authority`` is intentionally separate from a Revenue/menu
    observation.  A visible menu row proves presence, not an end date.  An
    adapter may supply a complete #66 occurrence or another explicitly trusted
    read-only Runtime period, but static recurrence and guessed dates are not
    accepted here.
    """

    resolved_task_id = str(task_id or "").strip()
    if not resolved_task_id:
        raise ValueError("活动生命周期缺少 Scheduler Job id")
    activity_id = _as_int(observation.get("activity_id"))
    if activity_id is None or activity_id <= 0:
        return _blocked(
            task_id=resolved_task_id,
            activity_id=None,
            reason="活动 observation 缺少正整数 activity_id",
        )
    if observation.get("is_schedule_occurrence") is not False:
        return _blocked(
            task_id=resolved_task_id,
            activity_id=activity_id,
            reason="活动生命周期要求独立 observation，拒绝混用 occurrence",
        )
    if not isinstance(end_authority, Mapping) or not end_authority.get("complete"):
        return _blocked(
            task_id=resolved_task_id,
            activity_id=activity_id,
            reason="缺少完整的权威活动结束时间，拒绝生成生命周期触发",
        )
    source_kind = str(end_authority.get("source_kind") or "").strip()
    if source_kind not in TRUSTED_ACTIVITY_END_SOURCE_KINDS:
        return _blocked(
            task_id=resolved_task_id,
            activity_id=activity_id,
            reason=f"活动结束时间来源不受信任：{source_kind or 'missing'}",
        )
    authority_activity_id = _as_int(end_authority.get("activity_id"))
    if authority_activity_id != activity_id:
        return _blocked(
            task_id=resolved_task_id,
            activity_id=activity_id,
            reason="活动 observation 与结束时间权威身份不一致",
        )
    instance_key = str(end_authority.get("instance_key") or "").strip()
    if not instance_key:
        return _blocked(
            task_id=resolved_task_id,
            activity_id=activity_id,
            reason="权威活动周期缺少稳定 instance_key",
        )

    timezone = ZoneInfo(timezone_name)
    current = now or datetime.now(timezone)
    if current.tzinfo is None:
        raise ValueError("活动生命周期时钟必须带时区")
    current = current.astimezone(timezone).replace(microsecond=0)
    earliest_activation = (
        _aware_datetime(activation_earliest_at, timezone)
        if activation_earliest_at is not None
        else None
    )
    if activation_earliest_at is not None and earliest_activation is None:
        return _blocked(
            task_id=resolved_task_id,
            activity_id=activity_id,
            reason="新实例 activation_earliest_at 缺失时区或格式无效",
        )
    if activation_delay_minutes < 0:
        raise ValueError("新实例激活延迟分钟数不能为负数")
    if activation_delay_minutes:
        observed_at = _aware_datetime(
            observation.get("observed_at") or observation.get("captured_at"),
            timezone,
        )
        if observed_at is None:
            return _blocked(
                task_id=resolved_task_id,
                activity_id=activity_id,
                reason="配置激活延迟时 observation 必须提供带时区 observed_at",
            )
        delayed_activation = observed_at + timedelta(
            minutes=int(activation_delay_minutes)
        )
        earliest_activation = max(
            value
            for value in (earliest_activation, delayed_activation)
            if value is not None
        )
    end_at = _aware_datetime(end_authority.get("end_at"), timezone)
    if end_at is None:
        return _blocked(
            task_id=resolved_task_id,
            activity_id=activity_id,
            reason="权威活动结束时间缺失或不带时区",
        )
    tail_at = _authoritative_tail_at(end_at)
    if tail_at >= end_at:
        return _blocked(
            task_id=resolved_task_id,
            activity_id=activity_id,
            reason="结束日 21:10 不在权威活动周期内",
        )
    if earliest_activation is not None and earliest_activation >= end_at:
        return _blocked(
            task_id=resolved_task_id,
            activity_id=activity_id,
            reason="新实例最早激活时间不在权威活动周期内",
        )
    if current >= end_at:
        return {
            "status": "ready",
            "reason": "权威活动周期已经结束，不再触发作业",
            "task_id": resolved_task_id,
            "activity_id": activity_id,
            "instance_key": instance_key,
            "trigger": "none",
            "next_time": None,
            "authoritative_end_at": end_at.isoformat(timespec="seconds"),
            "end_tail_at": tail_at.isoformat(timespec="seconds"),
            "completion_token": None,
        }

    previous = dict(previous_completion or {})
    same_instance = str(previous.get("instance_key") or "") == instance_key
    completed_triggers = (
        {
            str(value)
            for value in previous.get("completed_triggers") or []
            if str(value)
        }
        if same_instance
        else set()
    )
    previous_resource = (
        _as_int(previous.get("resource_count")) if same_instance else None
    )
    current_resource = _as_int(resource_count)
    if current_resource is not None and current_resource < 0:
        raise ValueError("活动资源数量不能为负数")
    if ten_draw_threshold <= 0:
        raise ValueError("十连阈值必须为正整数")

    if "instance_activation" not in completed_triggers:
        trigger = "instance_activation"
        trigger_at = max(current, earliest_activation or current)
        reason = (
            "发现尚未成功处理的新活动实例"
            if earliest_activation is None or earliest_activation <= current
            else "发现新活动实例，按配置的最早激活时间触发"
        )
    elif (
        previous_resource is not None
        and current_resource is not None
        and previous_resource < ten_draw_threshold <= current_resource
    ):
        trigger = "resource_ten_draw_crossing"
        trigger_at = current
        reason = (
            f"活动资源从 {previous_resource} 跨过十连阈值 "
            f"{ten_draw_threshold} 到 {current_resource}"
        )
    elif "authoritative_end_tail" not in completed_triggers and current < end_at:
        trigger = "authoritative_end_tail"
        trigger_at = max(current, tail_at)
        reason = "按权威结束日安排 21:10 收尾"
    else:
        return {
            "status": "ready",
            "reason": "本实例没有未完成的生命周期触发",
            "task_id": resolved_task_id,
            "activity_id": activity_id,
            "instance_key": instance_key,
            "trigger": "none",
            "next_time": None,
            "authoritative_end_at": end_at.isoformat(timespec="seconds"),
            "end_tail_at": tail_at.isoformat(timespec="seconds"),
            "completion_token": None,
        }

    next_time = trigger_at.strftime("%Y-%m-%d %H:%M:%S")
    return {
        "status": "ready",
        "reason": reason,
        "task_id": resolved_task_id,
        "activity_id": activity_id,
        "instance_key": instance_key,
        "trigger": trigger,
        "next_time": next_time,
        "authoritative_end_at": end_at.isoformat(timespec="seconds"),
        "end_tail_at": tail_at.isoformat(timespec="seconds"),
        "resource_count": current_resource,
        "ten_draw_threshold": int(ten_draw_threshold),
        "completion_token": {
            "task_id": resolved_task_id,
            "activity_id": activity_id,
            "instance_key": instance_key,
            "trigger": trigger,
            "planned_for": next_time,
        },
    }


def complete_activity_job_lifecycle(
    decision: Mapping[str, Any],
    *,
    previous_completion: Mapping[str, Any] | None = None,
    resource_count_after: int | None,
    completed_at: datetime,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    """Build state that may be persisted only after the Job succeeds."""

    token = decision.get("completion_token")
    if str(decision.get("status") or "") != "ready" or not isinstance(
        token, Mapping
    ):
        raise ValueError("只有待执行的 ready 生命周期决策才能确认完成")
    timezone = ZoneInfo(timezone_name)
    if completed_at.tzinfo is None:
        raise ValueError("生命周期完成时间必须带时区")
    instance_key = str(token.get("instance_key") or "").strip()
    trigger = str(token.get("trigger") or "").strip()
    if not instance_key or trigger not in {
        "instance_activation",
        "resource_ten_draw_crossing",
        "authoritative_end_tail",
    }:
        raise ValueError("生命周期 completion token 无效")
    previous = dict(previous_completion or {})
    completed = (
        {
            str(value)
            for value in previous.get("completed_triggers") or []
            if str(value)
        }
        if str(previous.get("instance_key") or "") == instance_key
        else set()
    )
    completed.intersection_update(
        {"instance_activation", "authoritative_end_tail"}
    )
    # Resource threshold is a repeatable edge, not a one-shot lifecycle phase.
    # Its sole persisted memory is the post-success resource count below.  A
    # later successful drain to <10 followed by another rise to >=10 must emit
    # another Job trigger in the same activity instance.
    if trigger != "resource_ten_draw_crossing":
        completed.add(trigger)
    resource = _as_int(resource_count_after)
    if resource is not None and resource < 0:
        raise ValueError("活动完成后资源数量不能为负数")
    return {
        "version": 1,
        "task_id": str(token.get("task_id") or ""),
        "activity_id": _as_int(token.get("activity_id")),
        "instance_key": instance_key,
        "completed_triggers": sorted(completed),
        "resource_count": resource,
        "completed_at": completed_at.astimezone(timezone).isoformat(
            timespec="seconds"
        ),
    }


__all__ = [
    "DEFAULT_TEN_DRAW_THRESHOLD",
    "END_DAY_TAIL_TIME",
    "TRUSTED_ACTIVITY_END_SOURCE_KINDS",
    "complete_activity_job_lifecycle",
    "plan_activity_job_lifecycle",
]
