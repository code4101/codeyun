"""Real receipt persistence and pure quantity policy; no simulated game flow."""

import pytest

from backend.core.fanxiu.activity import pet_resource_receipts as receipts


def rank(score, second, *, role=9):
    return {"complete": True, "rank_activity_id": 1,
            "self_ranking": {"role_id": role, "score": score},
            "captured_at": f"2026-09-06T17:00:{second:02d}+08:00"}


@pytest.fixture
def ledger(monkeypatch, tmp_path):
    monkeypatch.setattr(receipts, "fanxiu_data_annotation_dir", lambda: tmp_path)

    def add(quantity, second, item=5):
        return receipts.record_pet_resource_receipt(1, "2026-09-06", {
            "status": "verified", "activity_id": 1, "pet_id": 7,
            "item_id": item, "quantity": quantity, "base_total": quantity,
            "captured_at": f"2026-09-06T17:00:{second:02d}+08:00",
        })
    return add


def test_same_item_aggregate_is_atomic_and_idempotent(ledger):
    actions = [ledger(125, 10), ledger(221, 20)]
    kwargs = dict(pet_id=7, action_ids=actions, before_rank=rank(1000, 0), after_rank=rank(1692, 30))
    first = receipts.record_pet_rank_sample(1, "2026-09-06", **kwargs)
    assert first["quantity"] == 346
    assert first["base_total"] == 346
    assert first["rank_delta"] == 692
    assert receipts.record_pet_rank_sample(1, "2026-09-06", **kwargs) == first
    assert len(receipts.read_pet_resource_receipts(1, "2026-09-06")) == 3
    with pytest.raises(ValueError, match="different rank calibration"):
        receipts.record_pet_rank_sample(1, "2026-09-06", **{**kwargs, "after_rank": rank(1700, 30)})
    with pytest.raises(ValueError, match="different rank calibration"):
        receipts.record_pet_rank_sample(1, "2026-09-06", **{
            **kwargs, "before_rank": rank(1000, 0, role=10), "after_rank": rank(1692, 30, role=10)})


@pytest.mark.parametrize("effect", [None, "submitted", "verified"])
def test_cancel_observation_only_before_resource_submission(ledger, effect):
    old = ledger(10, 0)
    observation = receipts.record_pet_resource_receipt(1, "2026-09-06", {
        "status": "rank_observation_pending", "pet_id": 7, "prior_action_ids": [old]})
    if effect == "verified":
        ledger(5, 10)
    elif effect == "submitted":
        receipts.record_pet_resource_receipt(1, "2026-09-06", {"status": "submitted"})
    kwargs = dict(observation_action_id=observation, pet_id=7, reason="quantity preparation failed")
    assert receipts.cancel_unsubmitted_pet_rank_observation(1, "2026-09-06", **kwargs) is (effect is None)
    assert receipts.cancel_unsubmitted_pet_rank_observation(1, "2026-09-06", **kwargs) is (effect is None)
    rows = receipts.read_pet_resource_receipts(1, "2026-09-06")
    saved = next(r for r in rows if r["action_id"] == observation)
    assert saved["status"] == ("rank_observation_cancelled" if effect is None else "rank_observation_pending")
    assert next(r for r in rows if r["action_id"] == old)["status"] == "verified"


def test_cancel_observation_requires_identity_and_prior_history(ledger):
    observation = receipts.record_pet_resource_receipt(1, "2026-09-06", {
        "status": "rank_observation_pending", "pet_id": 7})
    kwargs = dict(observation_action_id=observation, reason="preparation failed")
    with pytest.raises(ValueError, match="identity"):
        receipts.cancel_unsubmitted_pet_rank_observation(1, "2026-09-06", pet_id=8, **kwargs)
    assert not receipts.cancel_unsubmitted_pet_rank_observation(1, "2026-09-06", pet_id=7, **kwargs)


def test_rank_sample_completes_observation_in_same_atomic_write(ledger):
    before = rank(1000, 0)
    observation = receipts.record_pet_resource_receipt(1, "2026-09-06", {
        "status": "rank_observation_pending", "pet_id": 7, "before_rank": before})
    action = ledger(125, 10)
    sample = receipts.record_pet_rank_sample(1, "2026-09-06", pet_id=7, action_ids=[action],
        before_rank=before, after_rank=rank(1200, 30), observation_action_id=observation)
    saved = next(r for r in receipts.read_pet_resource_receipts(1, "2026-09-06") if r["action_id"] == observation)
    assert saved["status"] == "rank_observation_verified"
    assert saved["sample_action_id"] == sample["action_id"]


def test_mixed_or_omitted_batches_cannot_invent_item_yield(ledger):
    first, second = ledger(125, 10), ledger(10, 20, item=6)
    for ids, error in (([first, second], "different resources"), ([first], "another resource batch")):
        with pytest.raises(ValueError, match=error):
            receipts.record_pet_rank_sample(1, "2026-09-06", pet_id=7,
                action_ids=ids, before_rank=rank(1000, 0), after_rank=rank(1200, 30))


def test_sample_rejects_unresolved_submission_and_wrong_player(ledger):
    action = ledger(125, 10)
    with pytest.raises(ValueError, match="player identity"):
        receipts.record_pet_rank_sample(1, "2026-09-06", pet_id=7, action_ids=[action],
            before_rank=rank(1000, 0), after_rank=rank(1200, 30, role=10))
    receipts.record_pet_resource_receipt(1, "2026-09-06", {"status": "submitted"})
    with pytest.raises(RuntimeError, match="unresolved"):
        receipts.record_pet_rank_sample(1, "2026-09-06", pet_id=7, action_ids=[action],
            before_rank=rank(1000, 0), after_rank=rank(1200, 30))


def test_baseline_second_cannot_hide_another_resource(ledger):
    ledger(10, 0, item=6)
    action = ledger(125, 10)
    with pytest.raises(ValueError, match="another resource batch"):
        receipts.record_pet_rank_sample(1, "2026-09-06", pet_id=7, action_ids=[action],
            before_rank=rank(1000, 0), after_rank=rank(1200, 30))


def test_ranking_batch_uses_rank_yield_and_probes_new_item():
    from backend.core.fanxiu.data_annotation.tasks.pet_rank_use import plan_pet_rank_batch

    item = {"item_id": 5, "item_name": "material", "count": 1000, "base_gain": 1,
            "mean_rank_gain": 2, "source": "same_item_sample"}
    plan = {"status": "act", "gap": 500, "estimate": {"resources": [item]}}
    assert plan_pet_rank_batch(plan)["quantity"] == 200
    item["source"] = "pooled_base_estimate"
    assert plan_pet_rank_batch(plan)["quantity"] == 100
    item["mean_rank_gain"] = 0
    assert plan_pet_rank_batch(plan)["quantity"] == 0


def test_loaded_rows_replan_without_changing_inventory_estimate():
    from backend.core.fanxiu.activity.pet_rank_plan import replan_pet_rank_from_snapshot

    estimate = {"estimated_gain": 500, "resources": []}
    plan = {"activity_id": 1, "pet_id": 7, "occurrence": "2026-09-06",
            "estimate": estimate, "reward_boundaries": [64, 128]}
    board = {**rank(80, 0), "rankings": [{"rank": 64, "score": 400}, {"rank": 128, "score": 100}]}
    missing = replan_pet_rank_from_snapshot(plan, board)
    assert missing["missing_ranks"] == [106, 107]
    board["rankings"].extend([{"rank": 106, "score": 180}, {"rank": 107, "score": 150}])
    ready = replan_pet_rank_from_snapshot(missing, board)
    assert ready["status"] == "act"
    assert ready["target_score"] == pytest.approx(160)
    assert ready["estimate"] is estimate
    assert "missing_ranks" not in ready
