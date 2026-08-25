from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


MAGIC_INVASION_FAMILY_KEY = "magic-invasion"
MAGIC_INVASION_EXPLORE_BATCH_SIZE = 500
MAGIC_INVASION_TARGET_BATCHES = 3
WHITE_DRAGON_EFFECT_ALIASES = (
    "御灵·白龙马",
    "白龙马",
    "小白龙",
    "神驹",
    "白龙",
)


@dataclass(frozen=True)
class MagicInvasionExploreEvidence:
    """One confirmed base explore action, independent of UI bonus results."""

    base_explore_count: int
    family_key: str = MAGIC_INVASION_FAMILY_KEY
    activity_instance_id: str = ""
    run_id: str = ""
    result_explore_count: int | None = None


@dataclass(frozen=True)
class MagicInvasionExplorePlan:
    completed_batches: int
    remaining_batches: int
    current_map_count: int
    topup_count: int
    should_explore: bool
    gameplay_available: bool
    blocked_by_manual_gameplay: bool
    reason: str


@dataclass(frozen=True)
class MagicInvasionEffectObservation:
    effect_observed: bool
    matched_alias: str | None
    direct_score_delta: int
    direct_currency_delta: int
    manual_challenge_required: bool
    rewards_complete: bool


def completed_magic_invasion_batches(
    evidence: Iterable[MagicInvasionExploreEvidence],
    *,
    activity_instance_id: str,
    batch_size: int = MAGIC_INVASION_EXPLORE_BATCH_SIZE,
) -> int:
    """Count confirmed base batches for one exact Runtime occurrence.

    ``result_explore_count`` deliberately does not participate: values such as
    834/854 include mount and activity bonuses while the base action remains
    one 500-count batch. Server-internal and cross-server occurrences share
    code, never progress: each occurrence independently owes 1500 explores.
    """

    if batch_size <= 0:
        raise ValueError("batch_size 必须为正数")
    occurrence = str(activity_instance_id or "").strip()
    if not occurrence:
        raise ValueError("activity_instance_id 不能为空")
    confirmed_total = sum(
        max(0, int(item.base_explore_count))
        for item in evidence
        if item.family_key == MAGIC_INVASION_FAMILY_KEY
        and str(item.activity_instance_id or "").strip() == occurrence
    )
    return confirmed_total // batch_size


def plan_magic_invasion_explore(
    evidence: Iterable[MagicInvasionExploreEvidence],
    *,
    activity_instance_id: str,
    current_map_count: int,
    target_batches: int = MAGIC_INVASION_TARGET_BATCHES,
    batch_size: int = MAGIC_INVASION_EXPLORE_BATCH_SIZE,
    gameplay_available: bool = False,
) -> MagicInvasionExplorePlan:
    if current_map_count < 0:
        raise ValueError("current_map_count 不能为负数")
    completed = completed_magic_invasion_batches(
        evidence,
        activity_instance_id=activity_instance_id,
        batch_size=batch_size,
    )
    remaining = max(0, int(target_batches) - completed)
    blocked = remaining > 0 and not bool(gameplay_available)
    should_explore = remaining > 0 and not blocked
    topup = max(0, int(batch_size) - int(current_map_count)) if should_explore else 0
    if remaining == 0:
        reason = f"当前魔道入侵实例已完成 {completed}/{int(target_batches)} 个基础批次"
    elif blocked:
        reason = "用户/手动挑战正在占用游戏画面，计划保留但禁止点击"
    else:
        reason = f"还需 {remaining} 个基础批次；当前地图 {int(current_map_count)}，补 {topup}"
    return MagicInvasionExplorePlan(
        completed_batches=completed,
        remaining_batches=remaining,
        current_map_count=int(current_map_count),
        topup_count=topup,
        should_explore=should_explore,
        gameplay_available=bool(gameplay_available),
        blocked_by_manual_gameplay=blocked,
        reason=reason,
    )


def actual_magic_invasion_topup(*, inventory_before: int, inventory_after: int) -> int:
    """Use the authoritative inventory delta instead of quantity-dialog OCR."""

    actual = int(inventory_before) - int(inventory_after)
    if actual < 0:
        raise ValueError("天眼符库存增加，不能解释为探查补充")
    return actual


def observe_magic_invasion_effect(
    *,
    result_text: str = "",
    score_before: int,
    score_after: int,
    currency_before: int,
    currency_after: int,
    challenges_completed: int,
    pending_event_count: int,
) -> MagicInvasionEffectObservation:
    """Classify direct settlement without claiming all encounters are settled."""

    matched_alias = next(
        (alias for alias in WHITE_DRAGON_EFFECT_ALIASES if alias in result_text),
        None,
    )
    score_delta = max(0, int(score_after) - int(score_before))
    currency_delta = max(0, int(currency_after) - int(currency_before))
    direct_without_challenge = (
        int(challenges_completed) == 0 and (score_delta > 0 or currency_delta > 0)
    )
    observed = matched_alias is not None or direct_without_challenge
    manual_required = int(pending_event_count) > 0
    return MagicInvasionEffectObservation(
        effect_observed=observed,
        matched_alias=matched_alias,
        direct_score_delta=score_delta,
        direct_currency_delta=currency_delta,
        manual_challenge_required=manual_required,
        rewards_complete=observed and not manual_required,
    )


__all__ = [
    "MAGIC_INVASION_EXPLORE_BATCH_SIZE",
    "MAGIC_INVASION_FAMILY_KEY",
    "MAGIC_INVASION_TARGET_BATCHES",
    "WHITE_DRAGON_EFFECT_ALIASES",
    "MagicInvasionEffectObservation",
    "MagicInvasionExploreEvidence",
    "MagicInvasionExplorePlan",
    "actual_magic_invasion_topup",
    "completed_magic_invasion_batches",
    "observe_magic_invasion_effect",
    "plan_magic_invasion_explore",
]
