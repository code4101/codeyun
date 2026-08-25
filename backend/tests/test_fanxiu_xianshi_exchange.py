from __future__ import annotations

from datetime import datetime

import pytest

from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.tasks.xianshi_exchange import (
    exchange_row_action_x,
    XianshiExchangeTaskMixin,
    is_langyage_detail_text,
    is_langyage_product_detail_text,
    plan_langyage_candidates,
    plan_zhenwuge_candidates,
    quantity_adjustment_shape,
    quantity_clicks,
    validate_common_shop_dialog,
)


def test_exchange_row_action_x_uses_right_side_of_formal_list_box():
    assert exchange_row_action_x({"x": 90, "w": 756}) == pytest.approx(755.28)


def _book(book_id: int, *, index: int, jie: int = 20, wujing: int = 2, full: bool = False):
    return {
        "book_id": book_id,
        "name": f"功法{book_id}",
        "skill_type_name": "神通",
        "filter_category": "剑修",
        "upgrade_index": index,
        "jie": jie,
        "max_jie": 200,
        "wujing": wujing,
        "max_wujing": 50,
        "full": full,
    }


def _item(book_id: int, *, item_id: int, currency: int = 1, remaining=49, unlimited=False):
    return {
        "item_id": item_id,
        "name": f"书{book_id}",
        "item_type": 999,
        "item_sub_type": 33,
        "linked_gongfa_id": book_id,
        "cost_item_id": currency,
        "cost_num": 80,
        "remaining": remaining,
        "unlimited": unlimited,
    }


def test_quantity_clicks_starts_from_one():
    assert quantity_clicks(1) == (0, 0)
    assert quantity_clicks(17) == (1, 6)
    assert quantity_clicks(35) == (3, 4)


def test_quantity_adjustment_shape_converges_from_observed_value():
    assert quantity_adjustment_shape(1, 35) == "+10"
    assert quantity_adjustment_shape(31, 35) == "+"
    assert quantity_adjustment_shape(35, 35) is None
    assert quantity_adjustment_shape(35, 1) == "-10"
    assert quantity_adjustment_shape(4, 1) == "-"


def test_common_shop_dialog_uses_runtime_quantity_unit_price_and_guards():
    snapshot = {
        "complete": True,
        "showNum": 3,
        "Price": 80,
        "HadPrice": 242,
        "CanBuy": True,
        "isEnough": True,
    }
    assert validate_common_shop_dialog(snapshot, quantity=3, unit_price=80) == 242
    with pytest.raises(RuntimeError, match="数量未闭环"):
        validate_common_shop_dialog({**snapshot, "showNum": 2}, quantity=3, unit_price=80)
    with pytest.raises(RuntimeError, match="购买资格未闭环"):
        validate_common_shop_dialog({**snapshot, "isEnough": False}, quantity=3, unit_price=80)


def test_langyage_detail_text_requires_all_saved_failure_frame_anchors():
    assert is_langyage_detail_text("融合层数：73重\n参悟效果\n价格：80\n兑换") is True
    assert is_langyage_detail_text("融合层数：73重\n价格：80\n兑换") is False
    assert is_langyage_detail_text("参悟效果\n融合层数：73重") is False


def test_langyage_product_detail_uses_runtime_name_and_rejects_list_row():
    assert is_langyage_product_detail_text("元磁神光 兑换", "元磁神光") is True
    assert is_langyage_product_detail_text("心法青锋映日兑换", "悟·青锋映日") is True
    assert is_langyage_product_detail_text("元磁神光 兑换所需 80", "元磁神光") is False
    assert is_langyage_product_detail_text("甲元仙符 兑换", "元磁神光") is False


def test_langyage_candidate_waits_for_scene_or_complete_detail_ocr():
    calls: list[tuple] = []

    class Match:
        def point(self, *, anchor):
            assert anchor == "center"
            return 12.0, 34.0

    class ListShape:
        @staticmethod
        def box():
            return {"x": 90.0, "w": 756.0}

    class View:
        @staticmethod
        def get_shape(title):
            assert title == "商品列表"
            return ListShape()

    class Runtime:
        def view(self, scene_id):
            assert scene_id == 468
            return View()

        def click_shape_center(self, *args):
            calls.append(("click_shape", *args))

        def wait_ocr_any_text(self, *args, **kwargs):
            calls.append(("find", args, kwargs))
            yield None
            return Match()

        def click_frame_point(self, *args):
            calls.append(("click", *args))

        def wait_action_settle(self, seconds):
            calls.append(("settle", seconds))
            yield None

        def view_visible(self, scene_id):
            return ("view", scene_id)

        def ocr_matches(self, predicate, **kwargs):
            return ("ocr", predicate, kwargs)

        def wait_any(self, conditions, **kwargs):
            calls.append(("wait_any", conditions, kwargs))
            yield None
            return "legacy_detail"

    row = {"book": {"filter_category": "剑修", "name": "元磁神光"}, "name": "元磁神光"}
    mixin = XianshiExchangeTaskMixin()
    list(mixin._select_exchange_candidate(Runtime(), 468, 470, row, label="仙市_琅琊榜"))

    wait_call = next(call for call in calls if call[0] == "wait_any")
    click_call = next(call for call in calls if call[0] == "click")
    assert click_call == ("click", 468, pytest.approx(755.28), 34.0)
    assert wait_call[1]["legacy_detail"] == ("view", 470)
    assert wait_call[1]["common_shop_detail"] == ("view", 634)
    predicate = wait_call[1]["legacy_detail_text"][1]
    assert predicate("元磁神光 兑换") is True
    assert predicate("元磁神光 兑换所需 80") is False
    assert wait_call[2]["timeout"] == 15.0


def test_zhenwuge_prefers_full_then_atlas_priority_and_skips_sold_out():
    books = [_book(1, index=1), _book(2, index=9, full=True), _book(3, index=2)]
    items = [_item(1, item_id=11), _item(2, item_id=12), _item(3, item_id=13, remaining=0)]
    plan = plan_zhenwuge_candidates(books, items)
    assert [row["item_id"] for row in plan] == [12, 11]
    assert plan[0]["desired"] == 48


def test_langyage_caps_fused_plus_backpack_at_100_and_rolls_forward():
    books = [_book(1, index=1, jie=65), _book(2, index=2, jie=54)]
    items = [
        _item(1, item_id=11, remaining=None, unlimited=True),
        _item(2, item_id=12, remaining=None, unlimited=True),
    ]
    plan = plan_langyage_candidates(books, items, {11: 35, 12: 7})
    assert [row["item_id"] for row in plan] == [12]
    assert plan[0]["effective_fusion"] == 61
    assert plan[0]["desired"] == 39


def test_langyage_excludes_immortal_art_tab_even_when_catalog_calls_it_gongfa():
    immortal_art = _book(9, index=1, jie=1)
    immortal_art["filter_category"] = "仙术"
    assert plan_langyage_candidates(
        [immortal_art],
        [_item(9, item_id=19, remaining=None, unlimited=True)],
        {19: 0},
    ) == []


def test_both_jobs_are_single_standard_tuesday_0010_instances():
    register_fanxiu_data_annotation_default_runtime_jobs()
    tasks = default_data_annotation_scheduler_tasks(datetime(2026, 8, 8, 12, 0, 0))
    expected = {
        "xianshi-zhenwuge": ("xianshi_zhenwuge", 10),
        "xianshi-langya-rankings": ("xianshi_langya_rankings", 20),
    }
    for task_id, (task_type, dispatch_order) in expected.items():
        matches = [task for task in tasks if task["id"] == task_id]
        assert len(matches) == 1
        task = matches[0]
        assert task["task_type"] == task_type
        assert task["next_time"] == "2026-08-11 00:10:00"
        assert task["dispatch_order"] == dispatch_order
        definition = get_fanxiu_data_annotation_task_cell_definition(task_type)
        assert definition is not None and definition.scheduler_supported is True
