from __future__ import annotations

"""Explicit Magic R&D flow: initialize one occurrence, then claim task rewards."""

from datetime import datetime
import threading
from typing import Any, Iterable, Iterator

from backend.core.fanxiu.activity.ranking_lifecycle import (
    RankingOccurrence,
    discover_ranking_occurrences,
)
from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.data_annotation.schedule_navigation import (
    select_schedule_activity,
)
from backend.core.fanxiu.data_annotation.tasks.magic_invasion import (
    wait_magic_invasion_cover_after_schedule_entry,
)
MAGIC_HOME_SCENE = 509


def select_unique_open_magic_occurrence(
    occurrences: Iterable[RankingOccurrence],
    *,
    now: datetime,
) -> RankingOccurrence:
    """Return the sole open Magic instance; never guess between server/cross rows."""

    if now.tzinfo is None:
        raise ValueError("魔道初始化与奖励研发要求带时区的当前时间")
    matches = tuple(
        occurrence
        for occurrence in occurrences
        if occurrence.family == "gameplay_rank"
        and occurrence.activity_type == "magic-invasion"
        and occurrence.start_at <= now <= occurrence.end_at
    )
    if len(matches) != 1:
        identities = ",".join(
            f"{item.runtime_id}/cross={item.cross_count}" for item in matches
        )
        raise RuntimeError(
            "魔道初始化与奖励研发无法唯一定位当前开放实例："
            f"matches={len(matches)}"
            + (f" [{identities}]" if identities else "")
        )
    return matches[0]


def _read_unique_open_occurrence(*, now: datetime) -> tuple[RankingOccurrence, dict[str, Any]]:
    from backend.core.fanxiu.activity.runtime_schedule import (
        read_fanxiu_activity_runtime_schedule,
    )

    schedule = read_fanxiu_activity_runtime_schedule(
        allow_discovery=True,
        force_refresh=True,
    )
    if not bool(schedule.get("available") and schedule.get("complete")):
        raise RuntimeError("魔道初始化与奖励研发：Runtime 日程不可用或不完整")
    occurrence = select_unique_open_magic_occurrence(
        discover_ranking_occurrences(schedule),
        now=now,
    )
    return occurrence, dict(schedule)


def execute_magic_invasion_initialization_rewards_rnd_cell(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
) -> Iterator[Any]:
    """Initialize and claim rewards only; this entry never challenges or purchases."""

    from backend.core.fanxiu.data_annotation.tasks.magic_invasion_initialization import (
        execute_magic_invasion_initialization_checkpoint,
    )
    from backend.core.fanxiu.data_annotation.tasks.magic_invasion_task_rewards import (
        claim_magic_invasion_task_rewards,
    )

    del payload
    started_at = job_now()
    if started_at.tzinfo is None:
        started_at = started_at.astimezone()
    occurrence, _schedule = _read_unique_open_occurrence(now=started_at)

    initialization = yield from execute_magic_invasion_initialization_checkpoint(
        runner,
        ctx,
        stop_event,
        occurrence=occurrence,
        captured_at=started_at,
        required_fact_watermark=occurrence.start_at.replace(
            hour=0,
            minute=30,
            second=0,
            microsecond=0,
        ),
    )

    reward_at = job_now()
    if reward_at.tzinfo is None:
        reward_at = reward_at.astimezone()
    reward_occurrence, schedule = _read_unique_open_occurrence(now=reward_at)
    if reward_occurrence.instance_key != occurrence.instance_key:
        raise RuntimeError("魔道初始化完成后当前开放实例发生切换，拒绝跨实例领取奖励")

    context = runner._behavior_tree_context(
        ctx,
        ctx.get("asset_tree_path"),
        stop_event=stop_event,
    )
    yield from context.go_scene(66)
    selected = yield from select_schedule_activity(
        context,
        r"魔道入侵",
        day_offset=(occurrence.start_at.date() - reward_at.date()).days,
        enter=True,
        runtime_schedule=schedule,
        require_runtime_alignment=True,
        expected_activity_id=int(occurrence.activity_id),
        expected_runtime_id=str(occurrence.runtime_id),
        expected_cross_count=int(occurrence.cross_count),
        now=reward_at,
    )
    if not str(getattr(selected, "runtime_key", "") or ""):
        raise RuntimeError("魔道领取任务奖励未回读精确 Runtime 实例标识")
    landed = yield from wait_magic_invasion_cover_after_schedule_entry(
        context,
        [MAGIC_HOME_SCENE],
        wait_seconds=30.0,
        label="魔道初始化后等待活动主页领取任务",
    )
    if int(getattr(landed, "scene_id", landed)) != MAGIC_HOME_SCENE:
        raise RuntimeError("魔道初始化后未进入活动主页，拒绝领取任务奖励")
    rewards = yield from claim_magic_invasion_task_rewards(
        context,
        activity_id=occurrence.activity_id,
    )
    return {
        "status": "completed",
        "occurrence": {
            "instance_key": occurrence.instance_key,
            "runtime_id": occurrence.runtime_id,
            "activity_id": occurrence.activity_id,
            "cross_count": occurrence.cross_count,
        },
        "initialization": initialization,
        "task_rewards": rewards,
        "challenge_performed": False,
        "exchange_purchase_performed": False,
        "message": (
            f"魔道 occurrence {occurrence.runtime_id}：初始化与任务奖励处理完成；"
            "未执行挑战或兑换购买"
        ),
    }


__all__ = [
    "execute_magic_invasion_initialization_rewards_rnd_cell",
    "select_unique_open_magic_occurrence",
]
