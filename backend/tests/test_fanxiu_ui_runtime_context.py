from __future__ import annotations

import pytest

from backend.core.fanxiu.instrumentation import ui_runtime_context
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    MemoryRegion,
    MumuProcessMemory,
    LuaRef,
)
from backend.core.fanxiu.instrumentation.ui_runtime_context import (
    UiRuntimeBinding,
    UiRuntimeContext,
    _fresh_memory,
    _validate_process_identity,
    active_ui_component_objects,
    acquire_ui_runtime_context,
    _validate_binding_fast,
)


def _binding(*, pid: int = 7, start_ticks: int = 11) -> UiRuntimeBinding:
    return UiRuntimeBinding(
        pid=pid,
        process_start_ticks=start_ticks,
        adb_serial="127.0.0.1:1",
        regions=(MemoryRegion(0x1000, 0x2000, "r--p", "x"),),
        state_address=0x1010,
        environment_address=0x1020,
        string_table_address=0x1030,
        string_mask=1023,
        string_seed=0,
        key_addresses={"m_panel": 0x1040},
        manager_module_address=0x1050,
        manager_instance_address=0x1060,
        components_address=0x1070,
        component_storage_address=0x1080,
    )


def test_fresh_memory_reuses_only_identity_and_never_mutable_page_cache():
    binding = _binding()
    first = _fresh_memory(binding)
    second = _fresh_memory(binding)

    first._read_cache[(0x1000, 8)] = b"old-page"
    assert second.pid == binding.pid
    assert second.process_start_ticks == binding.process_start_ticks
    assert second._read_cache == {}


def test_bound_process_identity_rejects_start_tick_or_device_change(monkeypatch):
    binding = _binding()
    stat = f"7 (game) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 {binding.process_start_ticks} 20"
    monkeypatch.setattr(
        ui_runtime_context.mumu_control,
        "_mumu_adb_session_shell_bytes",
        lambda *_args, **_kwargs: (
            stat.encode(),
            {"adb_serial": binding.adb_serial},
        ),
    )
    _validate_process_identity(binding)

    changed = stat.replace(
        f" {binding.process_start_ticks} 20", f" {binding.process_start_ticks + 1} 20"
    )
    monkeypatch.setattr(
        ui_runtime_context.mumu_control,
        "_mumu_adb_session_shell_bytes",
        lambda *_args, **_kwargs: (
            changed.encode(),
            {"adb_serial": binding.adb_serial},
        ),
    )
    with pytest.raises(FanxiuRuntimeMemoryError, match="PID/start_ticks"):
        _validate_process_identity(binding)


def test_acquire_uses_hot_binding_then_one_cold_rebind_on_validation_failure(
    monkeypatch,
):
    old = _binding()
    new = _binding(pid=8, start_ticks=12)
    monkeypatch.setattr(ui_runtime_context, "_binding_cache", old)
    calls = {"validate": 0, "discover": 0, "build": 0}

    def fail_validate(binding, *, required_keys, timings):
        calls["validate"] += 1
        raise FanxiuRuntimeMemoryError("process changed")

    memory = MumuProcessMemory(
        pid=new.pid,
        process_start_ticks=new.process_start_ticks,
        adb_serial=new.adb_serial,
        regions=new.regions,
    )

    def discover():
        calls["discover"] += 1
        return memory

    def build(memory_arg, *, required_keys, timings):
        calls["build"] += 1
        assert memory_arg is memory
        return new

    monkeypatch.setattr(ui_runtime_context, "_validate_binding", fail_validate)
    monkeypatch.setattr(MumuProcessMemory, "discover", staticmethod(discover))
    monkeypatch.setattr(ui_runtime_context, "_build_binding", build)

    result = acquire_ui_runtime_context(())
    assert result.cache_mode == "cold"
    assert result.binding is new
    assert calls == {"validate": 1, "discover": 1, "build": 1}


def test_acquire_hot_path_does_not_rediscover_process(monkeypatch):
    binding = _binding()
    monkeypatch.setattr(ui_runtime_context, "_binding_cache", binding)
    calls = {"validate": 0}

    def validate(binding_arg, *, required_keys, timings):
        calls["validate"] += 1
        memory = _fresh_memory(binding_arg)
        return UiRuntimeContext(
            memory,
            object.__new__(LuaJitReader),
            binding_arg,
            timings,
            "hot",
        )

    monkeypatch.setattr(ui_runtime_context, "_validate_binding", validate)
    monkeypatch.setattr(
        MumuProcessMemory,
        "discover",
        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("cold discover"))),
    )

    results = [acquire_ui_runtime_context(()) for _ in range(8)]
    assert all(result.cache_mode == "hot" for result in results)
    assert calls["validate"] == 8


def test_fast_validation_rejects_same_pid_with_changed_primary_lua_state(monkeypatch):
    binding = _binding()

    class Memory:
        pass

    monkeypatch.setattr(ui_runtime_context, "_fresh_memory", lambda _binding: Memory())
    monkeypatch.setattr(ui_runtime_context, "_validate_process_identity", lambda _binding: None)
    monkeypatch.setattr(
        ui_runtime_context,
        "_lua_addresses",
        lambda _memory: {"state": hex(binding.state_address + 8)},
    )

    with pytest.raises(FanxiuRuntimeMemoryError, match="state/environment"):
        _validate_binding_fast(binding, required_keys=frozenset(), timings={})


def test_fast_validation_rejects_same_pid_with_changed_loaded_ui_module(monkeypatch):
    binding = _binding()

    class Memory:
        def read(self, address, size):
            assert (address, size) == (binding.state_address + 72, 8)
            return binding.environment_address.to_bytes(8, "little")

    monkeypatch.setattr(ui_runtime_context, "_fresh_memory", lambda _binding: Memory())
    monkeypatch.setattr(ui_runtime_context, "_validate_process_identity", lambda _binding: None)
    monkeypatch.setattr(
        ui_runtime_context,
        "_lua_addresses",
        lambda _memory: {"state": hex(binding.state_address)},
    )
    monkeypatch.setattr(ui_runtime_context, "LuaJitReader", lambda _memory: object())

    def root_field(_reader, _keys, _address, name):
        return {
            "package": LuaRef("table", 1),
            "loaded": LuaRef("table", 2),
            "Core.UIManager.Manager.UIShowMgr": LuaRef("table", 0x9999),
        }[name]

    monkeypatch.setattr(ui_runtime_context, "_root_field", root_field)

    with pytest.raises(FanxiuRuntimeMemoryError, match="module identity"):
        _validate_binding_fast(binding, required_keys=frozenset(), timings={})


def test_snapshot_reader_cold_rebinds_once_after_child_memory_fault(monkeypatch):
    first = object()
    second = object()
    contexts = iter((first, second))
    cleared: list[bool] = []

    monkeypatch.setattr(
        ui_runtime_context,
        "acquire_ui_runtime_context",
        lambda _keys: next(contexts),
    )
    monkeypatch.setattr(
        ui_runtime_context,
        "clear_ui_runtime_context_cache",
        lambda: cleared.append(True),
    )

    def snapshot(context):
        if context is first:
            raise FanxiuRuntimeMemoryError("Runtime 内存地址越界")
        return "fresh"

    assert ui_runtime_context.read_ui_runtime_snapshot((), snapshot) == "fresh"
    assert cleared == [True]


class _ActiveComponentReader:
    def __init__(self, *, roots, fields, lists, wrapper_fields=None):
        self.roots = roots
        self.link_values = fields
        self.lists = lists
        self.wrapper_fields = wrapper_fields or {}
        self.link_reads: list[tuple[int, str]] = []

    def dictionary_fields(self, value):
        assert value.address == 0x1070
        return self.roots

    def fields(self, value):
        return self.wrapper_fields.get(value.address, {})

    def interned_string_field(self, address, name, **_kwargs):
        self.link_reads.append((address, name))
        return self.link_values.get((address, name))

    def hashed_string_field(self, address, *, expected_name, **_kwargs):
        self.link_reads.append((address, expected_name))
        return self.link_values.get((address, expected_name))

    def metatable_index_string_field(self, address, name, **_kwargs):
        self.link_reads.append((address, f"prototype:{name}"))
        return self.link_values.get((address, f"prototype:{name}"))

    def indexed_list_items(self, value):
        rows = self.lists.get(value.address)
        if isinstance(rows, Exception):
            raise rows
        if rows is None:
            raise FanxiuRuntimeMemoryError("not a CList")
        return rows, len(rows)


def _active_component_context(*, roots, fields=None, lists=None, wrapper_fields=None):
    binding = _binding()
    reader = _ActiveComponentReader(
        roots=roots,
        fields=fields or {},
        lists=lists or {},
        wrapper_fields=wrapper_fields,
    )
    return UiRuntimeContext(
        memory=object(),
        reader=reader,
        binding=binding,
        timings={},
        cache_mode="test",
    )


def _ref(address: int) -> LuaRef:
    return LuaRef("table", address)


def test_active_component_traversal_only_follows_verified_links_and_deduplicates():
    context = _active_component_context(
        roots={1: _ref(10), 2: _ref(11), 3: _ref(10)},
        fields={
            (10, "m_panel"): _ref(20),
            (20, "m_ChildCompList"): _ref(30),
            (11, "m_ChildCompList"): _ref(31),
            (21, "m_panel"): _ref(20),  # cycle back to an existing panel
            (10, "unrelated_list"): _ref(99),
        },
        lists={
            30: [(1, _ref(21)), (2, _ref(11))],
            31: [(1, _ref(20))],
            # A legacy direct list on a registry component must never be read.
            10: FanxiuRuntimeMemoryError("root direct list was traversed"),
        },
    )

    objects = active_ui_component_objects(context)

    assert [item.address for item in objects] == [10, 11, 20, 21]
    assert {name.removeprefix("prototype:") for _address, name in context.reader.link_reads} <= {
        "m_panel",
        "m_ChildCompList",
    }


def test_active_component_traversal_expands_only_declared_registry_index_list():
    context = _active_component_context(
        roots={1: _ref(10)},
        wrapper_fields={10: {"_dt_": _ref(20), "count": 1}},
        lists={10: [(1, _ref(11))]},
    )

    assert [item.address for item in active_ui_component_objects(context)] == [10, 11]


def test_active_component_traversal_stops_after_verified_panel_child_layer():
    context = _active_component_context(
        roots={1: _ref(1)},
        fields={
            (1, "m_panel"): _ref(2),
            (2, "m_ChildCompList"): _ref(20),
            (3, "m_panel"): _ref(4),
        },
        lists={20: [(1, _ref(3))]},
    )

    objects = active_ui_component_objects(context)

    assert [item.address for item in objects] == [1, 2, 3]
    assert (3, "m_panel") not in context.reader.link_reads


def test_active_component_traversal_rejects_oversized_child_list():
    context = _active_component_context(
        roots={1: _ref(1)},
        fields={(1, "m_panel"): _ref(10), (10, "m_ChildCompList"): _ref(2)},
        lists={2: [(index, _ref(100 + index)) for index in range(65)]},
    )

    with pytest.raises(FanxiuRuntimeMemoryError, match="超过 64") as exc:
        active_ui_component_objects(context)

    assert exc.value.code == "runtime_incomplete"


def test_active_component_traversal_rejects_total_closure_overflow():
    context = _active_component_context(
        roots={index: _ref(index + 1) for index in range(257)},
    )

    with pytest.raises(FanxiuRuntimeMemoryError, match="超过 256") as exc:
        active_ui_component_objects(context)

    assert exc.value.code == "runtime_incomplete"


def test_active_component_traversal_fails_closed_on_child_list_memory_fault():
    context = _active_component_context(
        roots={1: _ref(1)},
        fields={(1, "m_panel"): _ref(10), (10, "m_ChildCompList"): _ref(2)},
        lists={2: FanxiuRuntimeMemoryError("stale list")},
    )

    with pytest.raises(FanxiuRuntimeMemoryError, match="无法完整读取") as exc:
        active_ui_component_objects(context)

    assert exc.value.code == "runtime_incomplete"
