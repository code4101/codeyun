from backend.core.fanxiu.activity.exchange_planning import (
    ExchangeMeasurement,
    ExchangeYieldRate,
    calculate_exchange_currency_gap,
    estimate_remaining_attempts,
    latest_exchange_yield_rate,
    plan_available_attempts,
)


def test_latest_exchange_yield_rate_uses_latest_valid_batch() -> None:
    rate = latest_exchange_yield_rate(
        [
            ExchangeMeasurement(exchange_currency=1000),
            ExchangeMeasurement(exchange_currency=3800, attempt_count_delta=10),
            ExchangeMeasurement(exchange_currency=3800, attempt_count_delta=5),
            ExchangeMeasurement(exchange_currency=6800, attempt_count_delta=10),
        ]
    )

    assert rate == ExchangeYieldRate(
        exchange_currency_delta=3000,
        attempt_count=10,
    )
    assert rate.exchange_currency_per_attempt == 300


def test_estimate_remaining_attempts_uses_exact_ceiling_math() -> None:
    rate = ExchangeYieldRate(
        exchange_currency_delta=27984,
        attempt_count=100,
    )

    assert estimate_remaining_attempts(
        accumulated_exchange_currency=43910,
        target_exchange_currency=104000,
        yield_rate=rate,
    ) == 215
    assert estimate_remaining_attempts(
        accumulated_exchange_currency=43910,
        target_exchange_currency=482100,
        yield_rate=rate,
    ) == 1566


def test_estimate_remaining_attempts_leaves_non_targets_blank() -> None:
    rate = ExchangeYieldRate(exchange_currency_delta=1000, attempt_count=10)

    assert estimate_remaining_attempts(
        accumulated_exchange_currency=2000,
        target_exchange_currency=2000,
        yield_rate=rate,
    ) is None
    assert estimate_remaining_attempts(
        accumulated_exchange_currency=2000,
        target_exchange_currency=None,
        yield_rate=rate,
    ) is None
    assert estimate_remaining_attempts(
        accumulated_exchange_currency=2000,
        target_exchange_currency=3000,
        yield_rate=None,
    ) is None


def test_one_shot_budget_prefers_stage9_then_stage8_without_replanning() -> None:
    rate = ExchangeYieldRate(exchange_currency_delta=1000, attempt_count=10)

    full = plan_available_attempts(
        accumulated_exchange_currency=1000,
        ideal_target_currency=5000,
        fallback_target_currency=3000,
        available_attempts=50,
        yield_rate=rate,
    )
    fallback = plan_available_attempts(
        accumulated_exchange_currency=1000,
        ideal_target_currency=5000,
        fallback_target_currency=3000,
        available_attempts=25,
        yield_rate=rate,
    )
    approach = plan_available_attempts(
        accumulated_exchange_currency=1000,
        ideal_target_currency=5000,
        fallback_target_currency=3000,
        available_attempts=15,
        yield_rate=rate,
    )

    assert (full.target_level, full.planned_attempts) == ("stage9", 40)
    assert (fallback.target_level, fallback.planned_attempts) == ("stage8", 25)
    assert (approach.target_level, approach.planned_attempts) == (
        "approach_stage8",
        15,
    )


def test_one_shot_budget_fails_closed_without_speed_sample() -> None:
    budget = plan_available_attempts(
        accumulated_exchange_currency=1000,
        ideal_target_currency=5000,
        fallback_target_currency=3000,
        available_attempts=100,
        yield_rate=None,
    )

    assert budget.target_level == "unmeasured"
    assert budget.planned_attempts == 0


def test_currency_gap_uses_purchases_balance_and_cumulative_history() -> None:
    already_bought = calculate_exchange_currency_gap(
        target_total_tokens=1000,
        target_remaining_tokens=400,
        current_currency=100,
        cumulative_currency=700,
    )
    spent_outside_target = calculate_exchange_currency_gap(
        target_total_tokens=1000,
        target_remaining_tokens=900,
        current_currency=100,
        cumulative_currency=1200,
    )

    assert already_bought.balance_gap == 300
    assert already_bought.cumulative_gap == 300
    assert already_bought.required_new_currency == 300
    assert spent_outside_target.cumulative_gap == 0
    assert spent_outside_target.balance_gap == 800
    assert spent_outside_target.required_new_currency == 800
