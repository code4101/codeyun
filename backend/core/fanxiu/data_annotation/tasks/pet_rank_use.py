"""Development-only pet ranking loop; GUI navigation remains explicit callbacks.

``refresh_rank(context, *, activity_id, missing_ranks)`` establishes the initial
opponent board and loads requested rows. Subsequent batches use the separate
``refresh_self_rank`` callback, which refreshes only our score and position.
``enter_resources(context, *, activity_id, pet_id)`` must
return at the verified pet aptitude page without swallowing or using resources.
Any automatic pet preparation must finish before the initial rank baseline.
All navigation callbacks are generators. A
cached Runtime read alone does not satisfy the refresh callback's contract.
No automatic Job is registered by this module.
"""

from copy import deepcopy
from datetime import datetime, timedelta
from math import isfinite

from backend.core.fanxiu.activity.pet_rank_plan import (
    read_pet_rank_plan, replan_pet_rank_from_snapshot, validate_pet_rank_snapshot, select_pet_rank_samples,
)
from backend.core.fanxiu.activity.pet_rank_estimation import estimate_pet_rank_inventory
from backend.core.fanxiu.activity.pet_resource_planning import plan_pet_resource_batch
from backend.core.fanxiu.activity.pet_resource_receipts import (
    read_pet_resource_receipts, record_pet_resource_receipt, record_pet_rank_sample,
    cancel_unsubmitted_pet_rank_observation,
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


def update_fixed_pet_rank_plan(plan: dict, rank: dict, *, receipts: list[dict]) -> dict:
    """Update own score/yield/inventory without revisiting the initial rank target.

    Uses real verified receipts, including a batch calibrated after an
    interrupted call. The target and opponent rows are never recomputed.
    """
    board = validate_pet_rank_snapshot(rank, activity_id=plan["activity_id"])
    before_actor = plan.get("self_ranking") or {}
    after_actor = rank.get("self_ranking") or {}
    actor_id = before_actor.get("role_id") or before_actor.get("role_key")
    if (board["status"] != "ready" or not actor_id
            or actor_id != (after_actor.get("role_id") or after_actor.get("role_key"))):
        raise ValueError("Fixed target score update requires the same actor and activity")
    if any(r.get("status") in {"submitted", "rank_observation_pending"} for r in receipts):
        raise ValueError("Resource or rank effect still pending; calibrate before replanning")
    resources = [dict(item) for item in plan["estimate"]["resources"]]
    resources_by_id = {item["item_id"]: item for item in resources}
    verified = [row for row in receipts
        if (row.get("status") == "verified" and row.get("pet_id") == plan["pet_id"]
                and row.get("activity_id", plan["activity_id"]) == plan["activity_id"]
                and row.get("occurrence", plan["occurrence"]) == plan["occurrence"])]
    covered = _inventory_baseline_actions(plan, verified)
    for row in verified:
        if row["action_id"] not in covered and row.get("inventory_after") is not None:
            item = resources_by_id.get(row.get("item_id"))
            if item is not None:
                item["count"] = row["inventory_after"]
        covered.add(row["action_id"])
    samples = select_pet_rank_samples(receipts, activity_id=plan["activity_id"],
                                     pet_id=plan["pet_id"], occurrence=plan["occurrence"])
    estimate = estimate_pet_rank_inventory(resources, samples)
    current = board["current_score"]
    gap = plan["target_score"] - current
    return {**plan, "status": _fixed_target_status(plan, current, after_actor),
            "current_score": current, "gap": gap, "self_ranking": after_actor,
            "rank_captured_at": rank.get("captured_at"), "estimate": estimate,
            "inventory_baseline_action_ids": sorted(covered),
            "potential": current + estimate["estimated_gain"]}


def _inventory_baseline_actions(plan: dict, verified: list[dict]) -> set[str]:
    """Old observations estimate yield but must not overwrite fresh inventory."""
    if any(not isinstance(row.get("action_id"), str) or not row["action_id"] for row in verified):
        raise ValueError("Verified inventory receipts require action IDs")
    if "inventory_baseline_action_ids" in plan:
        return set(plan["inventory_baseline_action_ids"])
    if not verified:
        return set()
    # Development plans created before this field may resume only when their
    # timestamp proves which receipts the inventory snapshot already covered.
    # Plan timestamps have second precision: the same second is ambiguous.
    try:
        cutoff = datetime.fromisoformat(plan["captured_at"])
        if cutoff.tzinfo is None:
            raise ValueError("Missing snapshot timezone")
        covered = set()
        for row in verified:
            captured = datetime.fromisoformat(row["captured_at"])
            if captured.tzinfo is None:
                raise ValueError("Missing receipt timezone")
            if cutoff <= captured < cutoff + timedelta(seconds=1):
                raise ValueError("Inventory snapshot and receipt order is ambiguous")
            if captured < cutoff:
                covered.add(row["action_id"])
        return covered
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Fixed plan requires a proven inventory receipt baseline") from exc


def rebase_fixed_pet_rank_inventory(plan: dict, rank: dict, *,
                                    resources: list[dict], receipts: list[dict]) -> dict:
    """Resume after external use from fresh inventory and self-score observations.

    The caller supplies one verified observation boundary after external use
    has stopped and resolves pending operations first. Keep the initial target
    and historical verified yields; do not invent a receipt or infer manual
    consumption from inventory loss, which may include refunds or rewards.
    """
    observed = deepcopy(resources)
    item_ids = [row.get("item_id") for row in observed]
    if (any(isinstance(item_id, bool) or not isinstance(item_id, int) or item_id <= 0
            for item_id in item_ids) or len(set(item_ids)) != len(item_ids)):
        raise ValueError("Fresh inventory requires unique positive item IDs")
    for row in observed:
        count = row.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("Fresh inventory count must be a nonnegative integer")
        if "base_gain" not in row:
            raise ValueError("Fresh inventory requires current pet capacity gains")
    covered = [row["action_id"] for row in receipts
               if row.get("status") == "verified" and row.get("pet_id") == plan["pet_id"]
               and row.get("activity_id", plan["activity_id"]) == plan["activity_id"]
               and row.get("occurrence", plan["occurrence"]) == plan["occurrence"]]
    refreshed = {**plan, "estimate": {"resources": observed},
                 "inventory_baseline_action_ids": covered}
    return update_fixed_pet_rank_plan(refreshed, rank, receipts=receipts)


def _fixed_target_status(plan: dict, score: float, own_rank: dict) -> str:
    """Only reassess opponents after reaching the fixed score but missing its tier."""
    if score < plan["target_score"]:
        return "act"
    position = own_rank.get("rank")
    boundary = plan.get("target_boundary")
    if (isinstance(position, bool) or not isinstance(position, int) or position <= 0
            or isinstance(boundary, bool) or not isinstance(boundary, int) or boundary <= 0):
        return "rank_verification_required"
    return "complete" if position <= boundary else "rank_reassessment_required"


def use_pet_resources_for_rank(context, *, activity_id: int, pet_id: int,
                               occurrence: str, refresh_rank, enter_resources,
                               world_level: int | None = None, max_batches: int = 1,
                               initial_plan: dict | None = None, initial_rank: dict | None = None,
                               refresh_self_rank=None):
    """Fix the first complete target; update only own score and used inventory.

    initial_plan and initial_rank must describe the same verified baseline,
    with no intervening resource use. Opponent rows are loaded once only;
    subsequent GUI refreshes need just the first page's self_ranking.
    One batch is the default development step. Reaching the fixed score only
    completes the task if the observed self rank also meets target_boundary;
    otherwise return a verification/reassessment status without crawling rows.
    ``refresh_self_rank`` defaults to the short stage-tab development callback;
    it never falls back to the initial full-board loader. This GUI route still
    requires live acceptance before the development task becomes a normal Job.
    Interrupted submissions still require explicit factual calibration first.
    """
    if isinstance(max_batches, bool) or not isinstance(max_batches, int) or max_batches < 0:
        raise ValueError("max_batches must be a nonnegative integer")
    if (initial_plan is None) != (initial_rank is None):
        raise ValueError("initial_plan and initial_rank must be supplied together")
    if refresh_self_rank is None:
        from backend.core.fanxiu.data_annotation.tasks.pet_rank_navigation import refresh_peak_pet_self_rank
        refresh_self_rank = refresh_peak_pet_self_rank
    plan = deepcopy(initial_plan)
    rank = deepcopy(initial_rank)
    if plan is not None:
        board = validate_pet_rank_snapshot(rank, activity_id=activity_id)
        expected_actor = plan.get("self_ranking") or {}
        observed_actor = rank.get("self_ranking") or {}
        actor_id = expected_actor.get("role_id") or expected_actor.get("role_key")
        if (board["status"] != "ready" or plan.get("activity_id") != activity_id
                or plan.get("pet_id") != pet_id or plan.get("occurrence") != occurrence
                or plan.get("current_score") != board.get("current_score")
                or not actor_id
                or actor_id != (observed_actor.get("role_id") or observed_actor.get("role_key"))
                or plan.get("status") not in {"act", "complete"}
                or not isfinite(plan.get("target_score", float("nan")))):
            raise ValueError("Initial plan and rank baseline are unavailable or mismatched")
    results = []
    for batch_index in range(max_batches + 1):
        if occurrence != datetime.now().astimezone().date().isoformat():
            return {"status": "occurrence_changed", "batches": results}
        history = read_pet_resource_receipts(activity_id, occurrence)
        if any(r.get("status") == "submitted" for r in history):
            return {"status": "resource_action_pending", "batches": results}
        if any(r.get("status") == "rank_observation_pending" for r in history):
            return {"status": "rank_calibration_pending", "batches": results}
        if rank is None:
            rank = yield from refresh_rank(context, activity_id=activity_id, missing_ranks=[])
        if plan is None:
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
        if plan["status"] in {"act", "complete"}:
            plan["status"] = _fixed_target_status(
                plan, rank["self_ranking"]["score"], rank["self_ranking"],
            )
        if plan["status"] != "act":
            return {**plan, "rank": rank, "batches": results}
        if batch_index == max_batches:
            return {"status": "batch_limit", "plan": plan, "rank": rank, "batches": results}
        batch = plan_pet_rank_batch(plan)
        if batch["quantity"] <= 0:
            return {**batch, "plan": plan, "rank": rank, "batches": results}
        yield from enter_resources(context, activity_id=activity_id, pet_id=pet_id)
        if occurrence != datetime.now().astimezone().date().isoformat():
            return {"status": "occurrence_changed", "batches": results}
        observation_id = record_pet_resource_receipt(activity_id, occurrence,
            {"status": "rank_observation_pending", "pet_id": pet_id,
             "before_rank": rank, "prior_action_ids": [r["action_id"] for r in history]})
        try:
            receipt = yield from use_pet_resource_batch(
                context, activity_id=activity_id, pet_id=pet_id, item_id=batch["item_id"],
                item_name=batch["item_name"], quantity=batch["quantity"], base_gain=batch["base_gain"],
                verify_task_progress=False,
            )
        except Exception as exc:
            # No GUI rollback and no retry. Only the provider can establish
            # that preparation failed before any resource submission.
            if occurrence == datetime.now().astimezone().date().isoformat():
                try:
                    cancel_unsubmitted_pet_rank_observation(
                        activity_id, occurrence, observation_action_id=observation_id,
                        pet_id=pet_id, reason=f"{type(exc).__name__}: {exc}",
                    )
                except Exception as cleanup_error:
                    exc.add_note(f"Rank observation retained: {cleanup_error}")
            raise
        rank = yield from refresh_self_rank(context, activity_id=activity_id, missing_ranks=[])
        sample = calibrate_pending_pet_rank_batch(activity_id=activity_id, pet_id=pet_id,
                                                  occurrence=occurrence, after_rank=rank)
        results.append({"receipt": receipt, "rank_sample": sample,
                        "observation_id": observation_id})
        plan = update_fixed_pet_rank_plan(
            plan, rank, receipts=read_pet_resource_receipts(activity_id, occurrence),
        )
        if sample["rank_delta"] <= 0:
            return {"status": "no_rank_gain", "plan": plan, "rank": rank, "batches": results}


def use_peak_pet_rank_batch(context, *, activity_id: int, pet_id: int,
                            occurrence: str, world_level: int | None = None,
                            initial_plan: dict | None = None, initial_rank: dict | None = None):
    """Development entry: one batch, with initial-board and self-refresh wiring.

    Pass the previously verified plan and its current self snapshot together
    to continue without reloading opponent rows. A batch_limit result includes
    both ``plan`` and ``rank`` for that next step. This does not register a Job
    or imply live acceptance; iterating the generator performs game actions.
    """
    from backend.core.fanxiu.data_annotation.tasks.pet_rank_navigation import (
        enter_peak_pet_rank_resources,
        refresh_peak_pet_rank,
        refresh_peak_pet_self_rank,
    )

    return (yield from use_pet_resources_for_rank(
        context, activity_id=activity_id, pet_id=pet_id, occurrence=occurrence,
        world_level=world_level, max_batches=1,
        initial_plan=initial_plan, initial_rank=initial_rank,
        refresh_rank=refresh_peak_pet_rank, refresh_self_rank=refresh_peak_pet_self_rank,
        enter_resources=enter_peak_pet_rank_resources,
    ))
