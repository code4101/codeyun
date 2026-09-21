from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from backend.core.fanxiu.activity.lottery_strategy import (
    LotteryGoal,
    decide_lottery_action,
)
from backend.core.fanxiu.activity.theme_lottery_policy import (
    ThemeLotteryTicket,
    resolve_theme_lottery_phase,
    resolve_theme_lottery_policy,
    theme_lottery_tail_at,
)


TZ = ZoneInfo("Asia/Shanghai")
FINAL_DAY = date(2026, 9, 3)


def _ticket(item_id: int, retainability: str) -> ThemeLotteryTicket:
    return ThemeLotteryTicket(
        item_id=item_id,
        label=f"ticket-{item_id}",
        retainability=retainability,  # type: ignore[arg-type]
    )


def test_no_tickets_is_pending_evidence() -> None:
    resolution = resolve_theme_lottery_policy(())

    assert resolution.status == "pending_evidence"
    assert resolution.missing_evidence == ("no_tickets",)


def test_unknown_retainability_is_pending_not_non_retainable() -> None:
    resolution = resolve_theme_lottery_policy(
        (_ticket(40010, "unknown"), _ticket(40025, "unknown"))
    )

    assert resolution.status == "pending_evidence"
    assert resolution.policy is None
    assert resolution.missing_evidence == (
        "ticket_retainability:40010",
        "ticket_retainability:40025",
    )
    assert "exhaust_all" not in resolution.reason


def test_all_retainable_resolves_first_hit() -> None:
    resolution = resolve_theme_lottery_policy(
        (_ticket(40010, "retainable"), _ticket(40025, "retainable"))
    )

    assert resolution.status == "ready"
    assert resolution.aggregate_retainability == "retainable"
    assert resolution.policy is not None
    assert resolution.policy.goal.kind == "first_hit"
    assert resolution.policy.remainder_mode == "single"


def test_all_non_retainable_resolves_exhaust_all_with_defer() -> None:
    resolution = resolve_theme_lottery_policy(
        (_ticket(40010, "non_retainable"),)
    )

    assert resolution.status == "ready"
    assert resolution.aggregate_retainability == "non_retainable"
    assert resolution.policy is not None
    assert resolution.policy.goal.kind == "exhaust_all"
    assert resolution.policy.remainder_mode == "defer"


def test_mixed_retainability_needs_adapter_scope() -> None:
    resolution = resolve_theme_lottery_policy(
        (_ticket(40010, "retainable"), _ticket(40025, "non_retainable"))
    )

    assert resolution.status == "pending_evidence"
    assert resolution.missing_evidence == (
        "mixed_ticket_execution_scope_unproven",
    )


def test_goal_override_replaces_goal_explicitly() -> None:
    resolution = resolve_theme_lottery_policy(
        (_ticket(40010, "retainable"),),
        goal_override=LotteryGoal("target_count", target_count=3),
    )

    assert resolution.status == "ready"
    assert resolution.policy is not None
    assert resolution.policy.goal.kind == "target_count"
    assert resolution.policy.goal.target_count == 3


def test_non_retainable_phase_defers_until_final_day_tail() -> None:
    resolution = resolve_theme_lottery_policy((_ticket(40010, "non_retainable"),))

    phase = resolve_theme_lottery_phase(
        resolution,
        final_day=FINAL_DAY,
        now=datetime(2026, 9, 1, 12, 0, tzinfo=TZ),
    )

    assert phase.status == "ready"
    assert phase.remainder_mode == "defer"
    assert phase.next_time == datetime(2026, 9, 3, 21, 0, tzinfo=TZ)


def test_non_retainable_phase_switches_to_single_after_tail() -> None:
    resolution = resolve_theme_lottery_policy((_ticket(40010, "non_retainable"),))

    before = resolve_theme_lottery_phase(
        resolution,
        final_day=FINAL_DAY,
        now=datetime(2026, 9, 3, 20, 59, tzinfo=TZ),
    )
    after = resolve_theme_lottery_phase(
        resolution,
        final_day=FINAL_DAY,
        now=datetime(2026, 9, 3, 21, 0, tzinfo=TZ),
    )

    assert before.remainder_mode == "defer"
    assert before.next_time == datetime(2026, 9, 3, 21, 0, tzinfo=TZ)
    assert after.remainder_mode == "single"
    assert after.next_time is None
    assert after.policy is not None and after.policy.remainder_mode == "single"


def test_phase_after_final_day_is_expired_not_past_next_time() -> None:
    resolution = resolve_theme_lottery_policy((_ticket(40010, "non_retainable"),))

    phase = resolve_theme_lottery_phase(
        resolution,
        final_day=FINAL_DAY,
        now=datetime(2026, 9, 4, 0, 30, tzinfo=TZ),
    )

    assert phase.status == "expired"
    assert phase.next_time is None
    assert phase.policy is None


def test_retainable_phase_is_unchanged() -> None:
    resolution = resolve_theme_lottery_policy((_ticket(40010, "retainable"),))

    phase = resolve_theme_lottery_phase(
        resolution,
        final_day=FINAL_DAY,
        now=datetime(2026, 9, 3, 22, 0, tzinfo=TZ),
    )

    assert phase.status == "ready"
    assert phase.policy is not None and phase.policy.goal.kind == "first_hit"
    assert phase.remainder_mode == "single"
    assert phase.next_time is None


def test_retainable_with_exhaust_all_override_keeps_no_tail() -> None:
    resolution = resolve_theme_lottery_policy(
        (_ticket(40010, "retainable"),),
        goal_override=LotteryGoal("exhaust_all"),
    )

    phase = resolve_theme_lottery_phase(
        resolution,
        final_day=FINAL_DAY,
        now=datetime(2026, 9, 1, 12, 0, tzinfo=TZ),
    )

    assert phase.status == "ready"
    assert phase.policy is not None and phase.policy.goal.kind == "exhaust_all"
    assert phase.remainder_mode == "single"
    assert phase.next_time is None


def test_non_retainable_with_first_hit_override_still_tails() -> None:
    resolution = resolve_theme_lottery_policy(
        (_ticket(40025, "non_retainable"),),
        goal_override=LotteryGoal("first_hit"),
    )

    phase = resolve_theme_lottery_phase(
        resolution,
        final_day=FINAL_DAY,
        now=datetime(2026, 9, 1, 12, 0, tzinfo=TZ),
    )

    assert phase.status == "ready"
    assert phase.policy is not None and phase.policy.goal.kind == "first_hit"
    assert phase.remainder_mode == "defer"
    assert phase.next_time == datetime(2026, 9, 3, 21, 0, tzinfo=TZ)


def test_pending_resolution_phase_carries_missing_evidence() -> None:
    resolution = resolve_theme_lottery_policy((_ticket(40010, "unknown"),))

    phase = resolve_theme_lottery_phase(
        resolution,
        final_day=FINAL_DAY,
        now=datetime(2026, 9, 3, 22, 0, tzinfo=TZ),
    )

    assert phase.status == "pending_evidence"
    assert phase.policy is None
    assert phase.missing_evidence == ("ticket_retainability:40010",)


def test_resolved_policy_drives_existing_ten_draw_algorithm() -> None:
    resolution = resolve_theme_lottery_policy((_ticket(40010, "non_retainable"),))
    assert resolution.policy is not None

    decision = decide_lottery_action(
        {
            "complete": True,
            "available_draws": 13,
            "progress": 0,
            "hit_count": 0,
        },
        policy=resolution.policy,
    )

    assert decision.action == "draw"
    assert decision.draw_mode == "ten_draw"


def test_resolved_retainable_policy_stops_after_first_hit() -> None:
    resolution = resolve_theme_lottery_policy((_ticket(40010, "retainable"),))
    assert resolution.policy is not None

    decision = decide_lottery_action(
        {
            "complete": True,
            "available_draws": 5,
            "progress": 10,
            "hit_count": 1,
        },
        policy=resolution.policy,
    )

    assert decision.action == "stop"
    assert decision.stop_reason == "first_hit_reached"


def test_tail_at_uses_final_day_and_timezone() -> None:
    tail = theme_lottery_tail_at(
        final_day=FINAL_DAY,
        now=datetime(2026, 9, 3, 8, 0, tzinfo=TZ),
    )

    assert tail == datetime(2026, 9, 3, 21, 0, tzinfo=TZ)
