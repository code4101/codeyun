from __future__ import annotations

import pytest

from backend.core.fanxiu.instrumentation import activity_runtime
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
)


class _Reader:
    def fields(self, value):
        return value if isinstance(value, dict) else {}

    def list_items(self, value):
        return list(value["items"]), value.get("count")

    def long(self, _value):
        return None


def _beast_runtime_item() -> dict:
    return {
        "id": 4150001400002,
        "activityId": 4150001,
        "activityType": 15,
        "state": 2,
        "prepareEndTime": 1786309200000,
        "startTime": 1786413600000,
        "endTime": 1786543200000,
        "closePanelTime": 1786636739000,
        "scheduleId": 6400001,
        "loopDay": 0,
        "avgWorldLevel": 212,
        "crossGroup": 64,
    }


def _beast_definition() -> dict:
    return {
        "id": 4150001,
        "activityId": 15,
        "name_plain": "兽渊探秘",
        "littleName_plain": "跨服[4]",
        "crossGroup": 4,
        "baseId": 150000,
    }


def test_worldline_runtime_joins_exact_vo_with_static_identity() -> None:
    items, count = activity_runtime._decode_worldline_activity_items(
        _Reader(),
        {"_WorldLineActiveInfoList": {"count": 1, "items": [_beast_runtime_item()]}},
        {4150001: _beast_definition()},
    )

    assert count == 1
    assert items == [
        {
            "id": 4150001400002,
            "activityId": 4150001,
            "activityType": 15,
            "state": 2,
            "startTime": 1786413600000,
            "endTime": 1786543200000,
            "prepareEndTime": 1786309200000,
            "closePanelTime": 1786636739000,
            "scheduleId": 6400001,
            "loopDay": 0,
            "avgWorldLevel": 212,
            "runtimeCrossGroup": 64,
            "serverCount": 4,
            "name": "兽渊探秘",
            "identityComplete": True,
            "littleName": "跨服[4]",
            "baseId": 150000,
            "source": "runtime_memory+activity_config",
        }
    ]


def test_worldline_runtime_retains_but_does_not_name_unknown_identity() -> None:
    unknown = _beast_runtime_item()
    unknown["activityId"] = 14000000
    unknown["activityType"] = 243

    items, count = activity_runtime._decode_worldline_activity_items(
        _Reader(),
        {"_WorldLineActiveInfoList": {"count": 1, "items": [unknown]}},
        {},
    )

    assert count == 1
    assert items[0]["activityId"] == 14000000
    assert items[0]["name"] == ""
    assert items[0]["identityComplete"] is False


def test_worldline_runtime_fails_closed_when_declared_count_disagrees() -> None:
    with pytest.raises(FanxiuRuntimeMemoryError, match="声明数量"):
        activity_runtime._decode_worldline_activity_items(
            _Reader(),
            {"_WorldLineActiveInfoList": {"count": 2, "items": [_beast_runtime_item()]}},
            {4150001: _beast_definition()},
        )


def test_worldline_runtime_fails_closed_when_config_type_disagrees() -> None:
    definition = _beast_definition()
    definition["activityId"] = 14

    with pytest.raises(FanxiuRuntimeMemoryError, match="类型与静态配置不一致"):
        activity_runtime._decode_worldline_activity_items(
            _Reader(),
            {"_WorldLineActiveInfoList": {"count": 1, "items": [_beast_runtime_item()]}},
            {4150001: definition},
        )


def test_worldline_runtime_prefers_main_lua_activity_manager(monkeypatch) -> None:
    memory = object()
    marker_calls: list[object] = []
    monkeypatch.setattr(
        activity_runtime,
        "_lua_addresses",
        lambda current: {"state": "0x1234"},
    )
    monkeypatch.setattr(
        activity_runtime,
        "resolve_lua_global_manager_root",
        lambda current, **_kwargs: (456, True, 789),
    )
    monkeypatch.setattr(
        activity_runtime,
        "resolve_manager_root",
        lambda *_args, **_kwargs: marker_calls.append(memory),
    )

    assert activity_runtime._resolve_activity_manager_runtime(
        memory,
        allow_discovery=True,
        force_refresh=False,
    ) == (456, True, "lua_global")
    assert marker_calls == []


def test_worldline_runtime_falls_back_when_global_is_unavailable(
    monkeypatch,
) -> None:
    memory = object()
    monkeypatch.setattr(
        activity_runtime,
        "_lua_addresses",
        lambda current: {"state": "0x1234"},
    )
    monkeypatch.setattr(
        activity_runtime,
        "resolve_lua_global_manager_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FanxiuRuntimeMemoryError("主 Lua 全局环境中没有已加载的 ActivityMgr")
        ),
    )
    monkeypatch.setattr(
        activity_runtime,
        "resolve_manager_root",
        lambda current, **_kwargs: (654, False),
    )

    assert activity_runtime._resolve_activity_manager_runtime(
        memory,
        allow_discovery=True,
        force_refresh=False,
    ) == (654, False, "constructor_marker")


def test_worldline_hot_read_can_bind_current_process_without_manager_discovery(
    monkeypatch,
) -> None:
    memory = type(
        "Memory",
        (),
        {"pid": 2733, "process_start_ticks": 4935},
    )()
    discover_calls: list[bool] = []
    manager_discovery_calls: list[bool] = []

    monkeypatch.setattr(
        activity_runtime.MumuProcessMemory,
        "discover_cached",
        classmethod(
            lambda _cls, *, fallback_to_discovery: (
                discover_calls.append(fallback_to_discovery) or memory
            )
        ),
    )
    monkeypatch.setattr(
        activity_runtime,
        "_resolve_activity_manager_runtime",
        lambda current, **kwargs: (
            manager_discovery_calls.append(bool(kwargs["allow_discovery"]))
            or (0x1234, True, "lua_global")
        ),
    )
    monkeypatch.setattr(activity_runtime, "LuaJitReader", lambda current: _Reader())
    monkeypatch.setattr(
        activity_runtime,
        "_activity_data_fields",
        lambda reader, root: {
            "_WorldLineActiveInfoList": {
                "count": 1,
                "items": [_beast_runtime_item()],
            }
        },
    )
    monkeypatch.setattr(
        activity_runtime,
        "_load_activity_definitions",
        lambda _root=None: {4150001: _beast_definition()},
    )

    result = activity_runtime.read_worldline_activity_runtime_snapshot(
        allow_discovery=False
    )

    assert result["ok"] is True
    assert result["count"] == 1
    assert discover_calls == [True]
    assert manager_discovery_calls == [False]
