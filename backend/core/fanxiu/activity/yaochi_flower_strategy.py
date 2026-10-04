from __future__ import annotations

"""瑶池仙花策略：一期一人，领取回礼后按固定阶段重置，继续给同一人。

选人、榜期绑定、循环动作规划各有独立公共入口；均不操作游戏。
全库存是策略目标，不代表消耗授权；执行器仍须遵守调用方资源预算。
榜单档次奖励完成不能当成人物送满，也不能触发重选人物。
"""

from copy import deepcopy
from typing import Any, Mapping

from pydantic import BaseModel

from backend.core.fanxiu.instrumentation.xianyuan_atlas import (
    first_supported_wujing_target,
)


def _snapshot_dict(snapshot: Mapping[str, Any] | BaseModel) -> dict[str, Any]:
    if isinstance(snapshot, BaseModel):
        return snapshot.model_dump(mode="python")
    return deepcopy(dict(snapshot))


def _require_complete(snapshot: Mapping[str, Any], *, name: str, field: str) -> None:
    if snapshot.get(field) is not True:
        detail = str(snapshot.get("runtime_error") or "").strip()
        suffix = f"：{detail}" if detail else ""
        raise ValueError(f"{name}快照不完整，拒绝规划仙花去向{suffix}")


def _flower_items_and_friendship(
    flower_snapshot: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], int, int]:
    items: list[dict[str, Any]] = []
    total_count = 0
    total_friendship = 0
    for source in flower_snapshot.get("items") or []:
        if not isinstance(source, Mapping):
            raise ValueError("仙花资源快照包含无效条目，拒绝规划仙花去向")
        item = deepcopy(dict(source))
        count = item.get("count")
        friendship = item.get("friendship")
        if count is None or friendship is None:
            raise ValueError("仙花资源快照缺少数量或友好度，拒绝规划仙花去向")
        count = int(count)
        friendship = int(friendship)
        if count < 0 or friendship < 0:
            raise ValueError("仙花资源快照包含负数，拒绝规划仙花去向")
        if count <= 0:
            continue
        item["count"] = count
        item["friendship"] = friendship
        item["total_friendship"] = count * friendship
        items.append(item)
        total_count += count
        total_friendship += count * friendship
    return items, total_count, total_friendship


def plan_yaochi_flower_recipient(
    gongfa_snapshot: Mapping[str, Any] | BaseModel,
    xianyuan_snapshot: Mapping[str, Any] | BaseModel,
    flower_snapshot: Mapping[str, Any] | BaseModel,
    *,
    instance_key: str = "",
    binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """规划一期单人送花；已有绑定时绝不重新择人。

    The caller must provide three complete snapshots.  This function performs
    no Runtime, database, GUI, or scheduler operation.  It intentionally does
    not split the inventory or cap spending at a reward boundary: one activity
    sends all currently owned flowers to the single best recipient selected by
    the existing GongFa-to-Wujing recommendation model. 未传 instance_key 的
    结果仅供候选预览；正式执行必须先调用 bind_yaochi_flower_recipient
    落盘，然后每次将该期绑定传回来。Runtime index 每次从新快照解析，
    不落盘；绑定人物失踪、敌对或不可送礼时停止，不能自行换人。
    """

    gongfa = _snapshot_dict(gongfa_snapshot)
    xianyuan = _snapshot_dict(xianyuan_snapshot)
    flowers = _snapshot_dict(flower_snapshot)
    _require_complete(gongfa, name="功法图鉴", field="runtime_complete")
    _require_complete(xianyuan, name="仙缘图鉴", field="runtime_complete")
    _require_complete(flowers, name="仙花资源", field="complete")

    books = [
        deepcopy(dict(book))
        for book in gongfa.get("books") or []
        if isinstance(book, Mapping)
    ]
    people = [
        deepcopy(dict(person))
        for person in xianyuan.get("people") or []
        if isinstance(person, Mapping)
    ]
    if not books:
        raise ValueError("功法图鉴没有功法数据，拒绝规划仙花去向")
    if not people:
        raise ValueError("仙缘图鉴没有人物数据，拒绝规划仙花去向")

    if binding is not None:
        if not instance_key or binding.get("instance_key") != instance_key:
            raise ValueError("仙花人物绑定与当前榜期不一致")
        target = deepcopy(binding.get("target_gongfa"))
        recommendation = deepcopy(binding.get("recommendation"))
        projected_people = people
    else:
        target, projected_people, recommendation = first_supported_wujing_target(
            people,
            books,
        )
    if target is None or recommendation is None:
        raise ValueError("当前功法优先序中没有可由仙缘悟境奖励推进的仙品或神品功法")

    npc_id = int(recommendation.get("npc_id") or 0)
    chosen = next(
        (
            person
            for person in projected_people
            if int(person.get("npc_id") or 0) == npc_id
        ),
        None,
    )
    if chosen is None:
        raise ValueError("仙缘推荐结果未映射到 Runtime 人物，拒绝规划仙花去向")
    if binding is not None and (
        not chosen.get("giftable") or chosen.get("hostile")
        or int(chosen.get("activity_flower_gift_count") or 0) <= 0
    ):
        raise ValueError("本期固定人物当前不可送仙花；停止，不重新选人")

    flower_items, total_count, total_friendship = _flower_items_and_friendship(flowers)
    return {
        "status": "ready",
        "strategy": "single_recipient_all_flowers",
        "chosen_npc": {
            "npc_id": npc_id,
            "runtime_index": chosen.get("runtime_index"),
            "name": str(chosen.get("name") or recommendation.get("name") or npc_id),
        },
        "target_gongfa": deepcopy(target),
        "recommendation": deepcopy(recommendation),
        "flower_items": flower_items,
        "total_flower_count": total_count,
        "total_friendship": total_friendship,
        "ignore_reward_overflow": True,
        "recipient_count": 1,
        "instance_key": instance_key,
        "recipient_locked": binding is not None,
        "cycle_policy": {
            "reset_step": recommendation.get("reset_step"),
            "start_level": recommendation.get("cycle_start_level"),
            "end_level": recommendation.get("cycle_end_level"),
            "claim_rewards_before_reset": True,
            "keep_recipient_after_reset": True,
            "reset_requires_live_eligibility": True,
        },
    }


def plan_yaochi_flower_cycle_action(
    binding: Mapping[str, Any],
    observation: Mapping[str, Any],
    *,
    remaining_friendship: int,
) -> dict[str, Any]:
    """用本轮真实事实规划单步；不预先生成可盲目重放的赠礼/重置循环。

    observation 契约：complete、npc_id、gift_capacity_friendship（当前
    可接收友好度）、claimable_reward_count、can_reset、reset_step。
    满指本期固定 reset_step 的游戏容量耗尽，不是榜分或永久好感等级。
    容量为零时先领回礼，再验证固定阶段可重置；每个动作后必须重新
    观察。异常或不能重置则保留现场，不换人、不重复赠礼。
    返回 gift 的友好度只是批次上限，执行器还须按道具面值取整及预算。
    本函数不证明 GUI 重置执行器已接入或真实验收。
    """
    npc_id = int((binding.get("recommendation") or {}).get("npc_id") or 0)
    if not binding.get("instance_key") or npc_id <= 0:
        raise ValueError("循环规划要求有效榜期人物绑定")
    if observation.get("complete") is not True or observation.get("npc_id") != npc_id:
        raise ValueError("仙花循环观察不完整或人物不符")
    remaining = int(remaining_friendship)
    capacity = observation.get("gift_capacity_friendship")
    rewards = observation.get("claimable_reward_count")
    if capacity is None or rewards is None or min(remaining, int(capacity), int(rewards)) < 0:
        raise ValueError("仙花循环缺少可靠容量/回礼/预算事实")
    result = {"npc_id": npc_id, "instance_key": binding["instance_key"]}
    if int(rewards) > 0:
        return {**result, "action": "claim_rewards"}
    if remaining == 0:
        return {**result, "action": "complete"}
    step = (binding.get("recommendation") or {}).get("reset_step")
    if step is None or observation.get("reset_step") != step:
        return {**result, "action": "blocked", "reason": "尚未确认固定重置阶段的容量"}
    if int(capacity) > 0:
        return {**result, "action": "gift", "friendship_limit": min(remaining, int(capacity))}
    if observation.get("can_reset") is not True:
        return {**result, "action": "blocked", "reason": "送满但尚未确认固定阶段可重置"}
    return {**result, "action": "reset", "reset_step": step}


__all__ = ["plan_yaochi_flower_recipient", "plan_yaochi_flower_cycle_action"]
