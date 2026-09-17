import json

import pytest

from backend.core.fanxiu.activity import pet_resource_receipts
from backend.core.fanxiu.activity.pet_resource_planning import (
    order_pet_resources_low_to_high, plan_initialization_batch, plan_pet_probe_bulk_tail,
    plan_pet_ranking_target, plan_pet_resource_batch,
)


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


def test_resources_order_low_to_high_quality_then_num():
    rows = [
        {"item_id": 3, "quality": 6, "sort_order": 10},
        {"item_id": 1, "quality": 3, "sort_order": 5},
        {"item_id": 2, "quality": 3, "sort_order": 9},
        {"item_id": 4, "quality": 8, "sort_order": 1},
    ]
    assert [row["item_id"] for row in order_pet_resources_low_to_high(rows)] == [2, 1, 3, 4]


def test_probe_bulk_tail_probe_then_bulk_then_tail():
    assert plan_pet_probe_bulk_tail(gap=6000, available=283) == {
        "quantity": 1, "mode": "probe", "remaining_units": None,
    }
    bulk = plan_pet_probe_bulk_tail(gap=5830, available=283, sample_task_gain=170.0)
    assert bulk["remaining_units"] == 35
    assert (bulk["mode"], bulk["quantity"]) == ("bulk", 17)
    tail = plan_pet_probe_bulk_tail(gap=340, available=283, sample_task_gain=170.0)
    assert (tail["mode"], tail["quantity"], tail["remaining_units"]) == ("tail", 1, 2)


def test_probe_bulk_tail_caps_and_stops():
    assert plan_pet_probe_bulk_tail(gap=0, available=283)["quantity"] == 0
    assert plan_pet_probe_bulk_tail(gap=0, available=283)["mode"] == "complete"
    assert plan_pet_probe_bulk_tail(gap=6000, available=0,
                                    sample_task_gain=170.0)["mode"] == "empty"
    capped = plan_pet_probe_bulk_tail(gap=6000, available=283,
                                      sample_task_gain=170.0, remaining_cap=0)
    assert capped["quantity"] == 0
    allowance = plan_pet_probe_bulk_tail(gap=6000, available=283,
                                         sample_task_gain=170.0, remaining_cap=5)
    assert allowance["quantity"] == 5
    with pytest.raises(ValueError):
        plan_pet_probe_bulk_tail(gap=6000, available=283, sample_task_gain=0)


def test_unsampled_resource_is_never_batched_and_seed_keeps_priority():
    supplement = [
        {"resource_id": 8022005, "base_gain": 1, "available": 670},
        {"resource_id": 8022000, "base_gain": 5, "available": 359},
    ]
    # Seed without a sample probes with one unit even though it is high quality.
    batch = plan_initialization_batch(
        gap=6000, seed_option={"resource_id": 8022009, "base_gain": 100, "available": 283},
        supplement_options=supplement, seed_used=0,
    )
    assert (batch["resource_id"], batch["quantity"], batch["mode"]) == (8022009, 1, "probe")

    # Seed with its own sample batches ~50% of the estimate, capped by budget.
    sampled = plan_initialization_batch(
        gap=5830, seed_option={"resource_id": 8022009, "base_gain": 100, "available": 283},
        supplement_options=supplement, seed_used=1, samples={8022009: 170.0},
    )
    assert (sampled["resource_id"], sampled["quantity"], sampled["mode"]) == (8022009, 17, "bulk")

    # Exhausted seed budget falls through to the lowest quality resource; a
    # different resource's sample is not reused for it (probe, not batch).
    exhausted = plan_initialization_batch(
        gap=5830, seed_option={"resource_id": 8022009, "base_gain": 100, "available": 283},
        supplement_options=supplement, seed_used=30, samples={8022009: 170.0},
    )
    assert (exhausted["resource_id"], exhausted["quantity"], exhausted["mode"]) == (8022005, 1, "probe")

    assert plan_initialization_batch(
        gap=0, seed_option=None, supplement_options=[], seed_used=0,
    ) == {"resource_id": None, "base_gain": 0, "quantity": 0, "mode": "complete"}


def test_task_samples_are_per_resource_and_survive_repeated_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    monkeypatch.setattr(pet_resource_receipts, "fanxiu_data_annotation_dir", lambda: tmp_path)
    folder = tmp_path / "pet-resource-receipts"
    folder.mkdir()
    (folder / "4043501-2026-09-16.json").write_text(
        json.dumps([
            {"status": "verified", "pet_id": 7101, "item_id": 8022009, "quantity": 1,
             "task_before": 0, "task_after": 170, "aptitude_delta": 999},
            {"status": "verified", "pet_id": 7101, "item_id": 8022009, "quantity": 2,
             "task_before": 170, "task_after": 450},
            {"status": "verified", "pet_id": 9999, "item_id": 8022009, "quantity": 1,
             "task_before": 0, "task_after": 50},
            {"status": "submitted", "pet_id": 7101, "item_id": 8022002, "quantity": 1,
             "task_before": 0, "task_after": 60},
        ]),
        encoding="utf-8",
    )
    samples = pet_resource_receipts.select_pet_resource_task_samples(
        4043501, pet_id=7101, start_date="2026-09-16", end_date="2026-09-16",
    )
    assert samples == {8022009: 140.0}
    again = pet_resource_receipts.select_pet_resource_task_samples(
        4043501, pet_id=7101, start_date="2026-09-16", end_date="2026-09-16",
    )
    assert again == samples
