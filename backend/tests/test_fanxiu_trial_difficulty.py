"""Pure trial policy and text contracts; real interactions require game acceptance."""
from __future__ import annotations

import pytest
from backend.core.fanxiu.data_annotation.trial_difficulty import ObservedTrialDifficulty, build_even_trial_difficulty_plan, find_current_trial_difficulty, next_configurable_trial_difficulty
from backend.core.fanxiu.data_annotation.trial_strategy import choose_xianqiao_trial_sweep_track
from backend.core.fanxiu.data_annotation.trial_progression import ObservedTrialAttempts, parse_xianqiao_trial_attempts
from backend.core.fanxiu.instrumentation.xianqiao import select_xianqiao_trial_drop_element
from backend.core.fanxiu.data_annotation.tasks.xianqiao_trial_actions import normalize_xianqiao_trial_track


@pytest.mark.parametrize(
    ("value", "expected"),
    [("a", "higher"), ("高级", "higher"), ("b", "lower"), ("低级", "lower")],
)
def test_xianqiao_trial_track_aliases(value, expected):
    assert normalize_xianqiao_trial_track(value) == expected

@pytest.mark.parametrize(
    ("higher", "lower", "expected"),
    [
        (100, 49, "higher"),
        (100, 50, "higher"),
        (100, 51, "lower"),
        (None, 1, "lower"),
        (1, None, "higher"),
    ],
)
def test_xianqiao_trial_sweep_track_uses_weighted_verified_levels(
    higher, lower, expected
):
    decision = choose_xianqiao_trial_sweep_track(
        higher_level=higher,
        lower_level=lower,
    )
    assert decision["track"] == expected

def test_xianqiao_trial_sweep_track_rejects_no_verified_track():
    with pytest.raises(ValueError, match="均无"):
        choose_xianqiao_trial_sweep_track(higher_level=None, lower_level=None)

def test_even_trial_difficulty_model_matches_known_levels():
    level_25 = build_even_trial_difficulty_plan(25)
    level_26 = build_even_trial_difficulty_plan(26)

    assert level_25.positions == (5, 5, 5, 5, 4)
    assert level_25.values == (10, 10, 15, 10, 40)
    assert level_26.positions == (5, 5, 5, 5, 5)
    assert level_26.values == (10, 10, 15, 10, 50)

def test_new_trial_track_jumps_from_initial_level_to_first_configurable_level():
    assert next_configurable_trial_difficulty(1) == 6
    assert next_configurable_trial_difficulty(6) == 7

def test_current_trial_difficulty_parser_uses_the_live_display_text():
    observation = find_current_trial_difficulty(
        [{"text": "当前难度为25级，完成挑战可得以上奖励"}]
    )

    assert observation == ObservedTrialDifficulty(
        level=25,
        text="当前难度为25级，完成挑战可得以上奖励",
    )

def test_xianqiao_trial_drop_element_uses_least_desired_equipped_count():
    assert select_xianqiao_trial_drop_element({1: 9, 2: 1, 3: 3, 4: 4, 5: 1}) == {
        "element_id": 3,
        "element": "水",
        "desired_counts": {"金": 9, "水": 3, "火": 4},
    }

def test_xianqiao_trial_drop_element_tie_break_is_deterministic():
    assert select_xianqiao_trial_drop_element({1: 2, 3: 2, 4: 2})["element"] == "金"

def test_trial_attempt_parser():
    assert parse_xianqiao_trial_attempts("今日剩余:奖励次数:2/5") == ObservedTrialAttempts(
        remaining=2,
        capacity=5,
        text="今日剩余:奖励次数:2/5",
    )
    assert parse_xianqiao_trial_attempts("剩余奖励次数：１／３").remaining == 1
    assert parse_xianqiao_trial_attempts("励次数:5/2").remaining == 5
