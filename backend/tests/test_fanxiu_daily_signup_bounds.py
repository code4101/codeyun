from __future__ import annotations

import pytest

from backend.core.fanxiu.data_annotation.tasks.signup_misc import (
    daily_signup_traversal_limits,
    ensure_daily_signup_traversal_budget,
)


def test_daily_signup_traversal_limits_are_finite_and_configurable():
    assert daily_signup_traversal_limits() == (300.0, 20, 30)
    assert daily_signup_traversal_limits(
        {
            "signup_flow_timeout_seconds": 90,
            "signup_max_items": 7,
            "signup_max_scrolls": 9,
        }
    ) == (90.0, 7, 9)


def test_daily_signup_traversal_budget_rejects_expired_deadline():
    with pytest.raises(TimeoutError, match="绝对截止时间"):
        ensure_daily_signup_traversal_budget(
            deadline=100.0,
            now=100.0,
            claimed=2,
            scrolls=3,
            max_items=20,
            max_scrolls=30,
            phase="扫描报名列",
        )


@pytest.mark.parametrize(
    ("before_item", "before_scroll", "claimed", "scrolls", "message"),
    [
        (True, False, 20, 0, "报名项超过单次上限 20"),
        (False, True, 0, 30, "达到单次上限 30"),
    ],
)
def test_daily_signup_traversal_budget_rejects_item_and_scroll_exhaustion(
    before_item: bool,
    before_scroll: bool,
    claimed: int,
    scrolls: int,
    message: str,
):
    with pytest.raises(RuntimeError, match=message):
        ensure_daily_signup_traversal_budget(
            deadline=200.0,
            now=100.0,
            claimed=claimed,
            scrolls=scrolls,
            max_items=20,
            max_scrolls=30,
            phase="边界检查",
            before_item=before_item,
            before_scroll=before_scroll,
        )
