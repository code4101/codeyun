from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.core.fanxiu.instrumentation import peakrace
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaRef,
)


def _ref(address: int) -> LuaRef:
    return LuaRef("table", address)


class _Reader:
    field_map: dict[int, dict] = {}
    list_map: dict[int, tuple[list, int | None]] = {}
    dictionary_map: dict[int, dict] = {}

    def __init__(self, _memory) -> None:
        pass

    def fields(self, value):
        return self.field_map.get(value.address, {}) if isinstance(value, LuaRef) else {}

    def list_items(self, value):
        return self.list_map.get(value.address, ([], None))

    def dictionary_fields(self, value):
        return self.dictionary_map.get(value.address, {})

    def long(self, value):
        return value if isinstance(value, int) else None


def test_peakrace_snapshot_projects_self_groups_and_immutable_guesses(
    monkeypatch,
) -> None:
    _Reader.field_map = {
        1: {
            "selfRank": 12,
            "currRound": 1,
            "activity2GroupMap": _ref(2),
            "guessVOList": _ref(3),
            "fazeTriggerTimes": 4,
            "worshipDailyTimes": 0,
        },
        4: {
            "activityId": 1630100,
            "group": 2,
            "guessRoleIds": _ref(5),
        },
    }
    _Reader.dictionary_map = {2: {1630100: 2, 1630200: 2}}
    _Reader.list_map = {3: ([_ref(4)], 1), 5: ([9001, 9002], 2)}
    monkeypatch.setattr(peakrace, "LuaJitReader", _Reader)
    monkeypatch.setattr(
        peakrace,
        "_peakrace_data_fields",
        lambda *_args: {"selfData": _ref(1), "otherRankMap": _ref(6)},
    )

    result = peakrace._peakrace_snapshot(
        SimpleNamespace(pid=7, process_start_ticks=8),
        99,
        root_cache_hit=True,
    )

    assert result["complete"] is True
    assert result["self_rank"] == 12
    assert result["current_round"] == 1
    assert result["activity_groups"] == [
        {"activity_id": 1630100, "group": 2},
        {"activity_id": 1630200, "group": 2},
    ]
    assert result["guesses"] == [
        {
            "activity_id": 1630100,
            "group": 2,
            "role_ids": [9001, 9002],
            "declared_role_count": 2,
        }
    ]
    assert result["score_rank_loaded"] is True
    assert result["evidence"]["manager_resolver"] == "lua_global"
    assert "root_address" not in result["evidence"]


def test_peakrace_snapshot_reports_naturally_unloaded_self_data(
    monkeypatch,
) -> None:
    _Reader.field_map = {}
    _Reader.dictionary_map = {}
    _Reader.list_map = {}
    monkeypatch.setattr(peakrace, "LuaJitReader", _Reader)
    monkeypatch.setattr(
        peakrace,
        "_peakrace_data_fields",
        lambda *_args: {"selfData": None, "otherRankMap": _ref(6)},
    )

    result = peakrace._peakrace_snapshot(
        SimpleNamespace(pid=7, process_start_ticks=8),
        99,
        root_cache_hit=False,
    )

    assert result["available"] is True
    assert result["complete"] is False
    assert result["error_code"] == "data_not_loaded"
    assert result["manager_loaded"] is True
    assert result["self_data_loaded"] is False
    assert result["score_rank_loaded"] is True


def test_peakrace_snapshot_rejects_partial_self_data(monkeypatch) -> None:
    _Reader.field_map = {1: {"selfRank": 12}}
    _Reader.dictionary_map = {}
    _Reader.list_map = {}
    monkeypatch.setattr(peakrace, "LuaJitReader", _Reader)
    monkeypatch.setattr(
        peakrace,
        "_peakrace_data_fields",
        lambda *_args: {"selfData": _ref(1)},
    )

    with pytest.raises(FanxiuRuntimeMemoryError) as error:
        peakrace._peakrace_snapshot(
            SimpleNamespace(pid=7, process_start_ticks=8),
            99,
            root_cache_hit=False,
        )

    assert error.value.code == "schema_mismatch"


def test_read_peakrace_runtime_snapshot_uses_only_exact_loaded_global(
    monkeypatch,
) -> None:
    memory = SimpleNamespace(pid=7, process_start_ticks=8)
    calls: list[dict] = []
    monkeypatch.setattr(
        peakrace.MumuProcessMemory,
        "discover_cached",
        classmethod(lambda _cls, **_kwargs: memory),
    )
    monkeypatch.setattr(peakrace, "_lua_addresses", lambda _memory: {"state": "0x7b"})

    def resolve(*_args, **kwargs):
        calls.append(kwargs)
        return 99, True, 456

    monkeypatch.setattr(peakrace, "resolve_lua_global_manager_root", resolve)
    monkeypatch.setattr(
        peakrace,
        "_peakrace_snapshot",
        lambda *_args, **_kwargs: {
            "ok": True,
            "available": True,
            "complete": True,
            "evidence": {},
        },
    )

    result = peakrace.read_peakrace_runtime_snapshot()

    assert result["complete"] is True
    assert len(calls) == 1
    assert calls[0]["global_name"] == "PeakraceMgr"
    assert calls[0]["required_methods"] == frozenset(
        {"Inst_get", "LuaPeakraceMgr"}
    )


def test_read_peakrace_runtime_snapshot_preserves_typed_runtime_error(
    monkeypatch,
) -> None:
    memory = SimpleNamespace(pid=7, process_start_ticks=8)
    monkeypatch.setattr(
        peakrace.MumuProcessMemory,
        "discover_cached",
        classmethod(lambda _cls, **_kwargs: memory),
    )
    monkeypatch.setattr(peakrace, "_lua_addresses", lambda _memory: {"state": "0x7b"})
    monkeypatch.setattr(
        peakrace,
        "resolve_lua_global_manager_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FanxiuRuntimeMemoryError(
                "PeakraceMgr 尚未加载",
                code="manager_not_found",
            )
        ),
    )

    result = peakrace.read_peakrace_runtime_snapshot()

    assert result["available"] is False
    assert result["complete"] is False
    assert result["error_code"] == "manager_not_found"
    assert result["manager_loaded"] is False
