from __future__ import annotations

"""Pure business-state projection for the weekly Peak Race tournament.

The projector combines normalized activity schedule rows, the strictly
read-only PeakraceMgr snapshot, optional generic rank snapshots, and the three
currently known UI assets (#368 rank, #369 guess, #370 irreversible confirm).
It never reads Runtime directly and never performs a game action.
"""

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Mapping


PEAKRACE_OUTER_BASE_ID = 170000
PEAKRACE_TOTAL_RANK_BASE_ID = 170001
PEAKRACE_WORSHIP_BASE_ID = 170002
PEAKRACE_OUTER_ACTIVITY_TYPE = 79
PEAKRACE_STAGE_ACTIVITY_TYPE = 80
PEAKRACE_WORSHIP_ACTIVITY_TYPE = 81

PEAKRACE_UI_SURFACES = {
    368: "rank",
    369: "guess",
    370: "guess_confirmation",
}


@dataclass(frozen=True)
class PeakRaceStageDefinition:
    activity_id: int
    name: str
    resource_kind: str
    base_id: int
    round: int
    iso_weekday: int


@dataclass(frozen=True)
class PeakRaceVariantDefinition:
    outer_activity_id: int
    total_rank_activity_id: int
    worship_activity_id: int
    stages: tuple[PeakRaceStageDefinition, ...]

    @property
    def variant(self) -> str:
        return str(self.outer_activity_id)


PEAKRACE_VARIANTS = {
    32620001: PeakRaceVariantDefinition(
        outer_activity_id=32620001,
        total_rank_activity_id=623001,
        worship_activity_id=32620002,
        stages=(
            PeakRaceStageDefinition(1620100, "炼体巅峰", "lianti", 43000, 1, 2),
            PeakRaceStageDefinition(1620200, "丹道巅峰", "dandao", 43100, 1, 3),
            PeakRaceStageDefinition(1620400, "灵宠巅峰", "lingchong", 42900, 1, 4),
            PeakRaceStageDefinition(2620100, "炼体巅峰", "lianti", 43000, 2, 5),
            PeakRaceStageDefinition(2620200, "丹道巅峰", "dandao", 43100, 2, 6),
            PeakRaceStageDefinition(2620400, "灵宠巅峰", "lingchong", 42900, 2, 7),
        ),
    ),
    32630001: PeakRaceVariantDefinition(
        outer_activity_id=32630001,
        total_rank_activity_id=624001,
        worship_activity_id=32630002,
        stages=(
            PeakRaceStageDefinition(1630100, "洗灵巅峰", "xiling", 43800, 1, 2),
            PeakRaceStageDefinition(1630200, "灵装巅峰", "lingzhuang", 44300, 1, 3),
            PeakRaceStageDefinition(1630400, "花会巅峰", "huahui", 42800, 1, 4),
            PeakRaceStageDefinition(2630100, "洗灵巅峰", "xiling", 43800, 2, 5),
            PeakRaceStageDefinition(2630200, "灵装巅峰", "lingzhuang", 44300, 2, 6),
            PeakRaceStageDefinition(2630400, "花会巅峰", "huahui", 42800, 2, 7),
        ),
    ),
}


def _integer(value: Any) -> int | None:
    try:
        if isinstance(value, bool) or value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _timestamp(value: Any) -> datetime | None:
    raw = _integer(value)
    if raw is None or raw <= 0:
        return None
    return datetime.fromtimestamp(raw / 1000).astimezone().replace(microsecond=0)


def _row_phase(row: Mapping[str, Any], now: datetime) -> str:
    start_at = _timestamp(row.get("startTime"))
    end_at = _timestamp(row.get("endTime"))
    close_at = _timestamp(row.get("closePanelTime")) or end_at
    if start_at is None or end_at is None:
        return "unknown"
    if now < start_at:
        return "prepare"
    if now <= end_at:
        return "open"
    if close_at is not None and now <= close_at:
        return "reward"
    return "closed"


def _schedule_rows(schedule: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw_items = schedule.get("items")
    if raw_items is not None:
        return tuple(row for row in raw_items or () if isinstance(row, Mapping))

    # The persisted public schedule contract stores normalized ``occurrences``
    # and retains the exact Runtime row under ``raw``.  Accept that authoritative
    # shape directly; ``items`` remains supported for callers holding an
    # in-memory Runtime snapshot.
    return tuple(
        raw
        for occurrence in schedule.get("occurrences") or ()
        if isinstance(occurrence, Mapping)
        and isinstance((raw := occurrence.get("raw")), Mapping)
    )


def _schedule_complete(schedule: Mapping[str, Any]) -> bool:
    explicit = schedule.get("complete")
    if explicit is not None:
        return bool(explicit)

    # Versioned normalized snapshots do not repeat a ``complete`` flag.  Their
    # capture is complete when the declared Runtime count, observed source
    # count, and persisted occurrence count agree.  Identity completeness is
    # checked by the Peak Race row selection itself rather than rejecting the
    # whole world-line snapshot for an unrelated unresolved activity.
    if _integer(schedule.get("version")) is None:
        return False
    evidence = schedule.get("source_evidence")
    if not isinstance(evidence, Mapping):
        return False
    declared = _integer(evidence.get("declared_count"))
    observed = _integer(evidence.get("count"))
    persisted = _integer(schedule.get("occurrence_count"))
    occurrences = schedule.get("occurrences")
    return bool(
        isinstance(occurrences, list)
        and declared is not None
        and observed == declared
        and persisted == declared
        and len(occurrences) == declared
    )


def _matches_identity(
    row: Mapping[str, Any],
    *,
    activity_ids: set[int],
    base_id: int,
    activity_type: int,
) -> bool:
    activity_id = _integer(row.get("activityId"))
    return bool(
        activity_id in activity_ids
        or _integer(row.get("baseId")) == base_id
        or _integer(row.get("activityType")) == activity_type
    )


def _select_relevant_row(
    rows: tuple[Mapping[str, Any], ...],
    *,
    now: datetime,
) -> tuple[Mapping[str, Any] | None, bool]:
    relevant = [row for row in rows if _row_phase(row, now) != "closed"]
    if len(relevant) == 1:
        return relevant[0], False
    if len(relevant) > 1:
        return None, True
    if len(rows) == 1:
        return rows[0], False
    return None, len(rows) > 1


def _rank_summary(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    if not snapshot:
        return {
            "loaded": False,
            "complete": False,
            "rank": None,
            "score": None,
            "rank_list_size": None,
        }
    self_ranking = snapshot.get("self_ranking")
    self_ranking = self_ranking if isinstance(self_ranking, Mapping) else {}
    return {
        "loaded": bool(snapshot.get("available")),
        "complete": bool(snapshot.get("complete")),
        "rank": _integer(self_ranking.get("rank")),
        "score": _integer(self_ranking.get("score")),
        "rank_list_size": _integer(snapshot.get("rank_list_size")),
    }


def _activity_group_map(snapshot: Mapping[str, Any]) -> dict[int, int]:
    result: dict[int, int] = {}
    for row in snapshot.get("activity_groups") or ():
        if not isinstance(row, Mapping):
            continue
        activity_id = _integer(row.get("activity_id"))
        group = _integer(row.get("group"))
        if activity_id is not None and group is not None:
            result[activity_id] = group
    return result


def _guess_map(snapshot: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    result: dict[int, Mapping[str, Any]] = {}
    for row in snapshot.get("guesses") or ():
        if not isinstance(row, Mapping):
            continue
        activity_id = _integer(row.get("activity_id"))
        if activity_id is not None:
            result[activity_id] = row
    return result


def _current_stage(
    variant: PeakRaceVariantDefinition,
    rows: tuple[Mapping[str, Any], ...],
    *,
    now: datetime,
) -> tuple[PeakRaceStageDefinition | None, Mapping[str, Any] | None, bool]:
    definitions = {stage.activity_id: stage for stage in variant.stages}
    stage_rows = tuple(
        row
        for row in rows
        if _integer(row.get("activityId")) in definitions
        or _integer(row.get("activityType")) == PEAKRACE_STAGE_ACTIVITY_TYPE
    )
    open_rows = [row for row in stage_rows if _row_phase(row, now) == "open"]
    if len(open_rows) > 1:
        return None, None, True
    if len(open_rows) == 1:
        row = open_rows[0]
        return definitions.get(_integer(row.get("activityId"))), row, False
    expected = next(
        (stage for stage in variant.stages if stage.iso_weekday == now.isoweekday()),
        None,
    )
    expected_row = next(
        (
            row
            for row in stage_rows
            if _integer(row.get("activityId"))
            == (expected.activity_id if expected is not None else None)
        ),
        None,
    )
    return expected, expected_row, False


def project_peakrace_business_state(
    schedule: Mapping[str, Any],
    peakrace_snapshot: Mapping[str, Any],
    *,
    now: datetime,
    rank_snapshots: Mapping[int, Mapping[str, Any]] | None = None,
    guess_limits: Mapping[int, int] | None = None,
    guess_required: bool | None = None,
    guess_deadlines: Mapping[int, datetime] | None = None,
    worship_daily_limit: int | None = None,
    reward_claimed: bool | None = None,
    ui_scene_id: int | None = None,
) -> dict[str, Any]:
    """Project the current Peak Race lifecycle without performing actions.

    ``reward_claimed`` and the two limits are explicit supplemental facts.
    Unknown values remain unknown rather than being guessed from screenshots.

    :param Mapping schedule: Normalized world-line activity schedule snapshot.
    :param Mapping peakrace_snapshot: Result of ``read_peakrace_runtime_snapshot``.
    :param datetime now: Business time used to select the current phase.
    :return dict: Tournament identity, phase, stage, safety, and completion facts.
    """

    rank_snapshots = rank_snapshots or {}
    guess_limits = guess_limits or {}
    guess_deadlines = guess_deadlines or {}
    rows = _schedule_rows(schedule)
    outer_rows = tuple(
        row
        for row in rows
        if _matches_identity(
            row,
            activity_ids=set(PEAKRACE_VARIANTS),
            base_id=PEAKRACE_OUTER_BASE_ID,
            activity_type=PEAKRACE_OUTER_ACTIVITY_TYPE,
        )
    )
    outer, outer_conflict = _select_relevant_row(outer_rows, now=now)
    outer_activity_id = _integer((outer or {}).get("activityId"))
    variant = PEAKRACE_VARIANTS.get(outer_activity_id or 0)
    blockers: list[str] = []
    if not _schedule_complete(schedule):
        blockers.append("activity_schedule_incomplete")
    if outer_conflict:
        blockers.append("multiple_peakrace_occurrences")
    if outer is None:
        blockers.append("peakrace_occurrence_missing")
    if outer is not None and variant is None:
        blockers.append("unknown_peakrace_variant")

    worship_rows = tuple(
        row
        for row in rows
        if _matches_identity(
            row,
            activity_ids={item.worship_activity_id for item in PEAKRACE_VARIANTS.values()},
            base_id=PEAKRACE_WORSHIP_BASE_ID,
            activity_type=PEAKRACE_WORSHIP_ACTIVITY_TYPE,
        )
    )
    worship_row, worship_conflict = _select_relevant_row(worship_rows, now=now)
    if worship_conflict:
        blockers.append("multiple_peakrace_worship_occurrences")

    outer_phase = _row_phase(outer, now) if outer is not None else "unknown"
    worship_phase = (
        _row_phase(worship_row, now) if worship_row is not None else "absent"
    )
    if worship_phase == "open" and outer_phase in {"closed", "unknown"}:
        phase = "worship"
    else:
        phase = outer_phase

    stage_definition = None
    stage_row = None
    stage_conflict = False
    if variant is not None:
        stage_definition, stage_row, stage_conflict = _current_stage(
            variant,
            rows,
            now=now,
        )
    if stage_conflict:
        blockers.append("multiple_open_peakrace_stages")

    snapshot_complete = bool(peakrace_snapshot.get("complete"))
    if phase == "open" and not snapshot_complete:
        blockers.append("peakrace_self_data_incomplete")
    current_round = _integer(peakrace_snapshot.get("current_round"))
    if (
        current_round is not None
        and stage_definition is not None
        and current_round != stage_definition.round
    ):
        blockers.append("runtime_round_mismatch")

    stage_activity_id = (
        stage_definition.activity_id if stage_definition is not None else None
    )
    group_map = _activity_group_map(peakrace_snapshot)
    current_group = group_map.get(stage_activity_id or 0)
    if not snapshot_complete or stage_activity_id is None:
        qualification = "unknown"
    elif current_group is not None and current_group > 0:
        qualification = "qualified"
    else:
        qualification = "not_qualified"

    stage_rank = _rank_summary(rank_snapshots.get(stage_activity_id or 0))
    total_rank = _rank_summary(
        rank_snapshots.get(
            variant.total_rank_activity_id if variant is not None else 0
        )
    )
    if phase == "open" and stage_activity_id is None:
        blockers.append("current_stage_unresolved")
    stage_schedule_phase = (
        _row_phase(stage_row, now) if stage_row is not None else "unloaded"
    )
    if phase == "open" and stage_row is None:
        blockers.append("current_stage_runtime_unobserved")
    elif phase == "open" and stage_schedule_phase != "open":
        blockers.append("current_stage_runtime_not_open")
    if phase == "open" and qualification == "qualified" and not stage_rank["complete"]:
        blockers.append("current_stage_rank_incomplete")
    if phase == "open" and not total_rank["complete"]:
        blockers.append("total_rank_incomplete")

    guess = _guess_map(peakrace_snapshot).get(stage_activity_id or 0, {})
    role_ids = [
        role_id
        for value in guess.get("role_ids") or ()
        if (role_id := _integer(value)) is not None
    ]
    guess_group = _integer(guess.get("group"))
    guess_limit = _integer(guess_limits.get(stage_activity_id or 0))
    guess_deadline = guess_deadlines.get(stage_activity_id or 0)
    if phase != "open":
        guess_status = "not_applicable"
        guess_complete: bool | None = True
    elif guess_required is False:
        guess_status = "skipped_by_explicit_policy"
        guess_complete = True
    elif guess_required is None:
        guess_status = "policy_unknown"
        guess_complete = None
        blockers.append("guess_policy_unknown")
    elif guess_limit is None:
        guess_status = "limit_unknown"
        guess_complete = None
        blockers.append("guess_limit_unknown")
    elif len(role_ids) >= guess_limit:
        guess_status = "immutable_selection_complete"
        guess_complete = True
    elif guess_deadline is not None and now > guess_deadline:
        guess_status = "expired_incomplete"
        guess_complete = False
        blockers.append("guess_window_expired")
    else:
        guess_status = "manual_required"
        guess_complete = False
        blockers.append("guess_selection_requires_explicit_policy")

    worship_times = _integer(peakrace_snapshot.get("worship_daily_times"))
    if phase != "worship":
        worship_status = "not_applicable"
        worship_complete: bool | None = True
    elif worship_times is None or worship_daily_limit is None:
        worship_status = "unknown"
        worship_complete = None
        blockers.append("worship_completion_unknown")
    elif worship_times >= worship_daily_limit:
        worship_status = "complete"
        worship_complete = True
    else:
        worship_status = "action_required_unsupported"
        worship_complete = False
        blockers.append("worship_action_not_implemented")

    if phase != "reward":
        reward_status = "not_applicable"
        reward_complete: bool | None = True
    elif reward_claimed is True:
        reward_status = "complete"
        reward_complete = True
    elif reward_claimed is False:
        reward_status = "action_required_unsupported"
        reward_complete = False
        blockers.append("reward_claim_action_not_implemented")
    else:
        reward_status = "unknown"
        reward_complete = None
        blockers.append("reward_claim_status_unknown")

    base_observation_complete = bool(
        _schedule_complete(schedule)
        and
        outer is not None
        and variant is not None
        and not outer_conflict
        and not worship_conflict
        and not stage_conflict
        and not (
            current_round is not None
            and stage_definition is not None
            and current_round != stage_definition.round
        )
    )
    observation_complete = bool(
        base_observation_complete
        and (
            phase != "open"
            or (
                snapshot_complete
                and stage_activity_id is not None
                and stage_row is not None
                and stage_schedule_phase == "open"
                and qualification != "unknown"
                and (qualification != "qualified" or stage_rank["complete"])
                and total_rank["complete"]
            )
        )
    )
    if phase == "prepare":
        current_phase_complete: bool | None = observation_complete
    elif phase == "open":
        if not observation_complete:
            current_phase_complete = False
        elif guess_complete is None:
            current_phase_complete = None
        else:
            current_phase_complete = guess_complete is True
    elif phase == "reward":
        current_phase_complete = reward_complete
    elif phase == "worship":
        current_phase_complete = worship_complete
    elif phase == "closed":
        current_phase_complete = True
    else:
        current_phase_complete = None

    ui_surface = PEAKRACE_UI_SURFACES.get(ui_scene_id)
    if ui_surface == "guess_confirmation":
        blockers.append("irreversible_guess_confirmation_visible")

    return {
        "status": (
            "complete"
            if current_phase_complete is True
            else "incomplete"
            if current_phase_complete is False
            else "unknown"
        ),
        "phase": phase,
        "outer_phase": outer_phase,
        "worship_phase": worship_phase,
        "variant": variant.variant if variant is not None else None,
        "outer_activity_id": outer_activity_id,
        "runtime_id": str((outer or {}).get("id") or "") or None,
        "current_round": current_round,
        "expected_round": (
            stage_definition.round if stage_definition is not None else None
        ),
        "qualification": qualification,
        "current_group": current_group,
        "self_rank": _integer(peakrace_snapshot.get("self_rank")),
        "stage": (
            {
                **asdict(stage_definition),
                "schedule_phase": stage_schedule_phase,
                "rank": stage_rank,
            }
            if stage_definition is not None
            else None
        ),
        "total_rank_activity_id": (
            variant.total_rank_activity_id if variant is not None else None
        ),
        "total_rank": total_rank,
        "guess": {
            "status": guess_status,
            "complete": guess_complete,
            "limit": guess_limit,
            "required": guess_required,
            "deadline_at": (
                guess_deadline.isoformat(timespec="seconds")
                if guess_deadline is not None
                else None
            ),
            "selection_count": len(role_ids),
            "role_ids": role_ids,
            "locked_group": guess_group,
            "immutable_after_confirmation": True,
            "automation_allowed": False,
        },
        "reward": {
            "status": reward_status,
            "complete": reward_complete,
            "claimed": reward_claimed,
            "automation_allowed": False,
        },
        "worship": {
            "status": worship_status,
            "complete": worship_complete,
            "daily_times": worship_times,
            "daily_limit": worship_daily_limit,
            "automation_allowed": False,
        },
        "ui": {
            "scene_id": ui_scene_id,
            "surface": ui_surface,
            "known_assets": dict(PEAKRACE_UI_SURFACES),
            "safe_to_act": False,
        },
        "completion": {
            "observation_complete": observation_complete,
            "current_phase_complete": current_phase_complete,
            # Historical reward/worship checkpoints are not yet persisted, so
            # a closed page alone cannot prove the whole tournament lifecycle.
            "lifecycle_complete": None,
        },
        "blockers": sorted(set(blockers)),
    }


__all__ = [
    "PEAKRACE_UI_SURFACES",
    "PEAKRACE_VARIANTS",
    "PeakRaceStageDefinition",
    "PeakRaceVariantDefinition",
    "project_peakrace_business_state",
]
