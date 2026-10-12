"""玩法榜领奖与挑战批次解耦：首次100次后一次，21:30补查一次。

资源榜不走此入口。各玩法仍拥有真实任务页、领取终态及失败重入契约；
没有实机领奖适配器的玩法不能通过新增时点伪装为已接入。
"""
from datetime import datetime
from sqlmodel import Session

from backend.core.fanxiu.activity.ranking_lifecycle import RankingCheckpoint
from backend.core.fanxiu.activity.ranking_lifecycle_store import (
    completed_ranking_checkpoint_keys, record_ranking_checkpoint_result,
)


def claim_initial_gameplay_rewards_once(occurrence, claim):
    """在已完成首次100次的业务边界调用；成功才保存本期一次性凭证。

    claim 是无参数领奖 generator，不得启动挑战。异常不写完成凭证，
    新 attempt 可继续领取；历史已领取游戏事实仍由提供方幂等消费。
    """
    if occurrence.family != "gameplay_rank":
        raise ValueError("玩法榜首次领奖入口不接受资源榜")
    from backend.db import engine
    checkpoint = RankingCheckpoint(
        instance_key=occurrence.instance_key, activity_type=occurrence.activity_type,
        family=occurrence.family, runtime_id=occurrence.runtime_id, activity_id=occurrence.activity_id,
        checkpoint_kind="gameplay_initial_task_rewards", business_date=occurrence.start_at.date().isoformat(),
        due_at=occurrence.start_at,
    )
    with Session(engine) as session:
        if checkpoint.key in completed_ranking_checkpoint_keys(session, family="gameplay_rank"):
            return {"status": "completed", "skipped": True, "message": "本期首次100次后领奖已完成"}
    result = yield from claim()
    if (result.get("ok") is False or result.get("snapshot_complete") is False
            or result.get("authorized_remaining")
            or str(result.get("status") or "completed") not in {"completed", "claimed", "already_settled", "success"}):
        raise RuntimeError(f"玩法榜首次领奖未完成：{result}")
    with Session(engine) as session:
        record_ranking_checkpoint_result(session, checkpoint, status="completed", result=result,
            completed_at=datetime.now().astimezone())
        session.commit()
    return result


def execute_gameplay_rank_reward_checkpoint(runner, ctx, payload, stop_event, *, occurrence):
    """只执行21:30补查领奖；不探查、不对弈、不购买或使用挑战资源。"""
    if occurrence.family != "gameplay_rank":
        raise ValueError("玩法榜领奖入口不接受资源榜")
    context = runner._behavior_tree_context(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
    if occurrence.activity_type == "beast-abyss":
        from .beast_abyss_active import execute_beast_abyss_reward_checkpoint
        return (yield from execute_beast_abyss_reward_checkpoint(runner, ctx, payload, stop_event, occurrence=occurrence))
    if occurrence.activity_type == "magic-invasion":
        from .magic_invasion_compound import claim_magic_invasion_occurrence_rewards
        rewards = yield from claim_magic_invasion_occurrence_rewards(context, occurrence)
    elif occurrence.activity_type == "tiandi-yiju":
        from .tiandi_yiju import enter_tiandi_yiju_occurrence_home, claim_tiandi_yiju_task_rewards
        yield from enter_tiandi_yiju_occurrence_home(context, occurrence=occurrence)
        rewards = yield from claim_tiandi_yiju_task_rewards(context, activity_id=occurrence.activity_id)
    elif occurrence.activity_type == "yunmeng-trial":
        from .yunmeng_active import enter_yunmeng_activity_home
        from .yunmeng_task_rewards import claim_current_yunmeng_task_rewards
        yield from enter_yunmeng_activity_home(context, target_date=occurrence.end_at.astimezone().date())
        rewards = yield from claim_current_yunmeng_task_rewards(context, activity_id=occurrence.activity_id)
        if rewards.get("status") in {"nothing_claimable", "already_claimed"}:
            rewards = {**rewards, "status": "completed"}
    elif occurrence.activity_type == "xutian-palace":
        from backend.db import engine
        from backend.core.fanxiu.activity.ranking_reconcile import seed_ranking_occurrence
        from .xutian_active import execute_xutian_task_reward_gate
        with Session(engine) as session:
            activity = seed_ranking_occurrence(session, occurrence, captured_at=datetime.now().astimezone().isoformat())
            session.commit()
            session.refresh(activity)
        rewards = yield from execute_xutian_task_reward_gate(runner, ctx, payload, stop_event,
            occurrence=occurrence, activity=activity)
    else:
        raise RuntimeError(f"{occurrence.activity_type} 尚缺真实领奖适配器")
    if (rewards.get("ok") is False or rewards.get("snapshot_complete") is False
            or rewards.get("authorized_remaining")
            or str(rewards.get("status") or "completed") not in {"completed", "claimed", "already_settled", "success"}):
        raise RuntimeError(f"玩法榜21:30领奖未形成合法终态：{rewards}")
    yield from context.go_scene(34)
    return {"status": "completed", "message": "玩法榜21:30补查领奖完成", "rewards": rewards}
