from __future__ import annotations

import pytest
from types import SimpleNamespace

from backend.core.fanxiu.instrumentation import activity_rank_runtime
from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError


class FakeReader:
    @staticmethod
    def fields(value):
        return value

    @staticmethod
    def dictionary_fields(value):
        return value

    @staticmethod
    def list_items(value):
        return value, len(value)


def _manager_with_ranks(rank_dictionary):
    return {
        "inst": {
            "Model": {
                "ActivityrankData": {
                    "V_RankDataDic": rank_dictionary,
                }
            }
        }
    }


def test_activity_rank_adapter_is_parameterized_by_activity_id(monkeypatch):
    monkeypatch.setattr(
        activity_rank_runtime,
        "manager_index_fields",
        lambda *_args, **_kwargs: _manager_with_ranks(
            {
                98765: {
                    "selfRankVO": {
                        "rank": 2,
                        "score": 321,
                        "name": "测试角色",
                        "serverId": 22077,
                    },
                    "rankVOS": [
                        {"rank": 1, "score": 456, "name": "榜首"},
                        {"rank": 2, "score": 321, "name": "测试角色"},
                    ],
                    "rankListSize": 8,
                }
            }
        ),
    )

    result = activity_rank_runtime.read_activity_rank_snapshot(
        FakeReader(), 0x2000, 98765
    )

    assert result.activity_id == 98765
    assert result.rank_list_size == 8
    assert result.self_ranking["score"] == 321
    assert [row["rank"] for row in result.rankings] == [1, 2]


def test_activity_rank_adapter_reports_loaded_ids_when_target_is_missing(
    monkeypatch,
):
    monkeypatch.setattr(
        activity_rank_runtime,
        "manager_index_fields",
        lambda *_args, **_kwargs: _manager_with_ranks({111: {}, 222: {}}),
    )

    with pytest.raises(FanxiuRuntimeMemoryError) as caught:
        activity_rank_runtime.read_activity_rank_snapshot(
            FakeReader(), 0x2000, 333
        )

    assert caught.value.code == "data_not_loaded"
    assert "当前已加载：111,222" in str(caught.value)


def test_fast_activity_rank_resolution_never_starts_discovery(monkeypatch):
    monkeypatch.setattr(
        activity_rank_runtime,
        "resolve_manager_root",
        lambda _memory, **kwargs: (
            pytest.fail("fast read enabled discovery")
            if kwargs["allow_discovery"]
            else (0x1234, True)
        ),
    )

    assert activity_rank_runtime.resolve_activity_rank_root(
        object(), allow_discovery=False
    ) == (0x1234, True)


def test_generic_runtime_snapshot_classifies_fast_cache_miss(monkeypatch):
    monkeypatch.setattr(
        activity_rank_runtime.MumuProcessMemory,
        "discover_cached",
        classmethod(
            lambda _cls, **_kwargs: (_ for _ in ()).throw(
                FanxiuRuntimeMemoryError(
                    "进程缓存未预热", code="process_cache_miss"
                )
            )
        ),
    )

    result = activity_rank_runtime.read_activity_rank_runtime_snapshot(98765)

    assert result["ok"] is False
    assert result["error_code"] == "process_cache_miss"
    assert result["recovery_required"] is True


@pytest.mark.parametrize("options,expected", [
    ({}, {"allow_discovery": True, "force_refresh": False}),
    ({"allow_discovery": False}, {"allow_discovery": False, "force_refresh": False}),
    ({"force_refresh": True}, {"allow_discovery": True, "force_refresh": True}),
])
def test_prepare_rank_runtime_forwards_explicit_cache_policy(monkeypatch, options, expected):
    memory = SimpleNamespace(pid=123, process_start_ticks=456)
    resolutions = []
    monkeypatch.setattr(activity_rank_runtime.MumuProcessMemory, "discover_cached",
                        classmethod(lambda cls, **kwargs: memory))
    monkeypatch.setattr(activity_rank_runtime, "LuaJitReader", lambda value: value)
    monkeypatch.setattr(activity_rank_runtime, "loaded_activity_rank_ids", lambda *args: (98765,))

    def resolve(value, **kwargs):
        assert value is memory
        resolutions.append(kwargs)
        return 0x1234, True

    monkeypatch.setattr(activity_rank_runtime, "resolve_activity_rank_root", resolve)
    result = activity_rank_runtime.prepare_activity_rank_runtime([98765], **options)
    assert result["ok"] is True
    assert result["root_cache_hit"] is True
    assert resolutions == [expected]


@pytest.mark.parametrize("failure_stage", ["process", "root"])
def test_bounded_rank_prepare_returns_cache_miss_without_root_discovery(monkeypatch, failure_stage):
    memory = SimpleNamespace(pid=123, process_start_ticks=456)
    code = f"{failure_stage}_cache_miss"

    def discover(cls, **kwargs):
        if failure_stage == "process":
            raise FanxiuRuntimeMemoryError("missing", code=code)
        return memory

    def resolve(value, **kwargs):
        assert kwargs == {"allow_discovery": False, "force_refresh": False}
        raise FanxiuRuntimeMemoryError("missing", code=code)

    monkeypatch.setattr(activity_rank_runtime.MumuProcessMemory, "discover_cached", classmethod(discover))
    monkeypatch.setattr(activity_rank_runtime, "resolve_activity_rank_root", resolve)
    result = activity_rank_runtime.prepare_activity_rank_runtime([98765], allow_discovery=False)
    assert result["ok"] is False
    assert result["complete"] is False
    assert result["error_code"] == code
    assert result["recovery_required"] is True


def test_bounded_rank_prepare_rejects_force_refresh_before_accessing_memory(monkeypatch):
    monkeypatch.setattr(activity_rank_runtime.MumuProcessMemory, "discover_cached",
                        classmethod(lambda cls, **kwargs: pytest.fail("invalid policy accessed process")))
    with pytest.raises(ValueError):
        activity_rank_runtime.prepare_activity_rank_runtime(
            [98765], allow_discovery=False, force_refresh=True,
        )


def test_ordinary_rank_read_keeps_both_discovery_gates_closed(monkeypatch):
    memory = SimpleNamespace(pid=123, process_start_ticks=456)
    calls = []

    def discover(cls, **kwargs):
        calls.append(("process", kwargs))
        return memory

    def resolve(value, **kwargs):
        assert value is memory
        calls.append(("root", kwargs))
        raise FanxiuRuntimeMemoryError("missing", code="root_cache_miss")

    monkeypatch.setattr(activity_rank_runtime.MumuProcessMemory, "discover_cached", classmethod(discover))
    monkeypatch.setattr(activity_rank_runtime, "resolve_activity_rank_root", resolve)
    result = activity_rank_runtime.read_activity_rank_runtime_snapshot(98765)
    assert result["error_code"] == "root_cache_miss"
    assert calls == [
        ("process", {"fallback_to_discovery": False}),
        ("root", {"allow_discovery": False}),
    ]
