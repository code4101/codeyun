"""Development-only pet ranking loop; GUI navigation remains explicit callbacks.

``refresh_rank(context, *, activity_id, missing_ranks)`` must naturally reopen
the correct occurrence's board (and load requested rows), then return its live
Runtime snapshot. ``enter_resources(context, *, activity_id, pet_id)`` must
return at the verified pet aptitude page without swallowing or using resources.
Any automatic pet preparation must finish before the initial rank baseline.
Both callbacks are generators. A
cached Runtime read alone does not satisfy the refresh callback's contract.
No automatic Job is registered by this module.
"""

from datetime import datetime
from math import isfinite

from backend.core.fanxiu.activity.pet_rank_plan import read_pet_rank_plan, replan_pet_rank_from_snapshot
from backend.core.fanxiu.activity.pet_resource_planning import plan_pet_resource_batch
from backend.core.fanxiu.activity.pet_resource_receipts import (
    read_pet_resource_receipts, record_pet_resource_receipt, record_pet_rank_sample,
)
from backend.core.fanxiu.data_annotation.tasks.pet_resource_use import use_pet_resource_batch


def plan_pet_rank_batch(plan: dict) -> dict:
    """Convert rank gap through this item's own rank yield, never aptitude yield."""
    if plan.get("status") != "act":
        return {"status": "no_action", "quantity": 0}
    for item in plan["estimate"]["resources"]:
        mean = float(item.get("mean_rank_gain") or 0)
        base = int(item.get("base_gain") or 0)
        if item["count"] <= 0 or base <= 0 or not isfinite(mean) or mean <= 0:
            continue
        measured = item["source"] == "same_item_sample"
        # For an unmeasured item the first 20% probe uses the explicit pooled
        # estimate only to choose a batch. Its result becomes an independent
        # item sample; later batches use 80% feedback from that measured yield.
        batch = plan_pet_resource_batch(
            gap=plan["gap"] if measured else plan["gap"] * base / mean,
            base_gain=base, available=item["count"],
            measured_multiplier=mean / base if measured else None,
        )
        return {**item, **batch, "status": "act", "estimated_rank_gain": batch["quantity"] * mean}
    return {"status": "no_productive_resource", "quantity": 0}


def calibrate_pending_pet_rank_batch(*, activity_id: int, pet_id: int,
                                     occurrence: str, after_rank: dict) -> dict:
    """Resolve observed rank evidence after a completed batch; never click again.

    An interrupted/unverified resource submission stays unresolved. This only
    attaches rank evidence to verified resource facts, not to old GUI intent.
    """
    rows = read_pet_resource_receipts(activity_id, occurrence)
    pending = [r for r in rows if r.get("status") == "rank_observation_pending"]
    if len(pending) != 1 or pending[0].get("pet_id") != pet_id:
        raise ValueError("Expected one pending rank observation for this pet")
    observation = pending[0]
    prior = set(observation["prior_action_ids"])
    sources = [r["action_id"] for r in rows if r.get("status") == "verified"
               and r["action_id"] not in prior]
    sample = record_pet_rank_sample(
        activity_id, occurrence, pet_id=pet_id, action_ids=sources,
        before_rank=observation["before_rank"], after_rank=after_rank,
        observation_action_id=observation["action_id"],
    )
    return sample


def use_pet_resources_for_rank(context, *, activity_id: int, pet_id: int,
                               occurrence: str, refresh_rank, enter_resources,
                               world_level: int | None = None, max_batches: int = 12):
    """Fresh board -> plan -> one batch -> fresh board -> calibrate -> replan.

    Missing samples/rows and uncertain effects return an explicit diagnostic
    without additional consumption. The refresh callback owns GUI loading;
    callers may load returned ``missing_ranks`` and invoke this entry again.
    ``occurrence`` currently follows the resource provider's daily bucket.
    """
    if max_batches < 0:
        raise ValueError("max_batches must be nonnegative")
    results = []
    rank = None
    for batch_index in range(max_batches + 1):
        if occurrence != datetime.now().astimezone().date().isoformat():
            return {"status": "occurrence_changed", "batches": results}
        history = read_pet_resource_receipts(activity_id, occurrence)
        if any(r.get("status") == "rank_observation_pending" for r in history):
            return {"status": "rank_calibration_pending", "batches": results}
        if rank is None:
            rank = yield from refresh_rank(context, activity_id=activity_id, missing_ranks=[])
        plan = read_pet_rank_plan(activity_id=activity_id, pet_id=pet_id,
                                 occurrence=occurrence, world_level=world_level,
                                 rank_snapshot=rank)
        requested = set()
        for _ in range(len(plan.get("reward_boundaries", [])) + 2):
            if plan["status"] != "need_rank_rows":
                break
            missing = set(plan["missing_ranks"])
            if missing.issubset(requested):
                break
            requested.update(missing)
            rank = yield from refresh_rank(context, activity_id=activity_id,
                                           missing_ranks=sorted(requested))
            plan = replan_pet_rank_from_snapshot(plan, rank)
        if plan["status"] != "act":
            return {**plan, "batches": results}
        if batch_index == max_batches:
            return {"status": "batch_limit", "plan": plan, "batches": results}
        batch = plan_pet_rank_batch(plan)
        if batch["quantity"] <= 0:
            return {**batch, "plan": plan, "batches": results}
        yield from enter_resources(context, activity_id=activity_id, pet_id=pet_id)
        if occurrence != datetime.now().astimezone().date().isoformat():
            return {"status": "occurrence_changed", "batches": results}
        observation_id = record_pet_resource_receipt(activity_id, occurrence,
            {"status": "rank_observation_pending", "pet_id": pet_id,
             "before_rank": rank, "prior_action_ids": [r["action_id"] for r in history]})
        receipt = yield from use_pet_resource_batch(
            context, activity_id=activity_id, pet_id=pet_id, item_id=batch["item_id"],
            item_name=batch["item_name"], quantity=batch["quantity"], base_gain=batch["base_gain"],
        )
        rank = yield from refresh_rank(context, activity_id=activity_id, missing_ranks=[])
        sample = calibrate_pending_pet_rank_batch(activity_id=activity_id, pet_id=pet_id,
                                                  occurrence=occurrence, after_rank=rank)
        results.append({"receipt": receipt, "rank_sample": sample,
                        "observation_id": observation_id})
        if sample["rank_delta"] <= 0:
            return {"status": "no_rank_gain", "batches": results}
