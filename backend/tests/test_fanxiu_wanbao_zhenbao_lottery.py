from __future__ import annotations

import pytest

from backend.core.fanxiu.activity.wanbao_zhenbao_lottery import (
    normalize_wanbao_lottery_snapshot,
)


def test_normalize_wanbao_lottery_snapshot_maps_mining_scatter() -> None:
    point = normalize_wanbao_lottery_snapshot(
        {
            "complete": True,
            "captured_at": "2026-08-19T11:00:00+08:00",
            "activity_id": 712,
            "draw": {
                "enabled": True,
                "x": 10,
                "y": 1,
                "progress": 10,
                "available_currency": 138,
                "available_draws": 138,
                "cost_type": 40017,
                "cost_per_draw": 1,
                "grand_prize": {
                    "id": 71200001,
                    "reward": "Item|3110229_1_7",
                },
            },
            "evidence": {"read_only": True},
        }
    )

    assert (point["x"], point["y"]) == (10, 1)
    assert point["selected_library_id"] == 71200001
    assert point["selected_big_reward"]["item_id"] == 3110229


def test_normalize_wanbao_lottery_snapshot_rejects_unknown_reward() -> None:
    with pytest.raises(ValueError):
        normalize_wanbao_lottery_snapshot(
            {
                "complete": True,
                "activity_id": 712,
                "draw": {
                    "enabled": True,
                    "grand_prize": {"id": 71200001, "reward": "unknown"},
                },
            }
        )
