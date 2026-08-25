from __future__ import annotations

import backend.core.fanxiu.instrumentation.daily_task_rewards as rewards_module
from backend.core.fanxiu.instrumentation.daily_task_rewards import (
    LINGMAI_TASK_REWARD_SPEC,
    LUNDAO_TASK_REWARD_SPEC,
    QIXI_MOJIE_TASK_REWARD_SPEC,
    build_activity_task_reward_snapshot,
    read_activity_task_reward_fast_snapshot,
    read_activity_task_reward_snapshot,
    read_all_activity_task_reward_snapshots,
)


def _entry(
    task_id: int,
    *,
    status: int = 3,
    turn: int = 0,
    reward_time: int = 1,
    progress_finished: bool = False,
) -> dict:
    return {
        "taskId": task_id,
        "status": status,
        "turn": turn,
        "rewardTime": reward_time,
        "progressList": [{"finish": progress_finished}],
    }


def _all_pending(spec) -> list[dict]:
    return [_entry(task_id) for task_id in spec.task_ids]


def test_reverse_engineered_domain_specs_are_exact() -> None:
    assert LUNDAO_TASK_REWARD_SPEC.activity_id == 111601
    assert LUNDAO_TASK_REWARD_SPEC.task_ids == tuple(range(11160101, 11160113))
    assert LUNDAO_TASK_REWARD_SPEC.condition_key == "LundaoDaoxin"
    assert len(LUNDAO_TASK_REWARD_SPEC.thresholds) == 12

    assert LINGMAI_TASK_REWARD_SPEC.activity_id == 2005
    assert LINGMAI_TASK_REWARD_SPEC.task_ids == tuple(range(30350001, 30350013))
    assert LINGMAI_TASK_REWARD_SPEC.condition_key == "UnionVeinsXin"
    assert len(LINGMAI_TASK_REWARD_SPEC.thresholds) == 12

    assert QIXI_MOJIE_TASK_REWARD_SPEC.activity_id == 64220001
    assert QIXI_MOJIE_TASK_REWARD_SPEC.task_ids == tuple(range(64220001, 64220021))
    assert QIXI_MOJIE_TASK_REWARD_SPEC.condition_key == "CrossUnionMemberJoinDemonBossTimes"
    assert QIXI_MOJIE_TASK_REWARD_SPEC.thresholds == tuple(range(7, 141, 7))


def test_status_finish_is_claimable() -> None:
    entries = _all_pending(LUNDAO_TASK_REWARD_SPEC)
    entries[0] = _entry(entries[0]["taskId"], status=4)
    snapshot = build_activity_task_reward_snapshot(
        spec=LUNDAO_TASK_REWARD_SPEC, task_entries=entries, finished_task_ids=[]
    )
    assert snapshot["complete"] is True
    assert snapshot["state"] == "claimable"
    assert snapshot["authorized_claim_task_ids"] == [11160101]


def test_turn_and_progress_semantics_match_rank_task_item() -> None:
    entries = _all_pending(LINGMAI_TASK_REWARD_SPEC)
    entries[0] = _entry(entries[0]["taskId"], turn=2, reward_time=1)
    entries[1] = _entry(
        entries[1]["taskId"], turn=1, reward_time=1, progress_finished=True
    )
    snapshot = build_activity_task_reward_snapshot(
        spec=LINGMAI_TASK_REWARD_SPEC, task_entries=entries, finished_task_ids=[]
    )
    assert snapshot["authorized_claim_task_ids"] == [30350001, 30350002]
    assert 30350003 in snapshot["pending_task_ids"]


def test_finished_list_and_status_five_are_already_claimed() -> None:
    entries = _all_pending(QIXI_MOJIE_TASK_REWARD_SPEC)
    entries[1] = _entry(entries[1]["taskId"], status=5)
    snapshot = build_activity_task_reward_snapshot(
        spec=QIXI_MOJIE_TASK_REWARD_SPEC,
        task_entries=entries,
        finished_task_ids=[64220001],
    )
    assert snapshot["claimed_task_ids"][:2] == [64220001, 64220002]
    assert snapshot["authorized_claim_task_ids"] == []
    assert snapshot["state"] == "none"


def test_all_finished_is_idempotently_already_claimed() -> None:
    snapshot = build_activity_task_reward_snapshot(
        spec=LUNDAO_TASK_REWARD_SPEC,
        task_entries=[],
        finished_task_ids=list(LUNDAO_TASK_REWARD_SPEC.task_ids),
    )
    assert snapshot["complete"] is True
    assert snapshot["state"] == "already_claimed"
    assert snapshot["authorized_claim_task_ids"] == []


def test_partial_runtime_state_fails_closed() -> None:
    entries = _all_pending(LUNDAO_TASK_REWARD_SPEC)[:-1]
    entries[0] = _entry(entries[0]["taskId"], status=4)
    snapshot = build_activity_task_reward_snapshot(
        spec=LUNDAO_TASK_REWARD_SPEC, task_entries=entries, finished_task_ids=[]
    )
    assert snapshot["state"] == "ambiguous"
    assert snapshot["missing_task_ids"] == [11160112]
    assert snapshot["claimable_task_ids"] == [11160101]
    assert snapshot["authorized_claim_task_ids"] == []


def test_duplicate_or_malformed_runtime_rows_fail_closed() -> None:
    entries = _all_pending(LINGMAI_TASK_REWARD_SPEC)
    entries.append(dict(entries[0]))
    entries[1].pop("rewardTime")
    snapshot = build_activity_task_reward_snapshot(
        spec=LINGMAI_TASK_REWARD_SPEC, task_entries=entries, finished_task_ids=[]
    )
    assert snapshot["state"] == "ambiguous"
    assert snapshot["duplicate_task_ids"] == [30350001]
    assert snapshot["malformed_task_ids"] == [30350002]
    assert snapshot["authorized_claim_task_ids"] == []


class _FakeMemory:
    pid = 123
    process_start_ticks = 456


def _install_shared_runtime_fakes(
    monkeypatch,
    *,
    fail_root: bool = False,
) -> dict[str, int]:
    calls = {"discover": 0, "resolve": 0, "quest_data": 0, "serialize": 0}
    all_entries = [
        entry
        for spec in (
            LUNDAO_TASK_REWARD_SPEC,
            LINGMAI_TASK_REWARD_SPEC,
            QIXI_MOJIE_TASK_REWARD_SPEC,
        )
        for entry in _all_pending(spec)
    ]

    def discover_cached():
        calls["discover"] += 1
        return _FakeMemory()

    def resolve(_memory):
        calls["resolve"] += 1
        if fail_root:
            raise RuntimeError("QuestMgr root unavailable")
        return 0x1234, True, "test"

    def quest_data(_reader, _root):
        calls["quest_data"] += 1
        return {"taskInfoMap": "task-map"}

    def fields(_reader, value):
        if value == "activity-tasks":
            return {"taskEntryVOs": all_entries, "finishTasks": []}
        raise AssertionError(f"unexpected fields value: {value!r}")

    def serialize(_reader, value):
        calls["serialize"] += 1
        return dict(value)

    monkeypatch.setattr(
        rewards_module.MumuProcessMemory, "discover_cached", staticmethod(discover_cached)
    )
    monkeypatch.setattr(rewards_module, "_resolve_quest_root", resolve)
    monkeypatch.setattr(rewards_module, "LuaJitReader", lambda _memory: object())
    monkeypatch.setattr(rewards_module, "_quest_data_fields", quest_data)
    monkeypatch.setattr(
        rewards_module,
        "_dictionary_item",
        lambda _reader, mapping, key: "activity-tasks"
        if mapping == "task-map" and key == 3
        else None,
    )
    monkeypatch.setattr(rewards_module, "_fields", fields)
    monkeypatch.setattr(rewards_module, "_list_values", lambda _reader, value: list(value))
    monkeypatch.setattr(rewards_module, "_serialize_entry", serialize)
    return calls


def test_batch_reader_decodes_questmgr_once_for_all_three_domains(monkeypatch) -> None:
    calls = _install_shared_runtime_fakes(monkeypatch)

    result = read_all_activity_task_reward_snapshots()

    assert result["ok"] is True
    assert result["domain_order"] == ["lundao", "lingmai", "qixi_mojie"]
    assert set(result["domains"]) == {"lundao", "lingmai", "qixi_mojie"}
    assert all(snapshot["complete"] for snapshot in result["domains"].values())
    assert calls == {
        "discover": 1,
        "resolve": 1,
        "quest_data": 1,
        "serialize": 44,
    }
    assert set(result["stage_timings"]) == {
        "process_discovery_seconds",
        "quest_root_resolution_seconds",
        "quest_data_decode_seconds",
        "activity_task_decode_seconds",
        "domain_projection_seconds",
    }
    assert all(value >= 0 for value in result["stage_timings"].values())


def test_batch_reader_failure_closes_every_requested_domain(monkeypatch) -> None:
    calls = _install_shared_runtime_fakes(monkeypatch, fail_root=True)

    result = read_all_activity_task_reward_snapshots()

    assert result["ok"] is False
    assert result["failed_stage"] == "quest_root_resolution"
    assert calls["discover"] == 1
    assert calls["resolve"] == 1
    assert calls["quest_data"] == 0
    assert all(
        snapshot["state"] == "unavailable"
        for snapshot in result["domains"].values()
    )
    assert all(
        snapshot["authorized_claim_task_ids"] == []
        for snapshot in result["domains"].values()
    )
    assert "quest_root_resolution_failed_seconds" in result["stage_timings"]


def test_legacy_single_domain_reader_preserves_flat_shape(monkeypatch) -> None:
    calls = _install_shared_runtime_fakes(monkeypatch)

    result = read_activity_task_reward_snapshot("lundao")

    assert result["ok"] is True
    assert result["domain"] == "lundao"
    assert result["state"] == "none"
    assert "domains" not in result
    assert "stage_timings" in result
    assert calls["discover"] == calls["resolve"] == calls["quest_data"] == 1


def _install_fast_runtime_fakes(monkeypatch):
    entries = [
        entry
        for spec in (
            LUNDAO_TASK_REWARD_SPEC,
            LINGMAI_TASK_REWARD_SPEC,
            QIXI_MOJIE_TASK_REWARD_SPEC,
        )
        for entry in _all_pending(spec)
    ]
    finished: list[int] = []
    calls = {"selected_fields": 0}

    monkeypatch.setattr(
        rewards_module.MumuProcessMemory,
        "discover_cached",
        staticmethod(lambda: _FakeMemory()),
    )
    monkeypatch.setattr(
        rewards_module,
        "_resolve_quest_root",
        lambda _memory: (0x5678, True, "test"),
    )
    monkeypatch.setattr(rewards_module, "LuaJitReader", lambda _memory: object())
    monkeypatch.setattr(
        rewards_module,
        "_quest_data_fields",
        lambda _reader, _root: {"taskInfoMap": "task-map"},
    )
    monkeypatch.setattr(
        rewards_module,
        "_dictionary_item",
        lambda _reader, mapping, key: "activity-tasks"
        if mapping == "task-map" and key == 3
        else None,
    )
    monkeypatch.setattr(
        rewards_module,
        "_fields",
        lambda _reader, value: {
            "taskEntryVOs": "entry-list",
            "finishTasks": "finish-list",
        }
        if value == "activity-tasks"
        else dict(value) if isinstance(value, dict) else {},
    )
    monkeypatch.setattr(
        rewards_module,
        "_list_values",
        lambda _reader, value: list(finished) if value == "finish-list" else [],
    )

    def list_values_with_identity(_reader, value):
        if value == "entry-list":
            return list(entries), (0x9000, len(entries))
        return [], None

    def selected_fields(_reader, value, names):
        calls["selected_fields"] += 1
        return {name: value.get(name) for name in names}

    monkeypatch.setattr(
        rewards_module,
        "_list_values_with_identity",
        list_values_with_identity,
    )
    monkeypatch.setattr(rewards_module, "_selected_string_fields", selected_fields)
    with rewards_module._ENTRY_INDEX_CACHE_LOCK:
        rewards_module._ENTRY_INDEX_CACHE.clear()
    return entries, finished, calls


def test_fast_reader_rebuilds_once_then_reads_only_selected_domain(monkeypatch) -> None:
    entries, finished, calls = _install_fast_runtime_fakes(monkeypatch)
    entries[0].update(status=4)

    first = read_activity_task_reward_fast_snapshot("lundao")

    assert first["ok"] is True
    assert first["authorized_claim_task_ids"] == [11160101]
    assert first["evidence"]["entry_index_source"] == "rebuilt"
    first_field_reads = calls["selected_fields"]
    assert first_field_reads == len(entries) + len(LUNDAO_TASK_REWARD_SPEC.task_ids)

    entries[0].update(status=5)
    finished.append(11160101)
    second = read_activity_task_reward_fast_snapshot(
        "lundao",
        expected_claimed_task_id=11160101,
    )

    assert second["ok"] is True
    assert second["expected_task_claimed"] is True
    assert 11160101 not in second["authorized_claim_task_ids"]
    assert second["claimed_task_ids"] == [11160101]
    assert second["evidence"]["entry_index_source"] == "cached"
    assert calls["selected_fields"] - first_field_reads == (
        len(LUNDAO_TASK_REWARD_SPEC.task_ids) - 1
    )


def test_fast_reader_rebuilds_when_cached_slot_identity_changes(monkeypatch) -> None:
    entries, _finished, _calls = _install_fast_runtime_fakes(monkeypatch)
    first = read_activity_task_reward_fast_snapshot("lundao")
    assert first["ok"] is True

    entries[0], entries[-1] = entries[-1], entries[0]
    second = read_activity_task_reward_fast_snapshot("lundao")

    assert second["ok"] is True
    assert second["complete"] is True
    assert second["evidence"]["entry_index_source"] == "rebuilt_after_mismatch"


def test_fast_reader_derives_shifted_slots_after_expected_row_is_removed(
    monkeypatch,
) -> None:
    entries, finished, calls = _install_fast_runtime_fakes(monkeypatch)
    first = read_activity_task_reward_fast_snapshot("lundao")
    assert first["ok"] is True
    first_field_reads = calls["selected_fields"]

    removed = entries.pop(0)
    assert removed["taskId"] == 11160101
    finished.append(11160101)
    second = read_activity_task_reward_fast_snapshot(
        "lundao",
        expected_claimed_task_id=11160101,
    )

    assert second["ok"] is True
    assert second["expected_task_claimed"] is True
    assert second["evidence"]["entry_index_source"] == "derived_after_expected_claim"
    assert calls["selected_fields"] - first_field_reads == (
        len(LUNDAO_TASK_REWARD_SPEC.task_ids) - 1
    )


def test_fast_reader_rejects_expected_task_from_another_domain(monkeypatch) -> None:
    _install_fast_runtime_fakes(monkeypatch)

    try:
        read_activity_task_reward_fast_snapshot(
            "lundao",
            expected_claimed_task_id=30350001,
        )
    except ValueError as exc:
        assert "不属于奖励域" in str(exc)
    else:
        raise AssertionError("expected ValueError")
