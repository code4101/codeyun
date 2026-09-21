from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from backend.core.fanxiu.activity.lottery_strategy import decide_lottery_action
from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_default_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.tasks.holy_wood_prayer import (
    HOLY_WOOD_DEFAULT_SPEND_BUDGET,
    HOLY_WOOD_TASK_TYPE,
    parse_holy_wood_ticket_draws,
    resolve_holy_wood_lottery_phase,
    validate_holy_wood_store_increment,
)


TZ = ZoneInfo("Asia/Shanghai")
FINAL_DAY = date(2026, 9, 3)


def _snapshot(first: int, second: int) -> dict:
    return {
        "items": [
            {"id": 3040201, "purchased_times": first},
            {"id": 3040202, "purchased_times": second},
            {"id": 3040203, "purchased_times": 0},
        ]
    }


def test_ticket_parser_accepts_one_or_two_exact_counters() -> None:
    assert parse_holy_wood_ticket_draws("27/1", cost_per_draw=1) == 27
    assert parse_holy_wood_ticket_draws("27/1 0/1", cost_per_draw=1) == 27
    assert parse_holy_wood_ticket_draws("27/1 0/10", cost_per_draw=1) is None
    assert parse_holy_wood_ticket_draws("没有稳定计数", cost_per_draw=1) is None


def test_store_increment_requires_one_offer_and_exact_wallet_delta() -> None:
    validate_holy_wood_store_increment(
        _snapshot(0, 0),
        _snapshot(1, 0),
        offer_id=3040201,
        unit_cost=488,
        wallet_before=10000,
        wallet_after=9512,
    )
    with pytest.raises(RuntimeError, match="购买增量异常"):
        validate_holy_wood_store_increment(
            _snapshot(0, 0),
            _snapshot(1, 1),
            offer_id=3040201,
            unit_cost=488,
            wallet_before=10000,
            wallet_after=9512,
        )
    with pytest.raises(RuntimeError, match="灵石扣减异常"):
        validate_holy_wood_store_increment(
            _snapshot(0, 0),
            _snapshot(1, 0),
            offer_id=3040201,
            unit_cost=488,
            wallet_before=10000,
            wallet_after=9000,
        )


def test_holy_wood_prayer_is_internalized_ai_component() -> None:
    register_fanxiu_default_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition(HOLY_WOOD_TASK_TYPE)
    assert definition is not None
    # 圣木祈愿 is now executed by the canonical theme-collection Job; it keeps
    # its Cell for debugging but is no longer a standalone Scheduler Job.
    assert definition.scheduler_supported is False
    assert definition.standard_job is False

    theme = get_fanxiu_data_annotation_task_cell_definition("theme_collection")
    assert theme is not None
    assert theme.standard_job is True
    assert theme.standard_job_id == "theme-collection"


def test_holy_wood_policy_keeps_drawing_ten_after_grand_prize() -> None:
    phase = resolve_holy_wood_lottery_phase(
        final_day=FINAL_DAY,
        now=datetime(2026, 9, 1, 12, 0, tzinfo=TZ),
    )
    assert phase.status == "ready"
    assert phase.policy is not None
    assert phase.policy.goal.kind == "exhaust_all"

    decision = decide_lottery_action(
        {"complete": True, "available_draws": 16, "progress": 20, "hit_count": 1},
        policy=phase.policy,
    )

    assert decision.action == "draw"
    assert decision.draw_mode == "ten_draw"


def test_holy_wood_six_tickets_defer_before_final_day_tail() -> None:
    phase = resolve_holy_wood_lottery_phase(
        final_day=FINAL_DAY,
        now=datetime(2026, 9, 3, 20, 59, tzinfo=TZ),
    )
    assert phase.remainder_mode == "defer"
    assert phase.policy is not None

    decision = decide_lottery_action(
        {"complete": True, "available_draws": 6, "progress": 20, "hit_count": 1},
        policy=phase.policy,
    )

    assert decision.action == "stop"
    assert decision.stop_reason == "terminal_remainder_deferred"


def test_holy_wood_six_tickets_clear_single_after_final_day_tail() -> None:
    phase = resolve_holy_wood_lottery_phase(
        final_day=FINAL_DAY,
        now=datetime(2026, 9, 3, 21, 0, tzinfo=TZ),
    )
    assert phase.remainder_mode == "single"
    assert phase.policy is not None

    decision = decide_lottery_action(
        {"complete": True, "available_draws": 6, "progress": 20, "hit_count": 1},
        policy=phase.policy,
    )

    assert decision.action == "draw"
    assert decision.draw_mode == "single_draw"
