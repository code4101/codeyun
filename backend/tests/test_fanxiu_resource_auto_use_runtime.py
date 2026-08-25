import struct

from backend.core.fanxiu.data_annotation.tasks.resource_auto_use_policy import (
    plan_pet_quick_swallow,
    plan_talisman_quick_upgrade,
)
from backend.core.fanxiu.instrumentation import resource_auto_use
from backend.core.fanxiu.instrumentation.resource_auto_use import (
    classify_direct_material,
    parse_direct_item_consume,
    project_pet_quick_swallow_candidates,
    project_talisman_quick_upgrade_candidates,
    read_pet_quick_swallow_runtime,
    read_talisman_quick_upgrade_runtime,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaRef,
)


def _item(item_id: int, name: str) -> dict:
    return {"id": item_id, "name": name, "type_name": "养成材料"}


def test_consume_parser_allows_only_direct_item_material():
    assert parse_direct_item_consume("Item|4070007_12") == (4070007, 12)
    assert parse_direct_item_consume("Money|1_12") is None
    assert parse_direct_item_consume("Item|1_2|Item|3_4") is None
    assert parse_direct_item_consume("Item|1_0") is None


def test_material_classifier_fails_closed_for_unknown_cash_and_self_select():
    assert classify_direct_material(
        7, None, allowed_kind="talisman_upgrade_material"
    )["kind"] == "unknown"
    assert classify_direct_material(
        7, _item(7, "仙玉"), allowed_kind="talisman_upgrade_material"
    )["kind"] == "cash"
    assert classify_direct_material(
        8, _item(8, "法宝材料自选箱"), allowed_kind="talisman_upgrade_material"
    )["kind"] == "self_select"


def test_pet_reader_excludes_therion_and_projects_exact_materials():
    snapshot = project_pet_quick_swallow_candidates(
        pets=[{"pet_id": 101, "level": 1}, {"pet_id": 202, "level": 1}],
        pet_configs={101: {"therion_type": 0}, 202: {"therion_type": 1}},
        level_rows=[
            {"pet_id": 101, "level": 1, "item_id": 9001, "item_num": 1},
            {"pet_id": 101, "level": 2, "item_id": 9001, "item_num": 2},
            {"pet_id": 101, "level": 3, "item_id": 9001, "item_num": 4},
            {"pet_id": 202, "level": 1, "item_id": 9002, "item_num": 1},
        ],
        inventory={9001: 6, 9002: 99},
        item_catalog={9001: _item(9001, "普通灵兽升阶丹")},
    )
    assert snapshot["complete"] is True
    assert snapshot["candidate_count"] == 1
    assert snapshot["excluded_candidates"] == [{
        "pet_id": 202,
        "therion_type": 1,
        "reason": "holy_beast_outside_native_ordinary_pet_batch",
    }]
    assert snapshot["candidates"][0]["pet_id"] == 101
    assert snapshot["candidates"][0]["upgrade_count"] == 2
    assert snapshot["candidates"][0]["resources"][0]["quantity"] == 6
    assert snapshot["entity_progress"] == {101: 1, 202: 1}
    assert snapshot["inventory_counts"] == {9001: 6, 9002: 99}
    assert plan_pet_quick_swallow(snapshot).action == "execute"


def test_pet_reader_requires_explicit_therion_type_and_rejects_material_switch():
    base = dict(
        pets=[{"pet_id": 101, "level": 0}],
        pet_configs={101: {}},
        level_rows=[{"pet_id": 101, "level": 1, "item_id": 1, "item_num": 1}],
        inventory={1: 9},
        item_catalog={1: _item(1, "普通升阶丹")},
    )
    assert project_pet_quick_swallow_candidates(**base)["complete"] is False
    base["pet_configs"] = {101: {"therion_type": 0}}
    base["level_rows"].append(
        {"pet_id": 101, "level": 2, "item_id": 2, "item_num": 1}
    )
    assert project_pet_quick_swallow_candidates(**base)["complete"] is False


def test_pet_reader_rejects_shared_inventory_overcommit():
    snapshot = project_pet_quick_swallow_candidates(
        pets=[{"pet_id": 101, "level": 0}, {"pet_id": 102, "level": 0}],
        pet_configs={101: {"therion_type": 0}, 102: {"therion_type": 0}},
        level_rows=[
            {"pet_id": 101, "level": 1, "item_id": 1, "item_num": 4},
            {"pet_id": 102, "level": 1, "item_id": 1, "item_num": 4},
        ],
        inventory={1: 5},
        item_catalog={1: _item(1, "普通升阶丹")},
    )
    assert snapshot["complete"] is False
    assert snapshot["candidate_count"] == 2
    assert any("超卖" in error for error in snapshot["errors"])


def test_talisman_reader_projects_native_50_stage_bound():
    snapshot = project_talisman_quick_upgrade_candidates(
        talismans=[{
            "talisman_id": 501,
            "stage": 1,
            "category": "后天古宝",
            "active": True,
        }],
        grade_rows=[
            {"talisman_id": 501, "stage": stage, "consume": "Item|7001_1"}
            for stage in range(1, 60)
        ],
        inventory={7001: 100},
        item_catalog={7001: _item(7001, "法宝升阶石")},
    )
    assert snapshot["complete"] is True
    assert snapshot["candidates"][0]["upgrade_count"] == 50
    assert snapshot["candidates"][0]["resources"][0]["quantity"] == 50
    assert plan_talisman_quick_upgrade(snapshot).action == "execute"


def test_talisman_terminal_or_omitted_final_row_is_not_an_unknown_resource():
    terminal = project_talisman_quick_upgrade_candidates(
        talismans=[{
            "talisman_id": 501,
            "stage": 2,
            "category": "法宝",
            "active": True,
        }],
        grade_rows=[
            {"talisman_id": 501, "stage": 1, "consume": "Item|7001_1"},
            {"talisman_id": 501, "stage": 2, "consume": None},
        ],
        inventory={7001: 99},
        item_catalog={7001: _item(7001, "法宝升阶石")},
    )
    assert terminal["complete"] is True
    assert terminal["candidates"] == []

    omitted = project_talisman_quick_upgrade_candidates(
        talismans=[{
            "talisman_id": 501,
            "stage": 2,
            "category": "法宝",
            "active": True,
        }],
        grade_rows=[
            {"talisman_id": 501, "stage": 1, "consume": "Item|7001_1"},
        ],
        inventory={7001: 99},
        item_catalog={7001: _item(7001, "法宝升阶石")},
    )
    assert omitted["complete"] is True
    assert omitted["candidates"] == []


def test_talisman_reader_rejects_cash_self_select_unknown_and_category_gap():
    common = dict(
        talismans=[{
            "talisman_id": 501,
            "stage": 1,
            "category": "法宝",
            "active": True,
        }],
        grade_rows=[
            {"talisman_id": 501, "stage": 1, "consume": "Item|7001_1"},
            {"talisman_id": 501, "stage": 2, "consume": "Item|7001_1"},
        ],
        inventory={7001: 1},
    )
    for metadata in (None, _item(7001, "仙玉"), _item(7001, "法宝材料自选箱")):
        catalog = {} if metadata is None else {7001: metadata}
        assert project_talisman_quick_upgrade_candidates(
            **common, item_catalog=catalog
        )["complete"] is False
    common["talismans"][0]["category"] = ""
    assert project_talisman_quick_upgrade_candidates(
        **common, item_catalog={7001: _item(7001, "法宝升阶石")}
    )["complete"] is False


def test_talisman_reader_rejects_shared_inventory_overcommit():
    snapshot = project_talisman_quick_upgrade_candidates(
        talismans=[
            {"talisman_id": 501, "stage": 1, "category": "法宝", "active": True},
            {"talisman_id": 502, "stage": 1, "category": "先天古宝", "active": True},
        ],
        grade_rows=[
            {"talisman_id": 501, "stage": 1, "consume": "Item|7_4"},
            {"talisman_id": 501, "stage": 2, "consume": "Item|7_4"},
            {"talisman_id": 502, "stage": 1, "consume": "Item|7_4"},
            {"talisman_id": 502, "stage": 2, "consume": "Item|7_4"},
        ],
        inventory={7: 5},
        item_catalog={7: _item(7, "法宝升阶石")},
    )
    assert snapshot["complete"] is False
    assert any("超卖" in error for error in snapshot["errors"])


class _Memory:
    pid = 321
    process_start_ticks = 654


def test_pet_live_adapter_returns_explicit_not_loaded_with_stage_timings(monkeypatch):
    monkeypatch.setattr(
        resource_auto_use.MumuProcessMemory,
        "discover_cached",
        staticmethod(lambda: _Memory()),
    )

    def not_loaded(_memory, timings):
        timings["lua_state_seconds"] = 0.002
        raise FanxiuRuntimeMemoryError(
            "PetData._PetLevelCfgDic 尚未自然加载", code="data_not_loaded"
        )

    monkeypatch.setattr(resource_auto_use, "_collect_pet_runtime_inputs", not_loaded)
    result = read_pet_quick_swallow_runtime()
    assert result["state"] == "NotLoaded"
    assert result["available"] is False
    assert result["candidate_count"] == 0
    assert result["stage_timings"]["lua_state_seconds"] == 0.002
    assert result["evidence"] == {"pid": 321, "process_start_ticks": 654}


def test_talisman_live_adapter_projects_collected_runtime_inputs(monkeypatch):
    monkeypatch.setattr(
        resource_auto_use.MumuProcessMemory,
        "discover_cached",
        staticmethod(lambda: _Memory()),
    )

    def loaded(_memory, timings):
        timings["lua_state_seconds"] = 0.001
        return ({
            "talismans": [{
                "talisman_id": 501,
                "stage": 1,
                "category": "法宝",
                "active": True,
            }],
            "grade_rows": [
                {"talisman_id": 501, "stage": 1, "consume": "Item|7001_1"},
                {"talisman_id": 501, "stage": 2, "consume": "Item|7001_1"},
            ],
            "inventory": {7001: 1},
            "item_catalog": {7001: _item(7001, "法宝升阶石")},
        }, {"talisman_root": "0xabc", "talisman_root_cache_hit": True})

    monkeypatch.setattr(
        resource_auto_use, "_collect_talisman_runtime_inputs", loaded
    )
    result = read_talisman_quick_upgrade_runtime()
    assert result["state"] == "Loaded"
    assert result["complete"] is True
    assert result["candidate_count"] == 1
    assert result["candidates"][0]["upgrade_count"] == 1
    assert result["stage_timings"]["projection_seconds"] >= 0
    assert result["evidence"]["talisman_root"] == "0xabc"


def test_live_adapter_keeps_incomplete_projection_distinct_from_not_loaded(monkeypatch):
    monkeypatch.setattr(
        resource_auto_use.MumuProcessMemory,
        "discover_cached",
        staticmethod(lambda: _Memory()),
    )

    def unsafe(_memory, _timings):
        return ({
            "pets": [{"pet_id": 101, "level": 0}],
            "pet_configs": {101: {"therion_type": 0}},
            "level_rows": [
                {"pet_id": 101, "level": 1, "item_id": 8, "item_num": 1}
            ],
            "inventory": {8: 1},
            "item_catalog": {8: _item(8, "灵兽材料自选箱")},
        }, {"pet_root": "0xdef"})

    monkeypatch.setattr(resource_auto_use, "_collect_pet_runtime_inputs", unsafe)
    result = read_pet_quick_swallow_runtime()
    assert result["state"] == "Incomplete"
    assert result["available"] is True
    assert result["complete"] is False
    assert result["candidate_count"] == 1
    assert result["candidates"][0]["resources"][0]["kind"] == "self_select"


class _OversizedPetDictionaryMemory:
    storage_address = 0x1000
    array_address = 0x2000

    def __init__(self, *, missing_level: int | None = None):
        header = bytearray(64)
        struct.pack_into("<Q", header, 16, self.array_address)
        struct.pack_into("<II", header, 48, 4097, 0)
        raw = bytearray(4097 * 8)
        for level in range(1, 4001):
            if level != missing_level:
                struct.pack_into("<Q", raw, level * 8, level)
        self.header = bytes(header)
        self.array = bytes(raw)

    def read(self, address, size, *, max_size=None):
        del max_size
        if address == self.storage_address:
            return self.header[:size]
        if address == self.array_address:
            return self.array[:size]
        raise AssertionError(f"unexpected read: {address:#x}, {size}")

    def readable_region(self, address, size):
        return (
            object()
            if address == self.array_address and size == len(self.array)
            else None
        )


class _OversizedPetDictionaryReader:
    def __init__(self, *, missing_level=None):
        self.memory = _OversizedPetDictionaryMemory(missing_level=missing_level)

    def dictionary_fields(self, _value):
        raise FanxiuRuntimeMemoryError("Lua table 结构越界：0x1000")

    def fields(self, _value):
        return {
            "LuaDic_count": 4000,
            "_dt_": LuaRef("table", self.memory.storage_address),
        }

    @staticmethod
    def value(raw):
        return None if raw == 0 else raw


def test_pet_dictionary_accepts_only_proven_4000_row_4097_slot_layout():
    reader = _OversizedPetDictionaryReader()
    rows = resource_auto_use._pet_dictionary_fields(reader, object())
    assert len(rows) == 4000
    assert rows[1] == 1
    assert rows[4000] == 4000

    broken = _OversizedPetDictionaryReader(missing_level=217)
    try:
        resource_auto_use._pet_dictionary_fields(broken, object())
    except FanxiuRuntimeMemoryError as exc:
        assert "连续键集合不一致" in str(exc)
    else:  # pragma: no cover - fail-closed regression guard
        raise AssertionError("missing pet level must fail closed")


class _PetPrefixReader:
    @staticmethod
    def fields(value):
        return value


def test_pet_level_prefix_stops_after_first_unaffordable_or_switched_row():
    cursor = {
        101: {
            "current_level": 1,
            "max_level": 5,
            "first_item_id": 7,
            "rows": {
                2: {"itemId": 7, "itemNum": 1},
                3: {"itemId": 7, "itemNum": 1},
                4: {"itemId": 7, "itemNum": 1},
                5: {"itemId": 7, "itemNum": 1},
            },
        }
    }
    rows = resource_auto_use._pet_level_rows_for_inventory(
        _PetPrefixReader(), cursor, {7: 2}
    )
    assert [row["level"] for row in rows] == [2, 3, 4]

    cursor[101]["rows"][3] = {"itemId": 8, "itemNum": 1}
    switched = resource_auto_use._pet_level_rows_for_inventory(
        _PetPrefixReader(), cursor, {7: 99}
    )
    assert [row["level"] for row in switched] == [2, 3]


class _MaxedPetCursorReader:
    @staticmethod
    def dictionary_fields(value):
        if value == "outer":
            return {101: "levels"}
        if value == "levels":
            return {
                1: {"itemId": 9, "itemNum": 1},
                2: {"itemId": 9, "itemNum": 2},
            }
        raise AssertionError(value)

    @staticmethod
    def fields(value):
        return value


def test_maxed_pet_cursor_preserves_terminal_material_inventory_identity():
    cursors = resource_auto_use._pet_level_cursors(
        _MaxedPetCursorReader(),
        {"_PetLevelCfgDic": "outer"},
        pets=[{"pet_id": 101, "level": 2}],
        wanted_ids={101},
    )
    assert cursors[101]["current_level"] == 2
    assert cursors[101]["max_level"] == 2
    assert cursors[101]["first_item_id"] == 9
    assert cursors[101]["first_item_num"] == 2


class _PackedConfigMemory:
    @staticmethod
    def read(address, size, *, max_size=None):
        del max_size
        if address == 1000 and size == 12:
            header = bytearray(12)
            header[11] = 4
            return bytes(header)
        if address == 1000 + 0x28 and size == 32:
            return struct.pack("<QQQQ", 1100, 1200, 1300, 1400)
        values = {1116: 1, 1216: 2, 1316: 3, 1416: 4}
        if address in values and size == 8:
            return struct.pack("<Q", values[address])
        raise AssertionError(f"unexpected read: {address}, {size}")


class _PackedConfigReader:
    memory = _PackedConfigMemory()

    @staticmethod
    def value(raw):
        return LuaRef("table", {1: 2000, 2: 3000, 3: 4000, 4: 5000}[raw])

    @staticmethod
    def table(address):
        if address == 100:
            return {"metatable": 500, "fields": {}, "array": []}
        if address == 500:
            return {
                "metatable": 0,
                "fields": {"__index": LuaRef("function", 1000)},
                "array": [],
            }
        if address == 2000:
            return {
                "metatable": 0,
                "fields": {"id": 1, "name": 2, "therionType": 4},
                "array": [],
            }
        if address == 3000:
            return {
                "metatable": 0,
                "fields": {},
                "array": [None, 0, "", 0, 0],
            }
        if address == 4000:
            return {
                "metatable": 0,
                "fields": {},
                "array": [None, 0, 1, 0, 0],
            }
        raise FanxiuRuntimeMemoryError("environment table intentionally too large")


def test_pet_therion_default_is_read_from_runtime_config_closure():
    defaults = resource_auto_use._runtime_config_null_defaults(
        _PackedConfigReader(),
        {101: LuaRef("table", 100)},
        {"id": 1, "name": 2, "therionType": 4},
    )
    assert defaults["therionType"] == 0
