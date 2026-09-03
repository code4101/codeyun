from __future__ import annotations

from fractions import Fraction

import pytest

from backend.core.fanxiu.activity.beast_abyss_challenge_planning import (
    BeastAbyssResourceLedger,
    BeastAbyssAutoSettings,
    build_beast_abyss_yield_scatter_model,
    build_beast_abyss_shop_snapshot_key,
    measure_beast_abyss_batch,
    measure_beast_abyss_completed_batch,
    is_beast_abyss_currency_yield_stable,
    plan_beast_abyss_measurement_batch,
    plan_beast_abyss_formal_batch,
    plan_beast_abyss_next_batch,
    plan_beast_abyss_challenge_once,
    validate_beast_abyss_auto_settings,
)


def _ledger(**overrides):
    values = {
        "activity_instance_id": "beast-abyss-4-2026-08-11-2026-08-12",
        "shop_snapshot_key": "shop:2026-08-12T16:39:53+08:00:all-zero",
        "hierarchy": 1,
        "cumulative_currency": 36_474,
        "current_currency": 36_474,
        "explore_points": 400,
        "explore_items": 1_417,
        "challenge_points": 600,
        "challenge_items": 0,
        "personal_score": 0,
    }
    values.update(overrides)
    return BeastAbyssResourceLedger(**values)


def test_shop_snapshot_key_tracks_purchase_progress_not_input_order() -> None:
    rows = [
        {"goods_id": 2, "source_order": 2, "purchase_limit": 5, "purchased_count": 0},
        {"goods_id": 1, "source_order": 1, "purchase_limit": 3, "purchased_count": 1},
    ]
    first = build_beast_abyss_shop_snapshot_key(
        rows, captured_at="2026-08-12T16:39:53+08:00"
    )
    same = build_beast_abyss_shop_snapshot_key(
        reversed(rows), captured_at="2026-08-12T16:39:53+08:00"
    )
    changed = build_beast_abyss_shop_snapshot_key(
        [{**rows[0], "purchased_count": 1}, rows[1]],
        captured_at="2026-08-12T16:39:53+08:00",
    )

    assert first == same
    assert first != changed


def test_measurement_gui_settings_require_formal_profile_readback() -> None:
    safe = BeastAbyssAutoSettings(
        fairy_events=False,
        beast_events=True,
        player_events=True,
        auto_use_explore_items=True,
        stop_when_killed=False,
        fast_auto=True,
        skip_animation=True,
        requested_explores=100,
    )
    validate_beast_abyss_auto_settings(safe, measurement=True)

    with pytest.raises(ValueError, match="player_events"):
        validate_beast_abyss_auto_settings(
            BeastAbyssAutoSettings(**{**safe.__dict__, "player_events": False}),
            measurement=True,
        )
    with pytest.raises(ValueError, match="auto_use_explore_items"):
        validate_beast_abyss_auto_settings(
            BeastAbyssAutoSettings(
                **{**safe.__dict__, "auto_use_explore_items": False}
            ),
            measurement=True,
        )
    with pytest.raises(ValueError, match="快速自动"):
        validate_beast_abyss_auto_settings(
            BeastAbyssAutoSettings(
                **{**safe.__dict__, "fast_auto": False}
            ),
            measurement=True,
        )


def test_production_gui_settings_accept_user_beast_abyss_profile() -> None:
    validate_beast_abyss_auto_settings(
        BeastAbyssAutoSettings(
            fairy_events=False,
            beast_events=True,
            player_events=True,
            auto_use_explore_items=True,
            stop_when_killed=False,
            fast_auto=True,
            skip_animation=True,
            requested_explores=7242,
        ),
        measurement=False,
    )


def test_measurement_uses_cumulative_currency_and_challenge_ledger() -> None:
    result = measure_beast_abyss_batch(
        _ledger(),
        _ledger(
            cumulative_currency=66_474,
            current_currency=66_474,
            explore_points=300,
            challenge_points=540,
            personal_score=12_000,
        ),
        requested_explores=100,
        completed_explores=100,
        duration_seconds=80,
    )

    assert result.new_currency == 30_000
    assert result.currency_per_explore == Fraction(300, 1)
    assert result.challenge_per_explore == Fraction(3, 5)
    assert result.seconds_per_explore == 0.8


def test_measurement_fails_closed_without_positive_history_delta() -> None:
    with pytest.raises(ValueError, match="累计兽元"):
        measure_beast_abyss_batch(
            _ledger(),
            _ledger(),
            requested_explores=100,
            completed_explores=100,
            duration_seconds=80,
        )


def test_measurement_preflight_requires_exploration_capacity_not_one_to_one_challenge_points() -> None:
    assert plan_beast_abyss_measurement_batch(
        _ledger(), hierarchy_consume=1
    ) == 100
    with pytest.raises(ValueError, match="探索资源不足"):
        plan_beast_abyss_measurement_batch(
            _ledger(explore_points=99), hierarchy_consume=1
        )
    assert plan_beast_abyss_measurement_batch(
        _ledger(challenge_points=0, challenge_items=0), hierarchy_consume=1
    ) == 100


def test_partial_measurement_batch_is_not_extrapolated() -> None:
    with pytest.raises(ValueError, match="未完整完成100次"):
        measure_beast_abyss_batch(
            _ledger(),
            _ledger(cumulative_currency=66_174, current_currency=66_174),
            requested_explores=100,
            completed_explores=99,
            duration_seconds=80,
        )


def test_completed_formal_batch_updates_same_dual_y_scatter_model() -> None:
    initial = measure_beast_abyss_batch(
        _ledger(),
        _ledger(cumulative_currency=66_474, personal_score=10_000),
        requested_explores=100,
        completed_explores=100,
        duration_seconds=80,
    )
    formal = measure_beast_abyss_completed_batch(
        _ledger(cumulative_currency=66_474, current_currency=66_474),
        _ledger(
            cumulative_currency=81_474,
            current_currency=81_474,
            personal_score=15_000,
        ),
        requested_explores=50,
        completed_explores=50,
        duration_seconds=45,
    )

    model = build_beast_abyss_yield_scatter_model((initial, formal))

    assert model.points == ((100, 30_000, 10_000), (50, 15_000, 15_000))
    assert model.currency_per_explore == 300
    assert model.personal_score_per_explore == 140


def test_measurement_rejects_changed_shop_but_records_hierarchy_transition() -> None:
    with pytest.raises(ValueError, match="购买进度快照"):
        measure_beast_abyss_batch(
            _ledger(),
            _ledger(
                shop_snapshot_key="shop:new",
                cumulative_currency=66_474,
            ),
            requested_explores=100,
            completed_explores=100,
            duration_seconds=80,
        )
    measurement = measure_beast_abyss_batch(
        _ledger(),
        _ledger(hierarchy=2, cumulative_currency=66_474),
        requested_explores=100,
        completed_explores=100,
        duration_seconds=80,
    )
    assert measurement.hierarchy == 1
    assert measurement.ending_hierarchy == 2


def test_one_shot_plan_prefers_closing_then_other_discount_then_approach() -> None:
    measurement = measure_beast_abyss_batch(
        _ledger(),
        _ledger(
            cumulative_currency=66_474,
            current_currency=66_474,
            challenge_points=540,
        ),
        requested_explores=100,
        completed_explores=100,
        duration_seconds=80,
    )
    other_discount = plan_beast_abyss_challenge_once(
        _ledger(challenge_points=600),
        measurement,
        other_discount_new_currency=142_526,
        closing_goods_new_currency=274_526,
        explore_item_automatic=4,
    )
    approach = plan_beast_abyss_challenge_once(
        _ledger(challenge_points=60),
        measurement,
        other_discount_new_currency=142_526,
        closing_goods_new_currency=274_526,
        explore_item_automatic=4,
    )

    assert other_discount.target_tier == "其他折扣"
    assert other_discount.remaining_target_explores == 476
    assert other_discount.requested_explores == 238
    assert approach.remaining_target_explores == 80
    assert approach.target_tier == "尽量接近其他折扣"
    assert approach.requested_explores == 80


def test_formal_next_batch_runs_small_remainder_or_half_then_replans() -> None:
    assert plan_beast_abyss_next_batch(0) == 0
    assert plan_beast_abyss_next_batch(100) == 100
    assert plan_beast_abyss_next_batch(101) == 51
    assert plan_beast_abyss_next_batch(476) == 238
    assert plan_beast_abyss_next_batch(80, batch_size=80) == 80
    assert plan_beast_abyss_next_batch(81, batch_size=80) == 41
    with pytest.raises(ValueError, match="不能为负数"):
        plan_beast_abyss_next_batch(-1)


def test_formal_batch_reads_latest_shop_tiers_and_returns_only_next_half() -> None:
    measurement = measure_beast_abyss_batch(
        _ledger(),
        _ledger(cumulative_currency=66_474, current_currency=66_474),
        requested_explores=100,
        completed_explores=100,
        duration_seconds=80,
    )
    model = build_beast_abyss_yield_scatter_model((measurement,))
    plan = plan_beast_abyss_formal_batch(
        _ledger(challenge_points=600),
        model,
        {
            "budget_ready": True,
            "target_budgets": {
                "其他折扣": {"required_new_currency": 142_526},
                "收尾道具": {"required_new_currency": 274_526},
            },
        },
        explore_item_automatic=4,
    )

    assert plan.target_tier == "其他折扣"
    assert plan.remaining_target_explores == 476
    assert plan.requested_explores == 238
    assert plan.estimated_new_currency == 71_400

    with pytest.raises(ValueError, match="同窗口最新"):
        plan_beast_abyss_formal_batch(
            _ledger(),
            model,
            {"budget_ready": False},
            explore_item_automatic=4,
        )


def test_zero_challenge_sample_does_not_infer_infinite_capacity() -> None:
    measurement = measure_beast_abyss_batch(
        _ledger(),
        _ledger(cumulative_currency=66_474, current_currency=66_474),
        requested_explores=100,
        completed_explores=100,
        duration_seconds=80,
    )
    plan = plan_beast_abyss_challenge_once(
        _ledger(challenge_points=60),
        measurement,
        other_discount_new_currency=142_526,
        closing_goods_new_currency=274_526,
        explore_item_automatic=4,
    )

    assert plan.challenge_rate_with_margin == 1
    assert plan.challenge_limited_capacity == 60


def test_one_shot_plan_reuses_same_occurrence_yield_after_shop_refresh() -> None:
    measurement = measure_beast_abyss_batch(
        _ledger(),
        _ledger(cumulative_currency=66_474, current_currency=66_474),
        requested_explores=100,
        completed_explores=100,
        duration_seconds=80,
    )

    plan = plan_beast_abyss_challenge_once(
        _ledger(shop_snapshot_key="shop:new"),
        measurement,
        other_discount_new_currency=142_526,
        closing_goods_new_currency=274_526,
        explore_item_automatic=4,
    )
    assert plan.requested_explores > 0


def test_scatter_model_fits_all_same_occurrence_batch_points() -> None:
    first = measure_beast_abyss_batch(
        _ledger(),
        _ledger(cumulative_currency=66_474, current_currency=66_474),
        requested_explores=100,
        completed_explores=100,
        duration_seconds=80,
    )
    second = measure_beast_abyss_batch(
        _ledger(shop_snapshot_key="shop:new", hierarchy=2),
        _ledger(
            shop_snapshot_key="shop:new",
            hierarchy=1,
            cumulative_currency=76_474,
            current_currency=76_474,
        ),
        requested_explores=100,
        completed_explores=100,
        duration_seconds=100,
    )

    model = build_beast_abyss_yield_scatter_model((first, second))

    assert model.points == ((100, 30_000, 0), (100, 40_000, 0))
    assert model.currency_per_explore == 350
    assert model.personal_score_per_explore == 0
    assert model.seconds_per_explore == 0.9
    assert model.hierarchy_transitions == ((1, 1), (2, 1))


def test_scatter_speed_ignores_recovered_batch_downtime() -> None:
    recovered = measure_beast_abyss_batch(
        _ledger(),
        _ledger(cumulative_currency=66_474, current_currency=66_474),
        requested_explores=100,
        completed_explores=100,
        duration_seconds=36_000,
        duration_reliable=False,
    )
    observed_live = measure_beast_abyss_batch(
        _ledger(),
        _ledger(cumulative_currency=66_474, current_currency=66_474),
        requested_explores=100,
        completed_explores=100,
        duration_seconds=80,
    )

    model = build_beast_abyss_yield_scatter_model((recovered, observed_live))

    assert model.seconds_per_explore == 0.8


def test_scatter_requires_at_least_one_reliable_speed_sample() -> None:
    recovered = measure_beast_abyss_batch(
        _ledger(),
        _ledger(cumulative_currency=66_474, current_currency=66_474),
        requested_explores=100,
        completed_explores=100,
        duration_seconds=36_000,
        duration_reliable=False,
    )

    with pytest.raises(ValueError, match="可信的批次耗时"):
        build_beast_abyss_yield_scatter_model((recovered,))


def test_scatter_rejects_a_point_whose_configured_batch_was_not_completed() -> None:
    from dataclasses import replace

    valid = measure_beast_abyss_batch(
        _ledger(),
        _ledger(cumulative_currency=66_474, current_currency=66_474),
        requested_explores=100,
        completed_explores=100,
        duration_seconds=80,
    )

    with pytest.raises(ValueError, match="无效测速点"):
        build_beast_abyss_yield_scatter_model(
            (replace(valid, requested_explores=100, completed_explores=185),)
        )


def test_adjacent_batch_currency_stability_uses_previous_batch_baseline() -> None:
    previous = measure_beast_abyss_batch(
        _ledger(), _ledger(cumulative_currency=66_474, personal_score=10_000),
        requested_explores=100, completed_explores=100, duration_seconds=80,
    )
    stable = measure_beast_abyss_batch(
        _ledger(), _ledger(cumulative_currency=81_474, personal_score=15_000),
        requested_explores=100, completed_explores=100, duration_seconds=80,
    )
    unstable = measure_beast_abyss_batch(
        _ledger(), _ledger(cumulative_currency=81_475, personal_score=15_000),
        requested_explores=100, completed_explores=100, duration_seconds=80,
    )

    assert is_beast_abyss_currency_yield_stable(previous, stable) is True
    assert is_beast_abyss_currency_yield_stable(previous, unstable) is False
