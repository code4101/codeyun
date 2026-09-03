from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from backend.core.fanxiu.activity.peakrace_state import (
    project_peakrace_business_state,
)


TZ = ZoneInfo("Asia/Shanghai")


def _ms(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def _outer(*, end: str = "2026-09-06T22:00:05+08:00") -> dict:
    return {
        "id": 32630001000001,
        "activityId": 32630001,
        "activityType": 79,
        "baseId": 170000,
        "prepareEndTime": _ms("2026-08-31T00:00:00+08:00"),
        "startTime": _ms("2026-09-01T05:00:00+08:00"),
        "endTime": _ms(end),
        "closePanelTime": _ms("2026-09-06T23:59:59+08:00"),
    }


def _stage() -> dict:
    return {
        "id": 1630100000001,
        "activityId": 1630100,
        "activityType": 80,
        "baseId": 43800,
        "startTime": _ms("2026-09-01T05:00:05+08:00"),
        "endTime": _ms("2026-09-01T22:00:00+08:00"),
        "closePanelTime": _ms("2026-09-01T23:59:50+08:00"),
    }


def _schedule(*rows: dict) -> dict:
    return {"complete": True, "items": list(rows)}


def _normalized_schedule(*rows: dict) -> dict:
    occurrences = [
        {
            "key": f"{row['activityId']}|{row['startTime']}",
            "activity_id": row["activityId"],
            "activity_type": row["activityType"],
            "base_id": row["baseId"],
            "identity_complete": True,
            "raw": row,
        }
        for row in rows
    ]
    return {
        "version": 1,
        "source_kind": "worldline_activity_runtime_memory",
        "source_evidence": {
            "count": len(occurrences),
            "declared_count": len(occurrences),
        },
        "occurrence_count": len(occurrences),
        "occurrences": occurrences,
    }


def _peakrace(**overrides) -> dict:
    result = {
        "complete": True,
        "self_rank": 20,
        "current_round": 1,
        "activity_groups": [],
        "guesses": [],
        "worship_daily_times": 0,
    }
    result.update(overrides)
    return result


def _rank(activity_id: int, *, rank: int = 20, score: int = 100) -> dict:
    return {
        "complete": True,
        "available": True,
        "rank_activity_id": activity_id,
        "rank_list_size": 64,
        "self_ranking": {"rank": rank, "score": score},
    }


def test_prepare_phase_needs_only_stable_occurrence_identity() -> None:
    state = project_peakrace_business_state(
        _schedule(_outer()),
        {"complete": False},
        now=datetime(2026, 8, 31, 12, tzinfo=TZ),
    )

    assert state["phase"] == "prepare"
    assert state["variant"] == "32630001"
    assert state["completion"]["observation_complete"] is True
    assert state["completion"]["current_phase_complete"] is True
    assert "peakrace_self_data_incomplete" not in state["blockers"]


def test_normalized_persisted_schedule_contract_projects_peakrace() -> None:
    outer = _outer()
    outer["activityId"] = 32620001
    state = project_peakrace_business_state(
        _normalized_schedule(outer),
        {"complete": False},
        now=datetime(2026, 9, 1, 2, tzinfo=TZ),
    )

    assert state["phase"] == "prepare"
    assert state["variant"] == "32620001"
    assert state["runtime_id"] == str(outer["id"])
    assert state["completion"]["observation_complete"] is True
    assert "activity_schedule_incomplete" not in state["blockers"]


def test_open_qualified_round_requires_loaded_child_rank() -> None:
    rank_snapshot = _rank(1630100, rank=8, score=12345)
    state = project_peakrace_business_state(
        _schedule(_outer(), _stage()),
        _peakrace(activity_groups=[{"activity_id": 1630100, "group": 2}]),
        now=datetime(2026, 9, 1, 10, tzinfo=TZ),
        rank_snapshots={1630100: rank_snapshot, 624001: _rank(624001)},
        guess_required=False,
        ui_scene_id=368,
    )

    assert state["phase"] == "open"
    assert state["current_round"] == 1
    assert state["expected_round"] == 1
    assert state["qualification"] == "qualified"
    assert state["current_group"] == 2
    assert state["stage"]["activity_id"] == 1630100
    assert state["stage"]["resource_kind"] == "xiling"
    assert state["stage"]["rank"]["rank"] == 8
    assert state["guess"]["status"] == "skipped_by_explicit_policy"
    assert state["completion"]["current_phase_complete"] is True
    assert state["ui"]["surface"] == "rank"
    assert state["ui"]["safe_to_act"] is False


def test_open_nonqualified_player_requires_explicit_guess_policy() -> None:
    state = project_peakrace_business_state(
        _schedule(_outer(), _stage()),
        _peakrace(),
        now=datetime(2026, 9, 1, 10, tzinfo=TZ),
        guess_limits={1630100: 4},
        guess_required=True,
        ui_scene_id=369,
        rank_snapshots={624001: _rank(624001)},
    )

    assert state["qualification"] == "not_qualified"
    assert state["guess"]["status"] == "manual_required"
    assert state["guess"]["complete"] is False
    assert state["guess"]["automation_allowed"] is False
    assert "guess_selection_requires_explicit_policy" in state["blockers"]
    assert state["completion"]["current_phase_complete"] is False


def test_confirmed_guess_targets_are_projected_as_immutable_complete_selection() -> None:
    state = project_peakrace_business_state(
        _schedule(_outer(), _stage()),
        _peakrace(
            guesses=[
                {
                    "activity_id": 1630100,
                    "group": 3,
                    "role_ids": [11, 22, 33, 44],
                }
            ]
        ),
        now=datetime(2026, 9, 1, 10, tzinfo=TZ),
        guess_limits={1630100: 4},
        guess_required=True,
        rank_snapshots={624001: _rank(624001)},
    )

    assert state["guess"]["status"] == "immutable_selection_complete"
    assert state["guess"]["complete"] is True
    assert state["guess"]["selection_count"] == 4
    assert state["guess"]["locked_group"] == 3
    assert state["completion"]["current_phase_complete"] is True


def test_guess_confirmation_asset_is_always_fail_closed() -> None:
    state = project_peakrace_business_state(
        _schedule(_outer(), _stage()),
        _peakrace(),
        now=datetime(2026, 9, 1, 10, tzinfo=TZ),
        guess_limits={1630100: 4},
        guess_required=True,
        ui_scene_id=370,
        rank_snapshots={624001: _rank(624001)},
    )

    assert state["ui"]["surface"] == "guess_confirmation"
    assert state["ui"]["safe_to_act"] is False
    assert "irreversible_guess_confirmation_visible" in state["blockers"]


def test_required_guess_after_explicit_deadline_is_expired_incomplete() -> None:
    state = project_peakrace_business_state(
        _schedule(_outer(), _stage()),
        _peakrace(),
        now=datetime(2026, 9, 1, 21, 1, tzinfo=TZ),
        guess_limits={1630100: 4},
        guess_required=True,
        rank_snapshots={624001: _rank(624001)},
        guess_deadlines={
            1630100: datetime(2026, 9, 1, 21, 0, tzinfo=TZ),
        },
    )

    assert state["guess"]["status"] == "expired_incomplete"
    assert state["guess"]["complete"] is False
    assert "guess_window_expired" in state["blockers"]


def test_reward_phase_is_not_complete_without_explicit_claim_fact() -> None:
    state = project_peakrace_business_state(
        _schedule(_outer(end="2026-09-01T22:00:05+08:00")),
        _peakrace(),
        now=datetime(2026, 9, 1, 22, 30, tzinfo=TZ),
        rank_snapshots={624001: _rank(624001, rank=10, score=500)},
    )

    assert state["phase"] == "reward"
    assert state["total_rank"]["complete"] is True
    assert state["reward"]["status"] == "unknown"
    assert state["completion"]["current_phase_complete"] is None
    assert "reward_claim_status_unknown" in state["blockers"]


def test_worship_phase_uses_explicit_daily_limit_without_enabling_action() -> None:
    worship = {
        "id": 3263000200001,
        "activityId": 32630002,
        "activityType": 81,
        "baseId": 170002,
        "startTime": _ms("2026-09-07T05:00:05+08:00"),
        "endTime": _ms("2026-09-09T23:59:50+08:00"),
        "closePanelTime": _ms("2026-09-09T23:59:59+08:00"),
    }
    state = project_peakrace_business_state(
        _schedule(_outer(), worship),
        _peakrace(worship_daily_times=1),
        now=datetime(2026, 9, 7, 6, tzinfo=TZ),
        worship_daily_limit=1,
    )

    assert state["phase"] == "worship"
    assert state["worship"]["status"] == "complete"
    assert state["worship"]["automation_allowed"] is False
    assert state["completion"]["current_phase_complete"] is True


def test_overlapping_outer_variants_fail_closed() -> None:
    other = dict(_outer())
    other.update(activityId=32620001, id=3262000100001)

    state = project_peakrace_business_state(
        _schedule(_outer(), other),
        _peakrace(),
        now=datetime(2026, 9, 1, 10, tzinfo=TZ),
    )

    assert state["phase"] == "unknown"
    assert state["completion"]["observation_complete"] is False
    assert "multiple_peakrace_occurrences" in state["blockers"]


def test_closed_page_does_not_claim_historical_lifecycle_completion() -> None:
    state = project_peakrace_business_state(
        _schedule(_outer()),
        _peakrace(),
        now=datetime(2026, 9, 10, 10, tzinfo=TZ),
    )

    assert state["phase"] == "closed"
    assert state["completion"]["current_phase_complete"] is True
    assert state["completion"]["lifecycle_complete"] is None


def test_open_outer_only_is_not_observation_complete_from_weekday_guess() -> None:
    state = project_peakrace_business_state(
        _schedule(_outer()),
        _peakrace(activity_groups=[{"activity_id": 1630100, "group": 2}]),
        now=datetime(2026, 9, 1, 10, tzinfo=TZ),
        rank_snapshots={1630100: _rank(1630100), 624001: _rank(624001)},
        guess_required=False,
    )

    assert state["stage"]["schedule_phase"] == "unloaded"
    assert state["completion"]["observation_complete"] is False
    assert "current_stage_runtime_unobserved" in state["blockers"]


def test_round_mismatch_prevents_observation_completion() -> None:
    state = project_peakrace_business_state(
        _schedule(_outer(), _stage()),
        _peakrace(
            current_round=2,
            activity_groups=[{"activity_id": 1630100, "group": 2}],
        ),
        now=datetime(2026, 9, 1, 10, tzinfo=TZ),
        rank_snapshots={1630100: _rank(1630100), 624001: _rank(624001)},
        guess_required=False,
    )

    assert "runtime_round_mismatch" in state["blockers"]
    assert state["completion"]["observation_complete"] is False


def test_open_guess_policy_unknown_stays_unknown_after_observation_complete() -> None:
    state = project_peakrace_business_state(
        _schedule(_outer(), _stage()),
        _peakrace(),
        now=datetime(2026, 9, 1, 10, tzinfo=TZ),
        rank_snapshots={624001: _rank(624001)},
    )

    assert state["completion"]["observation_complete"] is True
    assert state["guess"]["complete"] is None
    assert state["completion"]["current_phase_complete"] is None
    assert state["status"] == "unknown"
