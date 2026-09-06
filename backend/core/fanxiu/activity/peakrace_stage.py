"""巅峰赛单阶段研发入口；不注册调度，不执行竞猜或资源消耗。"""

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from backend.core.fanxiu.instrumentation.activity_gift import read_activity_gift_runtime_snapshot
from backend.core.fanxiu.instrumentation.activity_rank_runtime import read_activity_rank_runtime_snapshot
from backend.core.fanxiu.instrumentation.peakrace import read_peakrace_runtime_snapshot


def read_peakrace_stage_snapshot(
    activity_id: int, *, task_activity_id: int | None = None,
    reward_activity_id: int | None = None, event_date: str | None = None,
    world_level: int | None = None, server_day: int | None = None,
) -> dict[str, Any]:
    """只读已加载数据，不假定轮次和资源顺序。

    灵宠任务和阶段奖励按显式身份选读；其他资源阶段不套用灵宠规则。
    奖励配置的日期应来自活动实例，不能用采集当天替代。
    ``captured_at`` 仅代表读缓存时间，不证明服务端榜单同步时间。
    """
    if isinstance(activity_id, bool) or not isinstance(activity_id, int) or activity_id <= 0:
        raise ValueError("需要明确的当前阶段活动 ID")
    if reward_activity_id is not None and event_date is None:
        raise ValueError("读取阶段奖励需要活动实例日期")
    peak = read_peakrace_runtime_snapshot()
    groups = [r for r in peak.get("activity_groups", []) if r["activity_id"] == activity_id]
    if not peak.get("complete") or len(groups) != 1:
        return {"ok": False, "activity_id": activity_id, "peakrace": peak,
                "reason": "当前巅峰赛阶段归属未确认", "automatic_execution_enabled": False}
    rank = read_activity_rank_runtime_snapshot(activity_id)
    gifts = read_activity_gift_runtime_snapshot([activity_id])
    group = groups[0]["group"]
    supported = [rid for g in peak.get("guesses", [])
                 if g["activity_id"] == activity_id and g["group"] == group
                 for rid in g["role_ids"]]
    result = {
        "ok": bool(rank.get("complete") and rank.get("rank_activity_id") == activity_id),
        "activity_id": activity_id, "group": group,
        "current_round": peak["current_round"],
        "peakrace": peak, "ranking": rank, "gifts": gifts,
        "supported_role_ids": supported,
        "top_four": [dict(row, supported=row.get("role_id") in supported)
                     for row in rank.get("rankings", []) if 1 <= row["rank"] <= 4],
        "automatic_execution_enabled": False,
    }
    if task_activity_id is not None:
        from backend.core.fanxiu.activity.lingchong_jingwu import read_lingchong_task_milestones

        result["tasks"] = read_lingchong_task_milestones(task_activity_id)
    if reward_activity_id is not None:
        from backend.core.fanxiu.activity.rank_reward import (
            ActivityRankRewardConfigError, load_activity_rank_reward_tiers,
        )

        try:
            tiers = load_activity_rank_reward_tiers(
                reward_activity_id=reward_activity_id, event_date=event_date,
                world_level=world_level, server_day=server_day,
            )
            result["stage_rewards"] = {"complete": True, "tiers": tiers,
                                       "reward_activity_id": reward_activity_id}
        except ActivityRankRewardConfigError as exc:
            result["stage_rewards"] = {"complete": False, "tiers": [], "reason": str(exc),
                                       "reward_activity_id": reward_activity_id}
    return result


def preview_peakrace_support(
    snapshot: dict[str, Any], *, now: datetime, max_observation_age_seconds: int = 120,
) -> dict[str, Any]:
    """预览每天 20:30 后支持前四名的业务意图；不是点击授权或执行器。"""
    if now.tzinfo is None:
        raise ValueError("竞猜预览需要带时区的时间")
    if max_observation_age_seconds <= 0:
        raise ValueError("采集有效期必须大于零")
    local = now.astimezone(ZoneInfo("Asia/Shanghai"))
    rows = snapshot.get("top_four", [])
    peak, ranking = snapshot.get("peakrace", {}), snapshot.get("ranking", {})
    activity_id, group = snapshot.get("activity_id"), snapshot.get("group")
    blockers = []
    if (not snapshot.get("ok") or not peak.get("complete") or not ranking.get("complete")
            or ranking.get("rank_activity_id") != activity_id
            or not isinstance(activity_id, int) or isinstance(activity_id, bool) or activity_id <= 0
            or not isinstance(group, int) or isinstance(group, bool) or group <= 0
            or [r.get("group") for r in peak.get("activity_groups", [])
                if r.get("activity_id") == activity_id] != [group]):
        blockers.append("stage_identity_incomplete")
    if (len(rows) != 4 or {r.get("rank") for r in rows} != {1, 2, 3, 4}
            or len({r.get("role_id") for r in rows}) != 4
            or any(not isinstance(r.get("role_id"), int) or isinstance(r.get("role_id"), bool)
                   or r["role_id"] <= 0 for r in rows)):
        blockers.append("top_four_incomplete")
    rank_rows = ranking.get("rankings", [])
    if any(not any(all(actual.get(key) == row.get(key) for key in ("rank", "role_id", "support_value"))
                   for actual in rank_rows) for row in rows):
        blockers.append("ranking_projection_mismatch")
    if any(peak.get("evidence", {}).get(key) != ranking.get("evidence", {}).get(key)
           for key in ("pid", "process_start_ticks")):
        blockers.append("runtime_process_mismatch")
    if any(not isinstance(r.get("support_value"), int) or isinstance(r.get("support_value"), bool)
           or r["support_value"] < 0 for r in rows):
        blockers.append("support_values_missing")
    ages = []
    for source in (peak, ranking):
        try:
            captured = datetime.fromisoformat(source.get("captured_at") or "")
            age = (now - captured).total_seconds() if captured.tzinfo is not None else -1
        except (TypeError, ValueError):
            age = -1
        ages.append(age)
    if any(age < 0 or age > max_observation_age_seconds for age in ages):
        blockers.append("observation_stale_or_unknown")
    supported = {rid for guess in peak.get("guesses", [])
                 if guess.get("activity_id") == activity_id and guess.get("group") == group
                 for rid in guess.get("role_ids", [])}
    return {"status": "preview" if not blockers else "data_incomplete",
            "activity_id": activity_id, "group": group, "blockers": blockers,
            "after_daily_trigger": local.time() >= time(20, 30),
            "candidates": [dict(r, supported=False) for r in sorted(rows, key=lambda r: r.get("rank", 0))
                           if r.get("role_id") not in supported] if not blockers else [],
            "server_sync_freshness_known": False,
            "execution_implemented": False, "authorized_action_count": 0}
