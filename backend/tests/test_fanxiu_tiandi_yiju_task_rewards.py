from __future__ import annotations

import pytest

import backend.core.fanxiu.instrumentation.tiandi_yiju_task_rewards as rewards_module
from backend.core.fanxiu.instrumentation.daily_task_rewards import (
    build_activity_task_reward_snapshot,
)
from backend.core.fanxiu.instrumentation.tiandi_yiju_task_rewards import (
    build_tiandi_yiju_task_reward_snapshot,
    read_tiandi_yiju_task_reward_snapshot,
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


@pytest.fixture(autouse=True)
def _clear_live_spec_cache():
    with rewards_module._LIVE_TASK_SPEC_LOCK:
        rewards_module._LIVE_TASK_SPECS.clear()
    yield
    with rewards_module._LIVE_TASK_SPEC_LOCK:
        rewards_module._LIVE_TASK_SPECS.clear()


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


def _shared_snapshot(*, pid: int = 123, finished=()) -> dict:
    entries = [_entry(task_id) for task_id in range(400501, 400510)]
    entries += [_entry(task_id) for task_id in range(400510, 400519)]
    finished_ids = {int(value) for value in finished}
    entries = [row for row in entries if int(row["taskId"]) not in finished_ids]
    entries[0]["status"] = 4
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "task_entries": entries,
        "finished_task_ids": sorted(finished_ids),
        "evidence": {"pid": pid, "process_start_ticks": 456},
    }


def test_reader_uses_process_bound_fast_path_only_after_full_occurrence_binding(
    monkeypatch,
) -> None:
    calls = {"full": 0, "fast": 0}

    def full(*_args, **_kwargs):
        calls["full"] += 1
        return _shared_snapshot()

    def fast(spec, *, expected_claimed_task_id):
        calls["fast"] += 1
        assert expected_claimed_task_id == 400501
        return {
            **build_activity_task_reward_snapshot(
                spec=spec,
                task_entries=[
                    _entry(task_id) for task_id in spec.task_ids if task_id != 400501
                ],
                finished_task_ids=[400501],
            ),
            "ok": True,
            "available": True,
            "expected_task_claimed": True,
            "evidence": {"pid": 123, "process_start_ticks": 456},
        }

    monkeypatch.setattr(rewards_module, "read_activity_task_reward_snapshots", full)
    monkeypatch.setattr(rewards_module, "read_task_reward_spec_fast_snapshot", fast)

    before = read_tiandi_yiju_task_reward_snapshot(8090001)
    after = read_tiandi_yiju_task_reward_snapshot(
        8090001,
        expected_claimed_task_id=400501,
    )

    assert before["authorized_claim_task_ids"] == [400501]
    assert after["expected_task_claimed"] is True
    assert after["claimed_task_ids"] == [400501]
    assert calls == {"full": 1, "fast": 1}


def test_process_generation_change_rejects_fast_result_and_rebuilds_full_snapshot(
    monkeypatch,
) -> None:
    calls = {"full": 0, "fast": 0}

    def full(*_args, **_kwargs):
        calls["full"] += 1
        return _shared_snapshot(pid=123 if calls["full"] == 1 else 999)

    def fast(spec, *, expected_claimed_task_id):
        calls["fast"] += 1
        return {
            **build_activity_task_reward_snapshot(
                spec=spec,
                task_entries=[],
                finished_task_ids=list(spec.task_ids),
            ),
            "ok": True,
            "available": True,
            "expected_task_claimed": True,
            "evidence": {"pid": 999, "process_start_ticks": 456},
        }

    monkeypatch.setattr(rewards_module, "read_activity_task_reward_snapshots", full)
    monkeypatch.setattr(rewards_module, "read_task_reward_spec_fast_snapshot", fast)

    read_tiandi_yiju_task_reward_snapshot(8090001)
    after = read_tiandi_yiju_task_reward_snapshot(
        8090001,
        expected_claimed_task_id=400501,
    )

    assert after["expected_task_claimed"] is False
    assert after["authorized_claim_task_ids"] == [400501]
    assert calls == {"full": 2, "fast": 1}


def test_incomplete_fast_read_falls_back_to_one_full_rebuild(monkeypatch) -> None:
    calls = {"full": 0, "fast": 0}

    def full(*_args, **_kwargs):
        calls["full"] += 1
        return _shared_snapshot(finished=[400501] if calls["full"] == 2 else [])

    def fast(_spec, *, expected_claimed_task_id):
        calls["fast"] += 1
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "expected_claimed_task_id": expected_claimed_task_id,
            "expected_task_claimed": None,
            "evidence": {"pid": 123, "process_start_ticks": 456},
        }

    monkeypatch.setattr(rewards_module, "read_activity_task_reward_snapshots", full)
    monkeypatch.setattr(rewards_module, "read_task_reward_spec_fast_snapshot", fast)

    read_tiandi_yiju_task_reward_snapshot(8090001)
    after = read_tiandi_yiju_task_reward_snapshot(
        8090001,
        expected_claimed_task_id=400501,
    )

    assert after["expected_task_claimed"] is True
    assert after["claimed_task_ids"] == [400501]
    assert calls == {"full": 2, "fast": 1}
