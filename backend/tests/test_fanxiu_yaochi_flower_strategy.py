from __future__ import annotations

import pytest

from backend.core.fanxiu.activity import yaochi_flower_strategy


def _gongfa_snapshot(**updates):
    snapshot = {
        "runtime_complete": True,
        "books": [{"book_id": 306401, "name": "浩然星灵诀", "quality_grade_name": "仙品"}],
    }
    snapshot.update(updates)
    return snapshot


def _xianyuan_snapshot(**updates):
    snapshot = {
        "runtime_complete": True,
        "people": [
            {
                "npc_id": 2201,
                "runtime_index": 17,
                "name": "天鹏祭司",
                "giftable": True,
                "activity_flower_gift_count": 5,
            },
            {
                "npc_id": 2202,
                "runtime_index": 18,
                "name": "另一人物",
                "giftable": True,
                "activity_flower_gift_count": 5,
            },
        ],
    }
    snapshot.update(updates)
    return snapshot


def _flower_snapshot(**updates):
    snapshot = {
        "complete": True,
        "items": [
            {"item_id": 11, "name": "瑶池玉莲", "count": 12, "friendship": 100},
            {"item_id": 12, "name": "造化青莲", "count": 3, "friendship": 500},
            {"item_id": 13, "name": "空库存", "count": 0, "friendship": 999},
        ],
    }
    snapshot.update(updates)
    return snapshot


@pytest.mark.parametrize(
    ("gongfa", "xianyuan", "flowers", "message"),
    [
        (_gongfa_snapshot(runtime_complete=False), _xianyuan_snapshot(), _flower_snapshot(), "功法图鉴快照不完整"),
        (_gongfa_snapshot(), _xianyuan_snapshot(runtime_complete=False), _flower_snapshot(), "仙缘图鉴快照不完整"),
        (_gongfa_snapshot(), _xianyuan_snapshot(), _flower_snapshot(complete=False), "仙花资源快照不完整"),
    ],
)
def test_incomplete_input_snapshot_fails_closed(gongfa, xianyuan, flowers, message) -> None:
    with pytest.raises(ValueError, match=message):
        yaochi_flower_strategy.plan_yaochi_flower_recipient(
            gongfa,
            xianyuan,
            flowers,
        )


def test_plan_sends_every_flower_to_one_recommended_person(monkeypatch) -> None:
    original_gongfa = _gongfa_snapshot()
    original_xianyuan = _xianyuan_snapshot()
    original_flowers = _flower_snapshot()

    def recommend(people, books):
        assert books[0]["book_id"] == 306401
        return books[0], people, {
            "npc_id": 2201,
            "name": "天鹏祭司",
            "average_wujing_cost": 88000,
            "support_kinds": ["悟境"],
        }

    monkeypatch.setattr(
        yaochi_flower_strategy,
        "_first_supported_wujing_target",
        recommend,
    )

    plan = yaochi_flower_strategy.plan_yaochi_flower_recipient(
        original_gongfa,
        original_xianyuan,
        original_flowers,
    )

    assert plan["status"] == "ready"
    assert plan["strategy"] == "single_recipient_all_flowers"
    assert plan["chosen_npc"] == {
        "npc_id": 2201,
        "runtime_index": 17,
        "name": "天鹏祭司",
    }
    assert plan["target_gongfa"]["name"] == "浩然星灵诀"
    assert plan["total_flower_count"] == 15
    assert plan["total_friendship"] == 2700
    assert [item["item_id"] for item in plan["flower_items"]] == [11, 12]
    assert plan["recipient_count"] == 1
    assert plan["ignore_reward_overflow"] is True
    assert original_flowers["items"][0].get("total_friendship") is None
    assert "target_rewards" not in original_xianyuan["people"][0]


def test_no_supported_wujing_recipient_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        yaochi_flower_strategy,
        "_first_supported_wujing_target",
        lambda people, books: (None, people, None),
    )

    with pytest.raises(ValueError, match="没有可由仙缘悟境奖励推进"):
        yaochi_flower_strategy.plan_yaochi_flower_recipient(
            _gongfa_snapshot(),
            _xianyuan_snapshot(),
            _flower_snapshot(),
        )


def test_flower_item_without_runtime_count_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        yaochi_flower_strategy,
        "_first_supported_wujing_target",
        lambda people, books: (
            books[0],
            people,
            {"npc_id": 2201, "name": "天鹏祭司"},
        ),
    )

    with pytest.raises(ValueError, match="缺少数量或友好度"):
        yaochi_flower_strategy.plan_yaochi_flower_recipient(
            _gongfa_snapshot(),
            _xianyuan_snapshot(),
            _flower_snapshot(items=[{"item_id": 11, "friendship": 100}]),
        )
