import pytest

from backend.core.fanxiu.data_annotation.behavior_tree_executor import BehaviorTreeExecutor
_KNOWN_PLACE_GROUPS = (
    {"level": 1, "prefix": "", "places": ("白玉京",)},
    {"level": 2, "prefix": "", "places": ("大罗天墟", "太明玉墟")},
    {
        "level": 3,
        "prefix": "[洞天]",
        "places": ("紫琅阕", "璇霄崖", "云天柱", "青冥台", "月虹梁", "星岩廊"),
    },
    {
        "level": 4,
        "prefix": "[福地]",
        "places": (
            "蛰龙窟",
            "琅霜涧",
            "镇岳台",
            "坠星滩",
            "莲舟矾",
            "芝云巢",
            "八色圃",
            "晦明渡",
            "月胎穴",
            "沉剑津",
            "朽龙骨",
            "太素窟",
            "紫庭山",
            "月虹窟",
            "劫波礁",
            "焚轮井",
            "幽阳泉",
            "罡煞渊",
            "玉兵冢",
            "霞金脉",
            "蟠龙窟",
            "青烟崖",
            "天鬼廊",
            "赤鼎洞",
            "天符狱",
            "斗罡峡",
            "五光坛",
            "巡天阁",
            "天罗门",
            "盖竹山",
        ),
    },
)


KNOWN_PLACE_SAMPLES = tuple(
    f"{level['prefix']}{place}" if level["prefix"] else place
    for level in _KNOWN_PLACE_GROUPS
    for place in level["places"]
)


def test_enemy_places_preserve_map_order_including_white_jade():
    runner = BehaviorTreeExecutor()
    snapshot = {
        "available": True, "complete": True, "own_union_id": 10,
        "own_union_name": "我方",
        "mines": [
            {"id": 1, "config_name": "白玉京", "cross_union_id": 20},
            {"id": 3, "config_name": "太明玉墟", "cross_union_id": 10},
            {"id": 2, "config_name": "大罗天墟", "cross_union_id": 30},
        ],
    }
    assert runner._daily_dongtian_enemy_places_from_runtime(
        {"__dongtian_runtime_snapshot": snapshot},
    ) == ["白玉京", "大罗天墟"]
    snapshot["mines"][0]["cross_union_id"] = 10
    assert runner._daily_dongtian_enemy_places_from_runtime(
        {"__dongtian_runtime_snapshot": snapshot},
    ) == ["大罗天墟"]


@pytest.mark.parametrize("place", KNOWN_PLACE_SAMPLES)
def test_dongtian_location_box_accepts_every_known_exact_place_name(place):
    runner = BehaviorTreeExecutor()
    normalized = runner._daily_dongtian_normalize_place_name(place)
    prefix = "[福地]" if place.startswith("[福地]") else "[洞天]" if place.startswith("[洞天]") else ""
    line = {
        "text": f"{prefix}{normalized}",
        "x": 100,
        "y": 200,
        "w": 120,
        "h": 30,
        "line_id": "known-place",
    }
    tokens = [{
        "text": normalized,
        "x": 110,
        "y": 200,
        "w": 100,
        "h": 30,
        "parent_line_id": "known-place",
    }]

    assert runner._daily_dongtian_location_box(line, tokens, normalized) == {
        "x": 100,
        "y": 200,
        "w": 120,
        "h": 30,
    }


def test_dongtian_location_box_rejects_same_prefix_different_place():
    runner = BehaviorTreeExecutor()
    line = {
        "text": "[福地]月虹窟",
        "x": 100,
        "y": 200,
        "w": 120,
        "h": 30,
        "line_id": "moon-rainbow-cave",
    }

    assert runner._daily_dongtian_location_box(line, [], "月虹梁") is None
