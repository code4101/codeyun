from __future__ import annotations

"""Pure, observation-only lifecycle planning for Peak Race.

This module translates the public Peak Race family projection into stable
checkpoint identities.  It does not register a Scheduler Job, write state,
navigate UI, or perform any tournament action.
"""

import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping


OCCURRENCE_RECONCILE_KIND = "occurrence_reconcile"
STAGE_OPEN_KIND = "stage_open_observe"
STAGE_CLOSE_KIND = "stage_close_observe"
GUESS_POLICY_KIND = "guess_policy_observe"
OUTER_REWARD_KIND = "outer_reward_observe"
WORSHIP_OPEN_KIND = "worship_open_observe"
WORSHIP_CLOSE_KIND = "worship_close_observe"
LIFECYCLE_CLOSE_KIND = "lifecycle_close_audit"

_CHECKPOINT_ORDER = {
    OCCURRENCE_RECONCILE_KIND: 0,
    STAGE_OPEN_KIND: 10,
    GUESS_POLICY_KIND: 20,
    STAGE_CLOSE_KIND: 30,
    OUTER_REWARD_KIND: 40,
    WORSHIP_OPEN_KIND: 50,
    WORSHIP_CLOSE_KIND: 60,
    LIFECYCLE_CLOSE_KIND: 70,
}

_TC_PATTERN = re.compile(
    r"^TC\|(?P<weekday>[1-7])_(?P<hour>\d{1,2})\s+"
    r"(?P<minute>\d{1,2})\s+(?P<second>\d{1,2})$"
)


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _timestamp(value: Any) -> datetime | None:
    raw = _integer(value)
    if raw is None or raw <= 0:
        return None
    return datetime.fromtimestamp(raw / 1000).astimezone().replace(microsecond=0)


def resolve_tc_time(expression: Any, *, week_anchor: datetime) -> datetime | None:
    """Resolve a validated weekly ``TC`` expression around a Runtime anchor."""

    if week_anchor.tzinfo is None or week_anchor.utcoffset() is None:
        return None
    match = _TC_PATTERN.fullmatch(str(expression or "").strip())
    if match is None:
        return None
    weekday = int(match.group("weekday"))
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    second = int(match.group("second"))
    if hour > 23 or minute > 59 or second > 59:
        return None
    monday = (week_anchor - timedelta(days=week_anchor.isoweekday() - 1)).date()
    return datetime.combine(
        monday + timedelta(days=weekday - 1),
        datetime.min.time(),
        tzinfo=week_anchor.tzinfo,
    ).replace(hour=hour, minute=minute, second=second)


@dataclass(frozen=True)
class PeakRaceStageWindow:
    activity_id: int
    name: str
    round_index: int
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True)
class PeakRaceOccurrence:
    outer_activity_id: int
    runtime_id: str
    prepare_at: datetime
    start_at: datetime
    end_at: datetime
    close_at: datetime
    stages: tuple[PeakRaceStageWindow, ...]
    worship_activity_id: int | None = None
    worship_start_at: datetime | None = None
    worship_end_at: datetime | None = None

    @property
    def instance_key(self) -> str:
        # Runtime row ids may be refreshed while the configured tournament
        # interval remains the same.  Keep durable checkpoint identity tied to
        # the business occurrence, retaining runtime_id as evidence only.
        return (
            f"peakrace:{self.outer_activity_id}:"
            f"{self.start_at.isoformat(timespec='seconds')}:"
            f"{self.end_at.isoformat(timespec='seconds')}"
        )


@dataclass(frozen=True)
class PeakRaceCheckpoint:
    instance_key: str
    checkpoint_kind: str
    subject_activity_id: int
    business_date: str
    due_at: datetime
    observation_only: bool = True

    @property
    def key(self) -> tuple[str, str, int, str]:
        return (
            self.instance_key,
            self.checkpoint_kind,
            self.subject_activity_id,
            self.business_date,
        )

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["due_at"] = self.due_at.isoformat(timespec="seconds")
        return result


def _single_runtime_occurrence(descriptor: Mapping[str, Any]) -> Mapping[str, Any] | None:
    rows = [
        row
        for row in descriptor.get("runtimeOccurrences") or ()
        if isinstance(row, Mapping)
    ]
    return rows[0] if descriptor.get("runtimeObserved") is True and len(rows) == 1 else None


def discover_peakrace_occurrences(schedule: Mapping[str, Any]) -> tuple[PeakRaceOccurrence, ...]:
    """Discover unambiguous, fully configured outer occurrences only."""

    if schedule.get("peakraceScheduleComplete") is not True:
        return ()
    result: list[PeakRaceOccurrence] = []
    for family in schedule.get("peakraceSchedules") or ():
        if not isinstance(family, Mapping) or family.get("configurationComplete") is not True:
            continue
        outer = family.get("outer")
        if not isinstance(outer, Mapping):
            continue
        runtime = _single_runtime_occurrence(outer)
        outer_activity_id = _integer(family.get("outerActivityId"))
        runtime_id = str((runtime or {}).get("id") or "").strip()
        prepare_at = _timestamp((runtime or {}).get("prepareEndTime"))
        start_at = _timestamp((runtime or {}).get("startTime"))
        end_at = _timestamp((runtime or {}).get("endTime"))
        close_at = _timestamp((runtime or {}).get("closePanelTime"))
        if (
            runtime is None
            or outer_activity_id is None
            or not runtime_id
            or prepare_at is None
            or start_at is None
            or end_at is None
            or close_at is None
            or not (prepare_at <= start_at <= end_at <= close_at)
        ):
            continue

        stages: list[PeakRaceStageWindow] = []
        stage_valid = True
        for descriptor in family.get("stages") or ():
            if not isinstance(descriptor, Mapping):
                stage_valid = False
                break
            configured = descriptor.get("configuredTimes")
            activity_id = _integer(descriptor.get("activityId"))
            round_index = _integer(descriptor.get("roundIndex"))
            if not isinstance(configured, Mapping):
                stage_valid = False
                break
            stage_start = resolve_tc_time(configured.get("startTime"), week_anchor=start_at)
            stage_end = resolve_tc_time(configured.get("endTime"), week_anchor=start_at)
            if (
                activity_id is None
                or round_index is None
                or stage_start is None
                or stage_end is None
                or not (start_at <= stage_start < stage_end <= close_at)
            ):
                stage_valid = False
                break
            stages.append(
                PeakRaceStageWindow(
                    activity_id=activity_id,
                    name=str(descriptor.get("name") or "").strip(),
                    round_index=round_index,
                    start_at=stage_start,
                    end_at=stage_end,
                )
            )
        if not stage_valid or len(stages) != 6 or len({item.activity_id for item in stages}) != 6:
            continue

        worship_activity_id = None
        worship_start_at = None
        worship_end_at = None
        worship = family.get("worship")
        if isinstance(worship, Mapping):
            worship_runtime = _single_runtime_occurrence(worship)
            if worship_runtime is not None:
                candidate_id = _integer(worship.get("activityId"))
                candidate_start = _timestamp(worship_runtime.get("startTime"))
                candidate_end = _timestamp(worship_runtime.get("endTime"))
                if (
                    candidate_id is not None
                    and candidate_start is not None
                    and candidate_end is not None
                    and candidate_start < candidate_end
                ):
                    worship_activity_id = candidate_id
                    worship_start_at = candidate_start
                    worship_end_at = candidate_end

        result.append(
            PeakRaceOccurrence(
                outer_activity_id=outer_activity_id,
                runtime_id=runtime_id,
                prepare_at=prepare_at,
                start_at=start_at,
                end_at=end_at,
                close_at=close_at,
                stages=tuple(sorted(stages, key=lambda item: item.start_at)),
                worship_activity_id=worship_activity_id,
                worship_start_at=worship_start_at,
                worship_end_at=worship_end_at,
            )
        )
    return tuple(sorted(result, key=lambda item: (item.start_at, item.outer_activity_id)))


def checkpoints_for_peakrace(occurrence: PeakRaceOccurrence) -> tuple[PeakRaceCheckpoint, ...]:
    """Build observation-only checkpoints; no checkpoint authorizes an action."""

    rows: list[PeakRaceCheckpoint] = []

    def add(kind: str, subject: int, due_at: datetime) -> None:
        rows.append(
            PeakRaceCheckpoint(
                instance_key=occurrence.instance_key,
                checkpoint_kind=kind,
                subject_activity_id=subject,
                business_date=due_at.date().isoformat(),
                due_at=due_at,
            )
        )

    add(OCCURRENCE_RECONCILE_KIND, occurrence.outer_activity_id, occurrence.prepare_at)
    for stage in occurrence.stages:
        add(STAGE_OPEN_KIND, stage.activity_id, stage.start_at)
        add(GUESS_POLICY_KIND, stage.activity_id, stage.start_at)
        add(STAGE_CLOSE_KIND, stage.activity_id, stage.end_at)
    add(OUTER_REWARD_KIND, occurrence.outer_activity_id, occurrence.end_at)
    if (
        occurrence.worship_activity_id is not None
        and occurrence.worship_start_at is not None
        and occurrence.worship_end_at is not None
    ):
        add(WORSHIP_OPEN_KIND, occurrence.worship_activity_id, occurrence.worship_start_at)
        add(WORSHIP_CLOSE_KIND, occurrence.worship_activity_id, occurrence.worship_end_at)
    add(LIFECYCLE_CLOSE_KIND, occurrence.outer_activity_id, occurrence.close_at)
    return tuple(
        sorted(
            rows,
            key=lambda item: (
                item.due_at,
                _CHECKPOINT_ORDER[item.checkpoint_kind],
                item.subject_activity_id,
            ),
        )
    )


def due_peakrace_checkpoints(
    occurrences: Iterable[PeakRaceOccurrence],
    *,
    now: datetime,
    completed_keys: Iterable[tuple[str, str, int, str]] = (),
) -> tuple[PeakRaceCheckpoint, ...]:
    completed = set(completed_keys)
    return tuple(sorted(
        (
            checkpoint
        for occurrence in occurrences
        for checkpoint in checkpoints_for_peakrace(occurrence)
        if checkpoint.due_at <= now and checkpoint.key not in completed
        ),
        key=lambda item: (
            item.due_at,
            _CHECKPOINT_ORDER[item.checkpoint_kind],
            item.subject_activity_id,
        ),
    ))


def next_peakrace_lifecycle_time(
    occurrences: Iterable[PeakRaceOccurrence],
    *,
    now: datetime,
    completed_keys: Iterable[tuple[str, str, int, str]] = (),
) -> datetime | None:
    completed = set(completed_keys)
    candidates = [
        checkpoint.due_at
        for occurrence in occurrences
        for checkpoint in checkpoints_for_peakrace(occurrence)
        if checkpoint.due_at > now and checkpoint.key not in completed
    ]
    return min(candidates) if candidates else None


__all__ = [
    "PeakRaceCheckpoint",
    "PeakRaceOccurrence",
    "PeakRaceStageWindow",
    "checkpoints_for_peakrace",
    "discover_peakrace_occurrences",
    "due_peakrace_checkpoints",
    "next_peakrace_lifecycle_time",
    "resolve_tc_time",
]
