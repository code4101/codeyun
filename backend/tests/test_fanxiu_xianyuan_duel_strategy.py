from backend.core.fanxiu.data_annotation.tasks.xianyuan_duel import (
    choose_xianyuan_duel_target,
    map_xianyuan_duel_targets_to_slots,
    xianyuan_duel_name_similarity,
)
from backend.core.fanxiu.data_annotation.duel_strategy import (
    best_xianyuan_partner_order,
    plan_swaps,
    xianyuan_partner_career,
)


def _target(name: str, score: int, power: int, camp: str) -> dict:
    return {
        "name": name,
        "score": score,
        "team_power": power,
        "camp": camp,
    }


def test_xianyuan_name_similarity_tolerates_ocr_symbols_and_one_wrong_character() -> None:
    assert xianyuan_duel_name_similarity("青玄丶且听风吟", "青玄、且听风呤") > 0.75


def test_xianyuan_target_mapping_uses_global_fuzzy_assignment_not_packet_order() -> None:
    result = map_xianyuan_duel_targets_to_slots(
        [
            _target("黛雪凝香", 2961, 300, "friendly"),
            _target("紫霄丶紫钰", 3150, 100, "non_friendly"),
            _target("神あ梦婉", 3017, 200, "non_friendly"),
        ],
        ["紫霄、紫钰", "神あ梦惋", "黛雪凝香"],
    )

    assert result["ok"] is True
    assert result["method"] == "fuzzy_name_assignment"
    assert [(item["ui_slot"], item["name"]) for item in result["targets"]] == [
        (1, "紫霄丶紫钰"),
        (2, "神あ梦婉"),
        (3, "黛雪凝香"),
    ]


def test_xianyuan_target_mapping_falls_back_to_unique_ui_score_order() -> None:
    result = map_xianyuan_duel_targets_to_slots(
        [
            _target("丙", 3000, 300, "non_friendly"),
            _target("甲", 3200, 100, "non_friendly"),
            _target("乙", 3100, 200, "non_friendly"),
        ],
        ["", "", ""],
    )

    assert result["ok"] is True
    assert result["method"] == "score_order_fallback"
    assert [(item["ui_slot"], item["name"]) for item in result["targets"]] == [
        (1, "甲"),
        (2, "乙"),
        (3, "丙"),
    ]


def test_xianyuan_target_mapping_stops_when_names_are_weak_and_scores_tie() -> None:
    result = map_xianyuan_duel_targets_to_slots(
        [
            _target("甲", 3200, 100, "non_friendly"),
            _target("乙", 3200, 200, "non_friendly"),
            _target("丙", 3000, 300, "non_friendly"),
        ],
        ["", "", ""],
    )

    assert result["ok"] is False
    assert result["method"] == "ambiguous"


def test_xianyuan_selection_prefers_highest_score_beatable_nonfriend() -> None:
    chosen = choose_xianyuan_duel_target(
        [
            _target("友军高分", 3300, 100, "friendly"),
            _target("敌军高分", 3200, 200, "non_friendly"),
            _target("敌军低分", 3100, 150, "non_friendly"),
        ],
        self_power=500,
    )

    assert chosen is not None
    assert chosen["name"] == "敌军高分"
    assert chosen["selection_group"] == "non_friendly"


def test_xianyuan_selection_uses_beatable_friend_only_as_soft_fallback() -> None:
    chosen = choose_xianyuan_duel_target(
        [
            _target("打不过的敌军", 3300, 600, "non_friendly"),
            _target("能打的友军", 3200, 200, "friendly"),
            _target("更低分友军", 3100, 150, "friendly"),
        ],
        self_power=500,
    )

    assert chosen is not None
    assert chosen["name"] == "能打的友军"
    assert chosen["selection_group"] == "friendly_fallback"


def test_xianyuan_selection_returns_none_when_all_three_are_stronger() -> None:
    chosen = choose_xianyuan_duel_target(
        [
            _target("甲", 3300, 600, "non_friendly"),
            _target("乙", 3200, 700, "friendly"),
            _target("丙", 3100, 800, "non_friendly"),
        ],
        self_power=500,
    )

    assert chosen is None


def test_xianyuan_selection_forces_lowest_power_after_refresh_is_exhausted() -> None:
    chosen = choose_xianyuan_duel_target(
        [
            _target("高分但较强", 3300, 800, "non_friendly"),
            _target("最低战力友军", 3100, 600, "friendly"),
            _target("中等战力", 3200, 700, "non_friendly"),
        ],
        self_power=500,
        allow_unbeatable_fallback=True,
    )

    assert chosen is not None
    assert chosen["name"] == "最低战力友军"
    assert chosen["selection_group"] == "lowest_power_forced"


def test_structured_partner_order_uses_authoritative_careers_and_minimal_swaps() -> None:
    current = [16, 23, 9, 2, 1]
    enemy = [28, 2, 1, 30, 9]

    result = best_xianyuan_partner_order(current, enemy)

    assert [xianyuan_partner_career(value) for value in enemy] == [2, 3, 1, 2, 4]
    assert result["careers"] == [4, 1, 2, 4, 3]
    assert len(plan_swaps(current, result["partner_ids"])) == result["swap_count"]


def test_structured_partner_order_rejects_unknown_new_partner() -> None:
    try:
        best_xianyuan_partner_order([16, 23, 9, 2, 999], [28, 2, 1, 30, 9])
    except ValueError as exc:
        assert "partnerId=999" in str(exc)
    else:
        raise AssertionError("unknown partner must fail closed")
