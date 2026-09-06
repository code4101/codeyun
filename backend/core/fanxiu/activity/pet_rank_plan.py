"""Read current pet inventory and already-loaded rankings to form a target."""

from datetime import datetime
from math import isfinite
from typing import Mapping, Sequence

from backend.core.fanxiu.activity.lingchong_jingwu import collect_lingchong_jingwu_resource_snapshot
from backend.core.fanxiu.activity.pet_rank_estimation import estimate_pet_rank_inventory
from backend.core.fanxiu.activity.pet_resource_planning import plan_pet_ranking_target
from backend.core.fanxiu.activity.pet_resource_receipts import read_pet_resource_receipts
from backend.core.fanxiu.activity.rank_reward import load_activity_rank_reward_tiers
from backend.core.fanxiu.instrumentation.activity_rank_runtime import read_activity_rank_runtime_snapshot
from backend.core.fanxiu.instrumentation.pet_aptitude import read_pet_aptitude_runtime


def select_pet_rank_samples(receipts: list[dict], *, activity_id: int,
                            pet_id: int, occurrence: str) -> list[dict]:
    """Select this occurrence's samples, counting each submitted batch once.

    The receipt file supplies the occurrence and activity scope for historical
    rows without those fields. Explicit conflicting identities are rejected.
    Standalone rank samples supersede the resource receipts they calibrate.
    """
    scoped = [r for r in receipts if r.get("pet_id") == pet_id
              and r.get("activity_id", activity_id) == activity_id
              and r.get("occurrence", occurrence) == occurrence
              and r.get("status") in {"verified", "rank_sample"}
              and r.get("rank_delta") is not None
              and isfinite(float(r["rank_delta"])) and float(r["rank_delta"]) >= 0
              and int(r.get("quantity") or 0) > 0]
    selected, used = [], set()
    # Prefer the most recent explicit calibration if source groups overlap.
    for row in reversed([r for r in scoped if r["status"] == "rank_sample"]):
        sources = set(row.get("source_action_ids") or [])
        if not sources or sources & used:
            continue
        selected.append(row)
        used.update(sources)
    for row in scoped:
        if row["status"] == "verified" and row.get("action_id") not in used:
            selected.append(row)
            if row.get("action_id"):
                used.add(row["action_id"])
    return selected


def validate_pet_rank_snapshot(rank: Mapping, *, activity_id: int) -> dict:
    """Validate observed rows without inventing missing ranks or freshness."""
    if rank.get("complete") is not True:
        return {"status": "rank_unavailable"}
    if rank.get("rank_activity_id") != activity_id:
        return {"status": "rank_invalid", "reason": "rank_activity_mismatch"}
    try:
        current = rank["self_ranking"]["score"]
        if isinstance(current, bool) or not isfinite(current) or current < 0:
            raise ValueError("Invalid self score")
        scores = {}
        for row in rank["rankings"]:
            position, score = row["rank"], row["score"]
            if (isinstance(position, bool) or not isinstance(position, int)
                    or position <= 0 or position in scores
                    or isinstance(score, bool) or not isfinite(score) or score < 0):
                raise ValueError("Invalid or duplicate rank row")
            scores[position] = score
        ordered = [scores[position] for position in sorted(scores)]
        if any(a < b for a, b in zip(ordered, ordered[1:])):
            raise ValueError("Rank scores increase toward worse positions")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        return {"status": "rank_invalid", "reason": str(exc)}
    return {"status": "ready", "current_score": current, "rank_scores": scores}


def plan_pet_rank_target_from_snapshot(rank_snapshot: Mapping, *, activity_id: int,
                                       potential_gain: float,
                                       reward_boundaries: Sequence[int]) -> dict:
    """Apply the rank policy with an explicit gate for uncertain equal-score entry."""
    board = validate_pet_rank_snapshot(rank_snapshot, activity_id=activity_id)
    if board["status"] != "ready":
        return board
    plan = plan_pet_ranking_target(current_score=board["current_score"],
        potential_gain=potential_gain, rank_scores=board["rank_scores"],
        reward_boundaries=reward_boundaries)
    boundary = plan.get("reachable_boundary")
    if (plan["status"] == "act" and boundary is not None
            and plan["potential"] == board["rank_scores"][boundary]):
        return {**plan, "status": "tie_verification_required",
                "reason": "Equal potential does not prove tier entry under live tie ordering"}
    return plan


def read_pet_rank_plan(*, activity_id: int, pet_id: int, occurrence: str,
                       world_level: int | None = None,
                       rank_snapshot: dict | None = None) -> dict:
    """Plan against the currently loaded board; missing rows require GUI loading.

    Calling this at 10:00 or 20:30 uses the same model and current observations.
    It does not navigate, submit resources, or infer absent leaderboard rows.
    ``rank_snapshot`` may carry the caller's freshly verified board so planning
    and the next batch's before-score use exactly the same observation. Its
    capture timestamp alone is not evidence of a server refresh.
    """
    identity = {"activity_id": activity_id, "pet_id": pet_id, "occurrence": occurrence}
    history = read_pet_resource_receipts(activity_id, occurrence)
    if any(r.get("status") == "submitted" for r in history):
        return {**identity, "status": "resource_action_pending"}
    if any(r.get("status") == "rank_observation_pending" for r in history):
        return {**identity, "status": "rank_calibration_pending"}
    rank = rank_snapshot if rank_snapshot is not None else read_activity_rank_runtime_snapshot(activity_id)
    board = validate_pet_rank_snapshot(rank, activity_id=activity_id)
    if board["status"] != "ready":
        return {**identity, **board, "rank": rank}
    samples = select_pet_rank_samples(history, **identity)
    if not any(r.get("item_id") != 8022009 for r in samples):
        return {**identity, "status": "ordinary_resource_sample_required"}
    pet_snapshot = read_pet_aptitude_runtime(expected_pet_id=pet_id)
    if not pet_snapshot.get("available") or not pet_snapshot.get("target"):
        return {**identity, "status": "pet_unavailable"}
    if pet_snapshot.get("pending_swallow_count"):
        return {**identity, "status": "pet_busy"}
    pet = pet_snapshot["target"]
    snapshot = collect_lingchong_jingwu_resource_snapshot(activity_id=str(activity_id))
    resources = [{"item_id": item.item_id, "item_name": item.name, "count": item.count,
                  "base_gain": sum(gain for gift, gain in item.aptitude_gain_by_gift_id.items()
                                   if pet["gift_remaining"].get(gift, 0) > 0)} for item in snapshot.items]
    estimate = estimate_pet_rank_inventory(resources, samples)
    if not estimate["complete"]:
        return {**identity, "status": "resource_estimate_incomplete", "estimate": estimate}
    tiers = load_activity_rank_reward_tiers(reward_activity_id=activity_id,
                                            event_date=occurrence, world_level=world_level)
    plan = plan_pet_rank_target_from_snapshot(rank, activity_id=activity_id,
        potential_gain=estimate["estimated_gain"],
        reward_boundaries=[r["rank_end"] for r in tiers])
    return {**identity, **plan, "current_score": rank["self_ranking"]["score"],
            "self_ranking": rank["self_ranking"], "estimate": estimate,
            "inventory_baseline_action_ids": [r["action_id"] for r in history
                if r.get("status") == "verified" and r.get("pet_id") == pet_id],
            "reward_boundaries": [r["rank_end"] for r in tiers],
            "rank_captured_at": rank.get("captured_at"),
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds")}


def replan_pet_rank_from_snapshot(plan: dict, rank: dict) -> dict:
    """Recompute only the rank target while loading rows, without re-reading inventory.

    Valid only before the next resource action. The caller's loading callback
    must not consume resources or switch the pet/activity occurrence.
    """
    result = plan_pet_rank_target_from_snapshot(rank, activity_id=plan["activity_id"],
        potential_gain=plan["estimate"]["estimated_gain"],
        reward_boundaries=plan["reward_boundaries"])
    metadata = {key: plan[key] for key in
                ("activity_id", "pet_id", "occurrence", "estimate", "reward_boundaries")}
    if "inventory_baseline_action_ids" in plan:
        metadata["inventory_baseline_action_ids"] = list(plan["inventory_baseline_action_ids"])
    return {**metadata, **result, "self_ranking": rank.get("self_ranking"),
            "current_score": (rank.get("self_ranking") or {}).get("score"),
            "rank_captured_at": rank.get("captured_at"),
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds")}
