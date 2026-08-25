from __future__ import annotations

import pytest

from backend.core.fanxiu.instrumentation import beast_abyss_runtime
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaRef,
)


class _Reader:
    def fields(self, value):
        return value if isinstance(value, dict) else {}

    def dictionary_fields(self, value):
        return value

    def long(self, value):
        return value


def test_beast_resource_counts_require_both_exact_types() -> None:
    rows = beast_abyss_runtime._decode_count_rows(
        _Reader(),
        {
            "_BeastExplodeCountInfoDic": {
                1: {"type": 1, "count": 12, "recoverTime": 100},
                2: {"type": 2, "count": 8, "recoverTime": 200},
            }
        },
    )

    assert rows[1]["count"] == 12
    assert rows[2]["recover_time"] == 200


def test_beast_resource_counts_fail_closed_on_missing_type() -> None:
    with pytest.raises(FanxiuRuntimeMemoryError, match=r"missing=\[2\]"):
        beast_abyss_runtime._decode_count_rows(
            _Reader(),
            {
                "_BeastExplodeCountInfoDic": {
                    1: {"type": 1, "count": 12, "recoverTime": 100},
                }
            },
        )


def test_beast_resource_counts_fail_closed_on_key_identity_conflict() -> None:
    with pytest.raises(FanxiuRuntimeMemoryError, match="身份冲突"):
        beast_abyss_runtime._decode_count_rows(
            _Reader(),
            {
                "_BeastExplodeCountInfoDic": {
                    1: {"type": 2, "count": 8, "recoverTime": 200},
                    2: {"type": 1, "count": 12, "recoverTime": 100},
                }
            },
        )


def test_hierarchy_candidates_are_distinct_and_sorted() -> None:
    result = beast_abyss_runtime._decode_hierarchy_candidates(
        _Reader(),
        {
            "_BeastExplodeInfo": {
                "hierarchyMap": {"self": 3, "member": 2, "duplicate": 3},
            }
        },
    )

    assert result == [2, 3]


def test_hierarchy_candidates_fail_closed_when_not_loaded() -> None:
    with pytest.raises(FanxiuRuntimeMemoryError, match="hierarchyMap"):
        beast_abyss_runtime._decode_hierarchy_candidates(
            _Reader(),
            {"_BeastExplodeInfo": {}},
        )


def test_hierarchy_map_decodes_lusuolong_player_identity() -> None:
    class Reader(_Reader):
        def long(self, value):
            if isinstance(value, LuaRef):
                return {0x1000: 101, 0x2000: 202}.get(value.address)
            return super().long(value)

    result = beast_abyss_runtime._decode_hierarchy_map(
        Reader(),
        {
            "_BeastExplodeInfo": {
                "hierarchyMap": {
                    LuaRef("table", 0x1000): 1,
                    LuaRef("table", 0x2000): 3,
                },
            }
        },
    )

    assert result == {101: 1, 202: 3}


def test_entity_user_id_reads_existing_user_view_v_id() -> None:
    class Reader(_Reader):
        def table(self, address):
            if address == 0x10:
                return {"metatable": 0x20}
            if address == 0x20:
                return {"fields": {"__index": LuaRef("table", 0x30)}}
            raise AssertionError(address)

        def fields(self, value):
            if isinstance(value, LuaRef) and value.address == 0x30:
                return {
                    "EntityMgr": object(),
                    "Inst_get": object(),
                    "GetUserId": object(),
                    "_type_": LuaRef("table", 0x10),
                    "inst": {"UserView": {"Entity": {"V_ID": "self-id"}}},
                }
            return super().fields(value)

        def long(self, value):
            return 24082878061086206 if value == "self-id" else None

    assert beast_abyss_runtime._entity_user_id(Reader(), 0x10) == 24082878061086206


def test_config_row_fields_decode_generated_count_schema() -> None:
    class Reader(_Reader):
        def table(self, address):
            assert address == 0x1000
            return {
                "array": [
                    None, 1, 114, 4, 20, 30, 30, 129256,
                    30050000, 81789,
                ],
                "fields": {},
            }

    result = beast_abyss_runtime._row_fields(
        Reader(),
        LuaRef("table", 0x1000),
        field_names=beast_abyss_runtime._COUNT_FIELDS,
    )

    assert result == {
        "id": 1,
        "item_id": 114,
        "automatic": 4,
        "initial": 20,
        "limit": 30,
        "interval_minutes": 30,
        "description_locale_id": 129256,
        "supplement_item_id": 30050000,
        "number_locale_id": 81789,
    }


def test_config_row_fields_preserve_sparse_hierarchy_consume_slot() -> None:
    class Reader(_Reader):
        def fields(self, value):
            if isinstance(value, LuaRef):
                return {1: 3, 2: "scbf_bg_0120", 9: 4}
            return super().fields(value)

        def table(self, address):
            assert address == 0x3000
            return {
                # The dense projection ends before the omitted slots 4/5/6.
                "array": [None, 3, "scbf_bg_0120", 129253],
                "fields": {1: 3, 2: "scbf_bg_0120", 9: 4},
            }

    result = beast_abyss_runtime._row_fields(
        Reader(),
        LuaRef("table", 0x3000),
        field_names=beast_abyss_runtime._HIERARCHY_FIELDS,
    )

    assert result["id"] == 3
    assert result["consume"] == 4


def test_budget_snapshot_combines_current_and_supplement_capacity(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        beast_abyss_runtime,
        "read_beast_abyss_resource_snapshot",
        lambda: {
            "explore": {"count": 31},
            "challenge": {"count": 60},
            "current_hierarchy": 2,
            "current_hierarchy_config": {"consume": 2},
            "hierarchy_candidates": [2],
            "count_configs": {
                1: {"supplement_item_id": 30050000, "automatic": 4},
                2: {"supplement_item_id": 30050001, "automatic": 0},
            },
            "evidence": {},
        },
    )
    from backend.core.fanxiu.instrumentation import backpack

    monkeypatch.setattr(
        backpack,
        "read_backpack_item_counts",
        lambda item_ids, **_kwargs: (
            {30050000: 1417, 30050001: 0},
            {"read_only": True, "requested": sorted(item_ids)},
        ),
    )

    result = beast_abyss_runtime.read_beast_abyss_budget_snapshot()

    assert result["capacity"] == {
        "current_hierarchy": 2,
        "explore_cost": 2,
        "explore_points_without_items": 31,
        "explore_points_with_items": 5699,
        "explore_attempts_without_items": 15,
        "explore_attempts_with_items": 2849,
        "challenge_without_items": 60,
        "challenge_with_items": 60,
    }
    assert result["evidence"]["backpack"]["read_only"] is True


def test_budget_snapshot_rejects_ambiguous_current_hierarchy(monkeypatch) -> None:
    monkeypatch.setattr(
        beast_abyss_runtime,
        "read_beast_abyss_resource_snapshot",
        lambda: {
            "hierarchy_candidates": [2, 3],
            "current_hierarchy": None,
            "current_hierarchy_config": None,
            "evidence": {},
        },
    )

    with pytest.raises(FanxiuRuntimeMemoryError, match="不能唯一确定"):
        beast_abyss_runtime.read_beast_abyss_budget_snapshot()
