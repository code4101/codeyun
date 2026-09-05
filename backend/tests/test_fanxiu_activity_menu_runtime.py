from __future__ import annotations

from dataclasses import dataclass

import pytest

from backend.core.fanxiu.data_annotation.tasks import (
    activity_menu_navigation as navigation_module,
)
from backend.core.fanxiu.data_annotation.tasks.activity_menu_navigation import (
    open_loaded_activity_menu_item,
)
from backend.core.fanxiu.instrumentation import activity_menu as module
from backend.core.fanxiu.instrumentation.activity_menu import (
    ActivityMenuItem,
    ActivityMenuReadTimings,
    ActivityMenuSnapshot,
    clear_activity_menu_cache,
    read_activity_menu_snapshot,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaRef,
)
from backend.core.fanxiu.runtime_gui.activity_menu import (
    ActivityMenuGrid,
    GROUP_POPUP_ACTIVITY_GRID,
    WORLD_LEFT_ACTIVITY_GRID,
    plan_activity_menu_click,
)


@dataclass(frozen=True)
class Binding:
    pid: int = 123
    process_start_ticks: int = 456
    components_address: int = 1
    string_table_address: int = 2
    string_mask: int = 3
    string_seed: int = 4


class Reader:
    def __init__(self, fields, dictionaries, lists, tables=None):
        self.field_values = fields
        self.dictionaries = dictionaries
        self.lists = lists
        self.tables = tables or {}
        self.dictionary_calls = []

    def dictionary_fields(self, ref):
        self.dictionary_calls.append(ref.address)
        if ref.address not in self.dictionaries:
            raise FanxiuRuntimeMemoryError("not a dictionary")
        return self.dictionaries[ref.address]

    def fields(self, ref):
        explicit = self.tables.get(ref.address)
        if explicit is not None:
            return explicit
        return {
            name: value
            for (address, name), value in self.field_values.items()
            if address == ref.address
        }

    def indexed_list_items(self, ref):
        if ref.address not in self.lists:
            raise FanxiuRuntimeMemoryError("not a list")
        rows = self.lists[ref.address]
        return rows, len(rows)

    def metatable_index_string_field(self, address, name, **_kwargs):
        return self.field_values.get((address, name))

    def interned_string_field(self, address, name, **_kwargs):
        return self.field_values.get((address, name))


class Context:
    def __init__(
        self, fields, dictionaries, lists, cache_mode="hot-fast", tables=None
    ):
        self.binding = Binding()
        self.reader = Reader(fields, dictionaries, lists, tables=tables)
        self.fields = fields
        self.cache_mode = cache_mode
        self.timings = {}

    def field(self, address, name):
        return self.fields.get((address, name))

    def object_field(self, address, name):
        return self.field(address, name)


def ref(address: int) -> LuaRef:
    return LuaRef("table", address)


def _world_context(cache_mode="hot-fast") -> Context:
    fields = {
        (10, "m_panel"): ref(20),
        (20, "ActContent"): ref(30),
        (30, "V_BtnList"): ref(40),
        (30, "ActivityContent"): ref(50),
        (60, "activityId"): 0,
        (60, "groupType"): 110001,
        (60, "name"): "特惠",
        (60, "sort"): 5,
        (60, "isCustom"): True,
        (61, "activityId"): 998877,
        (61, "name"): "",
        (61, "sort"): 9,
    }
    dictionaries = {1: {9951.0: ref(10)}}
    lists = {10: [], 40: [(0, ref(60)), (1, ref(61))]}
    return Context(fields, dictionaries, lists, cache_mode)


def _current_world_context(cache_mode="hot-fast") -> Context:
    fields = {
        (10, "m_panel"): ref(20),
        (20, "TopContent"): ref(30),
        (30, "BtnNodeComp"): ref(31),
        (31, "_CurList"): ref(40),
        (31, "ActivityBtnItem"): ref(41),
        (31, "Content"): ref(42),
        (31, "ChangeBtn"): ref(43),
        (31, "_IsOpen"): True,
        (50, "_DataIndex"): 1,
        (50, "_Data"): ref(60),
        (51, "_DataIndex"): 2,
        (51, "_Data"): ref(61),
        (52, "_DataIndex"): -1,
        (60, "activityId"): 998877,
        (60, "name"): "限时活动",
        (61, "activityId"): 0,
        (61, "groupType"): 110001,
        (61, "name"): "特惠",
    }
    dictionaries = {1: {2: ref(10)}}
    lists = {
        10: [],
        40: [(1, ref(50)), (2, ref(51)), (3, ref(52))],
    }
    return Context(fields, dictionaries, lists, cache_mode)


def _group_context(*, conflicting=False) -> Context:
    fields = {
        (10, "m_panel"): ref(20),
        (20, "activityContent"): ref(30),
        (20, "activityBtnItem"): ref(31),
        (60, "activityId"): 101,
        (60, "name"): "每日签到",
        (61, "activityId"): 102,
        (61, "name"): "每日限购",
        (70, "itemVo"): ref(80),
        (71, "itemVo"): ref(81),
        (80, "_Data"): ref(60),
        (81, "_Data"): ref(61),
        (62, "activityId"): 999,
        (62, "name"): "另一组",
    }
    dictionaries = {1: {49.0: ref(10)}}
    tables = {
        30: {"data": ref(40), "rendered": ref(50)},
        # Visible item views are an ordinary numeric Lua table, not CList.
        50: {1: ref(70), 2: ref(71)},
    }
    lists = {
        10: [],
        40: [(0, ref(60)), (1, ref(61))],
    }
    if conflicting:
        tables[30]["other"] = ref(41)
        tables[41] = {1: ref(62)}
        lists[41] = [(0, ref(62))]
    return Context(fields, dictionaries, lists, tables=tables)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_activity_menu_cache()
    yield
    clear_activity_menu_cache()


def test_world_left_reads_authoritative_order_and_preserves_unknown(monkeypatch):
    ctx = _world_context()
    monkeypatch.setattr(module, "acquire_ui_runtime_context_fast", lambda _keys: ctx)

    snapshot = read_activity_menu_snapshot("world_left")

    assert snapshot.complete is True
    assert [item.key for item in snapshot.items] == ["group:110001", "activity:998877"]
    assert [item.name for item in snapshot.items] == ["特惠", "活动998877"]
    assert snapshot.fingerprint


def test_world_left_reads_lazy_menu_fields_without_dictionary_scan(monkeypatch):
    ctx = _world_context()
    original_field = ctx.field

    def root_cache_misses_lazy_menu_keys(address, name):
        if name in {"m_panel", "ActContent", "V_BtnList", "ActivityContent"}:
            raise FanxiuRuntimeMemoryError("UI Runtime 未缓存字符串键")
        return original_field(address, name)

    ctx.field = root_cache_misses_lazy_menu_keys
    monkeypatch.setattr(module, "acquire_ui_runtime_context_fast", lambda _keys: ctx)

    snapshot = read_activity_menu_snapshot("world_left")

    assert snapshot.complete is True
    assert [item.key for item in snapshot.items] == [
        "group:110001",
        "activity:998877",
    ]


def test_world_left_reuses_validated_object_address(monkeypatch):
    ctx = _world_context()
    monkeypatch.setattr(module, "acquire_ui_runtime_context_fast", lambda _keys: ctx)

    first = read_activity_menu_snapshot("world_left")
    second = read_activity_menu_snapshot("world_left")

    assert first.timings.cache_mode.endswith("/relocated")
    assert second.timings.cache_mode.endswith("/object-hot")
    assert second.items == first.items


def test_world_left_reads_current_main_ui_row_pool(monkeypatch):
    ctx = _current_world_context()
    monkeypatch.setattr(module, "acquire_ui_runtime_context_fast", lambda _keys: ctx)

    snapshot = read_activity_menu_snapshot("world_left")

    assert snapshot.complete is True
    assert [item.key for item in snapshot.items] == [
        "activity:998877",
        "group:110001",
    ]
    assert [item.name for item in snapshot.items] == ["限时活动", "特惠"]


def test_world_left_current_pool_preserves_view_order_across_recycled_holes(monkeypatch):
    ctx = _current_world_context()
    # Physical pool order is the UI order; source _DataIndex is identity only.
    ctx.reader.lists[40] = [
        (1, ref(52)),
        (2, ref(51)),
        (3, ref(50)),
    ]
    monkeypatch.setattr(module, "acquire_ui_runtime_context_fast", lambda _keys: ctx)

    snapshot = read_activity_menu_snapshot("world_left")

    assert snapshot.complete is True
    assert [item.key for item in snapshot.items] == ["group:110001", "activity:998877"]
    assert [item.index for item in snapshot.items] == [1, 2]


def test_world_left_current_pool_compacts_hidden_source_index_gap(monkeypatch):
    ctx = _current_world_context()
    ctx.fields[(52, "_DataIndex")] = 3
    ctx.fields[(52, "_Data")] = ref(62)
    ctx.fields[(62, "activityId")] = 123
    # Turn the second slot into a pooled hole before the later active row.
    ctx.fields[(51, "_DataIndex")] = -1
    ctx.fields.pop((51, "_Data"))
    monkeypatch.setattr(module, "acquire_ui_runtime_context_fast", lambda _keys: ctx)

    snapshot = read_activity_menu_snapshot("world_left")

    assert snapshot.complete is True
    assert [item.index for item in snapshot.items] == [1, 2]
    assert [item.key for item in snapshot.items] == ["activity:998877", "activity:123"]


def test_world_left_current_pool_rejects_duplicate_logical_indices(monkeypatch):
    ctx = _current_world_context()
    ctx.fields[(52, "_DataIndex")] = 2
    ctx.fields[(52, "_Data")] = ref(62)
    ctx.fields[(62, "activityId")] = 123
    monkeypatch.setattr(module, "acquire_ui_runtime_context_fast", lambda _keys: ctx)

    with pytest.raises(FanxiuRuntimeMemoryError, match="逻辑序号重复：2") as exc:
        read_activity_menu_snapshot("world_left")

    assert exc.value.code == "runtime_incomplete"


def test_world_left_current_binding_requires_expanded_controller(monkeypatch):
    ctx = _current_world_context()
    ctx.fields[(31, "_IsOpen")] = False
    monkeypatch.setattr(module, "acquire_ui_runtime_context_fast", lambda _keys: ctx)

    snapshot = read_activity_menu_snapshot("world_left")

    assert snapshot.status == "not_loaded"


def test_not_loaded_is_explicit_and_does_not_invent_an_empty_menu(monkeypatch):
    ctx = Context({}, {1: {}}, {})
    monkeypatch.setattr(module, "acquire_ui_runtime_context_fast", lambda _keys: ctx)

    snapshot = read_activity_menu_snapshot("world_left")

    assert snapshot.status == "not_loaded"
    assert snapshot.complete is False
    assert snapshot.items == ()
    assert "尚未自然加载" in snapshot.reason


def test_group_popup_rebinds_current_scroll_data_and_deduplicates_views(monkeypatch):
    ctx = _group_context()
    monkeypatch.setattr(module, "acquire_ui_runtime_context_fast", lambda _keys: ctx)

    snapshot = read_activity_menu_snapshot("group_popup")

    assert snapshot.complete is True
    assert [item.name for item in snapshot.items] == ["每日签到", "每日限购"]
    assert snapshot.timings.cache_mode.endswith("/current-view-rebound")
    assert 30 not in ctx.reader.dictionary_calls
    assert 50 not in ctx.reader.dictionary_calls


def test_group_popup_resolves_function_backed_activity_config_name(monkeypatch):
    ctx = _group_context()
    ctx.fields[(60, "activityId")] = 1310001
    ctx.fields[(60, "name")] = ""
    ctx.fields[(60, "activityType")] = 110
    ctx.fields[(60, "baseId")] = 1310001
    ctx.fields[(60, "activitylo")] = ref(90)
    monkeypatch.setattr(module, "acquire_ui_runtime_context_fast", lambda _keys: ctx)
    monkeypatch.setattr(
        module,
        "_activity_definition_index",
        lambda: {
            1310001: {
                "id": 1310001,
                "activityId": 110,
                "baseId": 1310001,
                "name_plain": "每日签到",
            }
        },
    )

    snapshot = read_activity_menu_snapshot("group_popup")

    assert snapshot.items[0].key == "activity:1310001"
    assert snapshot.items[0].name == "每日签到"


def test_group_popup_rejects_static_name_when_runtime_identity_disagrees(monkeypatch):
    ctx = _group_context()
    ctx.fields[(60, "activityId")] = 1310001
    ctx.fields[(60, "name")] = ""
    ctx.fields[(60, "activityType")] = 110
    ctx.fields[(60, "baseId")] = 999999
    ctx.fields[(60, "activitylo")] = ref(90)
    monkeypatch.setattr(module, "acquire_ui_runtime_context_fast", lambda _keys: ctx)
    monkeypatch.setattr(
        module,
        "_activity_definition_index",
        lambda: {
            1310001: {
                "id": 1310001,
                "activityId": 110,
                "baseId": 1310001,
                "name_plain": "每日签到",
            }
        },
    )

    snapshot = read_activity_menu_snapshot("group_popup")

    assert snapshot.items[0].name == "活动1310001"


def test_group_popup_fails_closed_when_two_different_sequences_are_live(monkeypatch):
    ctx = _group_context(conflicting=True)
    monkeypatch.setattr(module, "acquire_ui_runtime_context_fast", lambda _keys: ctx)

    with pytest.raises(FanxiuRuntimeMemoryError, match="不同的可见业务序列") as exc:
        read_activity_menu_snapshot("group_popup")

    assert exc.value.code == "runtime_incomplete"


def test_group_popup_finds_verified_deep_child_controller(monkeypatch):
    """The real ActivityBtnGroup is a child below the active panel."""

    ctx = _group_context()
    ctx.fields.pop((20, "activityContent"))
    ctx.fields.pop((20, "activityBtnItem"))
    ctx.fields[(20, "m_ChildCompList")] = ref(21)
    ctx.fields[(22, "activityContent")] = ref(30)
    ctx.fields[(22, "activityBtnItem")] = ref(31)
    ctx.reader.lists[21] = [(0, ref(22))]
    monkeypatch.setattr(module, "acquire_ui_runtime_context_fast", lambda _keys: ctx)

    snapshot = read_activity_menu_snapshot("group_popup")

    assert snapshot.complete is True
    assert [item.name for item in snapshot.items] == ["每日签到", "每日限购"]


def test_group_popup_rejects_two_deep_controller_sequences(monkeypatch):
    ctx = _group_context()
    ctx.fields.pop((20, "activityContent"))
    ctx.fields.pop((20, "activityBtnItem"))
    ctx.fields[(20, "m_ChildCompList")] = ref(21)
    ctx.fields[(22, "activityContent")] = ref(30)
    ctx.fields[(22, "activityBtnItem")] = ref(31)
    ctx.fields[(23, "activityContent")] = ref(41)
    ctx.fields[(23, "activityBtnItem")] = ref(31)
    ctx.reader.tables[41] = {"data": ref(42), "rendered": ref(43)}
    ctx.reader.lists[42] = [(0, ref(62))]
    ctx.reader.tables[43] = {1: ref(72)}
    ctx.fields[(72, "itemVo")] = ref(82)
    ctx.fields[(82, "_Data")] = ref(62)
    ctx.reader.lists[21] = [(0, ref(22)), (1, ref(23))]
    monkeypatch.setattr(module, "acquire_ui_runtime_context_fast", lambda _keys: ctx)

    with pytest.raises(FanxiuRuntimeMemoryError, match="对象不唯一") as exc:
        read_activity_menu_snapshot("group_popup")

    assert exc.value.code == "runtime_incomplete"


def test_group_popup_rejects_oversized_verified_child_list(monkeypatch):
    ctx = _group_context()
    ctx.fields.pop((20, "activityContent"))
    ctx.fields.pop((20, "activityBtnItem"))
    ctx.fields[(20, "m_ChildCompList")] = ref(21)
    ctx.reader.lists[21] = [(index, ref(1000 + index)) for index in range(65)]
    monkeypatch.setattr(module, "acquire_ui_runtime_context_fast", lambda _keys: ctx)

    with pytest.raises(FanxiuRuntimeMemoryError, match="子组件超过") as exc:
        read_activity_menu_snapshot("group_popup")

    assert exc.value.code == "runtime_incomplete"


def test_group_popup_keeps_large_direct_uishowmgr_enumeration(monkeypatch):
    """Only descendants are bounded; the pre-existing root registry is not."""

    ctx = _group_context()
    ctx.reader.dictionaries[1].update(
        {index + 100: ref(index + 1000) for index in range(300)}
    )
    monkeypatch.setattr(module, "acquire_ui_runtime_context_fast", lambda _keys: ctx)

    snapshot = read_activity_menu_snapshot("group_popup")

    assert snapshot.complete is True
    assert [item.name for item in snapshot.items] == ["每日签到", "每日限购"]


def test_group_popup_does_not_treat_child_without_schema_pair_as_controller(monkeypatch):
    ctx = _group_context()
    ctx.fields.pop((20, "activityContent"))
    ctx.fields.pop((20, "activityBtnItem"))
    ctx.fields[(20, "m_ChildCompList")] = ref(21)
    ctx.fields[(22, "activityContent")] = ref(30)
    ctx.reader.lists[21] = [(0, ref(22))]
    monkeypatch.setattr(module, "acquire_ui_runtime_context_fast", lambda _keys: ctx)

    snapshot = read_activity_menu_snapshot("group_popup")

    assert snapshot.complete is False
    assert snapshot.status == "not_loaded"


def _snapshot(items, *, complete=True) -> ActivityMenuSnapshot:
    return ActivityMenuSnapshot(
        kind="group_popup",
        status="loaded" if complete else "not_loaded",
        complete=complete,
        items=tuple(items),
        pid=1,
        process_start_ticks=2,
        fingerprint="fp",
        reason="",
        timings=ActivityMenuReadTimings(0, 0, 0, 0, "test"),
    )


def test_planner_uses_runtime_identity_with_one_glyph_ocr_error():
    snapshot = _snapshot(
        [
            ActivityMenuItem(1, "activity:101", "每日签到", activity_id=101),
            ActivityMenuItem(2, "activity:102", "每日限购", activity_id=102),
        ]
    )

    plan = plan_activity_menu_click(
        snapshot,
        "每日签到",
        [{"text": "每曰签到", "x": 300, "y": 880, "w": 80, "h": 30}],
        grid=GROUP_POPUP_ACTIVITY_GRID,
    )

    assert plan.ready is True
    assert plan.target and plan.target.activity_id == 101
    assert plan.point == (340.0, 850.0)


def test_group_popup_planner_rejects_matching_world_menu_text_behind_popup():
    snapshot = _snapshot(
        [
            ActivityMenuItem(1, "activity:101", "每日签到", activity_id=101),
            ActivityMenuItem(2, "activity:102", "每日限购", activity_id=102),
        ]
    )

    plan = plan_activity_menu_click(
        snapshot,
        "每日签到",
        [
            {"text": "每日签到", "x": 400, "y": 190, "w": 80, "h": 30},
            {"text": "每日签到", "x": 300, "y": 880, "w": 80, "h": 30},
        ],
        grid=GROUP_POPUP_ACTIVITY_GRID,
    )

    assert plan.ready is True
    assert plan.point == (340.0, 850.0)


def test_planner_projects_target_from_ordered_grid_anchor():
    snapshot = _snapshot(
        [
            ActivityMenuItem(1, "activity:1", "甲", activity_id=1),
            ActivityMenuItem(2, "activity:2", "乙", activity_id=2),
            ActivityMenuItem(3, "activity:3", "丙", activity_id=3),
            ActivityMenuItem(4, "activity:4", "丁", activity_id=4),
            ActivityMenuItem(5, "activity:5", "目标", activity_id=5),
        ]
    )

    plan = plan_activity_menu_click(
        snapshot,
        "目标",
        [{"text": "甲", "box": (100, 200, 20, 20)}],
        grid=ActivityMenuGrid(
            columns=4,
            column_pitch=118,
            row_pitch=140,
            click_offset_heights=1,
        ),
    )

    assert plan.ready is True
    assert plan.point == (110.0, 320.0)


def test_planner_refuses_not_loaded_runtime():
    plan = plan_activity_menu_click(
        _snapshot([], complete=False),
        "特惠",
        [{"text": "特惠", "box": (1, 2, 3, 4)}],
        grid=WORLD_LEFT_ACTIVITY_GRID,
    )

    assert plan.status == "incomplete_runtime"
    assert plan.point is None


class _MenuNavigationContext:
    def __init__(self, *, landed_scene_id=635):
        self.landed_scene_id = landed_scene_id
        self.clicked = []

    def cur_frame(self, *, update):
        assert update is True
        return "data:image/png;base64,test"

    def ocr_tokens_in_shapes(self, scene_id, shape_names, *, frame_data_url):
        assert scene_id == 34
        assert shape_names == ("world-activity-menu",)
        assert frame_data_url == "data:image/png;base64,test"
        return [{"text": "万象宝阁", "box": (100, 200, 80, 20)}]

    def click_frame_point(self, scene_id, x, y):
        self.clicked.append((scene_id, x, y))

    def wait_scene(self, expected, *, wait, label):
        assert expected == (635, 636)
        assert wait == 20.0
        assert label == "活动菜单：等待 2030001 后继"
        if False:
            yield None
        return self.landed_scene_id


def _run_generator(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


def _world_navigation_snapshot(items, *, fingerprint="fp"):
    return ActivityMenuSnapshot(
        kind="world_left",
        status="loaded",
        complete=True,
        items=tuple(items),
        pid=1,
        process_start_ticks=2,
        fingerprint=fingerprint,
        reason="",
        timings=ActivityMenuReadTimings(0, 0, 0, 0, "test"),
    )


def _open_wanxiang(context, *, max_scrolls=0):
    return open_loaded_activity_menu_item(
        context,
        2030001,
        kind="world_left",
        source_scene_id=34,
        ocr_shape_names=("world-activity-menu",),
        expected_scene_ids=(635, 636),
        target_gui_name="万象宝阁",
        grid=ActivityMenuGrid(columns=1, click_offset_heights=0),
        max_scrolls=max_scrolls,
    )


def test_navigation_gui_alias_is_bound_to_unique_runtime_identity(monkeypatch):
    snapshot = _world_navigation_snapshot(
        [
            ActivityMenuItem(1, "activity:1", "其他活动", activity_id=1),
            ActivityMenuItem(2, "activity:2030001", "活动2030001", activity_id=2030001),
        ]
    )
    monkeypatch.setattr(
        navigation_module, "read_activity_menu_snapshot", lambda _kind: snapshot
    )
    context = _MenuNavigationContext()

    landed = _run_generator(_open_wanxiang(context))

    assert landed == 635
    assert context.clicked == [(34, 140.0, 200)]


def test_navigation_scrolls_until_exact_alias_is_visible(monkeypatch):
    snapshot = _world_navigation_snapshot(
        [ActivityMenuItem(2, "activity:2030001", "活动2030001", activity_id=2030001)]
    )
    monkeypatch.setattr(
        navigation_module, "read_activity_menu_snapshot", lambda _kind: snapshot
    )

    class ScrollContext(_MenuNavigationContext):
        def __init__(self):
            super().__init__()
            self.screen = 0
            self.scrolls = []

        def ocr_tokens_in_shapes(self, scene_id, shape_names, *, frame_data_url):
            if self.screen == 0:
                return [{"text": "累充豪礼", "box": (100, 200, 80, 20)}]
            return [{"text": "万象宝阁", "box": (100, 400, 80, 20)}]

        def scroll_shape_content(self, scene_id, shape_name, *, direction):
            self.scrolls.append((scene_id, shape_name, direction))
            self.screen += 1
            if False:
                yield None
            return True

    context = ScrollContext()
    landed = _run_generator(_open_wanxiang(context, max_scrolls=2))

    assert landed == 635
    assert context.scrolls == [(34, "world-activity-menu", "up")]
    assert context.clicked == [(34, 140.0, 400)]


def test_navigation_prefers_exact_alias_span_over_fuzzy_character(monkeypatch):
    snapshot = _world_navigation_snapshot(
        [
            ActivityMenuItem(3, "activity:505", "累充豪礼", activity_id=505),
            ActivityMenuItem(
                8,
                "group:110001",
                "特惠",
                activity_id=0,
                group_type=110001,
            ),
        ]
    )
    monkeypatch.setattr(
        navigation_module, "read_activity_menu_snapshot", lambda _kind: snapshot
    )

    class JoinedLabelContext(_MenuNavigationContext):
        def ocr_tokens_in_shapes(self, scene_id, shape_names, *, frame_data_url):
            assert scene_id == 34
            assert shape_names == ("左侧菜单",)
            return [
                {
                    "text": text,
                    "x": x,
                    "y": 875,
                    "w": width,
                    "h": 38,
                    "parent_line_id": "joined-row",
                    "order": order,
                }
                for order, (text, x, width) in enumerate(
                    (
                        ("累", 6, 41),
                        ("充", 38, 41),
                        ("豪", 64, 40),
                        ("礼", 102, 41),
                        ("特", 172, 40),
                        ("惠", 204, 33),
                    )
                )
            ]

        def wait_scene(self, expected, *, wait, label):
            assert expected == (403,)
            assert label == "活动菜单：等待 group:110001 后继"
            if False:
                yield None
            return 403

    context = JoinedLabelContext()
    landed = _run_generator(
        open_loaded_activity_menu_item(
            context,
            "group:110001",
            kind="world_left",
            source_scene_id=34,
            ocr_shape_names=("左侧菜单",),
            expected_scene_ids=(403,),
            target_gui_name="特惠",
            grid=ActivityMenuGrid(columns=1, click_offset_heights=0.5),
        )
    )

    assert landed == 403
    assert context.clicked == [(34, 204.5, 856.0)]


def test_navigation_gui_alias_collision_fails_closed(monkeypatch):
    snapshot = _world_navigation_snapshot(
        [
            ActivityMenuItem(1, "activity:1", "万象宝阁", activity_id=1),
            ActivityMenuItem(
                2, "activity:2030001", "活动2030001", activity_id=2030001
            ),
        ]
    )
    monkeypatch.setattr(
        navigation_module, "read_activity_menu_snapshot", lambda _kind: snapshot
    )
    context = _MenuNavigationContext()

    with pytest.raises(RuntimeError, match="目标无法安全定位"):
        _run_generator(_open_wanxiang(context))

    assert context.clicked == []


@pytest.mark.parametrize(
    "items",
    [
        [ActivityMenuItem(1, "activity:1", "其他活动", activity_id=1)],
        [
            ActivityMenuItem(
                1, "activity:2030001:a", "活动甲", activity_id=2030001
            ),
            ActivityMenuItem(
                2, "activity:2030001:b", "活动乙", activity_id=2030001
            ),
        ],
    ],
    ids=["missing", "duplicate"],
)
def test_navigation_gui_alias_requires_unique_runtime_identity(monkeypatch, items):
    snapshot = _world_navigation_snapshot(items)
    monkeypatch.setattr(
        navigation_module, "read_activity_menu_snapshot", lambda _kind: snapshot
    )
    context = _MenuNavigationContext()

    with pytest.raises(RuntimeError, match="GUI 别名没有唯一 Runtime 身份"):
        _run_generator(_open_wanxiang(context))

    assert context.clicked == []


def test_navigation_rejects_changed_runtime_fingerprint_before_click(monkeypatch):
    first = _world_navigation_snapshot(
        [
            ActivityMenuItem(
                1, "activity:2030001", "活动2030001", activity_id=2030001
            )
        ]
    )
    changed = _world_navigation_snapshot(first.items, fingerprint="changed")
    snapshots = iter((first, changed))
    monkeypatch.setattr(
        navigation_module,
        "read_activity_menu_snapshot",
        lambda _kind: next(snapshots),
    )
    context = _MenuNavigationContext()

    with pytest.raises(RuntimeError, match="定位后发生变化"):
        _run_generator(_open_wanxiang(context))

    assert context.clicked == []
