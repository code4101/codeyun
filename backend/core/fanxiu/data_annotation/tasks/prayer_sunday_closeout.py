"""日常_祈愿周日收尾的业务时间；独立于养成集的每日凭证。

仙花祈愿“拉满”是本轮任务最高档16万积分，不能使用页头单项奖励
的“再获得”差额。奖励轮次与本轮积分分开观察，不跨轮累加积分。
已有积分不超过满档目标30%时，保留资源并作为幂等完成；只有超过
30%才补本轮剩余差额；炼丹特例不看门槛，补满并领取全部5轮。
已知收益用于库存/差额规划，每批后重读积分；未知收益留给平时研究。
执行链仍复用周日23点Job；新的炼丹跨轮分支等待周日正式运行验收。
"""
from datetime import datetime
import math
from typing import Any, Mapping

from backend.core.fanxiu.data_annotation.job_times import next_business_time
from backend.core.fanxiu.data_annotation.tasks.prayer_task_progress import PrayerRoundTargetRule


PRAYER_CLOSEOUT_MIN_PROGRESS_PERCENT = 30


def plan_prayer_closeout_batch(*, additional_score: int, expected_score_per_box: float,
                              available_count: int, remaining_budget: int,
                              maximum_batch: int = 1) -> int:
    """用当前差额估计下一批数量，并受单叠库存、总预算和单批上限约束。

    默认一箱；显式批量只减少往返次数，不把历史均值当作保证收益或
    最大收益。每批执行后须重新观察；达到满档立即停止，不能跨轮消耗。
    """
    if any(type(n) is not int or n <= 0 for n in
           (additional_score, available_count, remaining_budget, maximum_batch)):
        raise ValueError('祈愿批量规划需要正整数差额、库存和消耗上限')
    if (type(expected_score_per_box) not in (int, float)
            or not math.isfinite(expected_score_per_box) or expected_score_per_box <= 0):
        raise ValueError('祈愿批量规划需要完整正收益估计')
    limit = min(available_count, remaining_budget, maximum_batch)
    if additional_score >= expected_score_per_box * limit:
        return limit
    return max(1, math.floor(additional_score / expected_score_per_box))


def plan_prayer_closeout(progress: Mapping[str, Any], *, prayer_category: str | None = None) -> dict[str, Any]:
    """纯收尾决策：<=30%为合法完成，>30%仅补当前轮；不操作或授权资源。

    炼丹按业务要求每周完成全部5轮，不应用30%门槛；其他类别沿用门槛。
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
        "threshold_percent": 0 if prayer_category == '炼丹' else PRAYER_CLOSEOUT_MIN_PROGRESS_PERCENT,
        "threshold_score": 0 if prayer_category == '炼丹' else target * PRAYER_CLOSEOUT_MIN_PROGRESS_PERCENT // 100,
        "stop_after_current_round": prayer_category != '炼丹',
    }
    if gap == 0:
        return {**result, "action": "complete", "result": "success",
                "outcome": "already_full", "additional_score": 0}
    if prayer_category != '炼丹' and score * 100 <= target * PRAYER_CLOSEOUT_MIN_PROGRESS_PERCENT:
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
    round_targets: Mapping[str, PrayerRoundTargetRule],
    reward_modifier_snapshots: Mapping[int, Mapping[str, Any]] | None = None,
    explicit_base_id: int | None = None,
) -> dict[str, Any]:
    """从完整积分、持久研究和完整库存生成本轮下一次开箱建议；不操作游戏。

    inventory 每行提供 base_id、quantity、catalog_fingerprint，调用方须先
    核实完整库存并用当前目录绑定版本。未知、目录变更、零收益、外箱及
    未测齐自选均不授权消耗。用途比较沿用研究模块，专属先、多用途后。
    每次最多建议一箱，再观察真实积分和库存后重算；历史期望不保证实际
    补分，也不跨轮复用计划。返回 planned 仅代表建议，不代表作业成功。
    自选同时给出全部并列选项，执行方按声明策略选择本次动作，不改变并列结论。
    带技能条件的收益必须与调用方重新观察的当前条件一致；缺少条件时不
    使用该组均值。执行方仍须核验技能证据与动作属于同进程且时间新鲜。
    同用途广度的资源允许显式指定本次物品，默认维持稳定顺序；指定不能
    跳过专属资源去消耗用途更广的资源，也不修改永久用途结论。
    """
    from backend.core.fanxiu.storage_bag_prayer_research import (
        order_prayer_top_up_resources, plan_choice_prayer_allocations, soil_box_reward_modifier,
    )

    decision = plan_prayer_closeout(progress, prayer_category=prayer_category)
    if decision["action"] != "top_up":
        return {**decision, "resources": [], "next_open": None}
    from backend.core.fanxiu.data_annotation.tasks.prayer_task_progress import resolve_prayer_round_target
    if resolve_prayer_round_target(round_targets.get(prayer_category), decision["current_round"]) != decision["round_target_score"]:
        raise ValueError("当前祈愿类型与满轮目标不一致")
    stock = {}
    for row in inventory:
        base_id, quantity = row.get("base_id"), row.get("quantity")
        if (type(base_id) is not int or base_id <= 0 or base_id in stock
                or type(quantity) is not int or quantity < 0
                or not row.get("catalog_fingerprint")):
            raise ValueError("补分库存需要唯一物品、非负数量与当前目录版本")
        stock[base_id] = row
    # 每类只使用已核实的同轮规则；未知类别/轮次不进入用途比较。
    current_targets = {}
    for category, rule in round_targets.items():
        try:
            current_targets[category] = resolve_prayer_round_target(rule, decision["current_round"])
        except ValueError:
            continue
    allocations = {row["base_id"]: row for row in
                   plan_choice_prayer_allocations(research, current_targets)}
    summaries = research.get("summaries") or []
    modifiers = {}
    for base, snapshot in (reward_modifier_snapshots or {}).items():
        modifier = soil_box_reward_modifier(snapshot)
        if type(base) is not int or base != modifier['base_id']:
            raise ValueError('当前奖励条件与物品不一致')
        modifiers[base] = modifier['fingerprint']
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
                   and s.get("eligibility") == "verified_positive"
                   and (s.get('reward_modifier_fingerprint') or '') == modifiers.get(base_id, '')]
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
            # allocation_categories 是复合用途竞争范围，故排除了炼丹；
            # 专属炼丹箱仍可在炼丹周使用，按完整单类资源事实判断。
            uses = rule.get("resource_categories", rule.get("allocation_categories")) or []
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
    # 保持专属优先、多用途后用；同一用途广度先用当前差额容得下的
    # 高分箱，尾差选更细粒度箱。期望不是收益上界，每批后仍需重读。
    gap = decision['additional_score']
    ordered.sort(key=lambda r: (len(r['usable_categories']), r['expected_score_per_box'] > gap,
        -r['expected_score_per_box'] if r['expected_score_per_box'] <= gap else r['expected_score_per_box'],
        r['base_id']))
    selected = ordered[0] if ordered else None
    if explicit_base_id is not None:
        if type(explicit_base_id) is not int or explicit_base_id <= 0:
            raise ValueError('本次祈愿物品选择需要正整数 ID')
        chosen = next((r for r in ordered if r['base_id'] == explicit_base_id), None)
        if chosen is None:
            raise ValueError('本次指定物品没有同版本、同条件的可用正收益样本')
        if len(set(chosen['usable_categories']) - {'炼丹'}) != len(set(selected['usable_categories']) - {'炼丹'}):
            raise ValueError('本次物品选择不能跳过专属资源消耗多用途资源')
        selected = chosen
    return {**decision, "prayer_category": prayer_category, "resources": ordered,
            "next_open": {**selected, "open_quantity": 1} if selected else None,
            "outcome": "planned" if ordered else "waiting_verified_resources"}


def select_prayer_closeout_choice(action: Mapping[str, Any], *, policy: str = "stable_id",
                                 explicit_choice: str | None = None) -> str:
    """同类并列最优本次可任选；默认按ID稳定选一次，仍保留全部最优用途。

    require_explicit用于需要外部指定的场合；指定值必须在当前已核实选项内。
    不把本次动作的选项回写为永久推荐，也不把其他并列用途从研究中删除。
    """
    keys = action.get("choice_keys") or []
    if not keys or len(keys) != len(set(keys)) or any(not isinstance(k,str) or not k for k in keys):
        raise ValueError("祈愿自选缺少唯一已核实选项集合")
    if policy not in {"stable_id","require_explicit"}:
        raise ValueError("祈愿自选并列决策策略未定义")
    if explicit_choice is not None:
        if explicit_choice not in keys:
            raise ValueError("指定祈愿选项不在已核实最优集合内")
        return explicit_choice
    if len(keys)>1 and policy=="require_explicit":
        raise ValueError("并列祈愿选项需要明确指定本次选择")
    return min(keys)


def plan_prayer_closeout_transition(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any] | None:
    """活动或轮次变化后停止且不记样本；变化本身不能证明原轮收尾成功。"""
    if any(o.get("complete") is not True or not o.get("activity_key")
           or type(o.get("current_round")) is not int for o in (before,after)):
        raise ValueError("收尾轮次迁移判断需要完整活动身份观察")
    if before["activity_key"] != after["activity_key"]:
        outcome = "activity_changed"
    elif before["current_round"] != after["current_round"]:
        outcome = "round_changed"
    else:
        return None
    return {"result":"pending","action":"stop","outcome":outcome,
            "message":"祈愿活动或轮次已变化，停止消耗并丢弃本次研究样本"}
