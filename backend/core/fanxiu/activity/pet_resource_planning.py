"""Pure batch and ranking policy for the taught pet-resource workflow."""

from math import ceil, floor, isfinite
from typing import Mapping, Sequence


def plan_pet_resource_batch(*, gap: float, base_gain: int, available: int,
                            measured_multiplier: float | None = None) -> dict:
    """20% first probe, then 80% feedback; clip to inventory after planning."""
    if not isfinite(gap) or base_gain <= 0 or available < 0:
        raise ValueError("Invalid gap, base gain or inventory")
    if measured_multiplier is not None and (not isfinite(measured_multiplier) or measured_multiplier <= 0):
        raise ValueError("Measured multiplier must be positive")
    if gap <= 0 or available == 0:
        return {"quantity": 0, "mode": "complete" if gap <= 0 else "empty"}
    remaining_base = gap / (measured_multiplier or 1)
    if remaining_base <= 100:
        planned_base, mode = remaining_base, "tail"
    else:
        planned_base = max(100, remaining_base * (0.2 if measured_multiplier is None else 0.8))
        mode = "probe" if measured_multiplier is None else "feedback"
    quantity = min(available, ceil(planned_base / base_gain))
    return {"quantity": quantity, "base_total": quantity * base_gain, "mode": mode,
            "inventory_limited": quantity < ceil(planned_base / base_gain)}


def plan_pet_ranking_target(*, current_score: float, potential_gain: float,
                            rank_scores: Mapping[int, float], reward_boundaries: Sequence[int]) -> dict:
    """Reachable tier a, target b=a+1, actual leaderboard interpolation at 1/3."""
    if any(not isfinite(v) or v < 0 for v in (current_score, potential_gain)):
        raise ValueError("Scores must be finite and nonnegative")
    boundaries = list(reward_boundaries)
    if not boundaries or boundaries != sorted(set(boundaries)) or boundaries[0] < 1:
        raise ValueError("Reward boundaries must be strictly increasing")
    potential = current_score + potential_gain
    a = None
    for index, boundary in enumerate(boundaries):
        if boundary not in rank_scores:
            return {"status": "need_rank_rows", "missing_ranks": [boundary], "potential": potential}
        if potential >= rank_scores[boundary]:
            a = index
            break
    if a is None or a + 1 == len(boundaries):
        return {"status": "no_lower_reward_tier", "potential": potential}
    ra, rb = boundaries[a:a + 2]
    position = rb - (rb - ra) / 3
    lower, upper = floor(position), ceil(position)
    missing = [r for r in {lower, upper} if r not in rank_scores]
    if missing:
        return {"status": "need_rank_rows", "missing_ranks": sorted(missing), "potential": potential}
    weight = position - lower
    target = (1 - weight) * rank_scores[lower] + weight * rank_scores[upper]
    gap = target - current_score
    return {"status": "complete" if gap <= 0 else "act", "potential": potential,
            "reachable_boundary": ra, "target_boundary": rb, "target_position": position,
            "target_score": target, "gap": gap}
