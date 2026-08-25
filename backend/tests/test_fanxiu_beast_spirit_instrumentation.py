import pytest
from types import SimpleNamespace

from backend.core.fanxiu.instrumentation import beast_spirit
from backend.core.fanxiu.instrumentation.beast_spirit import (
    _active_beast_bag_item_ids,
    _apply_active_ui_bag_order,
    _entry_list,
    _single_entry_list,
    beast_soul_bag_sort_key,
    parse_beast_soul_shape,
    synthesis_expected_material_cost,
)
from backend.core.fanxiu.instrumentation.runtime_memory import LuaRef


def test_singular_main_entry_is_snapshotted_once_with_score():
    class Reader:
        def fields(self, value):
            assert value == LuaRef("table", 1)
            return {"id": 101, "group": 7, "attrId": 9}

        def table(self, address):
            assert address == 2
            return {
                "array": [None, None, None, None, None, None, 123, 456, None]
            }

    entries = _single_entry_list(
        Reader(),
        LuaRef("table", 1),
        attr_rows={101: LuaRef("table", 2)},
        skill_rows={},
    )

    assert entries == [{
        "kind": "attribute",
        "config_id": 101,
        "group": 7,
        "attribute_id": 9,
        "value": 123,
        "score": 456,
    }]


def test_plural_main_entries_preserve_ui_order_and_values():
    class Reader:
        def fields(self, value):
            return {
                LuaRef("table", 1): {
                    "1": LuaRef("table", 2),
                    "2": LuaRef("table", 3),
                },
                LuaRef("table", 2): {"id": 101, "group": 7, "attrId": 9},
                LuaRef("table", 3): {"id": 102, "group": 8, "attrId": 10},
            }.get(value, {})

        def table(self, address):
            value = 123 if address == 11 else 456
            return {"array": [None, None, None, None, None, None, value, 0, None]}

    entries = _entry_list(
        Reader(),
        LuaRef("table", 1),
        attr_rows={101: LuaRef("table", 11), 102: LuaRef("table", 12)},
        skill_rows={},
    )

    assert [(entry["attribute_id"], entry["value"]) for entry in entries] == [
        (9, 123),
        (10, 456),
    ]


def test_active_ui_bag_order_assigns_exact_indices_and_rejects_partial_projection():
    items = [
        {"item_id": "a", "equipped": False},
        {"item_id": "b", "equipped": True},
        {"item_id": "c", "equipped": False},
    ]

    complete, reason = _apply_active_ui_bag_order(items, [None, "c", "a"])

    assert (complete, reason) == (True, None)
    assert [item["ui_bag_index"] for item in items] == [2, None, 1]
    incomplete, reason = _apply_active_ui_bag_order(items, ["a"])
    assert incomplete is False
    assert "does not exactly cover" in reason
    assert [item["ui_bag_index"] for item in items] == [None, None, None]


def test_active_beast_bag_reads_exact_current_panel_clist(monkeypatch):
    class Memory:
        pid = 321
        process_start_ticks = 654

        def read(self, address, size, **_kwargs):
            assert (address, size) == (0x1000 + 72, 8)
            return (1).to_bytes(8, "little")

    class Reader:
        transient_duplicate = False
        values = {
            (1, "package"): LuaRef("table", 2),
            (2, "loaded"): LuaRef("table", 3),
            (3, "Core.UIManager.Manager.UIShowMgr"): LuaRef("table", 4),
            (5, "V_M_compDic"): LuaRef("table", 6),
            (6, "_dt_"): LuaRef("table", 7),
            (9, "m_panel"): LuaRef("table", 10),
            (10, "tabPanelGroup"): LuaRef("table", 11),
            (11, "curTabIndex"): 0,
            (11, "panelShowComps"): LuaRef("table", 12),
            (12, "_dt_"): LuaRef("table", 18),
            (13, "m_panel"): LuaRef("table", 14),
            (14, "v_showList"): LuaRef("table", 15),
            (14, "scrollview"): LuaRef("table", 20),
            (20, "ItemClassDic"): LuaRef("table", 21),
            (21, "_dt_"): LuaRef("table", 22),
            (16, "isEmpty"): False,
            (16, "id"): 200,
            (17, "isEmpty"): False,
            (17, "id"): 100,
        }

        def interned_string_field(self, address, name, **_kwargs):
            return self.values.get((address, name))

        def metatable_index_string_field(self, address, name, **_kwargs):
            assert (address, name) == (4, "inst")
            return LuaRef("table", 5)

        def table(self, address):
            if address == 7:
                fields = {478: LuaRef("table", 8)}
                if self.transient_duplicate:
                    fields[999] = LuaRef("table", 108)
                return {"array": [], "fields": fields}
            if address == 18:
                return {"array": [None, LuaRef("table", 13)], "fields": {}}
            if address == 22:
                return {"array": [], "fields": {91: LuaRef("table", 23)}}
            raise AssertionError(address)

        def string_fields(self, address, names):
            assert address == 23
            return {
                "V_Data": LuaRef("table", 24),
                "root": LuaRef("table", 25),
            }

        def list_items(self, value):
            return {
                8: ([LuaRef("table", 9)], 1),
                15: ([LuaRef("table", 16), LuaRef("table", 17)], 2),
            }[value.address]

        def fields(self, value):
            return {
                16: {"id": 200, "isEmpty": False},
                17: {"id": 100, "isEmpty": False},
                24: {"id": 100, "isEmpty": False},
            }.get(value.address, {})

        def long(self, value):
            raise AssertionError(value)

        def prefetch_hashed_string_fields(self, addresses, *, key_addresses):
            assert tuple(addresses) == (16, 17)
            assert tuple(key_addresses) == (101, 102)

    monkeypatch.setattr(beast_spirit, "_lua_addresses", lambda _memory: {"state": "0x1000"})
    monkeypatch.setattr(
        beast_spirit,
        "lua_jit_intern_state",
        lambda _memory, _state: (0, 0x2000, 7, 0),
    )

    memory = Memory()
    reader = Reader()
    beast_spirit.clear_beast_spirit_order_cache()
    item_ids, evidence = _active_beast_bag_item_ids(
        memory, reader, expected_item_ids={"100", "200"}
    )

    assert item_ids == ["200", "100"]
    assert evidence["show_list_count"] == 2
    assert evidence["materialized_bindings"] == [{
        "instance_id": "100",
        "is_empty": False,
        "root_address": "0x19",
        "item_instance_address": "0x17",
    }]
    cache = beast_spirit._beast_order_cache
    assert cache is not None
    assert cache.pid == 321
    assert cache.process_start_ticks == 654
    assert cache.window_address == 8
    assert cache.panel_address == 14
    assert cache.show_list_address == 15
    assert cache.item_ids == ("200", "100")

    # A just-closed detail window may remain in V_M_compDic for one frame.
    # The cache seeded by the unique full snapshot must validate and reuse the
    # exact original open panel without cold first-matching the transient one.
    reader.transient_duplicate = True

    context = SimpleNamespace(
        memory=memory,
        reader=reader,
        binding=SimpleNamespace(
            component_storage_address=7,
            key_addresses={"isEmpty": 101, "id": 102},
        ),
        field=lambda address, name: reader.interned_string_field(address, name),
    )

    narrow_ids, narrow_evidence = _active_beast_bag_item_ids(
        memory,
        reader,
        expected_item_ids={"100", "200"},
        context=context,
        include_materialized=False,
    )
    assert narrow_ids == ["200", "100"]
    assert narrow_evidence["window_address"] == "0x8"
    beast_spirit.clear_beast_spirit_order_cache()


def test_targeted_ui_projection_reuses_shared_context_without_full_snapshot(monkeypatch):
    class Memory:
        pid = 321
        process_start_ticks = 654

    class Context:
        memory = Memory()
        reader = object()
        timings = {"process_identity": 0.01}
        cache_mode = "hot"

    calls = []
    monkeypatch.setattr(
        beast_spirit,
        "acquire_ui_runtime_context",
        lambda keys: calls.append(set(keys)) or Context(),
    )
    monkeypatch.setattr(
        beast_spirit,
        "_active_beast_bag_item_ids",
        lambda memory, reader, **kwargs: (
            ["200", "100"],
            {
                "show_list_count": 2,
                "materialized_bindings": [{"instance_id": "100"}],
            },
        ),
    )

    result = beast_spirit.read_active_beast_bag_projection({"100", "200"})

    assert result["complete"] is True
    assert result["ui_bag_item_ids"] == ["200", "100"]
    assert result["ui_materialized_bindings"] == [{"instance_id": "100"}]
    assert result["performance"]["cache_mode"] == "hot"
    assert result["evidence"]["pid"] == 321
    assert calls == [set(beast_spirit._BEAST_UI_KEYS)]


def test_order_only_projection_explicitly_skips_materialized_dictionary(monkeypatch):
    class Memory:
        pid = 1
        process_start_ticks = 2

    class Context:
        memory = Memory()
        reader = object()
        timings = {}
        cache_mode = "hot"

    options = []
    monkeypatch.setattr(
        beast_spirit, "acquire_ui_runtime_context", lambda _keys: Context()
    )
    monkeypatch.setattr(
        beast_spirit,
        "_active_beast_bag_item_ids",
        lambda _memory, _reader, **kwargs: options.append(
            kwargs["include_materialized"]
        ) or (["a"], {"materialized_bindings": []}),
    )

    result = beast_spirit.read_active_beast_bag_projection(
        {"a"}, include_materialized=False
    )

    assert result["complete"] is True
    assert options == [False]


def test_order_cache_freshly_reads_ids_when_same_slot_refs_swap_in_place():
    class Memory:
        pid = 10
        process_start_ticks = 20

    class Reader:
        window_open = True
        prefetch_calls = []

        def table(self, address):
            if address == 7:
                return {
                    "array": [],
                    "fields": (
                        {478: LuaRef("table", 8)} if self.window_open else {}
                    ),
                }
            if address == 18:
                return {"array": [None, LuaRef("table", 13)], "fields": {}}
            raise AssertionError(address)

        def list_items(self, value):
            return {
                8: ([LuaRef("table", 9)], 1),
                15: ([LuaRef("table", 16), LuaRef("table", 17)], 2),
                19: ([LuaRef("table", 16), LuaRef("table", 17)], 2),
            }[value.address]

        def long(self, value):
            raise AssertionError(value)

        def prefetch_hashed_string_fields(self, addresses, *, key_addresses):
            self.prefetch_calls.append((tuple(addresses), tuple(key_addresses)))

    values = {
        (9, "m_panel"): LuaRef("table", 10),
        (10, "tabPanelGroup"): LuaRef("table", 11),
        (11, "curTabIndex"): 0,
        (11, "panelShowComps"): LuaRef("table", 12),
        (12, "_dt_"): LuaRef("table", 18),
        (13, "m_panel"): LuaRef("table", 14),
        (14, "v_showList"): LuaRef("table", 15),
        (16, "isEmpty"): False,
        (16, "id"): 100,
        (17, "isEmpty"): False,
        (17, "id"): 200,
    }

    class Context:
        memory = Memory()
        reader = Reader()
        binding = SimpleNamespace(
            component_storage_address=7,
            key_addresses={"isEmpty": 101, "id": 102},
        )

        @staticmethod
        def field(address, name):
            return values.get((address, name))

    beast_spirit.clear_beast_spirit_order_cache()
    first, _evidence = _active_beast_bag_item_ids(
        Context.memory,
        Context.reader,
        expected_item_ids={"100", "200"},
        context=Context(),
        include_materialized=False,
    )
    values[(16, "id")] = 200
    values[(17, "id")] = 100
    second, evidence = _active_beast_bag_item_ids(
        Context.memory,
        Context.reader,
        expected_item_ids={"100", "200"},
        context=Context(),
        include_materialized=False,
    )

    assert first == ["100", "200"]
    assert second == ["200", "100"]
    assert evidence["show_list_address"] == "0xf"
    assert Context.reader.prefetch_calls[:2] == [
        ((16, 17), (101, 102)),
        ((16, 17), (101, 102)),
    ]

    # Same process/window with a replaced showList must cold-rebind once.
    values[(14, "v_showList")] = LuaRef("table", 19)
    rebound, rebound_evidence = _active_beast_bag_item_ids(
        Context.memory,
        Context.reader,
        expected_item_ids={"100", "200"},
        context=Context(),
        include_materialized=False,
    )
    assert rebound == ["200", "100"]
    assert rebound_evidence["show_list_address"] == "0x13"

    # Closing the cached window invalidates membership and cannot reuse it.
    Context.reader.window_open = False
    assert _active_beast_bag_item_ids(
        Context.memory,
        Context.reader,
        expected_item_ids={"100", "200"},
        context=Context(),
        include_materialized=False,
    ) is None

    # Reopen/rebind, then changing the active tab must invalidate the panel.
    Context.reader.window_open = True
    values[(11, "curTabIndex")] = 0
    assert _active_beast_bag_item_ids(
        Context.memory,
        Context.reader,
        expected_item_ids={"100", "200"},
        context=Context(),
        include_materialized=False,
    ) is not None
    values[(11, "curTabIndex")] = 1
    assert _active_beast_bag_item_ids(
        Context.memory,
        Context.reader,
        expected_item_ids={"100", "200"},
        context=Context(),
        include_materialized=False,
    ) is None
    beast_spirit.clear_beast_spirit_order_cache()


def test_active_beast_bag_rejects_two_matching_cached_panels(monkeypatch):
    class Memory:
        def read(self, address, size, **_kwargs):
            assert (address, size) == (0x1000 + 72, 8)
            return (1).to_bytes(8, "little")

    class Reader:
        values = {
            (1, "package"): LuaRef("table", 2),
            (2, "loaded"): LuaRef("table", 3),
            (3, "Core.UIManager.Manager.UIShowMgr"): LuaRef("table", 4),
            (5, "V_M_compDic"): LuaRef("table", 6),
            (6, "_dt_"): LuaRef("table", 7),
        }
        for offset in (0, 100):
            values.update({
                (9 + offset, "m_panel"): LuaRef("table", 10 + offset),
                (10 + offset, "tabPanelGroup"): LuaRef("table", 11 + offset),
                (11 + offset, "curTabIndex"): 0,
                (11 + offset, "panelShowComps"): LuaRef("table", 12 + offset),
                (12 + offset, "_dt_"): LuaRef("table", 18 + offset),
                (13 + offset, "m_panel"): LuaRef("table", 14 + offset),
                (14 + offset, "v_showList"): LuaRef("table", 15 + offset),
            })

        def interned_string_field(self, address, name, **_kwargs):
            return self.values.get((address, name))

        def metatable_index_string_field(self, address, name, **_kwargs):
            return LuaRef("table", 5)

        def table(self, address):
            if address == 7:
                return {
                    "array": [],
                    "fields": {
                        478: LuaRef("table", 8),
                        999: LuaRef("table", 108),
                    },
                }
            if address in (18, 118):
                offset = address - 18
                return {
                    "array": [None, LuaRef("table", 13 + offset)],
                    "fields": {},
                }
            raise AssertionError(address)

        def list_items(self, value):
            if value.address in (8, 108):
                return [LuaRef("table", value.address + 1)], 1
            if value.address in (15, 115):
                offset = value.address - 15
                return [LuaRef("table", 16 + offset)], 1
            raise AssertionError(value.address)

        def fields(self, value):
            return {"id": 200, "isEmpty": False} if value.address in (16, 116) else {}

        def long(self, value):
            raise AssertionError(value)

    monkeypatch.setattr(beast_spirit, "_lua_addresses", lambda _memory: {"state": "0x1000"})
    monkeypatch.setattr(
        beast_spirit,
        "lua_jit_intern_state",
        lambda _memory, _state: (0, 0x2000, 7, 0),
    )

    assert _active_beast_bag_item_ids(
        Memory(), Reader(), expected_item_ids={"200"}
    ) is None


def test_bag_sort_key_matches_new_favorite_level_score_and_base_priority():
    items = [
        {"item_id": "low", "level": 8, "score": 1, "base_id": 8},
        {
            "item_id": "favorite",
            "favorite": True,
            "level": 1,
            "score": 0,
            "base_id": 1,
        },
        {
            "item_id": "new-low",
            "is_new": True,
            "level": 1,
            "score": 0,
            "base_id": 1,
        },
        {"item_id": "high", "level": 8, "score": 2, "base_id": 8},
    ]

    assert [
        item["item_id"] for item in sorted(items, key=beast_soul_bag_sort_key)
    ] == ["new-low", "favorite", "high", "low"]


def test_bag_sort_key_deliberately_does_not_invent_item_id_tie_breaker():
    first = {"item_id": "1", "level": 4, "score": 10, "base_id": 4}
    second = {"item_id": "2", "level": 4, "score": 10, "base_id": 4}

    assert beast_soul_bag_sort_key(first) == beast_soul_bag_sort_key(second)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1|1", ((0, 0),)),
        ("1|1_2,2|2_3", ((0, 0), (0, 1), (1, 1), (1, 2))),
        ("2|3,3|3_4", ((0, 0), (1, 0), (1, 1))),
    ],
)
def test_parse_beast_soul_shape_normalizes_coordinates(raw, expected):
    assert parse_beast_soul_shape(raw) == expected


@pytest.mark.parametrize("raw", ["", "1", "0|1", "1|0", "x|1"])
def test_parse_beast_soul_shape_rejects_invalid_data(raw):
    with pytest.raises(ValueError):
        parse_beast_soul_shape(raw)


def test_two_material_synthesis_is_robustly_best():
    options = ((2, 0.55), (3, 0.70), (4, 0.85), (5, 1.0))
    retained_costs = [
        synthesis_expected_material_cost(amount, probability)
        for amount, probability in options
    ]
    assert retained_costs == pytest.approx(
        [2.8181818182, 3.8571428571, 4.5294117647, 5.0]
    )
    pessimistic_two = synthesis_expected_material_cost(
        2,
        0.55,
        retained_on_failure=0,
    )
    assert pessimistic_two < retained_costs[1]


@pytest.mark.parametrize(
    ("amount", "probability", "retained"),
    [(1, 0.5, 0), (2, 0, 0), (2, 1.1, 0), (2, 0.5, -1), (2, 0.5, 3)],
)
def test_synthesis_expected_material_cost_validates_inputs(
    amount,
    probability,
    retained,
):
    with pytest.raises(ValueError):
        synthesis_expected_material_cost(
            amount,
            probability,
            retained_on_failure=retained,
        )
