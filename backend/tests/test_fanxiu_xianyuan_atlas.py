from backend.core.fanxiu.instrumentation import xianyuan_atlas


def test_npc_gift_options_follow_hobby_groups(monkeypatch) -> None:
    npcs = {
        3002: {"id": 3002, "hobby": [32, 54]},
        1215: {
            "id": 1215,
            "hobby": [14, 19],
            "careerDesc": 1,
            "cantSendFlower": 1,
        },
    }
    hobbies = {
        14: {"id": 14, "name_plain": "剑饰", "items": [7010009]},
        19: {
            "id": 19,
            "name_plain": "仙花",
            "items": [7020013],
            "hobbyEffect": 1,
            "giftEffect": 1,
        },
        32: {"id": 32, "name_plain": "秘闻", "items": [7020038]},
        54: {"id": 54, "name_plain": "遗篇", "items": [7020006]},
    }
    favorability = {
        7010009: {"id": 7010009, "favorability": 10, "type": 3},
        7020013: {"id": 7020013, "favorability": 10, "type": 1},
        7020038: {"id": 7020038, "favorability": 5000, "type": 2},
        7020006: {"id": 7020006, "favorability": 12000, "type": 0},
    }
    items = {
        item_id: {"id": item_id, "name_plain": f"礼物 {item_id}"}
        for item_id in favorability
    }
    monkeypatch.setattr(
        xianyuan_atlas,
        "_npc_gift_catalogs",
        lambda: (npcs, hobbies, favorability),
    )
    monkeypatch.setattr(xianyuan_atlas, "_item_index", lambda: items)

    reincarnation = xianyuan_atlas._npc_gift_options(3002)
    chen = xianyuan_atlas._npc_gift_options(1215)

    assert [item["hobby_name"] for item in reincarnation["gift_options"]] == [
        "秘闻",
        "遗篇",
    ]
    assert reincarnation["activity_flower_gift_count"] == 0
    assert chen["activity_flower_gift_count"] == 1
    assert chen["gift_options"][0]["career_conditional"] is False
    assert chen["gift_options"][1]["career_conditional"] is True
    assert chen["gift_restriction"] == "功法流派不符时，仙花与仙宝不可赠送"


def test_loaded_atlas_drops_scene_visibility_metadata(monkeypatch) -> None:
    monkeypatch.setattr(
        xianyuan_atlas,
        "load_inventory_hall_snapshot",
        lambda _session, _key: {
            "people": [{
                "npc_id": 3004,
                "name": "仙-南宫婉",
                "hostile": False,
                "recommended_visible": True,
            }],
            "summary": {"recommended_visible_count": 1},
        },
    )
    monkeypatch.setattr(
        xianyuan_atlas,
        "_npc_gift_options",
        lambda _npc_id: {
            "can_send_config": True,
            "gift_options": [],
            "gift_option_count": 0,
        },
    )

    snapshot = xianyuan_atlas.load_xianyuan_atlas_snapshot(object())

    assert "recommended_visible" not in snapshot["people"][0]
    assert "recommended_visible_count" not in snapshot["summary"]


def test_first_wujing_target_requires_activity_flower(monkeypatch) -> None:
    books = [
        {
            "book_id": 101,
            "name": "较早功法",
            "quality_grade_name": "神品",
            "upgrade_index": 1,
        },
        {
            "book_id": 202,
            "name": "可送仙花功法",
            "quality_grade_name": "仙品",
            "upgrade_index": 2,
        },
    ]
    people = [
        {
            "npc_id": 1,
            "name": "不可送仙花",
            "giftable": True,
            "hostile": False,
            "favor_level": 1,
            "activity_flower_gift_count": 0,
            "rewards": [{"item_id": 1001, "level": 2, "state": 0}],
        },
        {
            "npc_id": 2,
            "name": "可送仙花",
            "giftable": True,
            "hostile": False,
            "favor_level": 3,
            "activity_flower_gift_count": 12,
            "rewards": [{"item_id": 2002, "level": 7, "state": 0}],
        },
    ]
    monkeypatch.setattr(
        xianyuan_atlas,
        "_target_support_by_item",
        lambda target: {
            101: {1001: {"kind": "悟境", "mode": "直接"}},
            202: {2002: {"kind": "悟境", "mode": "直接"}},
        }.get(target.get("book_id"), {}),
    )

    target, projected, recommendation = (
        xianyuan_atlas._first_supported_wujing_target(people, books)
    )

    assert target and target["book_id"] == 202
    assert recommendation and recommendation["npc_id"] == 2
    assert recommendation["level_distance"] == 4
    assert projected[1]["target_rewards"][0]["target_support_kind"] == "悟境"


def test_claimed_wujing_reward_remains_in_repeatable_cycle_model(monkeypatch) -> None:
    monkeypatch.setattr(
        xianyuan_atlas,
        "_target_support_by_item",
        lambda _target: {3001: {"kind": "悟境", "mode": "直接"}},
    )
    people = [{
        "npc_id": 3,
        "name": "已领取",
        "giftable": True,
        "hostile": False,
        "favor_level": 10,
        "activity_flower_gift_count": 12,
        "rewards": [{"item_id": 3001, "level": 10, "state": 2}],
    }]

    projected, recommendation = xianyuan_atlas._project_target_recommendations(
        people,
        {"book_id": 303, "quality_grade_name": "仙品"},
        support_kind="悟境",
        require_activity_flower=True,
    )

    assert len(projected[0]["target_rewards"]) == 1
    assert recommendation and recommendation["npc_id"] == 3


def test_wujing_optional_box_inherits_target_book_support(monkeypatch) -> None:
    monkeypatch.setattr(
        xianyuan_atlas,
        "_item_index",
        lambda: {
            3012401: {
                "id": 3012401,
                "name_plain": "悟·浩然星灵诀",
                "type": 999,
                "subType": 33,
                "effectValue": "306401",
            },
            19030102: {
                "id": 19030102,
                "name_plain": "悟境功法自选匣",
                "effectValue": "1_16300102",
            },
        },
    )
    monkeypatch.setattr(
        xianyuan_atlas,
        "_optional_gift_groups",
        lambda: {"16300102": [3012401]},
    )

    support = xianyuan_atlas._target_support_by_item({
        "book_id": 306401,
        "name": "浩然星灵诀",
        "quality_grade_name": "仙品",
    })

    assert support[3012401] == {"kind": "悟境", "mode": "直接"}
    assert support[19030102] == {"kind": "悟境", "mode": "自选"}


def test_equivalent_wujing_candidates_share_first_rank(monkeypatch) -> None:
    monkeypatch.setattr(
        xianyuan_atlas,
        "_target_support_by_item",
        lambda _target: {4001: {"kind": "悟境", "mode": "自选"}},
    )
    people = [
        {
            "npc_id": npc_id,
            "name": name,
            "giftable": True,
            "hostile": False,
            "favor_level": 16,
            "activity_flower_gift_count": 12,
            "rewards": [{"item_id": 4001, "level": 16, "state": 0}],
        }
        for npc_id, name in ((1207, "天鹏祭司"), (1211, "元瑶"))
    ]

    projected, _ = xianyuan_atlas._project_target_recommendations(
        people,
        {"book_id": 401, "quality_grade_name": "仙品"},
        support_kind="悟境",
        require_activity_flower=True,
    )

    assert [person["target_recommendation_rank"] for person in projected] == [1, 1]


def test_completed_cycle_uses_reset_progress_even_when_reset_level_is_zero(monkeypatch) -> None:
    monkeypatch.setattr(
        xianyuan_atlas,
        "_target_support_by_item",
        lambda _target: {5001: {"kind": "悟境", "mode": "自选"}},
    )
    monkeypatch.setattr(
        xianyuan_atlas,
        "_npc_favor_thresholds",
        lambda: {(1215, 16): {"favor": 88_000, "reset_favor": 88_000}},
    )
    people = [{
        "npc_id": 1215,
        "name": "陈巧倩",
        "giftable": True,
        "hostile": False,
        "favor_level": 16,
        "favor": 88_000,
        "reset_favor_level": 0,
        "reset_favor": 0,
        "activity_flower_gift_count": 12,
        "rewards": [{"item_id": 5001, "level": 16, "state": 0}],
    }]

    projected, _ = xianyuan_atlas._project_target_recommendations(
        people,
        {"book_id": 501, "quality_grade_name": "仙品"},
        support_kind="悟境",
        require_activity_flower=True,
    )

    assert projected[0]["target_current_favor"] == 0
    assert projected[0]["target_required_favor"] == 88_000
    assert projected[0]["target_favor_gap"] == 88_000
    assert projected[0]["target_cycle_favor_cost"] == 88_000
    assert projected[0]["target_average_wujing_cost"] == 88_000


def test_repeatable_model_chooses_cheapest_reset_step(monkeypatch) -> None:
    monkeypatch.setattr(
        xianyuan_atlas,
        "_target_support_by_item",
        lambda _target: {6001: {"kind": "悟境", "mode": "自选"}},
    )
    thresholds = {}
    for grade in range(1, 49):
        if grade <= 8:
            step = 1
        elif grade <= 20:
            step = 2
        elif grade <= 32:
            step = 3
        else:
            step = 4
        reset_favor = {
            20: 265_000,
            28: 425_000,
            32: 505_000,
            40: 665_000,
            48: 825_000,
        }.get(grade, grade * 10_000)
        thresholds[(3007, grade)] = {
            "favor": 0,
            "reset_favor": reset_favor,
            "step": step,
        }
    monkeypatch.setattr(xianyuan_atlas, "_npc_favor_thresholds", lambda: thresholds)
    people = [{
        "npc_id": 3007,
        "name": "仙-紫灵",
        "giftable": True,
        "hostile": False,
        "favor_level": 48,
        "favor": 504_000,
        "reset_favor_level": 0,
        "reset_favor": 0,
        "activity_flower_gift_count": 12,
        "rewards": [
            {"item_id": 6001, "level": 28, "state": 0},
            {"item_id": 6001, "level": 40, "state": 0},
        ],
    }]

    projected, _ = xianyuan_atlas._project_target_recommendations(
        people,
        {"book_id": 601, "quality_grade_name": "仙品"},
        support_kind="悟境",
        require_activity_flower=True,
    )

    person = projected[0]
    assert person["target_best_reset_step"] == 3
    assert person["target_cycle_start_level"] == 20
    assert person["target_cycle_end_level"] == 32
    assert person["target_cycle_favor_cost"] == 240_000
    assert person["target_average_wujing_cost"] == 240_000


def test_selectable_reward_catalog_expands_all_box_contents(monkeypatch) -> None:
    monkeypatch.setattr(
        xianyuan_atlas,
        "_item_index",
        lambda: {
            7001: {
                "id": 7001,
                "name_plain": "其他功法自选匣",
                "effectValue": "1_77",
            },
            7002: {
                "id": 7002,
                "name_plain": "悟·甲功法",
                "type": 999,
                "subType": 33,
            },
            7003: {
                "id": 7003,
                "name_plain": "乙功法",
                "type": 3,
            },
        },
    )
    monkeypatch.setattr(
        xianyuan_atlas,
        "_optional_gift_groups",
        lambda: {"77": [7002, 7003]},
    )

    rewards = xianyuan_atlas._project_selectable_rewards([{
        "item_id": 7001,
        "name": "其他功法自选匣",
        "level": 8,
    }])

    assert [item["name"] for item in rewards[0]["optional_items"]] == [
        "悟·甲功法",
        "乙功法",
    ]
    assert rewards[0]["contains_wujing"] is True
