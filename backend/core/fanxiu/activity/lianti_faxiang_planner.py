from __future__ import annotations

"""Fail-closed resource plan for the current Lianti Faxiang task ladder.

This module is deliberately pure.  It does not read Runtime state and never
performs GUI actions.  Callers must first build one fresh, instance-bound fact
set from the current ``QuestEntryVO`` membership, activity score, backpack and
the current physical-body breakthrough path.
"""

from dataclasses import dataclass
from datetime import datetime
from math import ceil
from typing import Literal


LIANTI_FAXIANG_ACTIVITY_ID = 1043011
TEMPERING_ESSENCE_ITEM_ID = 5030001
BREAKTHROUGH_ESSENCE_ITEM_ID = 5030002
SCORE_PER_TEMPERING_ESSENCE = 100

PlanKind = Literal["use", "complete", "blocked"]


def _aware_datetime(value: str, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label}时间格式无效") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label}必须包含时区")
    return parsed


@dataclass(frozen=True)
class LiantiTaskMilestone:
    """One ActiveTask row that may be joined by current QuestEntryVO ids."""

    task_id: int
    target: int
    order: int
    must_get: bool
    condition: str = "PhysicalFightScore"

    def __post_init__(self) -> None:
        if self.task_id <= 0 or self.target <= 0 or self.order <= 0:
            raise ValueError("炼体任务配置字段无效")


@dataclass(frozen=True)
class LiantiQuestEntry:
    """The current period's decoded QuestEntryVO progress for one task."""

    task_id: int
    target: int
    progress: int
    finished: bool = False

    def __post_init__(self) -> None:
        if (
            self.task_id <= 0
            or self.target <= 0
            or self.progress < 0
            or self.progress > self.target
        ):
            raise ValueError("炼体 QuestEntryVO 字段无效")


@dataclass(frozen=True)
class LiantiResourceFacts:
    """Fresh facts required before any irreversible resource use is planned."""

    activity_instance_id: str
    activity_id: int
    quest_entries_complete: bool
    current_score: int
    tempering_essence_count: int
    breakthrough_essence_count: int
    # Must be computed from the current physical-body state to the chosen
    # target.  ``None`` means that the breakthrough path has not been proven.
    breakthroughs_required_to_target: int | None
    quest_captured_at: str
    score_captured_at: str
    inventory_captured_at: str

    def __post_init__(self) -> None:
        if (
            not self.activity_instance_id
            or self.activity_id <= 0
            or self.current_score < 0
            or self.tempering_essence_count < 0
            or self.breakthrough_essence_count < 0
            or (
                self.breakthroughs_required_to_target is not None
                and self.breakthroughs_required_to_target < 0
            )
        ):
            raise ValueError("炼体资源事实字段无效")


@dataclass(frozen=True)
class LiantiResourceUsePlan:
    kind: PlanKind
    reason: str
    activity_instance_id: str
    current_task_ids: tuple[int, ...]
    must_get_task_ids: tuple[int, ...]
    target_task_id: int | None
    target_score: int
    current_score: int
    remaining_score: int
    tempering_essence_available: int
    breakthrough_essence_available: int
    breakthroughs_required: int | None
    tempering_essence_to_use: int = 0
    score_per_item: int = SCORE_PER_TEMPERING_ESSENCE
    expected_score_after: int = 0


def _blocked(
    reason: str,
    *,
    facts: LiantiResourceFacts,
    current_task_ids: tuple[int, ...] = (),
    must_get_task_ids: tuple[int, ...] = (),
    target_task_id: int | None = None,
    target_score: int = 0,
) -> LiantiResourceUsePlan:
    return LiantiResourceUsePlan(
        kind="blocked",
        reason=reason,
        activity_instance_id=facts.activity_instance_id,
        current_task_ids=current_task_ids,
        must_get_task_ids=must_get_task_ids,
        target_task_id=target_task_id,
        target_score=target_score,
        current_score=facts.current_score,
        remaining_score=max(0, target_score - facts.current_score),
        tempering_essence_available=facts.tempering_essence_count,
        breakthrough_essence_available=facts.breakthrough_essence_count,
        breakthroughs_required=facts.breakthroughs_required_to_target,
        expected_score_after=facts.current_score,
    )


def plan_lianti_must_get_resource_use(
    *,
    facts: LiantiResourceFacts,
    configured_milestones: tuple[LiantiTaskMilestone, ...],
    quest_entries: tuple[LiantiQuestEntry, ...],
    now: str,
    max_fact_age_seconds: int = 120,
    max_fact_skew_seconds: int = 30,
) -> LiantiResourceUsePlan:
    """Plan only enough 5030001 to reach this period's highest ``必拿`` tier.

    Static ActiveTask rows only form the join domain.  Membership always comes
    from the complete current QuestEntryVO set, so an older retained ladder can
    never select the target by itself.
    """

    if max_fact_age_seconds <= 0 or max_fact_skew_seconds < 0:
        raise ValueError("事实新鲜度窗口无效")
    if facts.activity_id != LIANTI_FAXIANG_ACTIVITY_ID:
        return _blocked("活动身份不是炼体法相 1043011", facts=facts)
    if not facts.quest_entries_complete:
        return _blocked("本期 QuestEntryVO 任务集合不完整", facts=facts)
    if not quest_entries:
        return _blocked("本期 QuestEntryVO 任务集合为空", facts=facts)

    task_ids = tuple(sorted(entry.task_id for entry in quest_entries))
    if len(set(task_ids)) != len(task_ids):
        return _blocked("本期 QuestEntryVO 含重复任务 ID", facts=facts)
    configs = {row.task_id: row for row in configured_milestones}
    if len(configs) != len(configured_milestones):
        return _blocked(
            "ActiveTask 候选含重复任务 ID", facts=facts, current_task_ids=task_ids
        )
    missing = tuple(task_id for task_id in task_ids if task_id not in configs)
    if missing:
        return _blocked(
            f"本期 QuestEntryVO 任务无法完整连接 ActiveTask：{missing}",
            facts=facts,
            current_task_ids=task_ids,
        )

    selected = tuple(configs[task_id] for task_id in task_ids)
    if any(row.condition != "PhysicalFightScore" for row in selected):
        return _blocked(
            "本期任务条件不是唯一的 PhysicalFightScore",
            facts=facts,
            current_task_ids=task_ids,
        )
    entry_by_id = {entry.task_id: entry for entry in quest_entries}
    for row in selected:
        entry = entry_by_id[row.task_id]
        if entry.target != row.target:
            return _blocked(
                f"任务 {row.task_id} 的 Runtime/配置目标不一致",
                facts=facts,
                current_task_ids=task_ids,
            )
        expected_progress = min(facts.current_score, row.target)
        if entry.progress != expected_progress or entry.finished != (
            facts.current_score >= row.target
        ):
            return _blocked(
                f"任务 {row.task_id} 进度与实时炼体积分不一致",
                facts=facts,
                current_task_ids=task_ids,
            )

    must_get = tuple(sorted((row for row in selected if row.must_get), key=lambda row: row.target))
    must_get_ids = tuple(row.task_id for row in must_get)
    if not must_get:
        return _blocked(
            "本期 QuestEntryVO 对应任务没有明确必拿档",
            facts=facts,
            current_task_ids=task_ids,
        )
    target = must_get[-1]

    try:
        now_dt = _aware_datetime(now, label="当前")
        timestamps = (
            _aware_datetime(facts.quest_captured_at, label="任务"),
            _aware_datetime(facts.score_captured_at, label="积分"),
            _aware_datetime(facts.inventory_captured_at, label="背包"),
        )
    except ValueError as exc:
        return _blocked(
            str(exc),
            facts=facts,
            current_task_ids=task_ids,
            must_get_task_ids=must_get_ids,
            target_task_id=target.task_id,
            target_score=target.target,
        )
    ages = tuple((now_dt - value).total_seconds() for value in timestamps)
    if any(age < 0 or age > max_fact_age_seconds for age in ages):
        return _blocked(
            "任务、积分或背包事实已过期",
            facts=facts,
            current_task_ids=task_ids,
            must_get_task_ids=must_get_ids,
            target_task_id=target.task_id,
            target_score=target.target,
        )
    skew = (max(timestamps) - min(timestamps)).total_seconds()
    if skew > max_fact_skew_seconds:
        return _blocked(
            "任务、积分与背包事实不在同一采集窗口",
            facts=facts,
            current_task_ids=task_ids,
            must_get_task_ids=must_get_ids,
            target_task_id=target.task_id,
            target_score=target.target,
        )

    remaining = max(0, target.target - facts.current_score)
    common = {
        "activity_instance_id": facts.activity_instance_id,
        "current_task_ids": task_ids,
        "must_get_task_ids": must_get_ids,
        "target_task_id": target.task_id,
        "target_score": target.target,
        "current_score": facts.current_score,
        "remaining_score": remaining,
        "tempering_essence_available": facts.tempering_essence_count,
        "breakthrough_essence_available": facts.breakthrough_essence_count,
        "breakthroughs_required": facts.breakthroughs_required_to_target,
    }
    if remaining == 0:
        return LiantiResourceUsePlan(
            kind="complete",
            reason="本期最高必拿档已达到，幂等停止",
            tempering_essence_to_use=0,
            expected_score_after=facts.current_score,
            **common,
        )
    if facts.breakthroughs_required_to_target is None:
        return _blocked(
            "尚未证明当前炼体状态到目标所需的突破次数",
            facts=facts,
            current_task_ids=task_ids,
            must_get_task_ids=must_get_ids,
            target_task_id=target.task_id,
            target_score=target.target,
        )
    if facts.breakthrough_essence_count < facts.breakthroughs_required_to_target:
        return _blocked(
            "龙髓精魄不足以覆盖到必拿档的突破路径",
            facts=facts,
            current_task_ids=task_ids,
            must_get_task_ids=must_get_ids,
            target_task_id=target.task_id,
            target_score=target.target,
        )

    quantity = ceil(remaining / SCORE_PER_TEMPERING_ESSENCE)
    if facts.tempering_essence_count < quantity:
        return _blocked(
            "淬体精魄不足以达到本期最高必拿档",
            facts=facts,
            current_task_ids=task_ids,
            must_get_task_ids=must_get_ids,
            target_task_id=target.task_id,
            target_score=target.target,
        )
    return LiantiResourceUsePlan(
        kind="use",
        reason="只补到本期 QuestEntryVO 明确标记的最高必拿档；动作后必须重采集",
        tempering_essence_to_use=quantity,
        expected_score_after=facts.current_score
        + quantity * SCORE_PER_TEMPERING_ESSENCE,
        **common,
    )


__all__ = [
    "BREAKTHROUGH_ESSENCE_ITEM_ID",
    "LIANTI_FAXIANG_ACTIVITY_ID",
    "SCORE_PER_TEMPERING_ESSENCE",
    "TEMPERING_ESSENCE_ITEM_ID",
    "LiantiQuestEntry",
    "LiantiResourceFacts",
    "LiantiResourceUsePlan",
    "LiantiTaskMilestone",
    "plan_lianti_must_get_resource_use",
]
