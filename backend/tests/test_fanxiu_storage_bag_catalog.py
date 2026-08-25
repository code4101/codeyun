from __future__ import annotations

import json

import pytest

from backend.core.fanxiu.instrumentation.storage_bag_catalog import (
    build_storage_bag_catalog_snapshot,
    delete_storage_bag_atlas_item,
    load_storage_bag_atlas,
    sync_storage_bag_atlas,
)


def _runtime_snapshot() -> dict:
    return {
        "complete": True,
        "source": "active_backpack_panel_item_info_list",
        "tab": {"param": 0, "number": 1, "label": "全部"},
        "declared_slot_count": 4,
        "trailing_missing_indices": [4],
        "items": [
            {"ui_index": 0, "is_padding": False, "instance_id": "9001", "base_id": 101, "num": 2},
            {"ui_index": 1, "is_padding": False, "instance_id": "9002", "base_id": 202, "num": 7},
            {"ui_index": 2, "is_padding": False, "instance_id": "9003", "base_id": 303, "num": 1},
        ],
        "evidence": {"pid": 123, "read_only": True},
    }


def test_storage_bag_catalog_preserves_runtime_order_and_duplicate_names() -> None:
    cards = {
        "101": {"id": "101", "name": "同名箱", "quality_name": "紫色品质", "icon": "a.png"},
        "202": {"id": "202", "name": "同名箱", "quality_name": "红色品质", "icon": "b.png"},
        "303": {"id": "303", "name": "其他道具", "quality_name": "蓝色品质", "icon": "c.png"},
    }

    result = build_storage_bag_catalog_snapshot(
        _runtime_snapshot(),
        cards,
        captured_at="2026-08-16T22:30:00+08:00",
    )

    assert [row["runtime_order"] for row in result["items"]] == [1, 2, 3]
    assert [row["base_id"] for row in result["items"]] == [101, 202, 303]
    assert [row["num"] for row in result["items"]] == [2, 7, 1]
    assert [row["item"]["name"] for row in result["items"][:2]] == ["同名箱", "同名箱"]
    assert [row["item"]["icon"] for row in result["items"][:2]] == ["a.png", "b.png"]
    assert result["unresolved_catalog_count"] == 0


def test_storage_bag_catalog_keeps_unknown_id_as_a_live_row() -> None:
    result = build_storage_bag_catalog_snapshot(
        _runtime_snapshot(),
        {},
        captured_at="2026-08-16T22:30:00+08:00",
    )

    assert len(result["items"]) == 3
    assert result["items"][0]["base_id"] == 101
    assert result["items"][0]["item"] is None
    assert result["unresolved_item_ids"] == [101, 202, 303]


def test_storage_bag_catalog_aggregates_multiple_slots_of_the_same_id() -> None:
    snapshot = _runtime_snapshot()
    snapshot["items"].append(
        {"ui_index": 3, "is_padding": False, "instance_id": "9004", "base_id": 101, "num": 5}
    )

    result = build_storage_bag_catalog_snapshot(snapshot, {}, captured_at="2026-08-16T22:30:00+08:00")

    assert [row["base_id"] for row in result["items"]] == [101, 202, 303]
    assert result["items"][0]["num"] == 7
    assert result["items"][0]["instance_count"] == 2
    assert result["stack_count"] == 4


def test_storage_bag_atlas_incrementally_keeps_absent_types_at_zero(tmp_path) -> None:
    path = tmp_path / "atlas.json"
    cards = {
        str(item_id): {"id": item_id, "name": name, "icon": f"{name}.png"}
        for item_id, name in [(101, "a"), (202, "b"), (303, "c"), (404, "d"), (505, "e"), (606, "f")]
    }
    first = _runtime_snapshot()
    sync_storage_bag_atlas(first, cards, captured_at="2026-08-16T10:00:00+08:00", path=path)
    second = {
        **_runtime_snapshot(),
        "items": [
            {"ui_index": index, "is_padding": False, "instance_id": str(9100 + index), "base_id": base_id, "num": index + 1}
            for index, base_id in enumerate([101, 404, 505, 606])
        ],
    }

    result = sync_storage_bag_atlas(
        second,
        cards,
        captured_at="2026-08-17T10:00:00+08:00",
        path=path,
    )

    assert [row["base_id"] for row in result["items"]] == [101, 202, 303, 404, 505, 606]
    assert [row["num"] for row in result["items"]] == [1, 0, 0, 2, 3, 4]
    assert result["atlas_count"] == 6
    assert result["current_type_count"] == 4
    assert result["zero_count"] == 2
    assert result["items"][1]["item"]["name"] == "b"


def test_storage_bag_atlas_rejects_legacy_average_yield_projection(tmp_path) -> None:
    path = tmp_path / "atlas.json"
    sync_storage_bag_atlas(
        _runtime_snapshot(),
        {},
        captured_at="2026-08-16T10:00:00+08:00",
        path=path,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["items"][0]["average_yield"] = "不应由图鉴保存"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = sync_storage_bag_atlas(
        _runtime_snapshot(),
        {},
        captured_at="2026-08-17T10:00:00+08:00",
        path=path,
    )

    assert all("average_yield" not in row for row in result["items"])
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert all("average_yield" not in row for row in persisted["items"])


def test_storage_bag_atlas_delete_only_allows_zero_quantity_and_can_rediscover(tmp_path) -> None:
    path = tmp_path / "atlas.json"
    cards = {"101": {"name": "a"}, "202": {"name": "b"}}
    sync_storage_bag_atlas(_runtime_snapshot(), cards, captured_at="2026-08-16T10:00:00+08:00", path=path)
    second = {
        **_runtime_snapshot(),
        "items": [
            {"ui_index": 0, "is_padding": False, "instance_id": "9200", "base_id": 101, "num": 1},
        ],
    }
    sync_storage_bag_atlas(second, cards, captured_at="2026-08-17T10:00:00+08:00", path=path)

    with pytest.raises(ValueError, match="当前仍持有"):
        delete_storage_bag_atlas_item(101, path=path)
    deleted = delete_storage_bag_atlas_item(202, path=path)
    assert deleted["deleted"] is True
    assert [row["base_id"] for row in load_storage_bag_atlas(path=path)["items"]] == [101, 303]

    rediscovered = {
        **_runtime_snapshot(),
        "items": [
            {"ui_index": 0, "is_padding": False, "instance_id": "9300", "base_id": 202, "num": 9},
        ],
    }
    result = sync_storage_bag_atlas(
        rediscovered,
        cards,
        captured_at="2026-08-18T10:00:00+08:00",
        path=path,
    )
    assert [row["base_id"] for row in result["items"]] == [101, 303, 202]
    assert result["items"][-1]["num"] == 9


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.update(complete=False),
        lambda row: row.update(source="packet_capture"),
        lambda row: row["items"].__setitem__(1, {**row["items"][1], "ui_index": 0}),
    ],
)
def test_storage_bag_catalog_rejects_incomplete_or_reordered_sources(mutation) -> None:
    snapshot = _runtime_snapshot()
    mutation(snapshot)

    with pytest.raises(ValueError):
        build_storage_bag_catalog_snapshot(snapshot, {}, captured_at="2026-08-16T22:30:00+08:00")
