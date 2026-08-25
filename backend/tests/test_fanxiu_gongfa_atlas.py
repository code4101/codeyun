from backend.core.fanxiu.instrumentation import gongfa_atlas
from backend.core.fanxiu.instrumentation.gongfa_priority import (
    apply_gongfa_priority_to_books,
    apply_saved_gongfa_priority_to_plan,
)


def test_project_gongfa_books_excludes_secret_catalog_and_uses_equipment_priority(monkeypatch):
    monkeypatch.setattr(
        gongfa_atlas,
        "_book_catalog_index",
        lambda: {
            1: {
                "name": "上品心法",
                "skill_type_name": "心法",
                "quality_grade_name": "上品",
                "quality_grade_order": 0,
                "quality_grade_color": "#2a4b10",
            },
            2: {
                "name": "仙品神通",
                "skill_type_name": "神通",
                "quality_grade_name": "仙品",
                "quality_grade_order": 3,
                "quality_grade_color": "#9e1e09",
            },
            3: {
                "name": "秘传",
                "skill_type_name": "",
                "quality_grade_name": "圣品",
                "quality_grade_order": 5,
                "quality_grade_color": "#73123a",
            },
            4: {
                "name": "同品先行功法",
                "skill_type_name": "神通",
                "quality_grade_name": "仙品",
                "quality_grade_order": 3,
                "quality_grade_color": "#9e1e09",
            },
        },
    )

    books = gongfa_atlas._project_gongfa_books([
        {"book_id": 1, "jie": 1, "tongxuan": 0, "star": 1, "grade": 20},
        {"book_id": 3, "jie": 1, "tongxuan": 0, "star": 1},
        {"book_id": 2, "jie": 1, "tongxuan": 0, "star": 1, "grade": 100},
        {"book_id": 4, "jie": 1, "tongxuan": 0, "star": 1, "grade": 200, "upgrade_priority": 1},
    ])

    assert [book["book_id"] for book in books] == [4, 2, 1]
    assert [book["upgrade_index"] for book in books] == [1, 2, 3]
    assert books[0]["quality_grade_name"] == "仙品"
    assert books[2]["quality_grade_color"] == "#2a4b10"


def test_project_gongfa_books_orders_fallback_by_quality_before_level(monkeypatch):
    monkeypatch.setattr(
        gongfa_atlas,
        "_book_catalog_index",
        lambda: {
            1: {"name": "高层上品", "skill_type_name": "神通", "quality_grade_order": 0},
            2: {"name": "低层神品", "skill_type_name": "神通", "quality_grade_order": 4},
        },
    )

    books = gongfa_atlas._project_gongfa_books([
        {"book_id": 1, "grade": 2000},
        {"book_id": 2, "grade": 1},
    ])

    assert [book["book_id"] for book in books] == [2, 1]


def test_saved_atlas_priority_is_the_shared_book_order():
    books, priority_ids = apply_gongfa_priority_to_books(
        [{"book_id": 1}, {"book_id": 2}, {"book_id": 3}],
        [3, 1],
    )

    assert [book["book_id"] for book in books] == [3, 1, 2]
    assert [book["upgrade_index"] for book in books] == [1, 2, 3]
    assert priority_ids == [3, 1, 2]


def test_page_update_refreshes_priority_from_current_equipment(monkeypatch):
    monkeypatch.setattr(
        gongfa_atlas,
        "load_gongfa_priority_book_ids",
        lambda: [3, 2, 1],
    )
    books = [{"book_id": 1}, {"book_id": 2}, {"book_id": 3}]

    saved_books, saved_ids = gongfa_atlas._apply_atlas_priority(
        books,
        refresh_priority_from_equipment=False,
    )
    refreshed_books, refreshed_ids = gongfa_atlas._apply_atlas_priority(
        books,
        refresh_priority_from_equipment=True,
    )

    assert [book["book_id"] for book in saved_books] == [3, 2, 1]
    assert saved_ids == [3, 2, 1]
    assert [book["book_id"] for book in refreshed_books] == [1, 2, 3]
    assert refreshed_ids == [1, 2, 3]


def test_daily_experience_plan_consumes_saved_atlas_priority():
    plan = apply_saved_gongfa_priority_to_plan(
        {
            "complete": True,
            "books": [
                {"book_id": 1, "name": "甲", "progression": {"upgradeable": True}},
                {"book_id": 2, "name": "乙", "progression": {"upgradeable": False}},
            ],
            "fallback_candidates": [
                {"book_id": 3, "name": "丙", "progression": {"upgradeable": True}},
            ],
            "next_upgradable_book": {"book_id": 1},
        },
        priority_book_ids=[2, 3, 1],
    )

    assert plan["next_upgradable_book"]["book_id"] == 3
    assert plan["next_upgradable_book"]["selection_pool"] == "fallback_learned"
    assert plan["priority_source"] == "gongfa_atlas"


def test_attach_upgrade_plan_uses_daily_experience_priority_and_marks_fallback():
    books = gongfa_atlas._attach_upgrade_plan(
        [{"book_id": 10}, {"book_id": 20}, {"book_id": 30}],
        [
            {"priority": 2, "book_id": 20, "first_usage": {"slot": 2, "role": "xian"}},
            {"priority": 1, "book_id": 30, "first_usage": {"slot": 1, "role": "main"}},
        ],
    )

    assert books == [
        {
            "book_id": 10,
            "upgrade_priority": None,
            "upgrade_priority_pool": "fallback_learned",
            "upgrade_first_usage": None,
            "upgrade_usages": [],
        },
        {
            "book_id": 20,
            "upgrade_priority": 2,
            "upgrade_priority_pool": "equipped_dependency",
            "upgrade_first_usage": {"slot": 2, "role": "xian"},
            "upgrade_usages": [],
        },
        {
            "book_id": 30,
            "upgrade_priority": 1,
            "upgrade_priority_pool": "equipped_dependency",
            "upgrade_first_usage": {"slot": 1, "role": "main"},
            "upgrade_usages": [],
        },
    ]


def test_snapshot_summary_uses_projected_gongfa_books_only():
    assert gongfa_atlas._snapshot_summary([
        {"full": False, "wujing": 0, "tongxuan": 0},
        {"full": True, "wujing": 2, "tongxuan": 1},
    ]) == {
        "learned_count": 2,
        "full_count": 1,
        "upgradeable_count": 1,
        "wujing_count": 1,
        "tongxuan_count": 1,
    }


def test_projected_full_state_uses_fusion_jie_not_star(monkeypatch):
    monkeypatch.setattr(
        gongfa_atlas,
        "_book_catalog_index",
        lambda: {
            306103: {
                "name": "皓月剑诀",
                "skill_type_name": "神通",
                "max_jie": 200,
                "max_wujing": 50,
                "max_tongxuan": 50,
            },
        },
    )

    [book] = gongfa_atlas._project_gongfa_books([
        {
            "book_id": 306103,
            "jie": 74,
            "star": 24,
            "max_star": 24,
            "pin": 51,
            "tongxuan": 17,
            "full": True,
        },
    ])

    assert book["wujing"] == 50
    assert book["full"] is False
    assert book["remaining_fusion"] == 126


def test_projected_book_reuses_daily_experience_level_cap(monkeypatch):
    monkeypatch.setattr(
        gongfa_atlas,
        "_book_catalog_index",
        lambda: {
            306103: {
                "name": "皓月剑诀",
                "skill_type_name": "神通",
            },
        },
    )

    [book] = gongfa_atlas._project_gongfa_books([{
        "book_id": 306103,
        "grade": 116,
        "star": 3,
        "max_star": 24,
    }])

    assert book["max_grade"] == 150
