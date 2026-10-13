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


def resolve_active_dandao_activity(now: datetime):
    """Resolve the unique active alchemy occurrence from the schedule snapshot."""
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


def inspect_dandao_task_reward_panel(context: Any, *, expected_activity_id: int,
                                    now: datetime | None = None):
    """Observe the active task page and native metric without claiming or spending.

    This research entry deliberately does not require a supported spending
    metric. Navigation still uses the exact active occurrence and accepted
    page assets. An unknown page preserves the scene-wait failure; raw native
    observations never grant production permission.
    """
    current = now or job_now()
    active = resolve_active_dandao_activity(current)
    if active is None or active[1] != expected_activity_id:
        raise ValueError("丹道详情取证必须绑定当前唯一开放活动")
    adapter, activity_id = active
    yield from context.go_scene(34)
    scene = yield from open_resource_rank_activity_page(
        context, adapter, activity_id=activity_id, now=current)
    if int(scene) != DANDAO_TASK_REWARDS_SCENE_ID:
        context.click_shape_center(int(scene), "任务")
        yield from context.wait_scene([DANDAO_TASK_REWARDS_SCENE_ID], wait=20,
                                      label="丹道只读取证：等待任务页")
    frame = context.cur_frame(update=True)
    snapshot = read_dandao_task_reward_snapshot(activity_id)
    yield from context.go_scene(34)
    return {"activity_id": activity_id, "observation_only": True,
            "consumption_authorized": False, "claimed_count": 0,
            "native_snapshot": snapshot, "frame_data_url": frame, "final_scene": 34}


def inspect_dandao_score_task_panel(context: Any, *, expected_activity_id: int):
    """Open the verified 积分 tab and capture its unaccepted landing, no claims.

    This research call preserves the resulting page for visual inspection.
    It does not pretend an unannotated score page is scene #598 or authorize
    a reward click; leaving requires a separately verified visible exit.
    """
    from backend.core.fanxiu.instrumentation.dandao_task_rewards import read_dandao_score_task_observation
    current = job_now()
    active = resolve_active_dandao_activity(current)
    if active is None or active[1] != expected_activity_id:
        raise ValueError('丹道积分取证必须绑定本期唯一开放活动')
    adapter, activity_id = active
    yield from context.go_scene(34)
    scene = yield from open_resource_rank_activity_page(context, adapter,
                activity_id=activity_id, now=current)
    if int(scene) != DANDAO_TASK_REWARDS_SCENE_ID:
        context.click_shape_center(int(scene), '任务')
    yield from context.wait_scene_exact([DANDAO_TASK_REWARDS_SCENE_ID], timeout=25,
                label='丹道积分取证：确认炼制任务页')
    context.click_shape_center(DANDAO_TASK_REWARDS_SCENE_ID, '积分页签')
    yield from context.wait_action_settle(1.0)
    frame = context.cur_frame(update=True)
    snapshot = read_dandao_score_task_observation(activity_id)
    return dict(activity_id=activity_id, observation_only=True,
                consumption_authorized=False, claimed_count=0,
                landing_accepted=False, frame_data_url=frame, native_snapshot=snapshot)


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


def run_dandao_score_rewards_flow(context: Any, *, now: datetime | None = None,
                                  expected_activity_id: int | None = None,
                                  max_claims: int = 40) -> dict[str, Any]:
    """Collect earned score rows from the native current-round controller.

    This separate ladder grants no alchemy or round-summary click permission.
    A stopped/restarted job reads durable finished IDs again; it never replays
    an earlier claim. Four configured rounds are not equivalent to completion
    of the MedicalExp book.
    """
    from backend.core.fanxiu.data_annotation.tasks.revenue_task_claims import claim_open_revenue_task_rewards
    from backend.core.fanxiu.instrumentation.dandao_task_rewards import read_dandao_score_task_observation

    current = now or job_now()
    active = resolve_active_dandao_activity(current)
    if active is None:
        if expected_activity_id is not None:
            raise RuntimeError('指定丹道积分活动当前未开放')
        return dict(result='success', claimed_task_ids=[], boundary='not_open')
    adapter, activity_id = active
    if expected_activity_id is not None and activity_id != int(expected_activity_id):
        raise RuntimeError('丹道积分活动与指定实例不一致')
    observation = read_dandao_score_task_observation(activity_id)
    if not observation.get('complete'):
        raise RuntimeError(f'丹道积分原生状态不完整：{observation.get("reason")}')
    # With no earned rows, do not navigate or touch the unfinished first row.
    claims = dict(claimed_task_ids=[])
    if observation['claimable_task_ids']:
        yield from context.go_scene(34)
        scene = yield from open_resource_rank_activity_page(
            context, adapter, activity_id=activity_id, now=current)
        if int(scene) != DANDAO_TASK_REWARDS_SCENE_ID:
            context.click_shape_center(int(scene), '任务')
        yield from context.wait_scene_exact([DANDAO_TASK_REWARDS_SCENE_ID], timeout=25)
        context.click_shape_center(DANDAO_TASK_REWARDS_SCENE_ID, '积分页签')
        yield from context.wait_action_settle(1.0)
        claims = yield from claim_open_revenue_task_rewards(
            context, activity_id=activity_id, expected_subtype=999,
            scene_id=DANDAO_TASK_REWARDS_SCENE_ID,
            first_row_shape='积分首条任务领取区', max_claims=max_claims)
        observation = read_dandao_score_task_observation(activity_id)
        if not observation.get('complete') or not set(claims['claimed_task_ids']).issubset(
                observation['claimed_task_ids']):
            raise RuntimeError('丹道积分领取后的全梯度原生记录不完整')
        yield from context.go_scene(34)
    return dict(result='success', activity_id=activity_id,
                claimed_task_ids=claims['claimed_task_ids'],
                current_score=observation['current_score'],
                current_round=observation['current_round'],
                round_count=len({row['round'] for row in observation['rows']}),
                all_row_rewards_claimed=not observation['pending_task_ids'] and not observation['claimable_task_ids'],
                boundary='no_claimable_score_rows', consumption_authorized=False,
                native_snapshot=observation)


def run_dandao_task_rewards_flow(
    context: Any,
    *,
    now: datetime | None = None,
    expected_activity_id: int | None = None,
    initial_snapshot: Mapping[str, Any] | None = None,
    max_claims: int = 20,
    manage_schedule: bool = False,
    include_schedule_hint: bool = True,
    return_to_world: bool = True,
) -> dict[str, Any]:
    """Claim the active ladder; a caller may reuse its fresh same-activity read.

    No claim or external game action may intervene between initial_snapshot
    and this invocation. Every claim still receives a new confirmation read.
    """
    def result_with_optional_schedule_hint(result: dict[str, Any]) -> dict[str, Any]:
        if include_schedule_hint:
            return result
        ordinary_result = dict(result)
        ordinary_result.pop("next_time", None)
        return ordinary_result

    current = now or job_now()
    zone = ZoneInfo(DEFAULT_TIMEZONE)
    current = current.replace(tzinfo=zone) if current.tzinfo is None else current.astimezone(zone)
    active = resolve_active_dandao_activity(current)
    next_daily = next_dandao_task_reward_time(current).strftime("%Y-%m-%d %H:%M:%S")
    if active is None:
        if expected_activity_id is not None:
            raise RuntimeError("指定丹道活动当前未开放，拒绝将未领奖报告为完成")
        if manage_schedule:
            context.set_next_time(next_daily)
        return result_with_optional_schedule_hint({
            "result": "success",
            "claimed_count": 0,
            "next_time": next_daily,
            "message": f"{DANDAO_TASK_REWARDS_LABEL}：当前没有开放的丹道问鼎；下次 {next_daily}",
        })

    adapter, activity_id = active
    if expected_activity_id is not None and activity_id != int(expected_activity_id):
        raise RuntimeError("丹道领奖活动与资源使用活动不一致")
    snapshot = dict(initial_snapshot) if initial_snapshot is not None else read_dandao_task_reward_snapshot(activity_id)
    if int(snapshot.get("activity_id") or 0) != activity_id:
        raise RuntimeError("丹道领奖快照不属于当前活动")
    if snapshot.get("reason_code") == "unsupported_live_task_metric":
        return result_with_optional_schedule_hint({
            "result": "pending", "status": "pending", "claimed_count": 0,
            "reason_code": "unsupported_live_task_metric",
            "consumption_authorized": False, "performed_actions": False,
            "message": snapshot["reason"],
            "unsupported_task_conditions": snapshot["unsupported_task_conditions"],
        })
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
            context.set_next_time(next_time)
        return result_with_optional_schedule_hint({
            "result": "success",
            "claimed_count": 0,
            "boundary": boundary,
            "next_time": next_time,
            "message": f"{DANDAO_TASK_REWARDS_LABEL}：当前无可领奖励；下次 {next_time}",
        })

    scene = yield from open_resource_rank_activity_page(
        context,
        adapter,
        activity_id=activity_id,
        now=current,
    )
    if int(scene) != DANDAO_TASK_REWARDS_SCENE_ID:
        context.click_shape_center(int(scene), "任务")
        yield from context.wait_scene(
            [DANDAO_TASK_REWARDS_SCENE_ID],
            wait=20.0,
            label=f"{DANDAO_TASK_REWARDS_LABEL}：等待任务页",
        )
    # One task book has separate MedicalExp and ActivityItemScore tabs.
    # Bind this executor to 炼制 before any QuestMgr-authorized reward click.
    context.click_shape_center(DANDAO_TASK_REWARDS_SCENE_ID, '炼制页签')
    yield from context.wait_action_settle(0.6)

    claimed_ids: list[int] = []
    limit = max(1, int(max_claims))
    while True:
        claimable = [int(value) for value in snapshot.get("authorized_claim_task_ids") or []]
        if not claimable:
            break
        if len(claimed_ids) >= limit:
            raise RuntimeError(f"{DANDAO_TASK_REWARDS_LABEL}：领取达到安全上限 {limit} 仍未收敛")
        # Reward/system broadcasts can obscure the first row briefly. Wait
        # for the exact accepted task page; unknown is not a claim landing.
        _wait_scene_match = yield from context.wait_scene_exact(
            [DANDAO_TASK_REWARDS_SCENE_ID], timeout=25,
            label=f'{DANDAO_TASK_REWARDS_LABEL}：等待领取页恢复',
        )
        (scene_id, score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if int(scene_id or 0) != DANDAO_TASK_REWARDS_SCENE_ID or float(score or 0.0) < 80.0:
            raise RuntimeError(f"{DANDAO_TASK_REWARDS_LABEL}：领取前未可靠识别 #598")
        task_id = claimable[0]
        context.click_shape(
            DANDAO_TASK_REWARDS_SCENE_ID,
            DANDAO_TASK_REWARDS_CLAIM_SHAPE,
            frame_data_url=frame,
        )
        yield from context.wait_action_settle(1.0)
        after = read_dandao_task_reward_snapshot(activity_id)
        _verify_one_claim(snapshot, after, task_id=task_id)
        claimed_ids.append(task_id)
        snapshot = after

    pending = [int(value) for value in snapshot.get("pending_task_ids") or []]
    boundary = "already_claimed" if snapshot.get("state") == "already_claimed" else "no_claimable_progress"
    next_time = (
        _next_pending_check_time(current).strftime("%Y-%m-%d %H:%M:%S")
        if pending
        else next_daily
    )
    if manage_schedule:
        context.set_next_time(next_time)
    if not return_to_world:
        return result_with_optional_schedule_hint({
            "result": "success",
            "current_scene": DANDAO_TASK_REWARDS_SCENE_ID,
            "claimed_count": len(claimed_ids),
            "boundary": boundary,
            "claimed_ids": claimed_ids,
            "next_time": next_time,
            "message": f"{DANDAO_TASK_REWARDS_LABEL}：QuestMgr 已确认领取 {len(claimed_ids)} 档",
        })
    try:
        result = context.go_scene(34)
        if hasattr(result, "send"):
            yield from result
    except (InterruptedError, GeneratorExit):
        raise
    except Exception as exc:
        return result_with_optional_schedule_hint({
            "result": "success",
            "current_scene": DANDAO_TASK_REWARDS_SCENE_ID,
            "claimed_count": len(claimed_ids),
            "boundary": boundary,
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
        "boundary": boundary,
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

        def flow(context: Any):
            result = yield from run_dandao_task_rewards_flow(
                    context,
                    max_claims=int(options.get("max_claims") or 20),
                    manage_schedule=False,
                    include_schedule_hint=False,
                )
            if result.get('result') == 'success' and options.get('include_score_rewards', True):
                score = yield from run_dandao_score_rewards_flow(context)
                result['score_rewards'] = score
                result['message'] += f"；积分任务本批领取 {len(score['claimed_task_ids'])} 档"
            return result

        return self._execute_daily_task(
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
    "run_dandao_score_rewards_flow",
]
