from __future__ import annotations

import pytest

from backend.core.fanxiu.data_annotation.tasks.xutian_native_auto import (
    build_xutian_batch_observation,
    plan_xutian_native_batch,
    validate_xutian_auto_settings,
    xutian_target_quality_keys,
)
from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)


def _snapshot(*, count: int = 10) -> dict:
    raw = {
        str(key): {
            "use_item": True,
            "use_item_3": True,
            "use_item_4": True,
        }
        for key in (6, 7, 15)
    }
    return {
        "special_options": {
            "find_demon_available": False,
            "native_soul_lock_available": False,
            "find_demon_selected": False,
            "native_soul_lock_selected": False,
        },
        "available_quality_keys": [3, 4, 5, 6, 7, 15, 99],
        "auto_settings": {
            "quality_3": False,
            "quality_4": False,
            "quality_5": False,
            "quality_6": True,
            "quality_7": True,
            "quality_8": True,
            "quality_player": False,
            "refill_challenge": True,
            "refill_explore": True,
            "quick_auto": True,
            "skip_animation": True,
            "challenge_count": count,
        },
        "evidence": {"auto_settings_raw": raw},
    }


def test_target_quality_policy_starts_at_xianpin_and_maps_quality_8_key():
    assert xutian_target_quality_keys([3, 4, 5, 6, 7, 15, 99]) == {6, 7, 15}


def test_xutian_native_auto_is_a_manual_standard_job():
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition(
        "xutian_palace_native_auto"
    )
    assert definition is not None
    assert definition.scheduler_supported is True
    assert definition.standard_job is True
    task = next(
        item
        for item in default_data_annotation_scheduler_tasks()
        if item["id"] == "xutian-palace-native-auto"
    )
    assert task["trigger_description"] == "手动"
    assert task["next_time"] is None
    assert task["payload"]["requested_challenges"] == 10


def test_runtime_settings_validator_requires_both_groups_and_every_boost():
    snapshot = _snapshot()
    assert validate_xutian_auto_settings(snapshot, requested_challenges=10) == []

    snapshot["auto_settings"]["skip_animation"] = False
    snapshot["evidence"]["auto_settings_raw"]["7"]["use_item_3"] = False
    mismatches = validate_xutian_auto_settings(snapshot, requested_challenges=10)

    assert "skip_animation 应为 True" in mismatches
    assert "quality_7.use_item_3 应开启" in mismatches


def test_settings_validator_fails_closed_for_incomplete_runtime_snapshot():
    assert validate_xutian_auto_settings({}, requested_challenges=10) == [
        "Runtime 自动配置快照不完整"
    ]


def test_xutian_batch_planner_starts_with_ten_then_halves_estimate():
    probe = plan_xutian_native_batch(required_new_currency=167_210)
    next_batch = plan_xutian_native_batch(
        required_new_currency=160_000,
        measured_currency_delta=1_000,
        measured_challenges=10,
    )

    assert (probe.requested_challenges, probe.planning_mode) == (10, "probe")
    assert (next_batch.requested_challenges, next_batch.planning_mode) == (
        500,
        "capped_geometric_half",
    )


def test_batch_observation_requires_runtime_terminal_and_positive_wallet_delta():
    before = {
        "challenge": {"count": 24_047},
        "explore": {"count": 24_361},
        "auto_progress": {"running": False, "completed_challenges": 0},
    }
    after = {
        "challenge": {"count": 24_037},
        "explore": {"count": 24_351},
        "auto_progress": {"running": False, "completed_challenges": 10},
    }

    row = build_xutian_batch_observation(
        requested_challenges=10,
        before_resource=before,
        after_resource=after,
        currency_before=25_290,
        currency_after=26_490,
        elapsed_seconds=4.0,
    )

    assert row["currency_delta"] == 1_200
    assert row["currency_per_challenge"] == 120
    assert row["seconds_per_challenge"] == 0.4
    assert row["challenge_count_before"] == 24_047
    assert row["challenge_count_after"] == 24_037

    after["auto_progress"]["running"] = True
    with pytest.raises(ValueError, match="仍在运行"):
        build_xutian_batch_observation(
            requested_challenges=10,
            before_resource=before,
            after_resource=after,
            currency_before=25_290,
            currency_after=26_490,
            elapsed_seconds=4.0,
        )
