import pytest

from backend.core.fanxiu.activity.pet_resource_planning import plan_pet_resource_batch, plan_pet_ranking_target


def test_taught_probe_feedback_and_inventory_clip():
    assert plan_pet_resource_batch(gap=500, base_gain=1, available=1000)["quantity"] == 100
    assert plan_pet_resource_batch(gap=300, base_gain=1, available=1000, measured_multiplier=2)["quantity"] == 120
    assert plan_pet_resource_batch(gap=300, base_gain=1, available=80, measured_multiplier=2)["quantity"] == 80
    assert plan_pet_resource_batch(gap=60, base_gain=1, available=1000, measured_multiplier=2)["quantity"] == 30


def test_real_rank_interpolation_and_already_above_target():
    scores = {16: 1000, 32: 700, 64: 400, 128: 100, 106: 180, 107: 150}
    plan = plan_pet_ranking_target(current_score=80, potential_gain=500, rank_scores=scores,
                                   reward_boundaries=[16, 32, 64, 128])
    assert plan["target_position"] == pytest.approx(106 + 2/3)
    assert plan["target_score"] == pytest.approx(160)
    scores.update({53: 460, 54: 430})
    plan = plan_pet_ranking_target(current_score=750, potential_gain=100, rank_scores=scores,
                                   reward_boundaries=[16, 32, 64, 128])
    assert plan["target_boundary"] == 64
    assert plan["status"] == "complete"


def test_missing_actual_rank_never_interpolates_tier_endpoints():
    plan = plan_pet_ranking_target(current_score=80, potential_gain=500,
                                   rank_scores={64: 400, 128: 100}, reward_boundaries=[64, 128])
    assert plan["status"] == "need_rank_rows"
    assert plan["missing_ranks"] == [106, 107]
