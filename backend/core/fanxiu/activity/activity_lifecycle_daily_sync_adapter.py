from __future__ import annotations

"""Production gate between daily Runtime facts and canonical activity Jobs.

The daily activity synchronizer owns fact persistence.  This adapter only
projects those already-complete facts into per-Job ``next_time`` updates.  It
does not write Scheduler membership, retire legacy Jobs, or treat a visible
Revenue row as period authority.

The completion store is the Job-side half of the contract: the daily adapter
reads it before planning, while a canonical Job may write it only after that
Job has actually completed successfully.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlmodel import Session

from backend.core.fanxiu.activity.activity_job_lifecycle import (
    complete_activity_job_lifecycle,
)
from backend.core.fanxiu.activity.activity_lifecycle_store import (
    persist_activity_lifecycle_completion,
    read_activity_lifecycle_completion,
)
from backend.core.fanxiu.activity.authorized_activity_lifecycle import (
    AUTHORIZED_ACTIVITY_LIFECYCLE_SPECS,
    daily_plan_activity_end_authorities,
    plan_authorized_activity_lifecycles,
)
from backend.core.fanxiu.activity.daily_activity_discovery import DEFAULT_TIMEZONE


REVENUE_OBSERVATION_SOURCE_KIND = "revenue_activity_observation_runtime_memory"
WORLDLINE_OCCURRENCE_SOURCE_KIND = "worldline_activity_runtime_memory"

CompletionReader = Callable[[str], Mapping[str, Any] | None]


def _authorized_task_ids() -> frozenset[str]:
    return frozenset(spec.task_id for spec in AUTHORIZED_ACTIVITY_LIFECYCLE_SPECS)


def _observed_authorized_task_ids(plan: Mapping[str, Any]) -> frozenset[str]:
    names_to_task = {
        name: spec.task_id
        for spec in AUTHORIZED_ACTIVITY_LIFECYCLE_SPECS
        for name in spec.names
    }
    return frozenset(
        names_to_task[name]
        for raw in plan.get("activity_observations") or []
        if isinstance(raw, Mapping)
        and raw.get("is_schedule_occurrence") is False
        and (name := str(raw.get("name") or "").strip()) in names_to_task
    )


def _blocked_result(reason: str) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": reason,
        "scheduler_updates": {},
        "decisions": [],
        "migration": {
            "ready": False,
            "removed_task_ids": [],
        },
    }


def project_daily_sync_activity_lifecycles(
    plan: Mapping[str, Any],
    *,
    canonical_executor_task_ids: Sequence[str],
    completion_reader: CompletionReader | None,
    completion_store_ready: bool,
    resource_counts: Mapping[str, int | None] | None = None,
    now: datetime | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    """Return safe canonical Job field updates for one ready daily plan.

    Blocking is per observed activity once the shared fact/store gates pass:
    an unavailable Penglai executor cannot accidentally create or clear its
    canonical Scheduler row, while an independently ready Wanbao executor may
    still consume the same complete daily snapshot.  Legacy pairs are never
    mentioned in ``scheduler_updates`` and migration remains disabled here.
    """

    if str(plan.get("status") or "") != "ready":
        return _blocked_result("只有完整 ready 的 Runtime 日程才能投影活动生命周期")
    supplemental = dict(plan.get("source_evidence") or {}).get(
        "supplemental_activity_observation"
    )
    if not isinstance(supplemental, Mapping) or supplemental.get("complete") is not True:
        return _blocked_result("Revenue activity observation 不完整")
    if str(supplemental.get("source_kind") or "") != REVENUE_OBSERVATION_SOURCE_KIND:
        return _blocked_result("Revenue activity observation 来源不受信任")
    if str(plan.get("source_kind") or "") != WORLDLINE_OCCURRENCE_SOURCE_KIND:
        return _blocked_result("#66 occurrence 来源不受信任")
    if not completion_store_ready or completion_reader is None:
        return _blocked_result("活动生命周期 completion store 尚未就绪")

    observed_task_ids = _observed_authorized_task_ids(plan)
    previous: dict[str, Mapping[str, Any]] = {}
    try:
        for task_id in sorted(observed_task_ids):
            state = completion_reader(task_id)
            if state is not None:
                previous[task_id] = dict(state)
    except Exception as exc:
        return _blocked_result(
            "活动生命周期 completion store 读取失败："
            f"{type(exc).__name__}: {exc}"
        )

    try:
        projection = plan_authorized_activity_lifecycles(
            plan,
            end_authorities=daily_plan_activity_end_authorities(plan),
            previous_completions=previous,
            resource_counts=resource_counts,
            now=now,
            timezone_name=timezone_name,
        )
    except (TypeError, ValueError) as exc:
        return _blocked_result(str(exc))

    executable = {
        str(task_id or "").strip()
        for task_id in canonical_executor_task_ids
        if str(task_id or "").strip() in _authorized_task_ids()
    }
    decisions: list[dict[str, Any]] = []
    scheduler_updates: dict[str, str | None] = {}
    for raw in projection.get("decisions") or []:
        decision = dict(raw)
        task_id = str(decision.get("task_id") or "")
        if decision.get("status") == "ready" and task_id not in executable:
            decision.update(
                status="blocked",
                reason=f"canonical 活动执行器尚未就绪：{task_id}",
                next_time=None,
                completion_token=None,
            )
        if decision.get("status") == "ready":
            scheduler_updates[task_id] = decision.get("next_time")
        decisions.append(decision)

    blocked = [item for item in decisions if item.get("status") != "ready"]
    return {
        "status": "ready" if not blocked else "partially_blocked",
        "reason": (
            "全部已观测授权活动均可安全投影"
            if not blocked
            else "部分活动缺少权威事实或 canonical 执行器，已逐项失败关闭"
        ),
        "scheduler_updates": scheduler_updates,
        "decisions": decisions,
        "migration": {
            # This adapter can update fields only.  Membership migration still
            # requires the separate explicit removed_task_ids gate.
            "ready": False,
            "removed_task_ids": [],
        },
    }


def session_activity_lifecycle_completion_reader(
    session: Session,
) -> CompletionReader:
    """Bind the durable completion store to the pure daily adapter."""

    def read(task_id: str) -> Mapping[str, Any] | None:
        return read_activity_lifecycle_completion(session, task_id=task_id)

    return read


def persist_successful_activity_lifecycle_completion(
    session: Session,
    *,
    decision: Mapping[str, Any],
    resource_count_after: int | None,
    completed_at: datetime,
    job_succeeded: bool,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    """Persist completion only at an explicit successful canonical Job edge."""

    if job_succeeded is not True:
        raise ValueError("活动 Job 未成功，禁止写入生命周期 completion store")
    task_id = str(decision.get("task_id") or "").strip()
    if task_id not in _authorized_task_ids():
        raise ValueError("completion decision 不属于授权 canonical 活动 Job")
    # Read under the same database session as the write.  A caller-provided
    # snapshot could be stale and accidentally erase a one-shot completion.
    previous_completion = read_activity_lifecycle_completion(
        session,
        task_id=task_id,
    )
    state = complete_activity_job_lifecycle(
        decision,
        previous_completion=previous_completion,
        resource_count_after=resource_count_after,
        completed_at=completed_at,
        timezone_name=timezone_name,
    )
    return persist_activity_lifecycle_completion(session, state)


__all__ = [
    "REVENUE_OBSERVATION_SOURCE_KIND",
    "WORLDLINE_OCCURRENCE_SOURCE_KIND",
    "persist_successful_activity_lifecycle_completion",
    "project_daily_sync_activity_lifecycles",
    "session_activity_lifecycle_completion_reader",
]
