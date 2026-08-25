from __future__ import annotations

from backend.core.fanxiu.instrumentation.gongfa_equipment import (
    _catalog_filter_category,
    _equipped_records,
    _training_state_values,
    build_ordered_book_plan,
    select_fallback_upgradable_book,
    select_first_upgradable_book,
)


def _book(book_id: int, name: str = "", *, grid: int | None = None):
    result = {
        "book_id": book_id,
        "name": name,
        "source_skill_id": book_id * 1000,
        "canonical": True,
    }
    if grid is not None:
        result["grid"] = grid
    return result


def _equipment(
    category: str,
    slot: int,
    *,
    name: str,
    composition: str = "homemake",
    main=(),
    xian=(),
    side=(),
    grid=(),
):
    return {
        "category": category,
        "slot": slot,
        "name": name,
        "composition": composition,
        "components": {
            "main": list(main),
            "xian": list(xian),
            "side": list(side),
            "grid": list(grid),
        },
    }


def test_book_plan_orders_each_gongfa_main_xian_side_before_next_slot():
    equipped = [
        _equipment(
            "gongfa",
            2,
            name="第二神通",
            main=[_book(20)],
            xian=[_book(120)],
            side=[_book(220), _book(221)],
        ),
        _equipment(
            "xinfa",
            12,
            name="第一心法",
            main=[_book(30)],
            side=[_book(230), _book(231)],
        ),
        _equipment(
            "gongfa",
            1,
            name="第一神通",
            main=[_book(10)],
            xian=[_book(110)],
            side=[_book(210), _book(211)],
        ),
    ]

    books, duplicate_count = build_ordered_book_plan(equipped)

    assert [book["book_id"] for book in books] == [
        10,
        110,
        210,
        211,
        20,
        120,
        220,
        221,
        30,
        230,
        231,
    ]
    assert duplicate_count == 0
    assert [book["priority"] for book in books] == list(range(1, 12))


def test_book_plan_stably_deduplicates_and_retains_every_usage():
    equipped = [
        _equipment(
            "gongfa",
            1,
            name="第一神通",
            main=[_book(10, "主书")],
            side=[_book(20, "副书")],
        ),
        _equipment(
            "gongfa",
            2,
            name="第二神通",
            main=[_book(20, "后续主书")],
            side=[_book(10, "重复副书")],
        ),
    ]

    books, duplicate_count = build_ordered_book_plan(equipped)

    assert [book["book_id"] for book in books] == [10, 20]
    assert duplicate_count == 2
    assert books[0]["name"] == "主书"
    assert books[0]["first_usage"] == {
        "category": "gongfa",
        "slot": 1,
        "equipped_name": "第一神通",
        "role": "main",
        "source_skill_id": 10000,
    }
    assert books[0]["usages"][1]["role"] == "side"
    assert books[1]["name"] == "副书"
    assert books[1]["usages"][0]["role"] == "side"
    assert books[1]["usages"][1]["role"] == "main"


def test_book_plan_keeps_lingjie_xinfa_grid_order():
    equipped = [
        _equipment(
            "xinfa",
            13,
            name="自创心法",
            main=[_book(30)],
            side=[_book(31), _book(32)],
        ),
        _equipment(
            "xinfa",
            12,
            name="",
            composition="lingjie_xinfa_grid",
            grid=[_book(40, grid=1), _book(41, grid=2)],
        ),
    ]

    books, duplicate_count = build_ordered_book_plan(equipped)

    assert [book["book_id"] for book in books] == [40, 41, 30, 31, 32]
    assert books[0]["first_usage"]["grid"] == 1
    assert books[1]["first_usage"]["grid"] == 2
    assert duplicate_count == 0


def test_book_plan_does_not_mix_unknown_categories_into_candidates():
    books, duplicate_count = build_ordered_book_plan(
        [
            _equipment(
                "unknown",
                1,
                name="未知",
                main=[_book(99)],
            )
        ]
    )

    assert books == []
    assert duplicate_count == 0


def test_normal_equipped_skill_uses_live_skill_to_book_mapping():
    equipped = _equipped_records(
        [{"slot": 1, "skill_id": 301001, "skill_type": 0, "make_id": 0}],
        [],
        homemake={},
        xinfa_grids={},
        skill_to_book={301001: 300001},
        names={300001: "普通功法"},
    )

    books, duplicate_count = build_ordered_book_plan(equipped)

    assert equipped[0]["composition"] == "normal"
    assert books[0]["book_id"] == 300001
    assert books[0]["name"] == "普通功法"
    assert books[0]["canonical"] is True
    assert duplicate_count == 0


def test_unknown_normal_skill_is_not_fabricated_as_a_book():
    equipped = _equipped_records(
        [{"slot": 1, "skill_id": 399999, "skill_type": 0, "make_id": 0}],
        [],
        homemake={},
        xinfa_grids={},
        skill_to_book={},
        names={},
    )

    books, duplicate_count = build_ordered_book_plan(equipped)

    assert equipped[0]["components"]["main"] == []
    assert books == []
    assert duplicate_count == 0


def test_selector_always_scans_fresh_list_from_first_priority():
    books = [
        {"priority": 1, "book_id": 10, "name": "第一本"},
        {"priority": 2, "book_id": 20, "name": "第二本"},
        {"priority": 3, "book_id": 30, "name": "第三本"},
    ]
    progression = {
        10: {"grade": 1150, "star": 24, "pin": 1, "max_star": 24},
        20: {"grade": 1300, "star": 27, "pin": 2, "max_star": 55},
        30: {"grade": 350, "star": 8, "pin": 1, "max_star": 24},
    }

    enriched, selected, blocked_priority = select_first_upgradable_book(
        books,
        progression,
    )

    assert enriched[0]["progression"]["level_cap"] == 1150
    assert enriched[0]["progression"]["upgradeable"] is False
    assert enriched[1]["progression"]["level_cap"] == 1350
    assert selected is not None
    assert selected["book_id"] == 20
    assert blocked_priority is None


def test_selector_stops_at_unknown_earlier_book_instead_of_skipping_it():
    books = [
        {"priority": 1, "book_id": 10, "name": "状态未知"},
        {"priority": 2, "book_id": 20, "name": "可以升级"},
    ]

    enriched, selected, blocked_priority = select_first_upgradable_book(
        books,
        {
            20: {"grade": 750, "star": 16, "pin": 1, "max_star": 24},
        },
    )

    assert enriched[0]["progression"]["known"] is False
    assert selected is None
    assert blocked_priority == 1


class _PickLast:
    @staticmethod
    def choice(items):
        return items[-1]


def test_fallback_excludes_primary_and_selects_highest_quality_with_random_tie():
    progression = {
        10: {"grade": 750, "star": 16, "pin": 1, "max_star": 24},
        20: {"grade": 650, "star": 14, "pin": 1, "max_star": 24},
        30: {"grade": 650, "star": 14, "pin": 1, "max_star": 24},
        40: {"grade": 500, "star": 11, "pin": 1, "max_star": 24},
        50: {"grade": 1150, "star": 24, "pin": 1, "max_star": 24},
        60: {"grade": 650, "star": 14, "pin": 1, "max_star": 24},
    }
    catalog = {
        10: {"name": "一级清单", "skill_type": 2},
        20: {"name": "并列神通", "skill_type": 2, "quality_grade_order": 4, "quality_grade_name": "神品"},
        30: {"name": "并列心法", "skill_type": 5, "quality_grade_order": 4, "quality_grade_name": "神品"},
        40: {"name": "较低品级", "skill_type": 2, "quality_grade_order": 3, "quality_grade_name": "仙品"},
        50: {"name": "已经满级", "skill_type": 5, "quality_grade_order": 5, "quality_grade_name": "圣品"},
        60: {"name": "不是功法心法", "skill_type": 24},
    }

    candidates, selected, blocked = select_fallback_upgradable_book(
        [10],
        progression,
        catalog,
        rng=_PickLast(),
    )

    assert [item["book_id"] for item in candidates] == [20, 30, 40]
    assert selected is not None
    assert selected["book_id"] == 30
    assert selected["highest_quality_grade_tie_count"] == 2
    assert blocked == []


def test_fallback_unknown_relevant_book_blocks_selection():
    candidates, selected, blocked = select_fallback_upgradable_book(
        [],
        {
            20: {"grade": 650, "star": 14, "pin": 1, "max_star": 0},
            30: {"grade": 500, "star": 11, "pin": 1, "max_star": 24},
        },
        {
            20: {"name": "状态未知", "skill_type": 2, "quality_grade_order": 4},
            30: {"name": "可以升级", "skill_type": 5, "quality_grade_order": 3},
        },
        rng=_PickLast(),
    )

    assert [item["book_id"] for item in candidates] == [30]
    assert selected is None
    assert blocked == [20]


def test_catalog_filter_category_handles_xianshu_and_four_disciplines():
    assert _catalog_filter_category(
        {
            "quality_type_name": "仙术",
            "skills": [{"sub_type_name": "仙界书"}],
        }
    )[0] == "仙术"
    for category in ("剑修", "法修", "魔修", "体修"):
        assert _catalog_filter_category(
            {
                "quality_type_name": category,
                "skills": [{"sub_type_name": category}],
            }
        )[0] == category


def test_training_state_uses_runtime_full_tip_without_equipment_scan():
    class Reader:
        @staticmethod
        def long(value):
            assert value == "exp-pool"
            return 2973

    state = _training_state_values(
        Reader(),
        {
            "isFullTip": False,
            "_CurGongFaExpPoolValue": "exp-pool",
            "isLongPress": False,
            "isBottleLongPress": True,
        },
    )

    assert state == {
        "current_book_full": False,
        "experience_pool": 2973,
        "is_long_press": False,
        "is_bottle_long_press": True,
    }


def test_training_state_treats_unset_full_tip_as_false_after_pool_loaded():
    class Reader:
        @staticmethod
        def long(value):
            assert value == "exp-pool"
            return 2973

    state = _training_state_values(
        Reader(),
        {
            "_CurGongFaExpPoolValue": "exp-pool",
        },
    )

    assert state["current_book_full"] is False
    assert state["experience_pool"] == 2973
