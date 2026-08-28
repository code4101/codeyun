from __future__ import annotations

"""Activity-neutral feedback planning for bounded irreversible batches."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FeedbackBatchPlan:
    required_new_currency: int
    requested_challenges: int
    planning_mode: str


def plan_feedback_batch(
    *,
    required_new_currency: int,
    measured_currency_delta: int | None = None,
    measured_challenges: int | None = None,
    previous_currency_delta: int | None = None,
    previous_challenges: int | None = None,
    probe_challenges: int = 10,
    maximum_batch_challenges: int = 500,
    final_batch_threshold: int = 20,
    yield_stability_tolerance: float = 0.10,
) -> FeedbackBatchPlan:
    """Plan a probe, geometric half-batch, or stable final batch.

    The caller remains responsible for re-reading an authoritative absolute
    wallet after every irreversible batch.  Samples from another occurrence
    or process identity must never be supplied to this pure planner.
    """

    probe = int(probe_challenges)
    maximum = int(maximum_batch_challenges)
    if probe <= 0 or maximum <= 0 or probe > maximum:
        raise ValueError("反馈分批的探针次数与单批上限必须为有效正整数")

    gap = max(0, int(required_new_currency))
    if gap == 0:
        return FeedbackBatchPlan(0, 0, "target_reached")

    delta = int(measured_currency_delta or 0)
    attempts = int(measured_challenges or 0)
    if delta <= 0 or attempts <= 0:
        return FeedbackBatchPlan(gap, probe, "probe")

    estimated = (gap * attempts + delta - 1) // delta
    previous_delta = int(previous_currency_delta or 0)
    previous_attempts = int(previous_challenges or 0)
    stable = False
    if previous_delta > 0 and previous_attempts > 0:
        current_cross = delta * previous_attempts
        previous_cross = previous_delta * attempts
        denominator = max(current_cross, previous_cross)
        stable = denominator > 0 and (
            abs(current_cross - previous_cross) / denominator
            <= max(0.0, float(yield_stability_tolerance))
        )

    final_threshold = max(1, int(final_batch_threshold))
    if estimated <= final_threshold and stable:
        requested = min(maximum, max(1, estimated))
        return FeedbackBatchPlan(gap, requested, "stable_final")

    geometric = max(1, estimated // 2)
    requested = min(geometric, maximum)
    return FeedbackBatchPlan(
        gap,
        requested,
        "geometric_half" if requested == geometric else "capped_geometric_half",
    )


__all__ = ["FeedbackBatchPlan", "plan_feedback_batch"]
