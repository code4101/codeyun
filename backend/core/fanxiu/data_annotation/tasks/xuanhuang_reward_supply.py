from __future__ import annotations

"""玄荒日常补给：只买已验收的单枚令牌，预算由调用方明确提供。"""

from typing import Any

from .daily_xuanhuang import XUANHUANG_DAILY_TASK_ID, xuanhuang_daily_target_complete


XUANHUANG_SUPPLY_SCENE = 946


def plan_xuanhuang_reward_supply(dialog: dict[str, Any], *, budget_lingshi: int) -> dict[str, Any]:
    """购买框本次身份、单枚数量和价格必须完整，缺预算时不授权动作。"""
    if type(budget_lingshi) is not int or budget_lingshi < 0:
        raise ValueError("玄荒补给预算必须为非负整数灵石")
    if budget_lingshi == 0:
        return {"ready": False, "reason": "reward_supply_budget_required", "quantity": 0, "cost": 0}
    if (dialog.get("complete") is not True or dialog.get("identity_complete") is not True
            or dialog.get("goods_id") != 170001 or dialog.get("item_id") != 39031001
            or dialog.get("cost_item_id") != 1 or dialog.get("goodsNum") != 1):
        raise RuntimeError("玄荒补给购买框身份或单枚产出不完整，禁止购买")
    price = dialog.get("Price")
    if type(price) is not int or not 0 < price <= 300:
        raise RuntimeError("玄荒补给单价超出已验收的300灵石范围")
    if price > budget_lingshi:
        return {"ready": False, "reason": "reward_supply_budget_insufficient", "quantity": 0, "cost": 0}
    if (dialog.get("CanBuy") is not True or dialog.get("isEnough") is not True
            or type(dialog.get("maxNum")) is not int or dialog["maxNum"] < 1):
        return {"ready": False, "reason": "reward_supply_unavailable", "quantity": 0, "cost": 0}
    if dialog.get("showNum") != 1:
        raise RuntimeError("玄荒补给当前购买数量不是1，禁止购买")
    return {"ready": True, "reason": "single_daily_ticket", "quantity": 1, "cost": price}


def purchase_xuanhuang_daily_reward_chance(context: Any, *, budget_lingshi: int):
    """从真实 #418 补一次奖励；先验证日常缺口，买后确认奖励次数增加。

    调用方负责已授权预算、进入挑战页及完成后离场。默认预算0只返回
    未授权结果。失败后再次调用先读实际次数，已有次数时不重复购买。
    购买框已真实验收；购买后的次数增量仍须首次真实验收。
    """
    from backend.core.fanxiu.instrumentation.daily_task import read_daily_task_snapshot
    from backend.core.fanxiu.instrumentation.xuanhuang import read_xuanhuang_snapshot
    from backend.core.fanxiu.instrumentation.common_shop_buy_dialog import read_common_shop_buy_dialog_snapshot

    if type(budget_lingshi) is not int or budget_lingshi < 0:
        raise ValueError("玄荒补给预算必须为非负整数灵石")
    if budget_lingshi == 0:
        return plan_xuanhuang_reward_supply({}, budget_lingshi=budget_lingshi)
    daily = read_daily_task_snapshot(XUANHUANG_DAILY_TASK_ID)
    if xuanhuang_daily_target_complete(daily):
        return {"purchased": False, "reason": "daily_target_complete", "cost": 0}
    before = read_xuanhuang_snapshot()
    if before.get("complete") is not True:
        raise RuntimeError("玄荒补给前奖励次数不完整")
    if before["reward_chances_remaining"] > 0:
        return {"purchased": False, "reason": "reward_chances_available", "cost": 0}
    yield from context.wait_scene_exact([418], timeout=30)
    yield from context.wait_click_then_scene(418, "补充次数", XUANHUANG_SUPPLY_SCENE, timeout=30)
    plan = plan_xuanhuang_reward_supply(read_common_shop_buy_dialog_snapshot(), budget_lingshi=budget_lingshi)
    if not plan["ready"]:
        return {**plan, "purchased": False}
    # Snapshot紧邻唯一购买动作；不对购买按钮进行自动补点。
    context.click_shape_center(XUANHUANG_SUPPLY_SCENE, "购买并使用")
    yield from context.wait_action_settle(2)
    after = read_xuanhuang_snapshot()
    if (after.get("complete") is not True
            or after.get("reward_chances_remaining") != before["reward_chances_remaining"] + 1):
        raise RuntimeError("玄荒令牌购买后未确认奖励次数增加1，停止补点并保留现场")
    landing = yield from context.wait_scene([418, XUANHUANG_SUPPLY_SCENE], wait=5)
    if landing.id == XUANHUANG_SUPPLY_SCENE:
        yield from context.wait_click_then_scene(XUANHUANG_SUPPLY_SCENE, "返回", 418, timeout=30)
    elif landing.id != 418:
        raise RuntimeError(f"玄荒补给已增加次数，但离场遇到未验收页面#{landing.id}")
    return {"purchased": True, "cost": plan["cost"], "currency": "灵石", "quantity": 1,
            "reward_chances_before": before["reward_chances_remaining"],
            "reward_chances_after": after["reward_chances_remaining"]}
