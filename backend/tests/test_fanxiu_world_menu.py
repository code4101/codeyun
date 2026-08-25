from __future__ import annotations

from dataclasses import dataclass

import pytest

from backend.core.fanxiu.instrumentation import ui_runtime_context
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaRef,
)
from backend.core.fanxiu.instrumentation import world_menu
from backend.core.fanxiu.instrumentation.world_menu import (
    WorldMenuItem,
    WorldMenuReadTimings,
    WorldMenuSnapshot,
)
from backend.core.fanxiu.runtime_gui import (
    OrderedMenuGrid,
    plan_world_menu_click,
    verify_world_menu_successor,
)


def _snapshot() -> WorldMenuSnapshot:
    names = (
        "角色", "装备", "异火", "功法书", "剑灵", "神器", "万灵", "灵器",
        "家族", "宗门", "法宝", "灵兽", "仙缘", "邮件", "设置",
    )
    return WorldMenuSnapshot(
        complete=True,
        items=tuple(
            WorldMenuItem(index=index, function_id=index * 1000, name=name)
            for index, name in enumerate(names, start=1)
        ),
        pid=7,
        process_start_ticks=11,
        fingerprint="menu-v1",
        timings=WorldMenuReadTimings(1, 2, 3, 6, "hot/object-hot"),
    )


def test_partial_unique_ocr_uses_label_above_icon_business_geometry() -> None:
    plan = plan_world_menu_click(
        _snapshot(),
        "灵兽",
        [{"key": "ocr-兽", "text": "兽", "box": (731, 1424, 36, 38)}],
        expected_scene_ids=[483],
    )

    assert plan.ready
    assert plan.target is not None and plan.target.index == 12
    assert plan.point == (749.0, 1386.0)
    assert verify_world_menu_successor(plan, 483)
    assert not verify_world_menu_successor(plan, 34)


def test_split_ocr_tokens_are_grouped_by_parent_line_before_alignment() -> None:
    plan = plan_world_menu_click(
        _snapshot(),
        "灵兽",
        [
            {"text": "灵", "x": 703, "y": 1424, "w": 35, "h": 37, "parent_line_id": "line-65", "order": 0},
            {"text": "兽", "x": 732, "y": 1424, "w": 36, "h": 37, "parent_line_id": "line-65", "order": 1},
        ],
        expected_scene_ids=[483],
    )

    assert plan.ready
    assert plan.point == (735.5, 1387.0)


def test_four_column_grid_projects_target_from_another_unique_anchor() -> None:
    plan = plan_world_menu_click(
        _snapshot(),
        "灵兽",
        [{"key": "mail", "text": "邮件", "box": (700, 1500, 40, 40)}],
        expected_scene_ids=[483],
        grid=OrderedMenuGrid(columns=4, column_pitch=100, row_pitch=-90),
    )

    # 邮件 index=14 is row=3,col=1; 灵兽 index=12 is row=2,col=3.
    assert plan.ready
    assert plan.point == (920.0, 1550.0)


def test_missing_runtime_target_or_successor_contract_fails_closed() -> None:
    missing = plan_world_menu_click(
        _snapshot(), "商店", [{"text": "设置", "box": (1, 2, 3, 4)}],
        expected_scene_ids=[99],
    )
    unverified = plan_world_menu_click(
        _snapshot(), "灵兽", [{"text": "兽", "box": (731, 1424, 36, 38)}],
        expected_scene_ids=[],
    )

    assert missing.status == "target_not_found" and not missing.ready
    assert unverified.status == "insufficient_geometry" and not unverified.ready


@dataclass
class _Binding:
    pid: int = 7
    process_start_ticks: int = 11
    components_address: int = 1


class _Reader:
    def dictionary_fields(self, value):
        assert value == LuaRef("table", 1)
        return {34: LuaRef("table", 10)}

    def indexed_list_items(self, value):
        if value.address == 10:
            return [], None
        if value.address == 40:
            return [(1, LuaRef("table", 101)), (2, LuaRef("table", 102))], 2
        if value.address == 50:
            return [(1, LuaRef("table", 301)), (2, LuaRef("table", 302))], 2
        raise AssertionError(value)


class _Context:
    cache_mode = "hot-fast"
    binding = _Binding()
    reader = _Reader()

    _fields = {
        (10, "BottomContent"): LuaRef("table", 20),
        (20, "FuncBtnList"): LuaRef("table", 30),
        (30, "_BtnDataList"): LuaRef("table", 40),
        (30, "_BtnList"): LuaRef("table", 50),
        (301, "_CurFuncId"): 1000,
        (302, "_CurFuncId"): 4000,
        (101, "data"): LuaRef("table", 201),
        (102, "data"): LuaRef("table", 202),
        (201, "id"): 1000,
        (201, "name"): "角色",
        (201, "luaPath"): "PlayerMainPanel",
        (201, "windowId"): "",
        (201, "sort"): 1,
        (202, "id"): 4000,
        (202, "name"): "灵兽",
        (202, "luaPath"): "WinPetMainView",
        (202, "windowId"): "",
        (202, "sort"): 12,
    }

    def field(self, address, name):
        return self._fields.get((address, name))

    def object_field(self, address, name):
        return self.field(address, name)


def test_runtime_reader_descends_from_window_wrapper_to_main_panel(monkeypatch) -> None:
    world_menu.clear_world_menu_cache()
    context = _Context()
    context._fields = {
        **context._fields,
        (10, "BottomContent"): None,
        (10, "m_panel"): LuaRef("table", 11),
        (11, "BottomContent"): LuaRef("table", 20),
    }
    monkeypatch.setattr(world_menu, "acquire_ui_runtime_context_fast", lambda _keys: context)

    snapshot = world_menu.read_world_menu_snapshot()

    assert [item.name for item in snapshot.items] == ["角色", "灵兽"]


def test_runtime_reader_relocates_once_then_uses_process_bound_object_cache(monkeypatch) -> None:
    world_menu.clear_world_menu_cache()
    context = _Context()
    monkeypatch.setattr(world_menu, "acquire_ui_runtime_context_fast", lambda _keys: context)

    first = world_menu.read_world_menu_snapshot()
    second = world_menu.read_world_menu_snapshot()

    assert [item.name for item in first.items] == ["角色", "灵兽"]
    assert first.fingerprint == second.fingerprint
    assert first.timings.cache_mode == "hot-fast/relocated"
    assert second.timings.cache_mode == "hot-fast/object-hot"
    assert second.timings.binding_ms >= 0
    assert second.timings.locate_ms >= 0
    assert second.timings.decode_ms >= 0


def test_runtime_reader_skips_one_stale_component_but_keeps_unique_menu_gate(
    monkeypatch,
) -> None:
    world_menu.clear_world_menu_cache()
    context = _Context()
    context.reader = _Reader()
    context.reader.dictionary_fields = lambda _value: {
        47: LuaRef("table", 99),
        34: LuaRef("table", 10),
    }
    original_indexed = context.reader.indexed_list_items
    context.reader.indexed_list_items = lambda value: (
        ([], None) if value.address == 99 else original_indexed(value)
    )
    original_field = context.field

    def field(address, name):
        if address == 99:
            raise FanxiuRuntimeMemoryError("stale component address")
        return original_field(address, name)

    context.field = field
    monkeypatch.setattr(world_menu, "acquire_ui_runtime_context_fast", lambda _keys: context)

    snapshot = world_menu.read_world_menu_snapshot()

    assert [item.name for item in snapshot.items] == ["角色", "灵兽"]
    assert snapshot.timings.cache_mode == "hot-fast/relocated"


def test_runtime_reader_reports_not_loaded_instead_of_empty_menu(monkeypatch) -> None:
    world_menu.clear_world_menu_cache()
    context = _Context()
    context.reader = _Reader()
    context.reader.dictionary_fields = lambda _value: {47: LuaRef("table", 99)}
    context.reader.indexed_list_items = lambda _value: ([], None)
    monkeypatch.setattr(world_menu, "acquire_ui_runtime_context_fast", lambda _keys: context)

    with pytest.raises(FanxiuRuntimeMemoryError) as error:
        world_menu.read_world_menu_snapshot()

    assert error.value.code == "data_not_loaded"


def test_ui_binding_does_not_require_unrelated_lazily_interned_keys(monkeypatch) -> None:
    requested: list[str] = []

    def resolve(_memory, **kwargs):
        requested.append(kwargs["name"])
        return 0x1000 + len(requested)

    class Memory:
        pid = 7
        process_start_ticks = 11
        adb_serial = "serial"
        regions = ()

        def read(self, address, size):
            if address == 0x48:
                return (0x200).to_bytes(8, "little")
            return bytes(size)

    monkeypatch.setattr(ui_runtime_context, "_lua_addresses", lambda _m: {"state": "0x0"})
    monkeypatch.setattr(ui_runtime_context, "lua_jit_intern_state", lambda *_a: (0, 1, 1, 0))
    monkeypatch.setattr(ui_runtime_context, "resolve_interned_lua_string", resolve)
    monkeypatch.setattr(
        ui_runtime_context,
        "_root_field",
        lambda *_a, **_k: (_ for _ in ()).throw(FanxiuRuntimeMemoryError("stop")),
    )

    with pytest.raises(FanxiuRuntimeMemoryError, match="stop"):
        ui_runtime_context._build_binding(
            Memory(), required_keys=frozenset({"BottomContent"}), timings={}
        )

    assert "BottomContent" in requested
    assert "BackPackQuickItem" not in requested
