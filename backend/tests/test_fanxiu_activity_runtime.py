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

    def diagnostics(self):
        return {}

    def dictionary_fields(self, value):
        return value


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
        {"pid": 2733, "process_start_ticks": 4935, "adb_serial": "device", "diagnostics": lambda self: {}},
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

    # This test concerns root admission; retain its immutable mapping projection
    # while binding acquisition and exact-field batching have separate contracts.
    monkeypatch.setattr(activity_runtime, '_lua_addresses', lambda memory: {'state': '0x1000'})
    decoder = activity_runtime._decode_worldline_activity_items
    monkeypatch.setattr(activity_runtime, '_decode_worldline_activity_items',
        lambda reader, data, definitions, **kwargs: decoder(reader, data, definitions))
    result = activity_runtime.read_worldline_activity_runtime_snapshot(
        allow_discovery=False
    )

    assert result["ok"] is True
    assert result["count"] == 1
    assert discover_calls == [True]
    assert manager_discovery_calls == [False]


def test_period_read_rebinds_expired_process_cache_without_manager_discovery(monkeypatch):
    memory = type('Memory', (), {'pid': 2733, 'process_start_ticks': 4935})()
    process_calls, manager_calls = [], []
    monkeypatch.setattr(activity_runtime.MumuProcessMemory, 'discover_cached',
        classmethod(lambda cls, *, fallback_to_discovery:
            process_calls.append(fallback_to_discovery) or memory))
    monkeypatch.setattr(activity_runtime, '_resolve_activity_manager_runtime',
        lambda current, **kwargs:
            manager_calls.append(kwargs['allow_discovery']) or (0x1234, True, 'lua_global'))
    monkeypatch.setattr(activity_runtime, 'LuaJitReader', lambda current: _Reader())
    monkeypatch.setattr(activity_runtime, '_activity_data_fields', lambda reader, root:
        {'_ActivityInfo': {'1': {'activityId': 30000009,
            'startTime': 1790524805000, 'endTime': 1791388790000}}})
    from backend.core.fanxiu.instrumentation.runtime_memory import LuaRef
    reader = _Reader()
    fields = {'activityId': 30000009, 'id': 1, 'startTime': 1790524805000, 'endTime': 1791388790000}
    reader.dictionary_fields = lambda value: {'1': LuaRef('table', 0x1000)}
    reader.string_fields = lambda address, keys: {k: fields[k] for k in keys}
    monkeypatch.setattr(activity_runtime, 'LuaJitReader', lambda memory: reader)
    result = activity_runtime.read_activity_period_runtime_snapshot(30000009)
    assert (result['start_time_ms'], result['end_time_ms']) == (1790524805000, 1791388790000)
    assert process_calls == [True]
    assert manager_calls == [False]


def test_batched_exact_fields_preserve_stable_activity_vo_projection(monkeypatch):
    from backend.core.fanxiu.instrumentation.runtime_memory import LuaRef
    fields=_beast_runtime_item()
    legacy=activity_runtime._decode_worldline_activity_items(_Reader(),{'_WorldLineActiveInfoList':{'count':1,'items':[fields]}},{4150001:_beast_definition()})
    names={}
    monkeypatch.setattr(activity_runtime,'lua_jit_intern_state',lambda *a:(1,2,3,4))
    def intern(memory,**kwargs):
        name=kwargs['name'];names[len(names)+1]=name;return len(names)
    monkeypatch.setattr(activity_runtime,'resolve_interned_lua_string',intern)
    class PackedContractReader(_Reader):
        memory=object()
        def list_items(self,value):return [LuaRef('table',0x1000)],1
        def prefetch_hashed_string_fields(self,addresses,**kwargs):tuple(addresses)
        def hashed_string_field(self,address,*,key_address,expected_name):
            assert names[key_address]==expected_name
            return fields.get(expected_name)
    result=activity_runtime._decode_worldline_activity_items(PackedContractReader(),{'_WorldLineActiveInfoList':None},{4150001:_beast_definition()},state_address=1)
    assert result==legacy


@pytest.mark.parametrize('missing',['id','startTime','endTime','activityId'])
def test_exact_batched_projection_rejects_missing_required_identity(monkeypatch,missing):
    from backend.core.fanxiu.instrumentation.runtime_memory import LuaRef
    fields=_beast_runtime_item();fields.pop(missing)
    names={}
    monkeypatch.setattr(activity_runtime,'lua_jit_intern_state',lambda *a:(1,2,3,4))
    def intern(memory,**kwargs):names[len(names)+1]=kwargs['name'];return len(names)
    monkeypatch.setattr(activity_runtime,'resolve_interned_lua_string',intern)
    class FieldContract(_Reader):
        memory=object()
        def list_items(self,value):return [LuaRef('table',0x1000)],1
        def prefetch_hashed_string_fields(self,addresses,**kwargs):tuple(addresses)
        def hashed_string_field(self,address,*,key_address,expected_name):return fields.get(expected_name)
    with pytest.raises(FanxiuRuntimeMemoryError):
        activity_runtime._decode_worldline_activity_items(FieldContract(),{'_WorldLineActiveInfoList':None},{4150001:_beast_definition()},state_address=1)
