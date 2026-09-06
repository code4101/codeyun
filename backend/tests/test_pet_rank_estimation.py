"""Deterministic estimation and sample ownership contracts; no game doubles."""

import pytest

from backend.core.fanxiu.activity.pet_rank_estimation import estimate_pet_rank_inventory
from backend.core.fanxiu.activity.pet_rank_plan import (
    select_pet_rank_samples, validate_pet_rank_snapshot, plan_pet_rank_target_from_snapshot,
)


def test_zero_yield_batches_lower_same_item_mean():
    estimate = estimate_pet_rank_inventory(
        [{"item_id": 1, "count": 20, "base_gain": 1}],
        [{"item_id": 1, "quantity": 10, "rank_delta": 100},
         {"item_id": 1, "quantity": 10, "rank_delta": 0}],
    )
    assert estimate["estimated_gain"] == 100
    assert estimate["resources"][0]["sample_quantity"] == 20


def test_exhausted_capacity_overrides_historical_gain():
    estimate = estimate_pet_rank_inventory(
        [{"item_id": 1, "count": 20, "base_gain": 0}],
        [{"item_id": 1, "quantity": 10, "rank_delta": 100}],
    )
    assert estimate["estimated_gain"] == 0
    assert estimate["resources"][0]["source"] == "capacity_exhausted"


def test_pool_is_explicit_estimate_and_rejects_nonfinite_samples():
    estimate = estimate_pet_rank_inventory(
        [{"item_id": 2, "count": 3, "base_gain": 2}],
        [{"item_id": 1, "quantity": 10, "rank_delta": 100, "base_total": 20},
         {"item_id": 2, "quantity": 1, "rank_delta": float("nan")}],
    )
    assert estimate["estimated_gain"] == pytest.approx(30)
    assert estimate["is_lower_bound"] is False
    assert estimate["resources"][0]["source"] == "pooled_base_estimate"
    assert estimate["unmeasured_item_ids"] == [2]


def test_rank_sample_supersedes_receipt_and_overlapping_calibration():
    common = {"pet_id": 7, "item_id": 1, "quantity": 10, "rank_delta": 50}
    rows = [
        {**common, "status": "verified", "action_id": "a"},
        {**common, "status": "rank_sample", "source_action_ids": ["a"]},
        {**common, "status": "rank_sample", "source_action_ids": ["a", "b"], "rank_delta": 80},
        {**common, "status": "verified", "action_id": "wrong", "pet_id": 8},
        {**common, "status": "verified", "action_id": "wrong2", "occurrence": "2026-09-05"},
    ]
    result = select_pet_rank_samples(rows, activity_id=11, pet_id=7, occurrence="2026-09-06")
    assert len(result) == 1
    assert result[0]["rank_delta"] == 80


def test_zero_rank_sample_is_observation_not_missing():
    rows = [{"status": "verified", "action_id": "a", "pet_id": 7,
             "item_id": 1, "quantity": 10, "rank_delta": 0}]
    assert select_pet_rank_samples(rows, activity_id=11, pet_id=7, occurrence="2026-09-06") == rows


@pytest.mark.parametrize("rows", [
    [{"rank": 1, "score": float("nan")}],
    [{"rank": 1, "score": -1}],
    [{"rank": 1, "score": 10}, {"rank": 1, "score": 10}],
    [{"rank": 1, "score": 10}, {"rank": 2, "score": 20}],
    [{"rank": 0, "score": 10}],
])
def test_invalid_rank_data_never_reaches_consumption_policy(rows):
    snapshot = {"complete": True, "rank_activity_id": 11,
                "self_ranking": {"score": 0}, "rankings": rows}
    assert validate_pet_rank_snapshot(snapshot, activity_id=11)["status"] == "rank_invalid"


def test_rank_snapshot_requires_exact_activity_and_completeness():
    snapshot = {"complete": True, "rank_activity_id": 12,
                "self_ranking": {"score": 0}, "rankings": []}
    assert validate_pet_rank_snapshot(snapshot, activity_id=11)["status"] == "rank_invalid"
    snapshot["complete"] = False
    assert validate_pet_rank_snapshot(snapshot, activity_id=11)["status"] == "rank_unavailable"


@pytest.mark.parametrize("current,gain,status", [
    (0, 100, "tie_verification_required"),
    (70, 30, "complete"),
    (0, 101, "act"),
])
def test_reachable_boundary_tie_blocks_only_new_consumption(current, gain, status):
    snapshot = {"complete": True, "rank_activity_id": 11,
                "self_ranking": {"score": current},
                "rankings": [{"rank": r, "score": s} for r, s in
                             [(1, 100), (2, 80), (3, 60), (4, 40)]]}
    plan = plan_pet_rank_target_from_snapshot(snapshot, activity_id=11,
                                              potential_gain=gain, reward_boundaries=[1, 4])
    assert plan["status"] == status
    assert plan["target_score"] == 60
