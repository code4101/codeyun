"""纯策略契约：已确认的 #615 配置不应被自动任务改成另一套。"""

import pytest

from backend.core.fanxiu.data_annotation.tasks.xutian_native_auto import (
    build_xutian_batch_observation,
    validate_xutian_auto_settings,
    xutian_target_quality_keys,
)


def test_confirmed_xutian_default_quality_and_available_boosts() -> None:
    qualities = [3, 4, 5, 6, 7, 15, 99]
    assert xutian_target_quality_keys(qualities) == {6, 7}

    snapshot = {
        "available_quality_keys": qualities,
        "auto_settings": {
            "quality_player": False,
            "quality_3": False,
            "quality_4": False,
            "quality_5": False,
            "quality_6": True,
            "quality_7": True,
            "quality_8": False,
            "refill_challenge": True,
            "refill_explore": True,
            "quick_auto": True,
            "skip_animation": True,
            "challenge_count": 1993,
        },
        "special_options": {
            "find_demon_selected": False,
            "native_soul_lock_selected": False,
        },
        "evidence": {
            "auto_settings_raw": {
                "6": {"use_item": True, "use_item_3": True, "use_item_4": True},
                "7": {"use_item": True, "use_item_3": True, "use_item_4": None},
            },
        },
    }
    assert validate_xutian_auto_settings(
        snapshot,
        requested_challenges=1993,
        allow_item_refill=True,
        allow_boost_items=True,
    ) == []


def test_three_challenge_tail_has_a_bounded_terminal_overrun() -> None:
    before = {
        "challenge": {"count": 0},
        "explore": {"count": 0},
    }
    after = {
        "multiple_enabled": True,
        "auto_progress": {"running": False, "completed_challenges": 501},
        "challenge": {"count": 0},
        "explore": {"count": 0},
    }
    result = build_xutian_batch_observation(
        requested_challenges=500,
        before_resource=before,
        after_resource=after,
        currency_before=299170,
        currency_after=407004,
        elapsed_seconds=100,
    )
    assert result["completed_challenges"] == 501
    assert result["currency_delta"] == 107834
    with pytest.raises(ValueError, match="完成次数不一致"):
        build_xutian_batch_observation(
            requested_challenges=500,
            before_resource=before,
            after_resource={**after, "multiple_enabled": False},
            currency_before=299170,
            currency_after=407004,
            elapsed_seconds=100,
        )


def test_live_currency_target_allows_only_proven_early_stop() -> None:
    resource = {
        "multiple_enabled": True,
        "auto_progress": {"running": False, "completed_challenges": 12},
    }
    observed = build_xutian_batch_observation(
        requested_challenges=100,
        before_resource={},
        after_resource=resource,
        currency_before=423_892,
        currency_after=424_600,
        elapsed_seconds=10,
        target_currency=424_500,
    )
    assert observed["completed_challenges"] == 12
    with pytest.raises(ValueError, match="完成次数不一致"):
        build_xutian_batch_observation(
            requested_challenges=100,
            before_resource={},
            after_resource=resource,
            currency_before=423_892,
            currency_after=424_400,
            elapsed_seconds=10,
            target_currency=424_500,
        )
