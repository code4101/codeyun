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


def plan_prayer_closeout_boxes(
    progress: Mapping[str, Any], *, prayer_category: str,
    research: Mapping[str, Any], inventory: list[Mapping[str, Any]],
    round_targets: Mapping[str, int],
) -> dict[str, Any]:
    """从完整积分、持久研究和完整库存生成本轮下一次开箱建议；不操作游戏。

    inventory 每行提供 base_id、quantity、catalog_fingerprint，调用方须先
    核实完整库存并用当前目录绑定版本。未知、目录变更、零收益、外箱及
    未测齐自选均不授权消耗。用途比较沿用研究模块，专属先、多用途后。
    每次最多建议一箱，再观察真实积分和库存后重算；历史期望不保证实际
    补分，也不跨轮复用计划。返回 planned 仅代表建议，不代表作业成功。
    自选同时给出全部并列选项，执行方必须明确选择，不隐式替用户破并列。
    """
    from backend.core.fanxiu.storage_bag_prayer_research import (
        order_prayer_top_up_resources, plan_choice_prayer_allocations,
    )

    decision = plan_prayer_closeout(progress)
    if decision["action"] != "top_up":
        return {**decision, "resources": [], "next_open": None}
    if (prayer_category not in round_targets
            or round_targets[prayer_category] != decision["round_target_score"]):
        raise ValueError("当前祈愿类型与满轮目标不一致")
    stock = {}
    for row in inventory:
        base_id, quantity = row.get("base_id"), row.get("quantity")
        if (type(base_id) is not int or base_id <= 0 or base_id in stock
                or type(quantity) is not int or quantity < 0
                or not row.get("catalog_fingerprint")):
            raise ValueError("补分库存需要唯一物品、非负数量与当前目录版本")
        stock[base_id] = row
    allocations = {row["base_id"]: row for row in
                   plan_choice_prayer_allocations(research, round_targets)}
    summaries = research.get("summaries") or []
    resources = []
    for rule in research.get("rules") or []:
        base_id = rule["base_id"]
        owned = stock.get(base_id)
        if (not owned or owned["quantity"] == 0
                or owned["catalog_fingerprint"] != rule["catalog_fingerprint"]
                or rule.get("classification_complete") is not True
                or rule.get("exclude_from_prayer_top_up")
                or rule.get("is_outer_container")):
            continue
        samples = [s for s in summaries if s["base_id"] == base_id
                   and s["catalog_fingerprint"] == rule["catalog_fingerprint"]
                   and s["prayer_category"] == prayer_category
                   and s.get("eligibility") == "verified_positive"]
        kind = rule.get("operation_kind")
        choice_keys = []
        if kind == "choice":
            allocation = allocations.get(base_id, {})
            if (allocation.get("catalog_fingerprint") != rule["catalog_fingerprint"]
                    or allocation.get("status") != "recommended"):
                continue
            uses = allocation["usable_categories"]
            if prayer_category not in uses:
                continue
            plan = next(p for p in research["choice_plans"] if p["base_id"] == base_id)
            valid_choices = {o["choice_key"] for o in plan["options"]
                             if o["prayer_category"] == prayer_category}
            samples = [s for s in samples if s.get("choice_key") in valid_choices]
            if not samples:
                continue
            expected = max(s["mean_score_per_box"] for s in samples)
            choice_keys = sorted(s["choice_key"] for s in samples
                                 if s["mean_score_per_box"] == expected)
        elif kind in {"fixed", "random"}:
            # 混合随机奖池即使当前周测得分，也可能牺牲其他周收益；未定义
            # 跨周比较前保留。非自选的单类资源只需该周完整正收益样本。
            uses = rule.get("allocation_categories") or []
            if uses != [prayer_category]:
                continue
            samples = [s for s in samples if not s.get("choice_key")]
            if len(samples) != 1:
                continue
            expected = samples[0]["mean_score_per_box"]
        else:
            continue
        if expected <= 0:
            continue
        resources.append(dict(base_id=base_id, name=rule.get("name"),
            catalog_fingerprint=rule["catalog_fingerprint"], operation_kind=kind,
            quantity=owned["quantity"], usable_categories=uses,
            eligibility_verified=True, expected_score_per_box=expected,
            choice_keys=choice_keys))
    ordered = order_prayer_top_up_resources(resources, current_category=prayer_category)
    return {**decision, "prayer_category": prayer_category, "resources": ordered,
            "next_open": {**ordered[0], "open_quantity": 1} if ordered else None,
            "outcome": "planned" if ordered else "waiting_verified_resources"}
