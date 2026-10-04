"""日常_祈愿周日收尾的业务时间；独立于养成集的每日凭证。

仙花祈愿“拉满”是本轮任务最高档16万积分，不能使用页头单项奖励
的“再获得”差额。奖励轮次与本轮积分分开观察，不跨轮累加积分。
已有积分不超过满档目标30%时，保留资源并作为幂等完成；只有超过
30%才补本轮剩余差额。领完进入新轮后重新判断，禁止顺带拉满新一轮。
目前动作暂复用每日祈愿，主动补齐最高档尚未接入。
"""
from datetime import datetime
from typing import Any, Mapping

from backend.core.fanxiu.data_annotation.job_times import next_business_time


PRAYER_CLOSEOUT_MIN_PROGRESS_PERCENT = 30


def plan_prayer_closeout(progress: Mapping[str, Any]) -> dict[str, Any]:
    """纯收尾决策：<=30%为合法完成，>30%仅补当前轮；不操作或授权资源。

    成功跳过可以由作业记录完成并推进到下周，不作为失败重试。重复
    观察同一事实返回同一决策；领取后跨轮必须重新观察，不能沿用旧差额。
    complete 是观察完整性，result/outcome 是本次收尾的业务终态。
    """
    if progress.get("complete") is not True:
        raise ValueError("祈愿收尾需要完整本轮积分观察")
    try:
        score = int(progress["current_score"])
        target = int(progress["round_target_score"])
        gap = int(progress["round_score_gap"])
        current_round = int(progress["current_round"])
        total_rounds = int(progress["total_rounds"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("祈愿收尾积分或轮次缺失") from exc
    if target <= 0 or not 0 <= score <= target or gap != target - score or not 0 < current_round <= total_rounds:
        raise ValueError("祈愿收尾积分或轮次矛盾")
    result = {
        "current_round": current_round, "total_rounds": total_rounds,
        "current_score": score, "round_target_score": target,
        "threshold_percent": PRAYER_CLOSEOUT_MIN_PROGRESS_PERCENT,
        "threshold_score": target * PRAYER_CLOSEOUT_MIN_PROGRESS_PERCENT // 100,
        "stop_after_current_round": True,
    }
    if gap == 0:
        return {**result, "action": "complete", "result": "success",
                "outcome": "already_full", "additional_score": 0}
    if score * 100 <= target * PRAYER_CLOSEOUT_MIN_PROGRESS_PERCENT:
        return {**result, "action": "complete", "result": "success",
                "outcome": "skipped_low_progress", "additional_score": 0,
                "reason": "本轮已有积分不超过30%，保留资源，收尾幂等完成"}
    return {**result, "action": "top_up", "result": "pending",
            "outcome": "current_round_gap", "additional_score": gap}


def next_prayer_closeout_time(now: datetime | None = None) -> str:
    """每周日23点；成功后推进到下一周，失败仍由 Scheduler 技术重试。"""
    return next_business_time(("23:00",), weekdays=(6,), now=now)
