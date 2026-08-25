from __future__ import annotations

"""Reusable, QuestMgr-authorized 丹道问鼎 task reward job."""

from datetime import datetime, timedelta
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from backend.core.fanxiu.activity.daily_activity_discovery import DEFAULT_TIMEZONE
from backend.core.fanxiu.activity.daily_activity_sync import (
    load_worldline_activity_schedule_snapshot,
)
from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.data_annotation.tasks.resource_rank_daily_gift import (
    RESOURCE_RANK_GIFT_ADAPTERS,
    active_resource_rank_gift_adapters,
    open_resource_rank_activity_page,
)
from backend.core.fanxiu.instrumentation.dandao_task_rewards import (
    read_dandao_task_reward_snapshot,
)


DANDAO_TASK_REWARDS_TASK_TYPE = "dandao_task_rewards"
DANDAO_TASK_REWARDS_TASK_ID = "dandao-task-rewards"
DANDAO_TASK_REWARDS_LABEL = "丹道_任务奖励"
DANDAO_TASK_REWARDS_SCENE_ID = 598
DANDAO_TASK_REWARDS_CLAIM_SHAPE = "首条任务领取区"
DANDAO_TASK_REWARDS_TRIGGER = (18, 10)


def next_dandao_task_reward_time(
    now: datetime | None = None,
    *,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> datetime:
    zone = ZoneInfo(timezone_name)
    current = now or datetime.now(zone)
    current = current.replace(tzinfo=zone) if current.tzinfo is None else current.astimezone(zone)
    candidate = current.replace(
        hour=DANDAO_TASK_REWARDS_TRIGGER[0],
        minute=DANDAO_TASK_REWARDS_TRIGGER[1],
        second=0,
        microsecond=0,
    )
    if candidate <= current:
        candidate += timedelta(days=1)
    return candidate


def _next_pending_check_time(now: datetime) -> datetime:
    return now + timedelta(minutes=30)


def _active_dandao_adapter(now: datetime):
    active = active_resource_rank_gift_adapters(
        load_worldline_activity_schedule_snapshot(),
        now=now,
        adapters=RESOURCE_RANK_GIFT_ADAPTERS,
    )
    dandao = [(adapter, activity_id) for adapter, activity_id in active if adapter.key == "dandao-wending"]
    if len(dandao) > 1:
        raise RuntimeError(f"{DANDAO_TASK_REWARDS_LABEL}：同时开放 {len(dandao)} 个丹道活动实例")
    return dandao[0] if dandao else None


def _require_complete_snapshot(snapshot: Mapping[str, Any]) -> None:
    if not snapshot.get("ok") or not snapshot.get("complete"):
        raise RuntimeError(
            f"{DANDAO_TASK_REWARDS_LABEL}：QuestMgr 状态不完整："
            f"{snapshot.get('reason') or 'unknown'}"
        )


def _verify_one_claim(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    task_id: int,
) -> None:
    _require_complete_snapshot(after)
    before_claimed = {int(value) for value in before.get("claimed_task_ids") or []}
    after_claimed = {int(value) for value in after.get("claimed_task_ids") or []}
    if after_claimed - before_claimed != {int(task_id)}:
        raise RuntimeError(
            f"{DANDAO_TASK_REWARDS_LABEL}：点击任务 {task_id} 后 QuestMgr 未确认精确单档领取"
        )


def run_dandao_task_rewards_flow(
    runtime: Any,
    *,
    now: datetime | None = None,
    max_claims: int = 20,
    manage_schedule: bool = False,
    include_schedule_hint: bool = True,
) -> dict[str, Any]:
    def result_with_optional_schedule_hint(result: dict[str, Any]) -> dict[str, Any]:
        if include_schedule_hint:
            return result
        ordinary_result = dict(result)
        ordinary_result.pop("next_time", None)
        return ordinary_result

    current = now or job_now()
    zone = ZoneInfo(DEFAULT_TIMEZONE)
    current = current.replace(tzinfo=zone) if current.tzinfo is None else current.astimezone(zone)
    active = _active_dandao_adapter(current)
    next_daily = next_dandao_task_reward_time(current).strftime("%Y-%m-%d %H:%M:%S")
    if active is None:
        if manage_schedule:
            runtime.set_next_time(next_daily)
        return result_with_optional_schedule_hint({
            "result": "success",
            "claimed_count": 0,
            "next_time": next_daily,
            "message": f"{DANDAO_TASK_REWARDS_LABEL}：当前没有开放的丹道问鼎；下次 {next_daily}",
        })

    adapter, activity_id = active
    snapshot = read_dandao_task_reward_snapshot(activity_id)
    _require_complete_snapshot(snapshot)
    claimable = [int(value) for value in snapshot.get("authorized_claim_task_ids") or []]
    if not claimable:
        if snapshot.get("state") == "already_claimed":
            next_time = next_daily
            boundary = "already_claimed"
        else:
            next_time = _next_pending_check_time(current).strftime("%Y-%m-%d %H:%M:%S")
            boundary = "no_claimable_progress"
        if manage_schedule:
            runtime.set_next_time(next_time)
        return result_with_optional_schedule_hint({
            "result": "success",
            "claimed_count": 0,
            "boundary": boundary,
            "next_time": next_time,
            "message": f"{DANDAO_TASK_REWARDS_LABEL}：当前无可领奖励；下次 {next_time}",
        })

    scene = yield from open_resource_rank_activity_page(
        runtime,
        adapter,
        activity_id=activity_id,
        now=current,
    )
    if int(scene) != DANDAO_TASK_REWARDS_SCENE_ID:
        runtime.click_shape_center(int(scene), "任务")
        yield from runtime.wait_view(
            DANDAO_TASK_REWARDS_SCENE_ID,
            timeout=20.0,
            label=f"{DANDAO_TASK_REWARDS_LABEL}：等待任务页",
        )

    claimed_ids: list[int] = []
    limit = max(1, int(max_claims))
    while True:
        claimable = [int(value) for value in snapshot.get("authorized_claim_task_ids") or []]
        if not claimable:
            break
        if len(claimed_ids) >= limit:
            raise RuntimeError(f"{DANDAO_TASK_REWARDS_LABEL}：领取达到安全上限 {limit} 仍未收敛")
        scene_id, score, frame = runtime.current_scene(
            [DANDAO_TASK_REWARDS_SCENE_ID],
            update=True,
        )
        if int(scene_id or 0) != DANDAO_TASK_REWARDS_SCENE_ID or float(score or 0.0) < 80.0:
            raise RuntimeError(f"{DANDAO_TASK_REWARDS_LABEL}：领取前未可靠识别 #598")
        task_id = claimable[0]
        runtime.click_shape(
            DANDAO_TASK_REWARDS_SCENE_ID,
            DANDAO_TASK_REWARDS_CLAIM_SHAPE,
            frame_data_url=frame,
        )
        yield from runtime.wait_action_settle(1.0)
        after = read_dandao_task_reward_snapshot(activity_id)
        _verify_one_claim(snapshot, after, task_id=task_id)
        claimed_ids.append(task_id)
        snapshot = after

    pending = [int(value) for value in snapshot.get("pending_task_ids") or []]
    next_time = (
        _next_pending_check_time(current).strftime("%Y-%m-%d %H:%M:%S")
        if pending
        else next_daily
    )
    if manage_schedule:
        runtime.set_next_time(next_time)
    try:
        result = runtime.go_scene(34)
        if hasattr(result, "send"):
            yield from result
    except (InterruptedError, GeneratorExit):
        raise
    except Exception as exc:
        return result_with_optional_schedule_hint({
            "result": "success",
            "current_scene": DANDAO_TASK_REWARDS_SCENE_ID,
            "claimed_count": len(claimed_ids),
            "claimed_ids": claimed_ids,
            "next_time": next_time,
            "message": (
                f"{DANDAO_TASK_REWARDS_LABEL}：QuestMgr 已确认领取 {len(claimed_ids)} 档；"
                f"离场告警 {type(exc).__name__}: {exc}；下次 {next_time}"
            ),
        })
    return result_with_optional_schedule_hint({
        "result": "success",
        "current_scene": 34,
        "claimed_count": len(claimed_ids),
        "claimed_ids": claimed_ids,
        "next_time": next_time,
        "message": (
            f"{DANDAO_TASK_REWARDS_LABEL}：QuestMgr 已确认领取 {len(claimed_ids)} 档；"
            f"下次 {next_time}"
        ),
    })


class DandaoTaskRewardsTaskMixin:
    def _execute_dandao_task_rewards_task(
        self,
        ctx: dict[str, Any],
        stop_event: Any,
        payload: dict[str, Any] | None = None,
    ) -> str:
        options = dict(payload or {})

        def flow(runtime: Any):
            return (
                yield from run_dandao_task_rewards_flow(
                    runtime,
                    max_claims=int(options.get("max_claims") or 20),
                    manage_schedule=False,
                    include_schedule_hint=False,
                )
            )

        return self._execute_daily_runtime_task(
            ctx,
            stop_event,
            options,
            task_type=DANDAO_TASK_REWARDS_TASK_TYPE,
            label=DANDAO_TASK_REWARDS_LABEL,
            flow=flow,
        )


__all__ = [
    "DANDAO_TASK_REWARDS_LABEL",
    "DANDAO_TASK_REWARDS_TASK_ID",
    "DANDAO_TASK_REWARDS_TASK_TYPE",
    "DandaoTaskRewardsTaskMixin",
    "next_dandao_task_reward_time",
    "run_dandao_task_rewards_flow",
]
