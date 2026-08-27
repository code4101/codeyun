from __future__ import annotations

import threading
from pathlib import Path

import pytest

from backend.core.fanxiu.instrumentation import dongtian
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
)
from backend.core.fanxiu.behavior_tree.runtime import (
    create_behavior_tree_runtime_runner,
)


def _drain_generator(result):
    try:
        while True:
            next(result)
    except StopIteration as stopped:
        return stopped.value


def _run_seating_probe(
    monkeypatch,
    *,
    native_order: list[int],
    friendly_mine_ids: set[int],
    candidate_mine_ids: set[int],
    declared_count: int | None = 39,
) -> tuple[dict, list[int]]:
    """Run the pure Runtime decoder with controlled GUI position ordering."""

    decoded_mine_ids: list[int] = []

    class Reader:
        def __init__(self, _memory):
            pass

        def list_items(self, value):
            if value == "last-update-mines":
                return [native_order[-1]], 1
            if value == "mine-places":
                return list(range(1, 40)), declared_count
            return [], 0

        def dictionary_fields(self, value):
            if value == "teams":
                return {1: "idle-team"}
            if value == "mine-dictionary":
                return {mine_id: ("mine", mine_id) for mine_id in range(1, 40)}
            return {}

    monkeypatch.setattr(dongtian, "LuaJitReader", Reader)
    monkeypatch.setattr(
        dongtian,
        "_mines_data_fields",
        lambda _reader, _root: {
            "V_Mines": "last-update-mines",
            "V_MinesPlaceList": "mine-places",
            "V_MinesVoDic": "mine-dictionary",
            "V_TeamDic": "teams",
            "_MaxTeamNum": 1,
            "_memberNum": 0,
        },
    )
    monkeypatch.setattr(
        dongtian,
        "_mines_place_static_config",
        lambda: (
            {
                mine_id: {
                    "id": mine_id,
                    "special_mines": 0,
                    "people": 0,
                    "name": f"地点{mine_id}",
                    "group": 4,
                    "pos_y": (
                        40 - native_order.index(mine_id)
                    ) * 100,
                }
                for mine_id in range(1, 40)
            },
            "test-sha256",
        ),
    )
    monkeypatch.setattr(
        dongtian,
        "_club_data_fields",
        lambda _reader, _root: {"v_crossUnionInfo": 99},
    )
    monkeypatch.setattr(
        dongtian,
        "_union",
        lambda _reader, value: {"id": int(value), "name": str(value)},
    )
    monkeypatch.setattr(dongtian, "_role_id", lambda _reader, _root: 1001)
    monkeypatch.setattr(
        dongtian,
        "_team",
        lambda _reader, _value: {
            "id": 2,
            "state": dongtian._TEAM_STATE_FREE,
            "mine_id": 0,
            "seat_index": 0,
            "fight_score": 500,
            "dead": False,
            "xianlv_ids": [1, 2, 3, 4, 5],
            "complete": True,
            "idle": True,
        },
    )
    monkeypatch.setattr(
        dongtian,
        "_object_fields",
        lambda _reader, value: {
            "id": int(value[1]),
            "crossUnion": 99 if int(value[1]) in friendly_mine_ids else 77,
        },
    )

    def mine_seats(_reader, fields):
        mine_id = int(fields["id"])
        decoded_mine_ids.append(mine_id)
        return [{"mine_id": mine_id}], True

    monkeypatch.setattr(dongtian, "_mine_seats", mine_seats)
    monkeypatch.setattr(
        dongtian,
        "_mine_has_shallow_seating_candidate",
        lambda seats, **_kwargs: int(seats[0]["mine_id"]) in candidate_mine_ids,
    )
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )
    result = dongtian._seating_probe_snapshot(
        memory,
        0x2000,
        0x3000,
        0x4000,
        mines_cache_hit=True,
        club_cache_hit=True,
        mines_root_kind="manager",
        club_root_kind="manager",
        role_root_kind="manager",
        role_cache_hit=True,
        excluded_mine_ids=frozenset(),
    )
    return result, decoded_mine_ids


def test_shallow_probe_skips_occupied_friendly_masters_without_empty_row():
    seats = [
        {
            "quality": 1,
            "primary_master": index == 0,
            "empty": False,
            "guarder_type": 2,
            "guarder_cross_union_id": 77,
        }
        for index in range(3)
    ]

    assert dongtian._mine_has_shallow_seating_candidate(
        seats,
        own_union_id=99,
        own_role_id=1001,
        mine_union_id=99,
    ) is False


def test_shallow_probe_skips_nonprimary_enemy_master_but_keeps_primary():
    nonprimary = {
        "quality": 1,
        "primary_master": False,
        "empty": False,
        "guarder_type": 2,
        "guarder_cross_union_id": 77,
    }
    primary = {**nonprimary, "primary_master": True}

    assert dongtian._mine_has_shallow_seating_candidate(
        [nonprimary],
        own_union_id=99,
        own_role_id=1001,
        mine_union_id=77,
    ) is False
    assert dongtian._mine_has_shallow_seating_candidate(
        [primary],
        own_union_id=99,
        own_role_id=1001,
        mine_union_id=77,
    ) is True

    assert dongtian._mine_has_shallow_seating_candidate(
        [{**nonprimary, "empty": True, "guarder_type": 0}],
        own_union_id=99,
        own_role_id=1001,
        mine_union_id=77,
    ) is False


def test_shallow_probe_skips_all_friendly_masters_when_current_role_is_present():
    seats = [
        {
            "quality": 1,
            "primary_master": index == 0,
            "empty": index == 1,
            "guarder_present": index != 1,
            "guarder_type": 0 if index == 1 else 2,
            "guarder_role_id": 1001 if index == 0 else 2002,
            "guarder_cross_union_id": 99 if index != 1 else None,
        }
        for index in range(3)
    ]

    assert dongtian._mine_has_shallow_seating_candidate(
        seats,
        own_union_id=99,
        own_role_id=1001,
        mine_union_id=99,
    ) is False


def test_probe_excludes_every_location_already_occupied_by_own_teams():
    teams = [
        {"id": 1, "state": dongtian._TEAM_STATE_OCCUPY, "mine_id": 11},
        {"id": 2, "state": dongtian._TEAM_STATE_FREE, "mine_id": 0},
        {"id": 3, "state": dongtian._TEAM_STATE_OCCUPY, "mine_id": 22},
        # Inconsistent/stale records do not authorize a location exclusion.
        {"id": 4, "state": dongtian._TEAM_STATE_OCCUPY, "mine_id": 0},
        {"id": 5, "state": dongtian._TEAM_STATE_FREE, "mine_id": 33},
    ]

    assert dongtian._occupied_mine_ids(teams) == frozenset({11, 22})


def test_seat_prefers_native_guarder_id_and_preserves_presence(monkeypatch):
    seat_ref = object()
    guarder_ref = object()

    def fields(_reader, value):
        if value is seat_ref:
            return {"id": 4, "guarder": guarder_ref}
        if value is guarder_ref:
            return {"type": 2, "id": 1001, "roleId": 9999, "crossUnionId": 99}
        return {}

    monkeypatch.setattr(dongtian, "_object_fields", fields)

    seat = dongtian._seat(object(), seat_ref, quality=2, display_order=0)

    assert seat["guarder_present"] is True
    assert seat["guarder_role_id"] == 1001


def test_seat_keeps_role_id_only_as_legacy_fallback(monkeypatch):
    seat_ref = object()
    guarder_ref = object()

    def fields(_reader, value):
        if value is seat_ref:
            return {"id": 4, "guarder": guarder_ref}
        if value is guarder_ref:
            return {"type": 2, "roleId": 1001, "crossUnionId": 99}
        return {}

    monkeypatch.setattr(dongtian, "_object_fields", fields)

    seat = dongtian._seat(object(), seat_ref, quality=2, display_order=0)

    assert seat["guarder_present"] is True
    assert seat["guarder_role_id"] == 1001


def test_mine_seats_rejects_cross_quality_duplicate_site_id(monkeypatch):
    class Reader:
        def list_items(self, value):
            if value == "masters":
                return [1, 2, 3], 3
            if value == "miners":
                return [1, 4, 5, 6, 7, 8, 9, 10, 11], 9
            return [], 0

    monkeypatch.setattr(
        dongtian,
        "_seat",
        lambda _reader, value, *, quality, display_order: {
            "quality": quality,
            "id": value,
            "complete": True,
        },
    )

    _seats, complete = dongtian._mine_seats(
        Reader(),
        {"mineMasters": "masters", "miners": "miners"},
    )

    assert complete is False


def test_mine_map_rejects_duplicate_place_ids_even_when_count_is_39(monkeypatch):
    place_ids = [7, 7, *range(3, 40)]

    class Reader:
        def list_items(self, value):
            if value == "places":
                return place_ids, 39
            return [], 0

        def dictionary_fields(self, _value):
            return {}

    with pytest.raises(FanxiuRuntimeMemoryError, match="ID 重复"):
        dongtian._validated_mine_records(
            Reader(),
            {
                "V_MinesPlaceList": "places",
                "V_MinesVoDic": "mines",
                "_memberNum": 0,
            },
        )


def test_mine_map_uses_place_pos_order_and_accepts_one_item_update_batch(
    monkeypatch,
):
    config_ids = list(range(1, 52))
    config_rows = [LuaRef("table", mine_id) for mine_id in config_ids]
    visible_ids = set(range(1, 40))
    visual_order = [39, 4, *range(1, 4), *range(5, 39)]
    pos_y = {
        mine_id: (len(visual_order) - index) * 100
        for index, mine_id in enumerate(visual_order)
    }

    class Reader:
        def list_items(self, value):
            if value == "last-update":
                return [39], 1
            if value == "places":
                return config_rows, 51
            return [], 0

        def long(self, _value):
            return None

        def table(self, address):
            return {"array": [None, address]}

        def dictionary_fields(self, value):
            if value == "mines":
                return {mine_id: ("mine", mine_id) for mine_id in visible_ids}
            return {}

    monkeypatch.setattr(
        dongtian,
        "_mines_place_static_config",
        lambda: (
            {
                mine_id: {
                    "id": mine_id,
                    "special_mines": 0,
                    "people": 0 if mine_id in visible_ids else 250,
                    "pos_y": pos_y.get(mine_id, -10000 - mine_id),
                }
                for mine_id in config_ids
            },
            "test-sha256",
        ),
    )
    monkeypatch.setattr(
        dongtian,
        "_object_fields",
        lambda _reader, value: {"id": value[1]},
    )

    records, declared_count, last_update_batch_count, config_sha256 = (
        dongtian._validated_mine_records(
            Reader(),
            {
                "V_Mines": "last-update",
                "V_MinesPlaceList": "places",
                "V_MinesVoDic": "mines",
                "_memberNum": 0,
            },
        )
    )

    assert declared_count == 51
    assert last_update_batch_count == 1
    assert config_sha256 == "test-sha256"
    assert [record[2] for record in records] == visual_order


def test_mine_map_rejects_missing_dynamic_id_for_configured_place(
    monkeypatch,
):
    class Reader:
        def list_items(self, value):
            if value == "places":
                return list(range(1, 40)), 39
            return [], 0

        def dictionary_fields(self, value):
            if value == "mines":
                return {mine_id: ("mine", mine_id) for mine_id in range(1, 39)}
            return {}

    monkeypatch.setattr(
        dongtian,
        "_mines_place_static_config",
        lambda: (
            {
                mine_id: {
                    "special_mines": 0,
                    "people": 0,
                    "pos_y": mine_id,
                }
                for mine_id in range(1, 40)
            },
            "test-sha256",
        ),
    )
    monkeypatch.setattr(
        dongtian,
        "_object_fields",
        lambda _reader, value: {"id": value[1]},
    )

    with pytest.raises(FanxiuRuntimeMemoryError, match="动态字典与当前可见配置"):
        dongtian._validated_mine_records(
            Reader(),
            {
                "V_MinesPlaceList": "places",
                "V_MinesVoDic": "mines",
                "_memberNum": 0,
            },
        )


def test_mine_map_excludes_special_place_before_join(monkeypatch):
    class Reader:
        def list_items(self, value):
            if value == "places":
                return list(range(1, 40)), 39
            return [], 0

        def dictionary_fields(self, value):
            if value == "mines":
                return {mine_id: ("mine", mine_id) for mine_id in range(1, 40)}
            return {}

    monkeypatch.setattr(
        dongtian,
        "_mines_place_static_config",
        lambda: (
            {
                mine_id: {
                    "special_mines": int(mine_id == 40),
                    "people": 0,
                    "pos_y": 1000 - mine_id,
                }
                for mine_id in range(1, 41)
            },
            "test-sha256",
        ),
    )
    monkeypatch.setattr(
        dongtian,
        "_object_fields",
        lambda _reader, value: {"id": value[1]},
    )

    records, declared_count, _last_update_batch_count, _config_sha256 = (
        dongtian._validated_mine_records(
            Reader(),
            {
                "V_MinesPlaceList": "places",
                "V_MinesVoDic": "mines",
                "_memberNum": 0,
            },
        )
    )

    assert declared_count == 39
    assert [record[2] for record in records] == list(range(1, 40))


def test_seating_probe_prefers_friendly_without_decoding_earlier_nonfriendly(
    monkeypatch,
):
    native_order = list(range(1, 40))

    result, decoded = _run_seating_probe(
        monkeypatch,
        native_order=native_order,
        friendly_mine_ids={2},
        candidate_mine_ids={1, 2},
    )

    assert result["selected_mine"]["id"] == 2
    assert result["selected_mine"]["display_order"] == 1
    assert result["selection_policy"] == "friendly_native_display_order_only"
    assert result["strategy_name"] == "friendly_top_down_only"
    assert result["allow_nonfriendly"] is False
    assert decoded == [2]


def test_seating_probe_stops_at_first_friendly_in_native_non_id_order(
    monkeypatch,
):
    native_order = [39, 4, *range(1, 4), *range(5, 39)]

    result, decoded = _run_seating_probe(
        monkeypatch,
        native_order=native_order,
        friendly_mine_ids={39, 4},
        candidate_mine_ids={39, 4},
    )

    assert result["selected_mine"]["id"] == 39
    assert result["selected_mine"]["display_order"] == 0
    assert result["scanned_mine_count"] == 1
    assert decoded == [39]


def test_seating_probe_stops_after_friendly_locations_are_exhausted(
    monkeypatch,
):
    native_order = list(range(1, 40))

    result, decoded = _run_seating_probe(
        monkeypatch,
        native_order=native_order,
        friendly_mine_ids={2, 4},
        candidate_mine_ids={1, 3},
    )

    assert result["status"] == "no_shallow_candidate"
    assert result["selected_mine"] is None
    assert result["shallow_exhausted_mine_ids"] == [2, 4]
    assert result["allow_nonfriendly"] is False
    assert decoded == [2, 4]


def test_seating_probe_rejects_incomplete_place_config(monkeypatch):
    with pytest.raises(
        FanxiuRuntimeMemoryError,
        match="配置 ID 列表计数不一致",
    ):
        _run_seating_probe(
            monkeypatch,
            native_order=list(range(1, 39)),
            friendly_mine_ids={2},
            candidate_mine_ids={2},
            declared_count=38,
        )


def test_role_identity_reads_rolemgr_model_v_id(monkeypatch):
    class Reader:
        def fields(self, value):
            if value == "inst":
                return {"Model": "model"}
            if value == "model":
                return {"V_ID": 1001}
            return {}

    monkeypatch.setattr(
        dongtian,
        "manager_index_fields",
        lambda _reader, _root, _methods: {"inst": "inst"},
    )

    assert dongtian._role_id(Reader(), 0x4000) == 1001


def test_final_master_detail_uses_exact_site_info_guard_team_cache(monkeypatch):
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )
    cache_ref = object()
    team_ref = LuaRef("table", 0x2200)
    monkeypatch.setattr(
        dongtian,
        "_mines_data_fields",
        lambda _reader, _root: {"V_GuarderTeamDic": cache_ref},
    )
    monkeypatch.setattr(
        LuaJitReader,
        "dictionary_fields",
        lambda _reader, value: {"3_1": team_ref} if value is cache_ref else {},
    )
    monkeypatch.setattr(
        dongtian,
        "_guard_team_detail",
        lambda _reader, _value, **kwargs: {
            **kwargs,
            "fight_score": 321,
            "complete": True,
        },
    )

    result = dongtian._cached_guard_team_detail_snapshot(
        memory,
        0x2000,
        mines_root_kind="manager",
        mine_id=3,
        quality=1,
        seat_id=1,
    )

    assert result["ok"] is True
    assert result["detail_layer"] == "site_info_guard_team"
    assert result["detail"]["quality"] == 1
    assert result["detail"]["cache_generation_address"] == 0x2200


def test_dongtian_snapshot_converts_used_fatigue_to_remaining_power(monkeypatch):
    mines = LuaRef("table", 0x1000)
    mines_list = [LuaRef("table", 0x1100 + index * 0x10) for index in range(39)]
    club = LuaRef("table", 0x1200)
    monkeypatch.setattr(
        dongtian,
        "_mines_data_fields",
        lambda _reader, _root: {
            "V_AttackFatigueValue": 80.0,
            "_MaxAtkMaxTried": 300.0,
            "rewardRedDot": True,
            "V_Mines": mines,
        },
    )
    monkeypatch.setattr(
        dongtian,
        "_club_data_fields",
        lambda _reader, _root: {"v_crossUnionInfo": club},
    )
    monkeypatch.setattr(
        dongtian,
        "_validated_mine_records",
        lambda _reader, _data: (
            [
                (
                    value,
                    {"id": index, "crossUnion": club},
                    index,
                )
                for index, value in enumerate(mines_list, start=1)
            ],
            39,
            1,
            "test-sha256",
        ),
    )
    monkeypatch.setattr(
        LuaJitReader,
        "list_items",
        lambda _reader, value: (mines_list, 39) if value == mines else ([], 0),
    )

    def fake_fields(_reader, value):
        if isinstance(value, LuaRef) and value in mines_list:
            return {"id": float(mines_list.index(value) + 1), "crossUnion": club}
        if value == club:
            return {"id": 99.0, "name": "测试联盟"}
        return {}

    monkeypatch.setattr(LuaJitReader, "fields", fake_fields)
    monkeypatch.setattr(dongtian, "_role_id", lambda _reader, _root: 1001)
    monkeypatch.setattr(
        dongtian,
        "_mines_place_static_config",
        lambda: (
            {
                mine_id: {
                    "id": mine_id,
                    "name": f"地点{mine_id}",
                    "group": 4,
                    "pos_y": 1000 - mine_id,
                    "people": 0,
                }
                for mine_id in range(1, 40)
            },
            "test-sha256",
        ),
    )
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )

    result = dongtian._snapshot(
        memory,
        0x2000,
        0x3000,
        0x4000,
        mines_cache_hit=False,
        club_cache_hit=False,
        role_cache_hit=False,
    )

    assert result["ok"] is True
    assert result["reward_available"] is True
    assert result["fatigue_used"] == 80
    assert result["action_power_max"] == 300
    assert result["action_power"] == 220
    assert result["own_role_id"] == 1001


def test_dongtian_snapshot_resolves_mines_by_stable_data_fields(monkeypatch):
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )
    calls = []
    monkeypatch.setattr(MumuProcessMemory, "discover_cached", lambda: memory)
    monkeypatch.setattr(dongtian, "_lua_addresses", lambda _memory: {"state": "0x4000"})
    monkeypatch.setattr(
        dongtian,
        "resolve_lua_global_manager_root",
        lambda _memory, **kwargs: calls.append(("global", kwargs)) or (0x2000, False, 0x5000),
    )
    monkeypatch.setattr(
        dongtian,
        "_club_data_fields",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        dongtian,
        "_resolve_role_root",
        lambda *_args, **_kwargs: (0x3000, True, "lua_global:RoleMgr"),
    )
    monkeypatch.setattr(
        dongtian,
        "_snapshot",
        lambda _memory, mines_root, club_root, role_root, **kwargs: {
            "ok": True,
            "mines_root": mines_root,
            "club_root": club_root,
            "role_root": role_root,
            **kwargs,
        },
    )

    result = dongtian.read_dongtian_snapshot()

    assert result["ok"] is True
    assert result["mines_root_kind"] == "lua_global:XianLvMinesMgr"
    assert calls[1][0] == "global"
    assert calls[0][1]["global_name"] == "XianLvMinesMgr"
    assert calls[0][1]["required_methods"] == frozenset()
    assert set(result["evidence"]["phase_timings_seconds"]) == {
        "process_discovery",
        "lua_state",
        "mines_root",
        "club_root",
        "role_root",
        "decode",
    }


def test_dongtian_action_power_probe_skips_club_role_and_full_decode(monkeypatch):
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )
    calls = []
    monkeypatch.setattr(MumuProcessMemory, "discover_cached", lambda: memory)
    monkeypatch.setattr(dongtian, "_lua_addresses", lambda _memory: {"state": "0x4000"})
    monkeypatch.setattr(
        dongtian,
        "_resolve_mines_root",
        lambda *_args, **_kwargs: (0x2000, True, "lua_global:XianLvMinesMgr"),
    )
    monkeypatch.setattr(
        dongtian,
        "_resolve_club_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不得解析仙盟根")),
    )
    monkeypatch.setattr(
        dongtian,
        "_resolve_role_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不得解析角色根")),
    )
    monkeypatch.setattr(
        dongtian,
        "_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不得执行全快照")),
    )
    monkeypatch.setattr(
        dongtian,
        "_action_power_snapshot",
        lambda *_args, **kwargs: calls.append(kwargs) or {
            "ok": True,
            "available": True,
            "complete": True,
            "action_power": 200,
            "evidence": {},
        },
    )

    result = dongtian.read_dongtian_action_power_snapshot()

    assert result["action_power"] == 200
    assert len(calls) == 1
    assert set(result["evidence"]["phase_timings_seconds"]) == {
        "process_discovery",
        "lua_state",
        "mines_root",
        "decode",
    }


def test_dongtian_clear_plan_skips_role_seats_and_teams(monkeypatch):
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )
    calls = []
    monkeypatch.setattr(MumuProcessMemory, "discover_cached", lambda: memory)
    monkeypatch.setattr(dongtian, "_lua_addresses", lambda _memory: {"state": "0x4000"})
    monkeypatch.setattr(
        dongtian,
        "_resolve_mines_root",
        lambda *_args, **_kwargs: (0x2000, True, "lua_global:XianLvMinesMgr"),
    )
    monkeypatch.setattr(
        dongtian,
        "_resolve_club_root",
        lambda *_args, **_kwargs: (0x3000, True, "lua_global:ClubMgr"),
    )
    monkeypatch.setattr(
        dongtian,
        "_resolve_role_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不得解析角色根")),
    )
    monkeypatch.setattr(
        dongtian,
        "_mine_seats",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不得解码席位")),
    )
    monkeypatch.setattr(
        dongtian,
        "_team",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不得解码队伍")),
    )
    monkeypatch.setattr(
        dongtian,
        "_clear_plan_snapshot",
        lambda *_args, **kwargs: calls.append(kwargs) or {
            "ok": True,
            "available": True,
            "complete": True,
            "mines": [{"id": 1}],
            "own_union_id": 1,
            "evidence": {},
        },
    )

    result = dongtian.read_dongtian_clear_plan_snapshot()

    assert result["complete"] is True
    assert len(calls) == 1
    assert set(result["evidence"]["phase_timings_seconds"]) == {
        "process_discovery",
        "lua_state",
        "mines_root",
        "club_root",
        "decode",
    }


def test_dongtian_snapshot_retries_one_replaced_lua_generation(monkeypatch):
    memories = [
        MumuProcessMemory(
            pid=123,
            process_start_ticks=456,
            adb_serial="test",
            regions=[],
        ),
        MumuProcessMemory(
            pid=123,
            process_start_ticks=456,
            adb_serial="test",
            regions=[],
        ),
    ]
    force_refreshes = []
    decode_calls = 0

    monkeypatch.setattr(
        MumuProcessMemory,
        "discover_cached",
        lambda: memories.pop(0),
    )
    monkeypatch.setattr(dongtian, "_lua_addresses", lambda _memory: {"state": "0x4000"})

    def root(address):
        def resolve(_memory, **kwargs):
            force_refreshes.append(kwargs["force_refresh"])
            return address, not kwargs["force_refresh"], "lua_global:test"

        return resolve

    monkeypatch.setattr(dongtian, "_resolve_mines_root", root(0x2000))
    monkeypatch.setattr(dongtian, "_resolve_club_root", root(0x3000))
    monkeypatch.setattr(dongtian, "_resolve_role_root", root(0x4000))

    def snapshot(memory, *_args, **_kwargs):
        nonlocal decode_calls
        decode_calls += 1
        if decode_calls == 1:
            raise FanxiuRuntimeMemoryError(
                "Runtime 内存地址越界：0x760abdec8138+64"
            )
        return {
            "ok": True,
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
            },
        }

    monkeypatch.setattr(dongtian, "_snapshot", snapshot)

    result = dongtian.read_dongtian_snapshot()

    assert result["ok"] is True
    assert result["evidence"]["decode_attempt_count"] == 2
    assert "0x760abdec8138+64" in result["evidence"]["retried_generation_error"]
    assert force_refreshes == [False, False, False, True, True, True]


def test_dongtian_generation_retry_fails_closed_on_process_change(monkeypatch):
    memories = [
        MumuProcessMemory(pid=123, process_start_ticks=456, adb_serial="test", regions=[]),
        MumuProcessMemory(pid=124, process_start_ticks=789, adb_serial="test", regions=[]),
    ]
    monkeypatch.setattr(
        MumuProcessMemory,
        "discover_cached",
        lambda: memories.pop(0),
    )

    with pytest.raises(FanxiuRuntimeMemoryError, match="进程身份已变化"):
        dongtian._read_dongtian_with_generation_retry(
            lambda _memory, _force_refresh: (_ for _ in ()).throw(
                FanxiuRuntimeMemoryError("Runtime 内存地址越界：0x1234+64")
            )
        )


def test_dongtian_generation_retry_does_not_repeat_not_loaded_state(monkeypatch):
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )
    discoveries = 0

    def discover():
        nonlocal discoveries
        discoveries += 1
        return memory

    monkeypatch.setattr(MumuProcessMemory, "discover_cached", discover)

    with pytest.raises(FanxiuRuntimeMemoryError, match="尚未初始化"):
        dongtian._read_dongtian_with_generation_retry(
            lambda _memory, _force_refresh: (_ for _ in ()).throw(
                FanxiuRuntimeMemoryError("洞天 Runtime 数据表尚未初始化")
            )
        )

    assert discoveries == 1


def test_dongtian_manager_resolution_falls_back_across_client_aliases(monkeypatch):
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )
    aliases = []

    def fake_resolve(_memory, **kwargs):
        aliases.append(kwargs["global_name"])
        if kwargs["global_name"] == "XianLvMinesMgr":
            raise FanxiuRuntimeMemoryError("current alias absent")
        return 0x2000, False, 0x5000

    monkeypatch.setattr(dongtian, "resolve_lua_global_manager_root", fake_resolve)
    monkeypatch.setattr(
        dongtian,
        "resolve_manager_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Lua 全局解析成功后不应进入 marker 路径")
        ),
    )
    monkeypatch.setattr(
        dongtian,
        "resolve_data_table_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy manager alias should resolve before data-table fallback")
        ),
    )

    root, cache_hit, root_kind = dongtian._resolve_mines_root(
        memory,
        state_address=0x4000,
    )

    assert root == 0x2000
    assert cache_hit is False
    assert root_kind == "lua_global:LuaXianLvMinesMgr"
    assert aliases == ["XianLvMinesMgr", "LuaXianLvMinesMgr"]


def test_dongtian_fast_resolution_never_starts_heap_discovery(monkeypatch):
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )
    monkeypatch.setattr(
        dongtian,
        "resolve_lua_global_manager_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FanxiuRuntimeMemoryError("manager not loaded")
        ),
    )

    def cached_only(_memory, **kwargs):
        assert kwargs["allow_discovery"] is False
        raise FanxiuRuntimeMemoryError("cache not warm")

    monkeypatch.setattr(dongtian, "resolve_manager_root", cached_only)
    monkeypatch.setattr(
        dongtian,
        "resolve_data_table_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("正式作业不得启动字段表全堆发现")
        ),
    )

    with pytest.raises(FanxiuRuntimeMemoryError, match="拒绝退化为全堆扫描"):
        dongtian._resolve_mines_root(memory, state_address=0x4000)


def test_dongtian_seating_session_resolves_process_and_lua_roots_only_once(
    monkeypatch,
):
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )
    calls = {"discover": 0, "lua": 0, "mines": 0, "club": 0, "role": 0}
    probe_memories: list[MumuProcessMemory] = []

    def discover(**_kwargs):
        calls["discover"] += 1
        return memory

    def lua_addresses(_memory):
        calls["lua"] += 1
        return {"state": "0x4000"}

    def mines_root(_memory, **_kwargs):
        calls["mines"] += 1
        return 0x2000, True, "lua_global:XianLvMinesMgr"

    def club_root(_memory, **_kwargs):
        calls["club"] += 1
        return 0x3000, True, "lua_global:ClubMgr"

    def role_root(_memory, **_kwargs):
        calls["role"] += 1
        return 0x3500, True, "lua_global:RoleMgr"

    def probe_snapshot(probe_memory, *_args, excluded_mine_ids, **_kwargs):
        probe_memories.append(probe_memory)
        return {
            "ok": True,
            "available": True,
            "complete": True,
            "status": "ready",
            "selected_mine": {"id": 1},
            "excluded_mine_ids": sorted(excluded_mine_ids),
            "shallow_exhausted_mine_ids": [5],
        }

    monkeypatch.setattr(MumuProcessMemory, "discover_cached", discover)
    monkeypatch.setattr(dongtian, "_lua_addresses", lua_addresses)
    monkeypatch.setattr(dongtian, "_resolve_mines_root", mines_root)
    monkeypatch.setattr(dongtian, "_resolve_club_root", club_root)
    monkeypatch.setattr(dongtian, "_resolve_role_root", role_root)
    monkeypatch.setattr(dongtian, "_seating_probe_snapshot", probe_snapshot)

    session = dongtian.DongtianSeatingRuntimeSession.open()
    first = session.probe()
    second = session.probe(excluded_mine_ids={1})

    assert calls == {"discover": 1, "lua": 1, "mines": 1, "club": 1, "role": 1}
    assert first["evidence"]["runtime_session"] is True
    assert second["excluded_mine_ids"] == [1, 5]
    assert len(probe_memories) == 2
    assert probe_memories[0] is not memory
    assert probe_memories[0] is not probe_memories[1]
    assert all(item.pid == 123 for item in probe_memories)

    checked = session.revalidate_process_identity()

    assert checked["ok"] is True
    assert checked["current_roots"] == checked["expected_roots"]
    assert calls == {"discover": 2, "lua": 2, "mines": 2, "club": 2, "role": 2}


def test_dongtian_seating_session_rejects_changed_lua_root_before_occupy(
    monkeypatch,
):
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )
    mines_roots = iter((0x2000, 0x2222))

    monkeypatch.setattr(
        MumuProcessMemory,
        "discover_cached",
        lambda **_kwargs: memory,
    )
    monkeypatch.setattr(
        dongtian,
        "_lua_addresses",
        lambda _memory: {"state": "0x4000"},
    )
    monkeypatch.setattr(
        dongtian,
        "_resolve_mines_root",
        lambda _memory, **_kwargs: (
            next(mines_roots),
            True,
            "lua_global:XianLvMinesMgr",
        ),
    )
    monkeypatch.setattr(
        dongtian,
        "_resolve_club_root",
        lambda _memory, **_kwargs: (0x3000, True, "lua_global:ClubMgr"),
    )
    monkeypatch.setattr(
        dongtian,
        "_resolve_role_root",
        lambda _memory, **_kwargs: (0x3500, True, "lua_global:RoleMgr"),
    )

    session = dongtian.DongtianSeatingRuntimeSession.open()
    checked = session.revalidate_process_identity()

    assert checked["ok"] is False
    assert checked["reason"] == "lua_root_identity_changed"
    assert checked["expected_roots"][1] == 0x2000
    assert checked["current_roots"][1] == 0x2222


def test_daily_dongtian_skips_gui_when_runtime_confirms_no_reward(
    monkeypatch,
):
    runner = create_behavior_tree_runtime_runner()
    recorded: list[str] = []

    class Runtime:
        def current_scene(self, *_args, **_kwargs):
            raise AssertionError("无待领取收益时不应进入 GUI 识别流程")

    monkeypatch.setattr(
        runner,
        "_fanxiu_runtime",
        lambda *_args, **_kwargs: Runtime(),
    )
    monkeypatch.setattr(
        runner,
        "_record_daily_dongtian_done",
        lambda _payload, *, message: recorded.append(message) or "next",
    )
    payload = {
        "__dongtian_runtime_snapshot_override": {
            "available": True,
            "complete": True,
            "reward_available": False,
        }
    }
    ctx = {
        "asset_tree_path": Path("asset-tree.json"),
        "images": {279: {"shapes": []}},
    }

    result = _drain_generator(
        runner._execute_daily_dongtian_task(
            ctx,
            threading.Event(),
            payload,
        )
    )

    assert result == "success"
    assert recorded == ["Runtime 已确认当前没有待领取的洞天收益"]


def test_daily_dongtian_claim_does_not_scan_runtime_memory_by_default(
    monkeypatch,
):
    runner = create_behavior_tree_runtime_runner()
    recorded: list[str] = []

    class Runtime:
        def current_scene(self, *_args, **_kwargs):
            return 284, 100.0, "frame"

        def ocr_text(self, _frame):
            return ""

    def claim(*_args, **_kwargs):
        if False:
            yield None

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: Runtime())
    monkeypatch.setattr(
        runner,
        "_daily_dongtian_runtime_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("领取作业默认不应触发全量 Runtime 内存扫描")
        ),
    )
    monkeypatch.setattr(runner, "_claim_daily_dongtian_profit", claim)
    monkeypatch.setattr(
        runner,
        "_record_daily_dongtian_done",
        lambda _payload, *, message: recorded.append(message) or "next",
    )
    ctx = {
        "asset_tree_path": Path("asset-tree.json"),
        "images": {279: {"shapes": []}},
    }

    result = _drain_generator(
        runner._execute_daily_dongtian_task(ctx, threading.Event(), {})
    )

    assert result == "success"
    assert recorded == ["已领取洞天福地收益"]


def test_daily_dongtian_action_power_is_runtime_only(monkeypatch):
    runner = create_behavior_tree_runtime_runner()

    class Runtime:
        def cur_frame(self, **_kwargs):
            raise AssertionError("Runtime 字段缺失时不得降级读取 GUI")

        def ocr_numbers_in_shapes(self, *_args, **_kwargs):
            raise AssertionError("Runtime 字段缺失时不得降级 OCR")

    payload = {
        "__dongtian_runtime_snapshot_override": {
            "available": True,
            "complete": False,
            "action_power": None,
            "reason": "action_power_missing",
        },
    }

    try:
        runner._daily_dongtian_action_power(Runtime(), payload)
    except RuntimeError as exc:
        assert "拒绝降级 OCR" in str(exc)
        assert "action_power_missing" in str(exc)
    else:
        raise AssertionError("Runtime 字段缺失应明确失败")
