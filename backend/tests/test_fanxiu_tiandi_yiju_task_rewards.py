from __future__ import annotations

import pytest

from backend.core.fanxiu.instrumentation.tiandi_yiju_task_rewards import (
    build_tiandi_yiju_task_reward_snapshot,
    tiandi_yiju_task_activity_id,
)


def _entry(task_id: int, *, status: int = 1) -> dict:
    return {
        "taskId": task_id,
        "status": status,
        "turn": 0,
        "rewardTime": 1,
        "progressList": [{"finish": False}],
    }


@pytest.mark.parametrize("runtime_id,task_id", [(8090001, 8090001), (8090004, 8090002)])
def test_maps_only_playable_occurrences(runtime_id: int, task_id: int) -> None:
    assert tiandi_yiju_task_activity_id(runtime_id) == task_id


def test_group_selection_occurrence_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="不是可操作棋盘"):
        tiandi_yiju_task_activity_id(8090002)


@pytest.mark.parametrize(
    "activity_id,shared_start,score_variant_start",
    [(8090001, 400501, 400510), (8090004, 400301, 400310)],
)
def test_selects_one_live_score_ladder(
    activity_id: int, shared_start: int, score_variant_start: int
) -> None:
    entries = [_entry(task_id) for task_id in range(shared_start, shared_start + 9)]
    entries += [_entry(task_id) for task_id in range(score_variant_start, score_variant_start + 9)]
    snapshot = build_tiandi_yiju_task_reward_snapshot(
        activity_id=activity_id,
        task_entries=entries,
        finished_task_ids=[],
    )
    assert snapshot["ok"] is True
    assert snapshot["complete"] is True
    assert snapshot["expected_task_count"] == 18
    assert snapshot["authorized_claim_task_ids"] == []


def test_missing_logical_slot_never_authorizes_claim() -> None:
    entries = [_entry(task_id) for task_id in range(400501, 400509)]
    entries += [_entry(task_id) for task_id in range(400510, 400519)]
    snapshot = build_tiandi_yiju_task_reward_snapshot(
        activity_id=8090001,
        task_entries=entries,
        finished_task_ids=[],
    )
    assert snapshot["ok"] is False
    assert snapshot["state"] == "unavailable"
    assert snapshot["authorized_claim_task_ids"] == []


def test_two_score_versions_are_ambiguous() -> None:
    entries = [_entry(task_id) for task_id in range(400501, 400510)]
    entries += [_entry(task_id) for task_id in range(400510, 400528)]
    snapshot = build_tiandi_yiju_task_reward_snapshot(
        activity_id=8090001,
        task_entries=entries,
        finished_task_ids=[],
    )
    assert snapshot["ok"] is False
    assert snapshot["state"] == "ambiguous"
    assert snapshot["authorized_claim_task_ids"] == []


def test_claim_authorization_requires_complete_runtime_projection() -> None:
    entries = [_entry(task_id) for task_id in range(400501, 400510)]
    entries += [_entry(task_id) for task_id in range(400519, 400528)]
    entries[0] = _entry(400501, status=4)
    snapshot = build_tiandi_yiju_task_reward_snapshot(
        activity_id=8090001,
        task_entries=entries,
        finished_task_ids=[],
    )
    assert snapshot["complete"] is True
    assert snapshot["authorized_claim_task_ids"] == [400501]
