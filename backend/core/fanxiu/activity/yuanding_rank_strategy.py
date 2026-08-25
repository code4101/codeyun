from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Iterable, Mapping, Sequence


@dataclass(frozen=True)
class YuandingRankTier:
    rank_start: int
    rank_end: int
    guard_score: int


@dataclass(frozen=True)
class YuandingCapacityPlan:
    current_rank: int
    current_score: int
    remaining_own_score: int
    theoretical_capacity: int
    conservative_capacity: int
    achievable_rank_end: int | None
    target_rank_end: int | None
    target_guard_score: int | None
    ordered_scores: tuple[int, ...]


@dataclass(frozen=True)
class YuandingDiscipleScoreObservation:
    captured_count: int
    expected_count: int | None
    captured_score_sum: int
    complete: bool


def normalize_disciple_scores(scores: Iterable[int]) -> tuple[int, ...]:
    """Return the positive, ascending score queue used by the event."""

    return tuple(sorted(int(score) for score in scores if int(score) > 0))


def cumulative_marriage_capacity(
    scores: Iterable[int],
    *,
    opponent_score_ratio: float = 1.0,
    event_score_multiplier: float = 1.0,
) -> tuple[int, ...]:
    """Project cumulative event score while consuming disciples low-to-high.

    A marriage adds the score of both disciples.  ``opponent_score_ratio`` is
    the expected opponent/own score ratio; 1.0 represents a similar-score
    match and therefore roughly doubles the remaining own score.
    """

    if opponent_score_ratio < 0:
        raise ValueError("opponent_score_ratio 不能小于 0")
    if event_score_multiplier <= 0:
        raise ValueError("event_score_multiplier 必须大于 0")
    total = 0
    result: list[int] = []
    for score in normalize_disciple_scores(scores):
        basic_score = score + round(score * opponent_score_ratio)
        total += int(basic_score * event_score_multiplier)
        result.append(total)
    return tuple(result)


def observe_disciple_scores(
    scores: Iterable[int],
    *,
    expected_count: int | None = None,
) -> YuandingDiscipleScoreObservation:
    """Describe whether a captured disciple score list is a full account view.

    Packet fragments are useful lower bounds, but must never be presented as
    the account total unless their count covers the expected unmarried count.
    """

    ordered = normalize_disciple_scores(scores)
    normalized_expected = (
        max(0, int(expected_count)) if expected_count is not None else None
    )
    return YuandingDiscipleScoreObservation(
        captured_count=len(ordered),
        expected_count=normalized_expected,
        captured_score_sum=sum(ordered),
        complete=(
            normalized_expected is not None
            and len(ordered) >= normalized_expected
        ),
    )


def infer_event_score_multiplier(*, basic_pair_score: int, observed_score: int) -> float | None:
    """Infer an event multiplier from one observed marriage score delta."""

    basic = int(basic_pair_score)
    observed = int(observed_score)
    if basic <= 0 or observed < 0:
        return None
    return observed / basic


def _normalized_tiers(
    tiers: Iterable[YuandingRankTier | Mapping[str, int]],
) -> tuple[YuandingRankTier, ...]:
    rows: list[YuandingRankTier] = []
    for raw in tiers:
        if isinstance(raw, YuandingRankTier):
            row = raw
        else:
            row = YuandingRankTier(
                rank_start=int(raw["rank_start"]),
                rank_end=int(raw["rank_end"]),
                guard_score=int(raw.get("guard_score") or 0),
            )
        if row.rank_start <= 0 or row.rank_end < row.rank_start:
            raise ValueError(f"排名档位非法：{row}")
        rows.append(row)
    rows.sort(key=lambda row: (row.rank_end, row.rank_start))
    return tuple(rows)


def plan_yuanding_rank_capacity(
    *,
    current_rank: int,
    current_score: int,
    remaining_disciple_scores: Iterable[int],
    reward_tiers: Iterable[YuandingRankTier | Mapping[str, int]],
    opponent_score_ratio: float = 1.0,
    event_score_multiplier: float = 1.0,
    realization_ratio: float = 0.8,
    safety_tier_offset: int = 1,
) -> YuandingCapacityPlan:
    """Estimate the attainable tier and choose a safer target tier.

    The theoretical ceiling assumes every remaining own disciple marries an
    opponent at ``opponent_score_ratio``.  The conservative ceiling discounts
    that result for imperfect matching and late leaderboard growth.  The
    default target is one reward tier below the best conservatively attainable
    tier: if rank 17-32 is attainable, target rank 33-64.
    """

    if not 0 < realization_ratio <= 1:
        raise ValueError("realization_ratio 必须在 (0, 1] 内")
    if safety_tier_offset < 0:
        raise ValueError("safety_tier_offset 不能小于 0")

    ordered_scores = normalize_disciple_scores(remaining_disciple_scores)
    remaining_own_score = sum(ordered_scores)
    projected_gain = round(
        remaining_own_score
        * (1 + opponent_score_ratio)
        * event_score_multiplier
    )
    theoretical_capacity = int(current_score) + projected_gain
    conservative_capacity = int(current_score) + round(projected_gain * realization_ratio)
    tiers = _normalized_tiers(reward_tiers)

    attainable_indexes = [
        index
        for index, tier in enumerate(tiers)
        if tier.guard_score > 0 and tier.guard_score <= conservative_capacity
    ]
    achievable_index = min(attainable_indexes) if attainable_indexes else None
    target_index = (
        min(len(tiers) - 1, achievable_index + safety_tier_offset)
        if achievable_index is not None and tiers
        else None
    )
    achievable = tiers[achievable_index] if achievable_index is not None else None
    target = tiers[target_index] if target_index is not None else None
    return YuandingCapacityPlan(
        current_rank=max(0, int(current_rank)),
        current_score=max(0, int(current_score)),
        remaining_own_score=remaining_own_score,
        theoretical_capacity=theoretical_capacity,
        conservative_capacity=conservative_capacity,
        achievable_rank_end=achievable.rank_end if achievable else None,
        target_rank_end=target.rank_end if target else None,
        target_guard_score=target.guard_score if target else None,
        ordered_scores=ordered_scores,
    )


def marriages_needed_for_score(
    *,
    current_score: int,
    target_score: int,
    ordered_disciple_scores: Sequence[int],
    opponent_score_ratio: float = 1.0,
    event_score_multiplier: float = 1.0,
) -> int:
    """Return the smallest low-to-high prefix needed for ``target_score``."""

    gap = max(0, int(target_score) - int(current_score))
    if gap == 0:
        return 0
    projected = cumulative_marriage_capacity(
        ordered_disciple_scores,
        opponent_score_ratio=opponent_score_ratio,
        event_score_multiplier=event_score_multiplier,
    )
    for index, score in enumerate(projected, start=1):
        if score >= gap:
            return index
    return len(projected)


def conservative_single_step_batch(
    *,
    current_rank: int,
    target_rank: int,
    max_batch: int = 1,
) -> int:
    """Rank pushing rechecks the live board after every small batch."""

    if int(current_rank) > 0 and int(current_rank) <= int(target_rank):
        return 0
    return max(1, min(int(max_batch), 1))


__all__ = [
    "YuandingCapacityPlan",
    "YuandingDiscipleScoreObservation",
    "YuandingRankTier",
    "conservative_single_step_batch",
    "cumulative_marriage_capacity",
    "infer_event_score_multiplier",
    "marriages_needed_for_score",
    "normalize_disciple_scores",
    "observe_disciple_scores",
    "plan_yuanding_rank_capacity",
]
