from datetime import datetime, timedelta, timezone

import pytest

from backend.core.fanxiu.activity.exchange_challenge_planning import plan_exchange_challenge_batch


END = datetime(2026, 10, 3, 22, tzinfo=timezone(timedelta(hours=8)))
TIERS = [{"goods_id": i, "target_total_tokens": n, "target_remaining_tokens": n}
         for i, n in enumerate((1000, 2000, 3000), 1)]


def plan(now, balance=2000, *, samples=None, milestones=TIERS):
    return plan_exchange_challenge_batch(
        milestones=milestones, current_currency=balance, cumulative_currency=balance,
        samples=([{"completed_attempts": 100, "currency_delta": 1000}]
                 if samples is None else samples), capacity=1000, now=now, activity_end_at=END)


@pytest.mark.parametrize("days_before_end", [1, 2, 4, 9])
def test_n_day_activity_reserves_highest_target(days_before_end):
    now = END - timedelta(days=days_before_end)
    reserved = plan(now)
    assert (reserved["status"], reserved["reason"], reserved["count"]) == (
        "deferred", "final_day_reserved", 0)
    assert reserved["target"]["goods_id"] == 3
    assert reserved["unlock_at"] == "2026-10-03T00:00:00+08:00"
    lower = plan(now, balance=1000)
    assert lower["target"]["goods_id"] == 2 and lower["count"] == 100


def test_final_day_unlock_uses_activity_timezone_and_stops_at_end():
    midnight = END.replace(hour=0)
    assert plan(midnight - timedelta(microseconds=1))["status"] == "deferred"
    assert plan(midnight.astimezone(timezone.utc))["count"] == 100
    assert plan(END)["reason"] == "activity_ended"


def test_initial_sample_and_already_funded_target_keep_their_meaning():
    first_day = END - timedelta(days=4)
    assert plan(first_day, balance=0, samples=[])["count"] == 100
    assert plan(first_day, balance=3000)["status"] == "completed"
    assert plan(first_day, milestones=TIERS[-1:])["status"] == "deferred"


def test_final_day_replans_from_latest_yield_and_current_wallet():
    rows = [{"completed_attempts": 100, "currency_delta": 1000},
            {"completed_attempts": 50, "currency_delta": 1000}]
    final_day = END.replace(hour=10)
    # Overnight natural stamina has funded another 600 tokens. Latest batch
    # yielded 20/run, so the remaining 400 needs 20, not the old rate's 40.
    assert plan(final_day, balance=2600, samples=rows)["count"] == 20


def test_naive_clock_cannot_unlock_highest_target():
    with pytest.raises(ValueError, match="时区"):
        plan(END.replace(tzinfo=None))


@pytest.mark.parametrize("capacity,status,count", [(79, "pass", 0), (80, "ready", 80)])
def test_native_batch_unit_rounds_before_capacity_gate(capacity, status, count):
    result = plan_exchange_challenge_batch(
        milestones=TIERS, current_currency=290, cumulative_currency=290,
        samples=[{"completed_attempts": 100, "currency_delta": 1000}],
        capacity=capacity, now=END.replace(hour=10), activity_end_at=END, batch_unit=10)
    assert (result["needed"], result["status"], result["count"]) == (80, status, count)
