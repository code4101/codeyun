from __future__ import annotations

"""Lightweight contract for the consolidated resource Daily Processing Job.

The canonical ``resource-auto-use`` Job owns one business run at 00:00 and
aggregates six retired first-level Jobs.  This module only names that identity
and the six internalized stages (plus two pure helpers used by the Scheduler
catalogue migration).  It imports nothing from the Scheduler so both the
defaults module and pure tests can depend on it without cycles.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping

RESOURCE_DAILY_TASK_ID = "resource-auto-use"
RESOURCE_DAILY_TASK_TYPE = "resource_auto_use"
RESOURCE_DAILY_LABEL = "资源_每日处理"
RESOURCE_DAILY_TRIGGER_DESCRIPTION = "每日"

RESOURCE_DAILY_SCHEMA_VERSION = 1
RESOURCE_DAILY_PAYLOAD_SCHEMA_KEY = "resource_daily_schema_version"
RESOURCE_DAILY_INTERNALIZED_JOBS_KEY = "internalized_jobs"
RESOURCE_DAILY_PROGRESS_KEY = "aggregate_progress"
RESOURCE_DAILY_STAGE_VERSION = "1"

RESOURCE_DAILY_DAILY_CADENCE = "daily"
RESOURCE_DAILY_WEEKLY_CADENCE = "weekly_monday"


@dataclass(frozen=True)
class ResourceDailyStage:
    """One retired first-level Job absorbed by the canonical daily Job."""

    task_id: str
    task_type: str
    label: str
    cadence: str

    @property
    def weekly(self) -> bool:
        return self.cadence == RESOURCE_DAILY_WEEKLY_CADENCE


# Two Monday 00:00 exchanges are weekly; the other four stages are daily.
RESOURCE_DAILY_STAGES: tuple[ResourceDailyStage, ...] = (
    ResourceDailyStage("legacy-daily-vip", "daily_vip", "日常_vip", RESOURCE_DAILY_DAILY_CADENCE),
    ResourceDailyStage("daily-signin", "daily_signin", "日常_签到", RESOURCE_DAILY_DAILY_CADENCE),
    ResourceDailyStage("prayer-daily-resource", "prayer_daily_resource", "祈愿_每日资源", RESOURCE_DAILY_DAILY_CADENCE),
    ResourceDailyStage("legacy-daily-xianshi", "daily_xianshi", "仙市_秘藏阁", RESOURCE_DAILY_DAILY_CADENCE),
    ResourceDailyStage("xianshi-langya-rankings", "xianshi_langya_rankings", "仙市_琅琊榜", RESOURCE_DAILY_WEEKLY_CADENCE),
    ResourceDailyStage("xianshi-zhenwuge", "xianshi_zhenwuge", "仙市_真悟阁", RESOURCE_DAILY_WEEKLY_CADENCE),
)

RESOURCE_DAILY_STAGE_IDS: frozenset[str] = frozenset(
    stage.task_id for stage in RESOURCE_DAILY_STAGES
)
RESOURCE_DAILY_STAGE_TYPES: frozenset[str] = frozenset(
    stage.task_type for stage in RESOURCE_DAILY_STAGES
)

# Retired first-level identities.  The Scheduler catalogue migration removes
# these and refuses to readmit them.
RESOURCE_DAILY_RETIRED_TASK_IDS: frozenset[str] = RESOURCE_DAILY_STAGE_IDS
RESOURCE_DAILY_RETIRED_TASK_TYPES: frozenset[str] = RESOURCE_DAILY_STAGE_TYPES


def match_resource_daily_stage(item: Mapping[str, Any]) -> ResourceDailyStage | None:
    """Return the stage a persisted first-level instance belongs to, if any."""

    task_id = str(item.get("id") or "")
    task_type = str(item.get("task_type") or "")
    for stage in RESOURCE_DAILY_STAGES:
        if task_id == stage.task_id or task_type == stage.task_type:
            return stage
    return None


def next_resource_daily_time(now: datetime) -> str:
    """Return the canonical Job's next absolute 00:00 trigger after ``now``."""

    next_midnight = datetime.combine(now.date(), datetime.min.time()) + timedelta(days=1)
    return next_midnight.strftime("%Y-%m-%d %H:%M:%S")


def resource_daily_cycle_key(stage: ResourceDailyStage, moment: datetime) -> str:
    """Build the aggregate-progress cycle key for one proven stage completion."""

    if stage.weekly:
        monday = moment.date() - timedelta(days=moment.weekday())
        return f"week:{monday.isoformat()}"
    return moment.date().isoformat()


def _parse_finished_at(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def resource_daily_completion(
    stage: ResourceDailyStage,
    item: Mapping[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    """Return ``(cycle_key, aggregate_progress record)`` only on real evidence.

    A future ``next_time`` is never acceptance, and an ``error``/``interrupted``
    run is never a completion.  The retired instance must carry a terminal
    ``success`` *and* a recorded finish before any completion credential is
    minted; otherwise the caller keeps its history without one.
    """

    run_status = str(item.get("last_result") or "").strip().lower()
    finished_at = str(item.get("finished_at") or "").strip()
    finished = _parse_finished_at(finished_at)
    next_at = _parse_finished_at(item.get("next_time"))
    business_status = item.get("job_status")
    if business_status and business_status not in ("success", "complete", "completed"):
        return None
    # These six legacy components commit a future trigger only at their proven
    # business completion point. Cell success alone is insufficient evidence.
    if run_status != "success" or finished is None or next_at is None or next_at <= finished:
        return None
    record = {
        "stage_version": RESOURCE_DAILY_STAGE_VERSION,
        "status": "complete",
        "result": {
            "migrated": True,
            "from": str(item.get("id") or stage.task_id),
            "run_status": run_status,
            "completion_evidence": "success_with_committed_future_trigger",
            "next_time": item.get("next_time"),
            "finished_at": finished_at,
        },
    }
    return resource_daily_cycle_key(stage, finished), record


__all__ = [
    "RESOURCE_DAILY_DAILY_CADENCE",
    "RESOURCE_DAILY_INTERNALIZED_JOBS_KEY",
    "RESOURCE_DAILY_LABEL",
    "RESOURCE_DAILY_PAYLOAD_SCHEMA_KEY",
    "RESOURCE_DAILY_PROGRESS_KEY",
    "RESOURCE_DAILY_RETIRED_TASK_IDS",
    "RESOURCE_DAILY_RETIRED_TASK_TYPES",
    "RESOURCE_DAILY_SCHEMA_VERSION",
    "RESOURCE_DAILY_STAGES",
    "RESOURCE_DAILY_STAGE_IDS",
    "RESOURCE_DAILY_STAGE_TYPES",
    "RESOURCE_DAILY_STAGE_VERSION",
    "RESOURCE_DAILY_TASK_ID",
    "RESOURCE_DAILY_TASK_TYPE",
    "RESOURCE_DAILY_TRIGGER_DESCRIPTION",
    "RESOURCE_DAILY_WEEKLY_CADENCE",
    "ResourceDailyStage",
    "match_resource_daily_stage",
    "next_resource_daily_time",
    "resource_daily_completion",
    "resource_daily_cycle_key",
]
