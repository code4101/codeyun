"""社团炼体的任务奖励目标；查询不会打开页面或授权消费。"""
from datetime import datetime

from .daily_activity_sync import load_worldline_activity_schedule_snapshot
from ..instrumentation.shequn_lianti_task_rewards import (
    SHEQUN_LIANTI_ACTIVITY_ID, read_shequn_lianti_task_reward_snapshot,
)


def read_current_shequn_lianti_goal():
    """Bind the current world-line occurrence to its exact live reward ladder.

    World-line identities select the server/cross instance; Runtime must
    corroborate its period. An absent active occurrence is pending evidence,
    never completed. Only the currently taught 8043401 ladder is supported;
    another size/version needs its own verified task configuration.
    """
    now = datetime.now().astimezone()
    schedule = load_worldline_activity_schedule_snapshot()
    candidates = []
    for row in schedule.get("occurrences") or []:
        if int(row.get("base_id") or 0) != 43400:
            continue
        start = datetime.fromisoformat(row["start_at"])
        end = datetime.fromisoformat(row["end_at"])
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise RuntimeError("社团炼体世界线周期无效")
        if start <= now < end:
            candidates.append(row)
    if not candidates:
        return {"available": False, "complete": False, "goal_completed": False,
                "reason": "no_active_worldline_occurrence", "performed_actions": False}
    if len(candidates) != 1:
        raise RuntimeError("社团炼体当前世界线实例不唯一")
    occurrence = candidates[0]
    runtime_ids = list(occurrence.get("runtime_ids") or [])
    if (not occurrence.get("identity_complete") or occurrence.get("identity_conflict")
            or len(runtime_ids) != 1
            or int(occurrence["activity_id"]) != SHEQUN_LIANTI_ACTIVITY_ID):
        raise RuntimeError("社团炼体世界线身份不完整或尚无已验证梯度")
    result = read_shequn_lianti_task_reward_snapshot(runtime_id=int(runtime_ids[0]))
    period = result["period"]
    for field, source in (("start_time_ms", "start_at"), ("end_time_ms", "end_at")):
        if int(period[field]) != round(datetime.fromisoformat(occurrence[source]).timestamp() * 1000):
            raise RuntimeError("社团炼体原生周期与世界线实例不一致")
    return {**result, "occurrence_key": occurrence["key"],
            "cross_count": occurrence["cross_count"], "performed_actions": False,
            "worldline_captured_at": schedule.get("captured_at")}


__all__ = ["read_current_shequn_lianti_goal"]
