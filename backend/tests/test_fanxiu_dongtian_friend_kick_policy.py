from dataclasses import asdict

import pytest

from backend.core.fanxiu.data_annotation.dongtian_friend_kick_policy import (
    evaluate_dongtian_friend_xianlv_policy,
    select_dongtian_friend_swap_candidates,
)


def _decide(my_score, defender_score, *, confirmed=True):
    return evaluate_dongtian_friend_xianlv_policy(
        my_team3_fight_score=my_score,
        defender_current_xianlv_fight_score=defender_score,
        current_observation_confirmed=confirmed,
    )


def _seat(mine, seat, score, *, quality=2, role=None, confirmed=True):
    return {
        "mine_id": mine,
        "quality": quality,
        "seat_id": seat,
        "role_id": role or mine * 100 + seat,
        "current_xianlv_fight_score": score,
        "current_observation_confirmed": confirmed,
    }


def test_defender_must_be_strictly_below_80_percent_of_team3():
    assert _decide(1_000, 799).candidate is True
    assert _decide(1_000, 800).candidate is False
    assert _decide(1_000, 801).candidate is False

    decision = _decide(1_000, 799)
    assert decision.status == "eligible"
    assert decision.strict_upper_bound_score == 800
    assert "action" not in asdict(decision)


@pytest.mark.parametrize("my_score", [None, 0, -1, True, ""])
def test_missing_or_nonpositive_team3_score_fails_closed(my_score):
    decision = _decide(my_score, 100)

    assert decision.candidate is False
    assert decision.complete is False
    assert decision.status == "ambiguous"


def test_stale_current_defender_observation_fails_closed():
    decision = _decide(1_000, 100, confirmed=False)

    assert decision.candidate is False
    assert decision.complete is False
    assert decision.reason == "defender_current_observation_not_confirmed"


def test_real_observed_values_match_new_80_percent_rule():
    my_team3 = 78_788_364_918_495
    weak = _decide(my_team3, 7_192_916_551_261)
    stronger = _decide(my_team3, 65_724_906_006_937)

    assert weak.candidate is True
    assert weak.strict_upper_bound_score == 63_030_691_934_796
    assert stronger.candidate is False


def test_selects_only_lowest_score_per_mine_and_skips_own_occupied_mine():
    result = select_dongtian_friend_swap_candidates(
        my_team3_fight_score=1_000,
        seats=[
            _seat(5, 1, 700),
            _seat(5, 2, 100),
            _seat(5, 3, 200),
            _seat(6, 1, 300),
            _seat(6, 2, 200),
            _seat(9, 1, 1),
        ],
        own_occupied_mine_ids=[9],
    )

    assert result.complete is True
    assert result.status == "ready"
    assert [(row.mine_id, row.seat_id) for row in result.candidates] == [
        (5, 2),
        (6, 2),
    ]


def test_minimum_above_boundary_rejects_entire_mine():
    result = select_dongtian_friend_swap_candidates(
        my_team3_fight_score=1_000,
        seats=[_seat(5, 1, 800), _seat(5, 2, 900)],
    )

    assert result.complete is True
    assert result.status == "no_candidate"
    assert result.candidates == ()


def test_partial_or_stale_seat_set_fails_closed():
    result = select_dongtian_friend_swap_candidates(
        my_team3_fight_score=1_000,
        seats=[_seat(5, 1, 100), _seat(5, 2, 200, confirmed=False)],
    )

    assert result.complete is False
    assert result.status == "ambiguous"
    assert result.candidates == ()


def test_ties_are_deterministic_from_quality_then_seat_id():
    result = select_dongtian_friend_swap_candidates(
        my_team3_fight_score=1_000,
        seats=[
            _seat(5, 12, 100, quality=2),
            _seat(5, 7, 100, quality=1),
            _seat(5, 3, 100, quality=1),
        ],
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].seat_id == 3
