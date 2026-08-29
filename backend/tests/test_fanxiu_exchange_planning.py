from backend.core.fanxiu.activity.exchange_planning import (
    ExchangeMeasurement,
    ExchangeYieldFeatureSpec,
    ExchangeYieldRate,
    ExchangeYieldScatterSample,
    calculate_exchange_currency_gap,
    estimate_remaining_attempts,
    fit_exchange_yield_scatter_model,
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


def test_one_shot_budget_prefers_closing_then_other_discount_without_replanning() -> None:
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

    assert (full.target_level, full.planned_attempts) == ("收尾道具", 40)
    assert (fallback.target_level, fallback.planned_attempts) == ("其他折扣", 25)
    assert (approach.target_level, approach.planned_attempts) == (
        "尽量接近其他折扣",
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


def test_scatter_model_uses_attempt_weighted_plain_batches() -> None:
    model = fit_exchange_yield_scatter_model(
        [
            ExchangeYieldScatterSample(
                exchange_currency_delta=100,
                attempt_count=10,
            ),
            ExchangeYieldScatterSample(
                exchange_currency_delta=20_000,
                attempt_count=1_000,
            ),
        ]
    )

    assert model is not None
    assert model.plain_currency_per_attempt == 20_100 / 1_010
    assert model.total_attempts == 1_010
    assert model.plain_attempts == 1_010
    assert model.estimate_attempts(1_000) == 51


def test_scatter_model_separates_tiandi_yiju_plain_rate_from_shrunken_bonuses() -> None:
    """The large mixed batch fits bonuses, not a false 66-token plain rate."""

    model = fit_exchange_yield_scatter_model(
        [
            ExchangeYieldScatterSample(
                exchange_currency_delta=450,
                attempt_count=10,
            ),
            ExchangeYieldScatterSample(
                exchange_currency_delta=97_899,
                attempt_count=1_479,
                feature_item_usage=(("fourfold", 284), ("miaoshou", 480)),
            ),
            ExchangeYieldScatterSample(
                exchange_currency_delta=1_638,
                attempt_count=41,
            ),
        ],
        feature_specs=[
            # A nominal fourfold adds 3x, but the fitted realization is learned.
            ExchangeYieldFeatureSpec(
                key="fourfold",
                nominal_extra_multiplier=3.0,
                default_realization_ratio=0.5,
                prior_item_weight=50.0,
            ),
            ExchangeYieldFeatureSpec(
                key="miaoshou",
                nominal_extra_multiplier=1.0,
                default_realization_ratio=0.5,
                prior_item_weight=50.0,
            ),
        ],
    )

    assert model is not None
    # Both direct no-confirmed-bonus batches contribute by attempt count:
    # (450 + 1638) / (10 + 41).
    assert model.plain_currency_per_attempt == 2_088 / 51
    assert model.plain_attempts == 51
    assert model.total_attempts == 1_530
    fits = {fit.key: fit for fit in model.feature_fits}
    assert 0.5 < fits["fourfold"].realization_ratio < 0.8
    assert 1.0 < fits["fourfold"].effective_multiplier < 4.0
    assert 0.5 < fits["miaoshou"].realization_ratio < 0.8
    # A future no-item forecast must not reuse the mixed batch's 66.2/run.
    assert model.estimate_attempts(293_402) == 7_167
    # Planned inventories can still use the learned, discounted feature yield.
    assert model.currency_per_attempt(
        feature_item_fractions={"fourfold": 0.2, "miaoshou": 0.3}
    ) > model.plain_currency_per_attempt


def test_scatter_model_keeps_activity_specific_features_out_of_base_contract() -> None:
    model = fit_exchange_yield_scatter_model(
        [
            ExchangeYieldScatterSample(1_000, 100),
            ExchangeYieldScatterSample(
                1_750,
                100,
                feature_item_usage=(("activity_item", 50),),
            ),
        ],
        feature_specs=[
            ExchangeYieldFeatureSpec(
                key="activity_item",
                nominal_extra_multiplier=2.0,
                default_realization_ratio=0.5,
                prior_item_weight=0.0,
            )
        ],
    )

    assert model is not None
    fit = model.feature_fits[0]
    assert model.plain_currency_per_attempt == 10.0
    assert fit.realization_ratio == 0.75
    assert fit.effective_multiplier == 2.5
    assert model.estimate_attempts(
        1_000,
        feature_item_fractions={"activity_item": 0.5},
    ) == 58
