from __future__ import annotations

"""User-authorized one-activity/one-Job lifecycle projections.

The daily activity reader produces facts, not execution authority.  This
module is the narrow bridge between the user's explicitly approved activity
names and :mod:`activity_job_lifecycle`.  It is deliberately pure: callers may
inspect the proposed ``desired_next_times`` but no Scheduler state is written
here.

An activity-menu observation is insufficient by itself.  Every proposed Job
time also requires an independently authoritative period for the same
``activity_id``.  Missing or conflicting facts fail closed, which is important
while the legacy configuration/lottery pairs still protect production.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from backend.core.fanxiu.activity.activity_job_lifecycle import (
    TRUSTED_ACTIVITY_END_SOURCE_KINDS,
    plan_activity_job_lifecycle,
)
from backend.core.fanxiu.activity.daily_activity_discovery import DEFAULT_TIMEZONE


@dataclass(frozen=True)
class AuthorizedActivityLifecycleSpec:
    """One explicit user authorization and its eventual Scheduler migration."""

    binding_id: str
    names: frozenset[str]
    task_id: str
    task_type: str
    retired_task_ids: tuple[str, ...] = ()
    activation_delay_minutes: int = 0
    ten_draw_threshold: int = 10


# These names were individually authorized by the user.  Do not infer new
# entries from page-family similarity or Runtime discovery.
AUTHORIZED_ACTIVITY_LIFECYCLE_SPECS = (
    AuthorizedActivityLifecycleSpec(
        binding_id="penglai-xianzang-one-lifecycle-job",
        names=frozenset({"蓬莱仙藏"}),
        task_id="penglai-xianzang",
        task_type="penglai_xianzang",
        retired_task_ids=(
            "penglai-xianzang-config",
            "penglai-xianzang-lottery",
        ),
    ),
    AuthorizedActivityLifecycleSpec(
        binding_id="kunlun-secret-one-lifecycle-job",
        names=frozenset({"昆仑秘藏"}),
        task_id="kunlun-secret",
        task_type="kunlun_secret",
        retired_task_ids=("kunlun-secret-config", "kunlun-secret-lottery"),
    ),
    AuthorizedActivityLifecycleSpec(
        binding_id="lingxiao-xianhui-one-lifecycle-job",
        names=frozenset({"灵霄仙会", "凌霄仙会"}),
        task_id="lingxiao-xianhui",
        task_type="lingxiao_xianhui",
    ),
    AuthorizedActivityLifecycleSpec(
        binding_id="wanbao-zhenbao-one-lifecycle-job",
        names=frozenset({"万宝臻宝"}),
        task_id="wanbao-zhenbao",
        task_type="wanbao_zhenbao",
        activation_delay_minutes=10,
    ),
)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _spec_by_name(name: Any) -> AuthorizedActivityLifecycleSpec | None:
    text = str(name or "").strip()
    if not text:
        return None
    return next(
        (spec for spec in AUTHORIZED_ACTIVITY_LIFECYCLE_SPECS if text in spec.names),
        None,
    )


def _authority_index(
    authorities: Mapping[Any, Mapping[str, Any]] | Sequence[Mapping[str, Any]],
) -> dict[int, dict[str, Any]]:
    values: Sequence[Mapping[str, Any]]
    if isinstance(authorities, Mapping):
        values = tuple(
            dict(value)
            for value in authorities.values()
            if isinstance(value, Mapping)
        )
    else:
        values = tuple(value for value in authorities if isinstance(value, Mapping))
    result: dict[int, dict[str, Any]] = {}
    for raw in values:
        authority = dict(raw)
        activity_id = _as_int(authority.get("activity_id"))
        if activity_id is None or activity_id <= 0:
            continue
        if activity_id in result and result[activity_id] != authority:
            # Preserve an explicit conflict for the caller instead of picking
            # whichever source happened to be iterated last.
            result[activity_id] = {
                "complete": False,
                "activity_id": activity_id,
                "reason": "同一活动存在冲突的权威周期",
            }
        else:
            result[activity_id] = authority
    return result


def daily_plan_activity_end_authorities(
    plan: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Derive trusted period DTOs from complete #66 Runtime occurrences.

    The occurrence is used only as period authority.  It never replaces the
    separate Revenue/menu observation that proves the independent activity is
    currently exposed.  Pairing later still requires exact ``activity_id``.
    """

    source_kind = str(plan.get("source_kind") or "").strip()
    if source_kind not in TRUSTED_ACTIVITY_END_SOURCE_KINDS:
        return []
    result: list[dict[str, Any]] = []
    for raw in plan.get("occurrences") or []:
        if not isinstance(raw, Mapping):
            continue
        activity_id = _as_int(raw.get("activity_id"))
        schedule_id = _as_int(raw.get("schedule_id"))
        start_at = str(raw.get("start_at") or "").strip()
        end_at = str(raw.get("end_at") or "").strip()
        if (
            activity_id is None
            or activity_id <= 0
            or schedule_id is None
            or schedule_id <= 0
            or raw.get("identity_complete") is not True
            or str(raw.get("catalog_status") or "") != "known"
            or not start_at
            or not end_at
        ):
            continue
        try:
            start = datetime.fromisoformat(start_at)
            end = datetime.fromisoformat(end_at)
        except ValueError:
            continue
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            continue
        result.append(
            trusted_activity_lifecycle_authority(
                activity_id=activity_id,
                instance_key=(
                    f"schedule:{schedule_id}:activity:{activity_id}:"
                    f"{start.isoformat(timespec='seconds')}:"
                    f"{end.isoformat(timespec='seconds')}"
                ),
                end_at=end.isoformat(timespec="seconds"),
                source_kind=source_kind,
            )
        )
    return result


def plan_authorized_activity_lifecycles(
    plan: Mapping[str, Any],
    *,
    end_authorities: Mapping[Any, Mapping[str, Any]]
    | Sequence[Mapping[str, Any]],
    previous_completions: Mapping[str, Mapping[str, Any]] | None = None,
    resource_counts: Mapping[str, int | None] | None = None,
    now: datetime | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    """Project a ready daily discovery plan into one Job per activity.

    The result is intentionally a proposal.  It may be persisted only after
    the canonical Job implementations and completion-state store have passed
    their production migration gate.
    """

    if str(plan.get("status") or "") != "ready":
        raise ValueError("只有完整 ready 的 Runtime 日程才能规划活动生命周期")
    source = dict(plan.get("source_evidence") or {}).get(
        "supplemental_activity_observation"
    )
    if not isinstance(source, Mapping) or source.get("complete") is not True:
        raise ValueError("活动生命周期要求完整的 supplemental activity observation")

    authority_by_id = _authority_index(end_authorities)
    previous = previous_completions or {}
    resources = resource_counts or {}
    observations_by_task: dict[str, list[dict[str, Any]]] = {
        spec.task_id: [] for spec in AUTHORIZED_ACTIVITY_LIFECYCLE_SPECS
    }
    specs_by_task = {
        spec.task_id: spec for spec in AUTHORIZED_ACTIVITY_LIFECYCLE_SPECS
    }
    for raw in plan.get("activity_observations") or []:
        if not isinstance(raw, Mapping):
            continue
        spec = _spec_by_name(raw.get("name"))
        if spec is None:
            continue
        observation = dict(raw)
        if observation.get("is_schedule_occurrence") is not False:
            continue
        observations_by_task[spec.task_id].append(observation)

    desired = {spec.task_id: None for spec in AUTHORIZED_ACTIVITY_LIFECYCLE_SPECS}
    decisions: list[dict[str, Any]] = []
    for task_id, observations in observations_by_task.items():
        if not observations:
            continue
        spec = specs_by_task[task_id]
        activity_ids = {
            value
            for observation in observations
            if (value := _as_int(observation.get("activity_id"))) is not None
            and value > 0
        }
        if len(observations) != 1 or len(activity_ids) != 1:
            decisions.append(
                {
                    "binding_id": spec.binding_id,
                    "task_id": task_id,
                    "status": "blocked",
                    "reason": "同一 canonical Job 命中多个活动 observation，拒绝选取",
                    "next_time": None,
                }
            )
            continue
        observation = observations[0]
        activity_id = next(iter(activity_ids))
        authority = authority_by_id.get(activity_id)
        decision = plan_activity_job_lifecycle(
            task_id=task_id,
            observation=observation,
            end_authority=authority,
            previous_completion=previous.get(task_id),
            resource_count=resources.get(task_id),
            ten_draw_threshold=spec.ten_draw_threshold,
            activation_delay_minutes=spec.activation_delay_minutes,
            now=now,
            timezone_name=timezone_name,
        )
        decisions.append({"binding_id": spec.binding_id, **decision})
        if decision.get("status") == "ready":
            desired[task_id] = decision.get("next_time")

    return {
        "desired_next_times": desired,
        "decisions": decisions,
        "migration": {
            "canonical_task_ids": sorted(desired),
            "retirement_candidates": sorted(
                task_id
                for spec in AUTHORIZED_ACTIVITY_LIFECYCLE_SPECS
                for task_id in spec.retired_task_ids
            ),
            "fact_ready": all(
                decision.get("status") == "ready" for decision in decisions
            )
            and bool(decisions),
            # A fact projection cannot by itself authorize deleting production
            # Jobs.  build_activity_lifecycle_scheduler_migration owns that
            # explicit gate.
            "ready": False,
            "removed_task_ids": [],
        },
    }


def build_activity_lifecycle_scheduler_migration(
    projection: Mapping[str, Any],
    *,
    registered_standard_task_ids: Sequence[str],
    completion_store_ready: bool,
    daily_sync_adapter_ready: bool,
) -> dict[str, Any]:
    """Authorize the eventual atomic ``removed_task_ids`` migration.

    This helper still performs no write.  Its output may be passed to the
    Scheduler's unified atomic store only after the canonical Jobs, lifecycle
    completion store, and daily-sync adapter all exist in the same code
    version.  Therefore an old pair can never be retired merely because one
    discovery sample happened to be complete.
    """

    migration = projection.get("migration")
    if not isinstance(migration, Mapping):
        raise ValueError("活动生命周期 projection 缺少 migration 契约")
    canonical = {
        str(value or "").strip()
        for value in migration.get("canonical_task_ids") or []
        if str(value or "").strip()
    }
    registered = {
        str(value or "").strip()
        for value in registered_standard_task_ids
        if str(value or "").strip()
    }
    missing = sorted(canonical - registered)
    reasons: list[str] = []
    if missing:
        reasons.append(f"canonical 标准 Job 尚未注册：{', '.join(missing)}")
    if not completion_store_ready:
        reasons.append("生命周期 completion store 尚未就绪")
    if not daily_sync_adapter_ready:
        reasons.append("活动_每日清单同步生命周期 adapter 尚未就绪")
    if reasons:
        return {
            "status": "blocked",
            "reason": "；".join(reasons),
            "removed_task_ids": [],
        }
    return {
        "status": "ready",
        "reason": "canonical Job、completion store 与每日同步 adapter 均已就绪",
        "removed_task_ids": sorted(
            {
                str(value or "").strip()
                for value in migration.get("retirement_candidates") or []
                if str(value or "").strip()
            }
        ),
    }


def trusted_activity_lifecycle_authority(
    *,
    activity_id: int,
    instance_key: str,
    end_at: str,
    source_kind: str,
) -> dict[str, Any]:
    """Construct a validated authority DTO at a concrete Runtime adapter."""

    if source_kind not in TRUSTED_ACTIVITY_END_SOURCE_KINDS:
        raise ValueError(f"活动周期来源不受信任：{source_kind or 'missing'}")
    if int(activity_id) <= 0 or not str(instance_key).strip() or not str(end_at).strip():
        raise ValueError("活动周期权威身份不完整")
    return {
        "complete": True,
        "source_kind": source_kind,
        "activity_id": int(activity_id),
        "instance_key": str(instance_key).strip(),
        "end_at": str(end_at).strip(),
    }


__all__ = [
    "AUTHORIZED_ACTIVITY_LIFECYCLE_SPECS",
    "AuthorizedActivityLifecycleSpec",
    "build_activity_lifecycle_scheduler_migration",
    "daily_plan_activity_end_authorities",
    "plan_authorized_activity_lifecycles",
    "trusted_activity_lifecycle_authority",
]
