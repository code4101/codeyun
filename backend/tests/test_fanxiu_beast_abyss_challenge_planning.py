from __future__ import annotations

from fractions import Fraction
from datetime import datetime

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


FINAL_DAY = datetime.fromisoformat("2026-08-12T10:05:00+08:00")
END_AT = datetime.fromisoformat("2026-08-12T22:00:00+08:00")


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


def test_native_quick_batch_uses_completed_count_and_conservative_recovery_time():
    sample = measure_beast_abyss_completed_batch(
        _ledger(), _ledger(cumulative_currency=44_474, current_currency=44_474),
        requested_explores=71, completed_explores=80, native_batch_size=10,
        duration_seconds=400, duration_reliable=False, duration_is_upper_bound=True)
    assert sample.currency_per_explore == 100
    model = build_beast_abyss_yield_scatter_model([sample])
    assert model.points == ((80, 8000, 0),)
    assert model.seconds_per_explore is None
    plan = plan_beast_abyss_formal_batch(
        _ledger(), sample, {"budget_ready": True, "milestones": [
            {"goods_id": 1, "target_total_tokens": 43_574, "target_remaining_tokens": 43_574}]},
        now=FINAL_DAY, activity_end_at=END_AT, explore_item_automatic=4,
        native_batch_size=10, available_seconds=450)
    assert plan.requested_explores == 80
    with pytest.raises(ValueError, match="未完整完成"):
        measure_beast_abyss_completed_batch(
            _ledger(), _ledger(), requested_explores=71, completed_explores=70,
            native_batch_size=10, duration_seconds=10)


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


def test_zero_yield_is_recorded_and_negative_history_is_rejected() -> None:
    sample = measure_beast_abyss_batch(_ledger(), _ledger(), requested_explores=100,
        completed_explores=100, duration_seconds=80)
    assert sample.new_currency == 0
    plan = plan_beast_abyss_challenge_once(_ledger(), sample,
        other_discount_new_currency=1000, closing_goods_new_currency=2000, now=FINAL_DAY, activity_end_at=END_AT, explore_item_automatic=4)
    assert plan.status == "pass" and plan.reason == "no_positive_yield"
    assert plan.requested_explores == 0
    with pytest.raises(ValueError, match="累计兽元"):
        measure_beast_abyss_batch(_ledger(), _ledger(cumulative_currency=0),
            requested_explores=100, completed_explores=100, duration_seconds=80)


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


def test_next_target_runs_in_full_or_passes_without_spending() -> None:
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
        now=FINAL_DAY, activity_end_at=END_AT, explore_item_automatic=4,
    )
    approach = plan_beast_abyss_challenge_once(
        _ledger(challenge_points=60),
        measurement,
        other_discount_new_currency=142_526,
        closing_goods_new_currency=274_526,
        now=FINAL_DAY, activity_end_at=END_AT, explore_item_automatic=4,
    )

    assert other_discount.target_tier == "其他折扣"
    assert other_discount.remaining_target_explores == 476
    assert other_discount.requested_explores == 476
    assert approach.remaining_target_explores == 476
    assert approach.target_tier == "其他折扣"
    assert approach.status == "pass"
    assert approach.deficit == 396
    assert approach.requested_explores == 0


def test_formal_next_batch_keeps_the_entire_requested_remainder() -> None:
    assert plan_beast_abyss_next_batch(0) == 0
    assert plan_beast_abyss_next_batch(100) == 100
    assert plan_beast_abyss_next_batch(101) == 101
    assert plan_beast_abyss_next_batch(476) == 476
    assert plan_beast_abyss_next_batch(80, batch_size=80) == 80
    assert plan_beast_abyss_next_batch(81, batch_size=80) == 81
    with pytest.raises(ValueError, match="不能为负数"):
        plan_beast_abyss_next_batch(-1)


def test_formal_batch_uses_next_commodity_and_both_capacity_limits() -> None:
    measurement = measure_beast_abyss_batch(_ledger(),
        _ledger(cumulative_currency=66_474, current_currency=66_474),
        requested_explores=100, completed_explores=100, duration_seconds=80)
    exchange = {"budget_ready": True, "milestones": [
        {"goods_id": 1, "name": "第一行", "target_total_tokens": 92000, "target_remaining_tokens": 92000},
        {"goods_id": 2, "name": "第二行", "target_total_tokens": 102000, "target_remaining_tokens": 102000}]}
    plan = plan_beast_abyss_formal_batch(_ledger(challenge_points=600), measurement,
        exchange, now=FINAL_DAY, activity_end_at=END_AT, explore_item_automatic=4)
    assert plan.target_goods_id == 1
    assert plan.requested_explores == 186  # ceil((92000-36474)/300)
    blocked = plan_beast_abyss_formal_batch(_ledger(challenge_points=2), measurement,
        exchange, now=FINAL_DAY, activity_end_at=END_AT, explore_item_automatic=4)
    assert blocked.status == "pass" and blocked.requested_explores == 0
    assert blocked.deficit == 26 and blocked.target_goods_id == 1
    explored_out = plan_beast_abyss_formal_batch(
        _ledger(explore_points=185, explore_items=0), measurement, exchange, now=FINAL_DAY, activity_end_at=END_AT, explore_item_automatic=4)
    assert explored_out.requested_explores == 0 and explored_out.deficit == 1
    next_row = plan_beast_abyss_formal_batch(
        _ledger(current_currency=92000, cumulative_currency=92000), measurement,
        exchange, now=FINAL_DAY, activity_end_at=END_AT, explore_item_automatic=4)
    assert next_row.target_goods_id == 2 and next_row.requested_explores == 34
    with pytest.raises(ValueError, match="同窗口最新"):
        plan_beast_abyss_formal_batch(_ledger(), measurement, {"budget_ready": False}, now=FINAL_DAY, activity_end_at=END_AT, explore_item_automatic=4)
    with pytest.raises(ValueError, match="上一完整批次"):
        plan_beast_abyss_formal_batch(_ledger(), build_beast_abyss_yield_scatter_model((measurement,)),
                                    exchange, now=FINAL_DAY, activity_end_at=END_AT, explore_item_automatic=4)


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
        now=FINAL_DAY, activity_end_at=END_AT, explore_item_automatic=4,
    )

    assert plan.challenge_rate_with_margin == Fraction(1, 80)
    assert plan.challenge_limited_capacity == 4800


def test_beast_final_day_reservation_survives_unknown_speed_and_releases_next_day():
    measurement = measure_beast_abyss_batch(_ledger(),
        _ledger(cumulative_currency=66_474, current_currency=66_474),
        requested_explores=100, completed_explores=100, duration_seconds=80)
    exchange = {"budget_ready": True, "milestones": [
        {"goods_id": 1, "target_total_tokens": 92000, "target_remaining_tokens": 92000},
        {"goods_id": 2, "target_total_tokens": 102000, "target_remaining_tokens": 102000}]}
    from dataclasses import replace
    deferred = plan_beast_abyss_formal_batch(
        _ledger(current_currency=92000, cumulative_currency=92000),
        replace(measurement, duration_reliable=False), exchange,
        now=FINAL_DAY.replace(day=11), activity_end_at=END_AT,
        explore_item_automatic=4, available_seconds=1000)
    assert deferred.status == "deferred" and deferred.reason == "final_day_reserved"
    assert deferred.requested_explores == 0 and deferred.target_goods_id == 2
    assert deferred.unlock_at == "2026-08-12T00:00:00+08:00"
    released = plan_beast_abyss_formal_batch(
        _ledger(current_currency=96000, cumulative_currency=96000), measurement, exchange,
        now=FINAL_DAY, activity_end_at=END_AT, explore_item_automatic=4)
    assert released.requested_explores == 20


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
        now=FINAL_DAY, activity_end_at=END_AT, explore_item_automatic=4,
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


def test_recovered_yield_is_preserved_without_inventing_a_speed() -> None:
    recovered = measure_beast_abyss_batch(
        _ledger(),
        _ledger(cumulative_currency=66_474, current_currency=66_474),
        requested_explores=100,
        completed_explores=100,
        duration_seconds=36_000,
        duration_reliable=False,
    )

    model = build_beast_abyss_yield_scatter_model((recovered,))
    assert model.currency_per_explore == 300
    assert model.seconds_per_explore is None
    plan = plan_beast_abyss_formal_batch(_ledger(), recovered,
        {"budget_ready": True, "milestones": [{"goods_id": 1, "target_total_tokens": 92000,
                                               "target_remaining_tokens": 92000}]},
        now=FINAL_DAY, activity_end_at=END_AT, explore_item_automatic=4, available_seconds=1000)
    assert plan.status == "pass" and plan.reason == "duration_unknown"


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

def test_reliable_timing_does_not_replace_latest_currency_yield():
    from dataclasses import replace
    recovered = measure_beast_abyss_completed_batch(
        _ledger(), _ledger(cumulative_currency=37_474, current_currency=37_474),
        requested_explores=10, completed_explores=10, native_batch_size=10,
        duration_seconds=737, duration_reliable=False, duration_is_upper_bound=True)
    timing = measure_beast_abyss_completed_batch(
        _ledger(), _ledger(cumulative_currency=66_474, current_currency=66_474),
        requested_explores=100, completed_explores=100, native_batch_size=10,
        duration_seconds=200)
    kwargs = dict(now=FINAL_DAY, activity_end_at=END_AT, explore_item_automatic=4,
                  available_seconds=200, native_batch_size=10)
    exchange = {'budget_ready': True, 'milestones': [
        {'goods_id': 1, 'target_total_tokens': 41_474, 'target_remaining_tokens': 41_474}]}
    blocked = plan_beast_abyss_formal_batch(_ledger(), recovered, exchange, **kwargs)
    assert blocked.reason == 'time_insufficient'
    plan = plan_beast_abyss_formal_batch(_ledger(), recovered, exchange,
                                       timing_measurement=timing, **kwargs)
    assert plan.requested_explores == 50
    assert plan.estimated_new_currency == 5000
    with pytest.raises(ValueError, match='本期同批量模式'):
        plan_beast_abyss_formal_batch(_ledger(), recovered, exchange,
            timing_measurement=replace(timing, activity_instance_id='another'), **kwargs)

def test_quick_terminal_accepts_bounded_native_overshoot_and_uses_actual_count():
    sample = measure_beast_abyss_completed_batch(
        _ledger(), _ledger(cumulative_currency=41_974, current_currency=41_974),
        requested_explores=50, completed_explores=55, native_batch_size=10,
        duration_seconds=110)
    assert sample.currency_per_explore == 100
    assert build_beast_abyss_yield_scatter_model([sample]).points == ((55, 5500, 0),)
    for invalid_count in (49, 60):
        with pytest.raises(ValueError, match='未完整完成'):
            measure_beast_abyss_completed_batch(_ledger(), _ledger(),
                requested_explores=50, completed_explores=invalid_count,
                native_batch_size=10, duration_seconds=110)
