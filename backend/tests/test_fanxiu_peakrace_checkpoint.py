from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from backend.core.fanxiu.activity.peakrace_checkpoint import evaluate_peakrace_checkpoint
from backend.core.fanxiu.activity.peakrace_lifecycle import (
    GUESS_POLICY_KIND,
    LIFECYCLE_CLOSE_KIND,
    OCCURRENCE_RECONCILE_KIND,
    STAGE_CLOSE_KIND,
    STAGE_OPEN_KIND,
    PeakRaceOccurrence,
    PeakRaceStageWindow,
    checkpoints_for_peakrace,
)


TZ = ZoneInfo("Asia/Shanghai")


def _ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _fixture():
    start = datetime(2026, 9, 1, 5, 0, 5, tzinfo=TZ)
    end = datetime(2026, 9, 1, 22, 0, tzinfo=TZ)
    stage = PeakRaceStageWindow(1620100, "炼体", 1, start, end)
    other = tuple(
        PeakRaceStageWindow(
            1620100 + index,
            f"stage-{index}",
            1 if index < 3 else 2,
            start.replace(day=1 + index),
            end.replace(day=1 + index),
        )
        for index in range(1, 6)
    )
    occurrence = PeakRaceOccurrence(
        outer_activity_id=32620001,
        runtime_id="32620001400004",
        prepare_at=datetime(2026, 8, 31, 0, tzinfo=TZ),
        start_at=datetime(2026, 9, 1, 5, tzinfo=TZ),
        end_at=datetime(2026, 9, 6, 22, 0, 5, tzinfo=TZ),
        close_at=datetime(2026, 9, 6, 23, 59, 59, tzinfo=TZ),
        stages=(stage, *other),
    )
    outer_row = {
        "id": int(occurrence.runtime_id),
        "startTime": _ms(occurrence.start_at),
        "endTime": _ms(occurrence.end_at),
    }
    stage_row = {"id": 1, "startTime": _ms(start), "endTime": _ms(end)}
    schedule = {
        "peakraceScheduleComplete": True,
        "peakraceSchedules": [{
            "outerActivityId": occurrence.outer_activity_id,
            "configurationComplete": True,
            "outer": {
                "activityId": occurrence.outer_activity_id,
                "runtimeObserved": True,
                "runtimeOccurrences": [outer_row],
            },
            "stages": [{
                "activityId": stage.activity_id,
                "runtimeObserved": True,
                "runtimeOccurrences": [stage_row],
            }],
            "worship": None,
        }],
    }
    state = {
        "phase": "open",
        "outer_phase": "open",
        "variant": "32620001",
        "current_round": 1,
        "expected_round": 1,
        "qualification": "qualified",
        "current_group": 1,
        "self_rank": 2,
        "stage": {"activity_id": stage.activity_id, "schedule_phase": "open"},
        "total_rank": {"complete": True},
        "guess": {"required": None, "status": "policy_unknown"},
        "completion": {"observation_complete": True},
        "blockers": ["guess_policy_unknown"],
    }
    business = {
        "state": state,
        "sources": {"identity_coherent": True},
    }
    return occurrence, schedule, business


def _checkpoint(occurrence, kind):
    return next(item for item in checkpoints_for_peakrace(occurrence) if item.checkpoint_kind == kind)


def test_occurrence_and_stage_open_have_independent_observed_predicates():
    occurrence, schedule, business = _fixture()
    occurrence_result = evaluate_peakrace_checkpoint(
        _checkpoint(occurrence, OCCURRENCE_RECONCILE_KIND),
        occurrence,
        schedule,
        business,
        now=datetime(2026, 9, 1, 6, tzinfo=TZ),
    )
    stage_result = evaluate_peakrace_checkpoint(
        _checkpoint(occurrence, STAGE_OPEN_KIND),
        occurrence,
        schedule,
        business,
        now=datetime(2026, 9, 1, 6, tzinfo=TZ),
    )

    assert occurrence_result.terminal is True
    assert stage_result.terminal is True
    assert stage_result.reason == "stage_open_observed"


def test_occurrence_reconcile_accepts_runtime_row_id_refresh_for_same_interval():
    occurrence, schedule, business = _fixture()
    schedule["peakraceSchedules"][0]["outer"]["runtimeOccurrences"][0]["id"] = 999
    result = evaluate_peakrace_checkpoint(
        _checkpoint(occurrence, OCCURRENCE_RECONCILE_KIND),
        occurrence,
        schedule,
        business,
        now=datetime(2026, 9, 1, 6, tzinfo=TZ),
    )

    assert result.status == "observed"
    assert result.facts["current_runtime_id"] == "999"


def test_stage_open_blocks_when_child_is_not_runtime_observed():
    occurrence, schedule, business = _fixture()
    schedule["peakraceSchedules"][0]["stages"][0]["runtimeObserved"] = False
    schedule["peakraceSchedules"][0]["stages"][0]["runtimeOccurrences"] = []

    result = evaluate_peakrace_checkpoint(
        _checkpoint(occurrence, STAGE_OPEN_KIND), occurrence, schedule, business,
        now=datetime(2026, 9, 1, 6, tzinfo=TZ),
    )
    assert result.status == "blocked"
    assert result.reason == "stage_runtime_occurrence_unobserved"
    assert result.retryable is True


def test_guess_policy_unknown_never_becomes_observed():
    occurrence, schedule, business = _fixture()
    result = evaluate_peakrace_checkpoint(
        _checkpoint(occurrence, GUESS_POLICY_KIND), occurrence, schedule, business,
        now=datetime(2026, 9, 1, 6, tzinfo=TZ),
    )
    assert result.status == "blocked"
    assert result.reason == "guess_policy_not_authoritative"


def test_stage_close_requires_actual_runtime_row_and_boundary():
    occurrence, schedule, business = _fixture()
    checkpoint = _checkpoint(occurrence, STAGE_CLOSE_KIND)
    before = evaluate_peakrace_checkpoint(
        checkpoint, occurrence, schedule, business,
        now=datetime(2026, 9, 1, 21, tzinfo=TZ),
    )
    after = evaluate_peakrace_checkpoint(
        checkpoint, occurrence, schedule, business,
        now=datetime(2026, 9, 1, 22, tzinfo=TZ),
    )
    assert before.status == "blocked"
    assert after.status == "observed"


def test_lifecycle_close_requires_every_prior_checkpoint_key():
    occurrence, schedule, business = _fixture()
    close = _checkpoint(occurrence, LIFECYCLE_CLOSE_KIND)
    all_prior = {
        item.key for item in checkpoints_for_peakrace(occurrence) if item.key != close.key
    }
    incomplete = evaluate_peakrace_checkpoint(
        close, occurrence, schedule, business,
        now=datetime(2026, 9, 7, 0, tzinfo=TZ),
    )
    complete = evaluate_peakrace_checkpoint(
        close, occurrence, schedule, business,
        now=datetime(2026, 9, 7, 0, tzinfo=TZ), observed_keys=all_prior,
    )
    assert incomplete.status == "blocked"
    assert complete.status == "observed"
