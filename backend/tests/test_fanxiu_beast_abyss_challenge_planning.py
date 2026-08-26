from __future__ import annotations

from fractions import Fraction

import pytest

from backend.core.fanxiu.activity.beast_abyss_challenge_planning import (
    BeastAbyssResourceLedger,
    BeastAbyssAutoSettings,
    build_beast_abyss_shop_snapshot_key,
    measure_beast_abyss_batch,
    plan_beast_abyss_measurement_batch,
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
        "explore_points": 31,
        "explore_items": 1_417,
        "challenge_points": 60,
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


def test_measurement_gui_settings_require_safe_readback() -> None:
    safe = BeastAbyssAutoSettings(
        fairy_events=True,
        beast_events=True,
        player_events=False,
        auto_use_explore_items=False,
        stop_when_killed=True,
        fast_auto=True,
        skip_animation=True,
        requested_explores=10,
    )
    validate_beast_abyss_auto_settings(safe, measurement=True)

    with pytest.raises(ValueError, match="玩家事件"):
        validate_beast_abyss_auto_settings(
            BeastAbyssAutoSettings(**{**safe.__dict__, "player_events": True}),
            measurement=True,
        )
    with pytest.raises(ValueError, match="探查符"):
        validate_beast_abyss_auto_settings(
            BeastAbyssAutoSettings(
                **{**safe.__dict__, "auto_use_explore_items": True}
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
            cumulative_currency=39_474,
            current_currency=39_474,
            explore_points=21,
            challenge_points=54,
            personal_score=1_200,
        ),
        requested_explores=10,
        completed_explores=10,
        duration_seconds=8,
    )

    assert result.new_currency == 3_000
    assert result.currency_per_explore == Fraction(300, 1)
    assert result.challenge_per_explore == Fraction(3, 5)
    assert result.seconds_per_explore == 0.8


def test_measurement_fails_closed_without_positive_history_delta() -> None:
    with pytest.raises(ValueError, match="累计兽元"):
        measure_beast_abyss_batch(
            _ledger(),
            _ledger(),
            requested_explores=10,
            completed_explores=10,
            duration_seconds=8,
        )


def test_measurement_preflight_requires_full_batch_without_supplement_items() -> None:
    assert plan_beast_abyss_measurement_batch(
        _ledger(), hierarchy_consume=1
    ) == 10
    with pytest.raises(ValueError, match="探索点不足"):
        plan_beast_abyss_measurement_batch(
            _ledger(explore_points=9), hierarchy_consume=1
        )
    with pytest.raises(ValueError, match="挑战点不足"):
        plan_beast_abyss_measurement_batch(
            _ledger(challenge_points=9), hierarchy_consume=1
        )


def test_partial_measurement_batch_is_not_extrapolated() -> None:
    with pytest.raises(ValueError, match="未完整完成10次"):
        measure_beast_abyss_batch(
            _ledger(),
            _ledger(cumulative_currency=39_174, current_currency=39_174),
            requested_explores=10,
            completed_explores=9,
            duration_seconds=8,
        )


def test_measurement_rejects_changed_shop_or_hierarchy() -> None:
    with pytest.raises(ValueError, match="购买进度快照"):
        measure_beast_abyss_batch(
            _ledger(),
            _ledger(
                shop_snapshot_key="shop:new",
                cumulative_currency=39_474,
            ),
            requested_explores=10,
            completed_explores=10,
            duration_seconds=8,
        )
    with pytest.raises(ValueError, match="当前层级"):
        measure_beast_abyss_batch(
            _ledger(),
            _ledger(hierarchy=2, cumulative_currency=39_474),
            requested_explores=10,
            completed_explores=10,
            duration_seconds=8,
        )


def test_one_shot_plan_prefers_closing_then_other_discount_then_approach() -> None:
    measurement = measure_beast_abyss_batch(
        _ledger(),
        _ledger(
            cumulative_currency=39_474,
            current_currency=39_474,
            challenge_points=54,
        ),
        requested_explores=10,
        completed_explores=10,
        duration_seconds=8,
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
    assert other_discount.requested_explores == 476
    assert approach.target_tier == "尽量接近其他折扣"
    assert approach.requested_explores == 80


def test_zero_challenge_sample_does_not_infer_infinite_capacity() -> None:
    measurement = measure_beast_abyss_batch(
        _ledger(),
        _ledger(cumulative_currency=39_474, current_currency=39_474),
        requested_explores=10,
        completed_explores=10,
        duration_seconds=8,
    )
    plan = plan_beast_abyss_challenge_once(
        _ledger(),
        measurement,
        other_discount_new_currency=142_526,
        closing_goods_new_currency=274_526,
        explore_item_automatic=4,
    )

    assert plan.challenge_rate_with_margin == 1
    assert plan.challenge_limited_capacity == 60


def test_one_shot_plan_rejects_stale_shop_snapshot() -> None:
    measurement = measure_beast_abyss_batch(
        _ledger(),
        _ledger(cumulative_currency=39_474, current_currency=39_474),
        requested_explores=10,
        completed_explores=10,
        duration_seconds=8,
    )

    with pytest.raises(ValueError, match="购买进度已变化"):
        plan_beast_abyss_challenge_once(
            _ledger(shop_snapshot_key="shop:new"),
            measurement,
            other_discount_new_currency=142_526,
            closing_goods_new_currency=274_526,
            explore_item_automatic=4,
        )
