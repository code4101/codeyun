from __future__ import annotations

import pytest

from backend.core.fanxiu.instrumentation.wardrobe import _SECTION_BY_TYPE
from backend.core.fanxiu.instrumentation.wardrobe_collector import (
    build_wardrobe_database_snapshot,
)


def test_wardrobe_runtime_category_mapping_matches_hall_sections() -> None:
    assert _SECTION_BY_TYPE == {
        1: ("shizhuang", "时装"),
        2: ("wuqi", "武器"),
        3: ("huanshen", "环身"),
        4: ("beishi", "背饰"),
        5: ("yuqi", "御器"),
    }


def test_wardrobe_snapshot_uses_runtime_rank_and_preserves_manual_note() -> None:
    runtime = {
        "complete": True,
        "source": "loaded_runtime_memory",
        "captured_timestamp": 123.0,
        "items": [
            {
                "fashion_id": 5042,
                "item_id": 18015027,
                "name": "御器·龙舟",
                "section_key": "yuqi",
                "category": "御器",
                "type_id": 5,
                "rank": 20,
                "owned": True,
                "max_level": 50,
                "show_max_level": 40,
                "is_max_level": False,
                "is_forever": True,
                "dress": False,
                "condition": "ActivityPassed|9600001_1",
            }
        ],
    }
    existing = {
        "yuqi": [
            {
                "id": "legacy-uuid",
                "name": "御器·广府龙舟",
                "rank": 3,
                "date": "2026-05-07",
                "note_id": "663",
                "main_use": "保留人工价值判断",
            }
        ]
    }
    knowledge = {
        5042: {
            "id": 18015027,
            "name": "御器·广府龙舟",
            "quality": 6,
            "quality_name": "红色品质",
            "quality_color": "9e1e09",
            "icon": "fashionicon_icon_5043",
            "description": "20重解锁：虚天殿积分符提升至6倍。",
        }
    }

    snapshot = build_wardrobe_database_snapshot(runtime, existing, knowledge)

    dragon_boat = snapshot["yuqi"][0]
    assert dragon_boat["id"] == "5042"
    assert dragon_boat["name"] == "御器·广府龙舟"
    assert dragon_boat["rank"] == 20
    assert dragon_boat["max_level"] == 50
    assert dragon_boat["note_id"] == "663"
    assert dragon_boat["main_use"] == "保留人工价值判断"
    assert dragon_boat["catalog_description"].startswith("20重解锁")
    assert snapshot["runtime_complete"] is True
    assert snapshot["runtime_item_count"] == 1
    assert snapshot["runtime_owned_count"] == 1


def test_incomplete_wardrobe_runtime_fails_closed() -> None:
    with pytest.raises(ValueError, match="FashionMgr 尚未加载"):
        build_wardrobe_database_snapshot(
            {"complete": False, "reason": "FashionMgr 尚未加载", "items": []}
        )
