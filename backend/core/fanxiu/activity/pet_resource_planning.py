"""Pure batch and ranking policy for the taught pet-resource workflow."""

from math import ceil, floor, isfinite
from typing import Iterable, Mapping, Sequence


SEED_ALLOWANCE = 30


def _row_int(row, name: str) -> int:
    value = row.get(name) if isinstance(row, Mapping) else getattr(row, name, 0)
    return int(value or 0)


def order_pet_resources_low_to_high(resources: Iterable) -> list:
    """Low-to-high selection order, verified against the live Item rows.

    Quality ascends (2 singles, then 3..8 pills); within one quality the
    larger ``num`` (``sort_order``) is spent first so the order never depends
    on config iteration order. Works for mappings and resource models alike.
    """
    return sorted(
        resources,
        key=lambda row: (_row_int(row, "quality"), -_row_int(row, "sort_order"),
                         _row_int(row, "item_id")),
    )


def plan_pet_probe_bulk_tail(*, gap: float, available: int,
                             sample_task_gain: float | None = None,
                             remaining_cap: int | None = None) -> dict:
    """probe(1) -> bulk(~50% of estimated remainder) -> tail(1).

    ``sample_task_gain`` is this exact resource's measured per-unit task gain
    for this activity instance and pet; another resource's sample or an
    aptitude gain is never used here. A resource without a valid sample always
    probes with exactly one unit. With a sample, the estimated remaining units
    are ``ceil(gap / sample_task_gain)``; at most 3 remaining means one-unit
    tail batches, otherwise a conservative floor(50%) bulk. A single measured
    multiplier is a batch-size estimate, never a completion guarantee, so the
    caller must re-read the real progress after every batch.
    """
    if not isfinite(gap) or gap < 0 or available < 0:
        raise ValueError("Invalid gap or inventory")
    if remaining_cap is not None and (
        isinstance(remaining_cap, bool) or not isinstance(remaining_cap, int) or remaining_cap < 0
    ):
        raise ValueError("Invalid remaining cap")
    if sample_task_gain is not None and (
        not isfinite(sample_task_gain) or sample_task_gain <= 0
    ):
        raise ValueError("Measured per-unit task gain must be positive")
    cap = available if remaining_cap is None else min(available, remaining_cap)
    if gap <= 0:
        return {"quantity": 0, "mode": "complete", "remaining_units": 0}
    if cap <= 0:
        return {"quantity": 0, "mode": "empty", "remaining_units": None}
    if sample_task_gain is None:
        return {"quantity": 1, "mode": "probe", "remaining_units": None}
    remaining_units = ceil(gap / sample_task_gain)
    if remaining_units <= 3:
        return {"quantity": 1, "mode": "tail", "remaining_units": remaining_units}
    quantity = max(1, floor(remaining_units * 0.5))
    return {"quantity": min(quantity, cap), "mode": "bulk",
            "remaining_units": remaining_units}


def plan_initialization_batch(*, gap: float, seed_option: Mapping | None,
                              supplement_options: Iterable[Mapping], seed_used: int,
                              allowance: int = SEED_ALLOWANCE,
                              samples: Mapping | None = None) -> dict:
    """Pick exactly one batch: seed first while budget remains, else low->high.

    ``seed_option`` is the usable seed (``resource_id``/``base_gain``/
    ``available``) or None. ``supplement_options`` must already be ordered low
    to high quality and carry ``resource_id``/``base_gain``/``available``.
    ``samples`` maps a resource id to its measured per-unit task gain; a
    resource without its own sample probes with one unit. The seed keeps
    priority even though it is high quality; only an unusable or exhausted
    seed budget falls through to the ordered supplement list. The seed's
    remaining allowance caps its batch alongside inventory.
    """
    if not isfinite(gap) or gap < 0 or seed_used < 0 or allowance < 0:
        raise ValueError("Invalid gap, seed usage or allowance")
    if gap <= 0:
        return {"resource_id": None, "base_gain": 0, "quantity": 0, "mode": "complete"}
    measured = samples or {}
    if seed_option is not None:
        resource_id = _row_int(seed_option, "resource_id")
        plan = plan_pet_probe_bulk_tail(
            gap=gap, available=_row_int(seed_option, "available"),
            sample_task_gain=measured.get(resource_id),
            remaining_cap=max(0, allowance - seed_used),
        )
        if plan["quantity"] > 0:
            return {"resource_id": resource_id, "base_gain": _row_int(seed_option, "base_gain"),
                    "quantity": plan["quantity"], "mode": plan["mode"]}
    for option in supplement_options:
        resource_id = _row_int(option, "resource_id")
        available = _row_int(option, "available")
        base_gain = _row_int(option, "base_gain")
        if resource_id <= 0 or available <= 0 or base_gain <= 0:
            continue
        plan = plan_pet_probe_bulk_tail(
            gap=gap, available=available,
            sample_task_gain=measured.get(resource_id),
        )
        if plan["quantity"] > 0:
            return {"resource_id": resource_id, "base_gain": base_gain,
                    "quantity": plan["quantity"], "mode": plan["mode"]}
    return {"resource_id": None, "base_gain": 0, "quantity": 0, "mode": "exhausted"}


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
