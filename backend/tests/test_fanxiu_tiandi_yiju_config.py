from __future__ import annotations

import pytest

from backend.core.fanxiu.data_annotation.tasks.tiandi_yiju_config import (
    desired_tiandi_yiju_auto_challenge_choices,
    plan_tiandi_yiju_auto_challenge_configuration,
    plan_tiandi_yiju_auto_challenge_from_runtime,
)


LOCAL_CHOICES = {
    "auto_use_strength_item": True,
    "continue_after_defeat": True,
    "skip_animation": True,
    "master_skill_item": False,
    "quadruple_chess_token_item": False,
}


def test_local_server_target_enables_top_three_and_disables_bottom_two() -> None:
    assert desired_tiandi_yiju_auto_challenge_choices(1) == LOCAL_CHOICES


@pytest.mark.parametrize("cross_count", [2, 8])
def test_cross_server_target_enables_all_five(cross_count: int) -> None:
    assert desired_tiandi_yiju_auto_challenge_choices(cross_count) == {
        key: True for key in LOCAL_CHOICES
    }


def test_plan_contains_only_differences_in_dialog_order() -> None:
    plan = plan_tiandi_yiju_auto_challenge_configuration(
        {
            "auto_use_strength_item": False,
            "continue_after_defeat": True,
            "skip_animation": False,
            "master_skill_item": False,
            "quadruple_chess_token_item": False,
        },
        cross_count=8,
    )

    assert [action["key"] for action in plan["actions"]] == [
        "auto_use_strength_item",
        "skip_animation",
        "master_skill_item",
        "quadruple_chess_token_item",
    ]
    assert all(action["current"] is False for action in plan["actions"])
    assert all(action["desired"] is True for action in plan["actions"])
    assert plan["already_configured"] is False


def test_plan_is_empty_when_runtime_already_matches() -> None:
    plan = plan_tiandi_yiju_auto_challenge_configuration(
        LOCAL_CHOICES,
        cross_count=1,
    )
    assert plan["actions"] == []
    assert plan["already_configured"] is True


def test_runtime_plan_uses_authoritative_cross_count_and_choices() -> None:
    plan = plan_tiandi_yiju_auto_challenge_from_runtime(
        {
            "ok": True,
            "available": True,
            "complete": True,
            "cross_count": 8,
            "auto_challenge_choices": {key: False for key in LOCAL_CHOICES},
        }
    )
    assert plan["mode"] == "cross_server"
    assert len(plan["actions"]) == 5


def test_runtime_plan_fails_closed_for_missing_choice_fields() -> None:
    with pytest.raises(RuntimeError, match="continue_after_defeat"):
        plan_tiandi_yiju_auto_challenge_from_runtime(
            {
                "ok": True,
                "available": True,
                "complete": True,
                "cross_count": 1,
                "auto_challenge_choices": {
                    "auto_use_strength_item": False,
                },
            }
        )


def test_runtime_plan_fails_closed_for_incomplete_snapshot() -> None:
    with pytest.raises(RuntimeError, match="事实不完整"):
        plan_tiandi_yiju_auto_challenge_from_runtime(
            {"ok": True, "available": True, "complete": False},
            cross_count=1,
        )


def test_runtime_plan_rejects_occurrence_cross_count_mismatch() -> None:
    with pytest.raises(RuntimeError, match="跨数.*不一致"):
        plan_tiandi_yiju_auto_challenge_from_runtime(
            {
                "ok": True,
                "available": True,
                "complete": True,
                "cross_count": 8,
                "auto_challenge_choices": LOCAL_CHOICES,
            },
            cross_count=1,
        )


@pytest.mark.parametrize("cross_count", [None, 0, -1, True, "unknown"])
def test_target_rejects_invalid_cross_count(cross_count) -> None:
    with pytest.raises(RuntimeError, match="有效活动跨数"):
        desired_tiandi_yiju_auto_challenge_choices(cross_count)
