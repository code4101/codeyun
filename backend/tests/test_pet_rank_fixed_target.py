"""Pure fixed-target progress policy; no Runtime, GUI or receipt writes."""

from copy import deepcopy
from datetime import datetime

import pytest

from backend.core.fanxiu.data_annotation.tasks.pet_rank_use import (
    plan_pet_rank_batch,
    rebase_fixed_pet_rank_inventory,
    update_fixed_pet_rank_plan,
)
from backend.core.fanxiu.data_annotation.tasks import pet_rank_use
from backend.core.fanxiu.activity import pet_rank_plan


def baseline():
    return {
        "status": "act", "activity_id": 1, "pet_id": 7,
        "occurrence": "2026-09-06", "target_score": 10000,
        "target_boundary": 64, "current_score": 1000,
        "gap": 9000, "self_ranking": {"role_id": 9, "rank": 79, "score": 1000},
        "inventory_baseline_action_ids": [],
        "estimate": {"resources": [
            {"item_id": 5, "count": 5000, "base_gain": 10},
        ]},
    }


def snapshot(score, position, role=9):
    return {"complete": True, "scope": "self", "rank_activity_id": 1,
            "rankings": [], "self_ranking": {"role_id": role, "score": score, "rank": position}}


@pytest.mark.parametrize("score,position,status", [
    (9999, 79, "act"),
    (10000, 64, "complete"),
    (11000, 60, "complete"),
    (10000, 65, "rank_reassessment_required"),
    (10000, None, "rank_verification_required"),
    (10000, 0, "rank_verification_required"),
])
def test_score_boundary_needs_actual_self_rank(score, position, status):
    plan = baseline()
    original = deepcopy(plan)
    result = update_fixed_pet_rank_plan(plan, snapshot(score, position), receipts=[])
    assert result["status"] == status
    assert result["target_score"] == 10000
    assert result["target_boundary"] == 64
    assert plan == original


def test_verified_inventory_and_yield_replace_initial_estimate():
    rows = [{"status": "verified", "activity_id": 1, "pet_id": 7,
             "occurrence": "2026-09-06", "action_id": "a", "item_id": 5,
             "quantity": 100, "base_total": 1000, "rank_delta": 2000,
             "inventory_after": 4900}]
    result = update_fixed_pet_rank_plan(baseline(), snapshot(3000, 70), receipts=rows)
    item = result["estimate"]["resources"][0]
    assert item["count"] == 4900
    assert item["mean_rank_gain"] == 20
    batch = plan_pet_rank_batch(result)
    assert batch["mode"] == "feedback"
    assert batch["quantity"] == 280  # 80% of the remaining 7000 / 20.


def test_new_item_retains_twenty_percent_probe():
    plan = baseline()
    plan["estimate"]["resources"][0].update(
        mean_rank_gain=20, source="pooled_base_estimate")
    batch = plan_pet_rank_batch(plan)
    assert batch["mode"] == "probe"
    assert batch["quantity"] == 90


def test_old_receipt_cannot_restore_stock_over_a_newer_inventory_snapshot():
    plan = baseline()
    plan["inventory_baseline_action_ids"] = ["old"]
    plan["estimate"]["resources"][0]["count"] = 2
    rows = [{"status": "verified", "pet_id": 7, "item_id": 5,
             "action_id": "old", "inventory_after": 100}]
    result = update_fixed_pet_rank_plan(plan, snapshot(3000, 70), receipts=rows)
    assert result["estimate"]["resources"][0]["count"] == 2
    rows.append({"status": "verified", "pet_id": 7, "item_id": 5,
                 "action_id": "new", "inventory_after": 1})
    result = update_fixed_pet_rank_plan(result, snapshot(4000, 69), receipts=rows)
    assert result["estimate"]["resources"][0]["count"] == 1
    assert set(result["inventory_baseline_action_ids"]) == {"old", "new"}


@pytest.mark.parametrize("receipt_time,expected_count", [
    ("2026-09-06T17:00:00+08:00", 2),
    ("2026-09-06T17:00:02+08:00", 100),
])
def test_legacy_plan_can_prove_its_baseline_from_distinct_timestamps(receipt_time, expected_count):
    plan = baseline()
    del plan["inventory_baseline_action_ids"]
    plan["captured_at"] = "2026-09-06T17:00:01+08:00"
    plan["estimate"]["resources"][0]["count"] = 2
    rows = [{"status": "verified", "pet_id": 7, "item_id": 5,
             "action_id": "a", "inventory_after": 100, "captured_at": receipt_time}]
    result = update_fixed_pet_rank_plan(plan, snapshot(3000, 70), receipts=rows)
    assert result["estimate"]["resources"][0]["count"] == expected_count


def test_legacy_plan_rejects_ambiguous_inventory_snapshot_order():
    plan = baseline()
    del plan["inventory_baseline_action_ids"]
    plan["captured_at"] = "2026-09-06T17:00:01+08:00"
    rows = [{"status": "verified", "pet_id": 7, "item_id": 5,
             "action_id": "a", "inventory_after": 100,
             "captured_at": "2026-09-06T17:00:01.500+08:00"}]
    with pytest.raises(ValueError, match="baseline"):
        update_fixed_pet_rank_plan(plan, snapshot(3000, 70), receipts=rows)


@pytest.mark.parametrize("pending", ["submitted", "rank_observation_pending"])
def test_pending_effect_cannot_be_replanned(pending):
    with pytest.raises(ValueError, match="pending"):
        update_fixed_pet_rank_plan(baseline(), snapshot(3000, 70),
                                   receipts=[{"status": pending}])


def test_other_actor_cannot_update_the_fixed_target():
    with pytest.raises(ValueError, match="same actor"):
        update_fixed_pet_rank_plan(baseline(), snapshot(3000, 70, role=10), receipts=[])


@pytest.mark.parametrize("pending,expected", [
    ("submitted", "resource_action_pending"),
    ("rank_observation_pending", "rank_calibration_pending"),
])
def test_resumed_plan_checks_pending_receipts_before_navigation(monkeypatch, pending, expected):
    # Only the pre-action ownership gate runs; no Context or game is simulated.
    plan = baseline()
    plan["occurrence"] = datetime.now().astimezone().date().isoformat()
    monkeypatch.setattr(pet_rank_use, "read_pet_resource_receipts",
                        lambda *_: [{"status": pending}])

    def forbidden(*args, **kwargs):
        pytest.fail("Pending receipts must stop before any game callback")

    operation = pet_rank_use.use_pet_resources_for_rank(
        None, activity_id=1, pet_id=7, occurrence=plan["occurrence"],
        initial_plan=plan, initial_rank=snapshot(1000, 79),
        refresh_rank=forbidden, refresh_self_rank=forbidden, enter_resources=forbidden,
    )
    with pytest.raises(StopIteration) as stopped:
        next(operation)
    assert stopped.value.value["status"] == expected


@pytest.mark.parametrize("pending,expected", [
    ("submitted", "resource_action_pending"),
    ("rank_observation_pending", "rank_calibration_pending"),
])
def test_direct_plan_checks_pending_before_runtime(monkeypatch, pending, expected):
    monkeypatch.setattr(pet_rank_plan, "read_pet_resource_receipts",
                        lambda *_: [{"status": pending}])

    def forbidden(*args, **kwargs):
        pytest.fail("Pending receipts must stop before Runtime access")

    monkeypatch.setattr(pet_rank_plan, "read_activity_rank_runtime_snapshot", forbidden)
    result = pet_rank_plan.read_pet_rank_plan(activity_id=1, pet_id=7, occurrence="2026-09-06")
    assert result["status"] == expected


def test_external_use_rebases_inventory_without_fabricating_a_yield_sample():
    plan = baseline()
    resources = [{"item_id": 5, "count": 2, "base_gain": 10}]
    history = [{"status": "verified", "pet_id": 7, "item_id": 5,
                "action_id": "old", "quantity": 100, "rank_delta": 2000,
                "base_total": 1000, "inventory_after": 100}]
    original = deepcopy((plan, resources, history))
    result = rebase_fixed_pet_rank_inventory(
        plan, snapshot(9000, 65), resources=resources, receipts=history,
    )
    assert result["target_score"] == 10000
    assert result["target_boundary"] == 64
    assert result["gap"] == 1000
    item = result["estimate"]["resources"][0]
    assert item["count"] == 2
    assert item["sample_quantity"] == 100
    assert item["mean_rank_gain"] == 20
    assert (plan, resources, history) == original


def test_external_use_cannot_bypass_an_unresolved_submission():
    with pytest.raises(ValueError, match="pending"):
        rebase_fixed_pet_rank_inventory(
            baseline(), snapshot(9000, 65),
            resources=[{"item_id": 5, "count": 2, "base_gain": 10}],
            receipts=[{"status": "submitted"}],
        )
