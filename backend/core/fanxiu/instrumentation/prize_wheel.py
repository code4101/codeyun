"""Read naturally loaded prize-wheel facts, including 天河仙会.

Client RevenueData owns prizeWheelDic[activityId]; this provider never opens a
page, creates a manager, draws, gifts, or purchases. A visible activity does
not prove its wheel data has loaded. Missing data remains unavailable.
"""
from datetime import datetime
from time import perf_counter

from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import acquire_lua_global_runtime_context

_REVENUE_METHODS = frozenset({"Inst_get", "GetRevenueDataInfo", "RevenueDataInfo"})


def read_prize_wheel_view_snapshot(activity_id: int) -> dict:
    """Observe the active wheel's last animation target without opening it.

    The client chooses a grand-prize entry as ``prizeIndex`` whenever the
    latest response contains one. Unlike ``hasBigReward`` (cleared after the
    animation), that target remains visible. This is only the last response,
    never a cumulative hit count or proof a newly requested draw settled.
    A caller must correlate a fresh cost/receipt and persist a proven hit
    before leaving the page; reopening can reset this transient evidence.
    """
    if type(activity_id) is not int or activity_id <= 0:
        raise ValueError("需要明确的正整数活动 ID")
    from .ui_runtime_context import (
        active_ui_component_objects, has_ui_object_fields,
        read_ui_object_field, read_ui_runtime_snapshot,
    )

    def read(context):
        candidates = []
        for obj in active_ui_component_objects(context):
            if (has_ui_object_fields(context, obj.address,
                                     {"arrowImg", "tenTimeToggle", "itemList", "V_ActivityId"})
                    and as_int(read_ui_object_field(context, obj.address, "V_ActivityId")) == activity_id):
                candidates.append(obj)
        base = {"activity_id": activity_id, "read_only": True,
                "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "source": "active_PrizeWheelView.prizeIndex"}
        if not candidates:
            return {**base, "complete": False, "reason": "当期宝盘页面未打开"}
        if len(candidates) != 1:
            raise FanxiuRuntimeMemoryError("当期宝盘页面不唯一", code="runtime_incomplete")
        panel = candidates[0]
        field = lambda key: read_ui_object_field(context, panel.address, key)
        activity = context.reader.fields(field("V_ActivityVO"))
        runtime_id, start_ms, end_ms = (context.reader.long(activity.get(key))
                                        for key in ("id", "startTime", "endTime"))
        if (as_int(activity.get("activityId")) != activity_id
                or runtime_id is None or runtime_id <= 0
                or start_ms is None or end_ms is None or not 0 < start_ms <= end_ms):
            raise FanxiuRuntimeMemoryError("当前宝盘页面活动周期不完整", code="runtime_incomplete")
        ten_draw = field("isToggle")
        currency_amount = context.reader.long(field("curAmount"))
        cost_item_id = as_int(field("costItemId"))
        if (type(ten_draw) is not bool or currency_amount is None or currency_amount < 0
                or cost_item_id is None or cost_item_id <= 0):
            raise FanxiuRuntimeMemoryError("当前宝盘抽奖模式或消费道具未就绪", code="runtime_incomplete")
        indexed, count = context.reader.indexed_list_items(field("itemList"))
        if (count is None or not 0 < count == len(indexed) <= 1000
                or [key for key, _ in indexed] != list(range(1, count + 1))):
            raise FanxiuRuntimeMemoryError("当前宝盘奖池列表不完整", code="runtime_incomplete")
        # Client Cipairs indices are zero-based. Keep the native order.
        rewards = []
        for _, value in indexed:
            row = context.reader.fields(value)
            key, big = as_int(row.get("id")), as_int(row.get("bigReward"))
            if not key or big not in (0, 1):
                raise FanxiuRuntimeMemoryError("当前宝盘奖池条目异常", code="runtime_incomplete")
            rewards.append({"id": key, "is_big_reward": bool(big)})
        if len({row["id"] for row in rewards}) != count:
            raise FanxiuRuntimeMemoryError("当前宝盘奖池ID重复", code="runtime_incomplete")
        index = as_int(field("prizeIndex"))
        if index is not None and not -1 <= index < count:
            raise FanxiuRuntimeMemoryError("当前宝盘动画目标越界", code="runtime_incomplete")
        target = rewards[index] if index is not None and 0 <= index < count else None
        return {**base, "complete": True, "wheel_rewards": rewards,
                "runtime_id": runtime_id, "start_time_ms": start_ms, "end_time_ms": end_ms,
                "ten_draw": ten_draw, "displayed_currency_amount": currency_amount,
                "cost_item_id": cost_item_id,
                "last_response_target": target,
                "waiting_response": field("isWaitingMsg"),
                "animation_running": field("needTween"),
                "evidence": {"pid": context.binding.pid,
                             "process_start_ticks": context.binding.process_start_ticks}}

    # Panel strings may not be interned until its first visit. Keep them lazy
    # so a closed/unvisited window is an observation, not a discovery fault.
    return read_ui_runtime_snapshot((), read, fast=True)


def read_prize_wheel_snapshot(activity_id: int) -> dict:
    """Read progress, gift balance, claimed IDs and configured wheel rewards.

    The exact map key binds the snapshot to one loaded activity. This is an
    observation, not budget/recipient authorization or evidence of a draw.
    Missing counters and truncated lists cannot become zero/empty facts.

    RevenueData.UpdatePrizeWheelResults updates ``flower`` only; ``progress``
    comes from a later FlowerInfo snapshot and may lag a completed draw.
    RevenuePlayVO.records contains public named-player records, not a complete
    personal grand-prize counter. Neither is a fresh per-action settlement.
    Execute against a durable intent and correlate the wallet debit with the
    active view's latest response target before recording a confirmed hit.
    """
    if type(activity_id) is not int or activity_id <= 0:
        raise ValueError("需要明确的正整数活动 ID")
    started = perf_counter()
    context = None
    result = {"activity_id": activity_id, "read_only": True,
              "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "source": "RevenueMgr.RevenueData.prizeWheelDic"}
    try:
        context = acquire_lua_global_runtime_context({"RevenueMgr"})
        reader = context.reader

        def fields(value, label):
            if table_ref(value) is None:
                raise FanxiuRuntimeMemoryError(label + "尚未加载", code="data_not_loaded")
            return reader.fields(value)

        def complete_list(value, label):
            if table_ref(value) is None:
                raise FanxiuRuntimeMemoryError(label + "尚未加载", code="data_not_loaded")
            values, count = reader.list_items(value)
            if count is None or not 0 <= count <= 1000 or len(values) != count:
                raise FanxiuRuntimeMemoryError(label + "列表不完整", code="runtime_incomplete")
            return values

        manager = context.loaded_manager_fields("RevenueMgr", _REVENUE_METHODS)
        instance = fields(manager.get("inst"), "RevenueMgr实例")
        model = fields(instance.get("Model"), "RevenueMgr.Model")
        data = fields(model.get("RevenueData"), "RevenueData")
        wheel_map_ref = data.get("prizeWheelDic")
        wheel_map = fields(wheel_map_ref, "宝盘活动列表")
        if "_dt_" in wheel_map:
            wheel_map = reader.dictionary_fields(wheel_map_ref)
        info = fields(wheel_map.get(activity_id), f"宝盘活动{activity_id}")
        progress, flowers = (reader.long(info.get(key)) for key in ("progress", "flower"))
        if progress is None or flowers is None or min(progress, flowers) < 0:
            raise FanxiuRuntimeMemoryError("宝盘进度或赠礼余额缺失", code="runtime_incomplete")
        claimed = [as_int(value) for value in complete_list(info.get("draws"), "宝盘已领进度奖励")]
        if any(value is None or value <= 0 for value in claimed) or len(set(claimed)) != len(claimed):
            raise FanxiuRuntimeMemoryError("宝盘已领奖励ID异常", code="runtime_incomplete")
        rewards = []
        for value in complete_list(info.get("items"), "宝盘奖池"):
            item = fields(value, "宝盘奖励")
            item_id = as_int(item.get("id"))
            reward = item.get("turnReward")
            big_reward = as_int(item.get("bigReward"))
            if not item_id or not isinstance(reward, str) or not reward or big_reward not in (0, 1):
                raise FanxiuRuntimeMemoryError("宝盘奖励配置不完整", code="runtime_incomplete")
            rewards.append({"id": item_id, "reward": reward, "is_big_reward": bool(big_reward)})
        if not rewards or len({row["id"] for row in rewards}) != len(rewards):
            raise FanxiuRuntimeMemoryError("宝盘奖池为空或ID重复", code="runtime_incomplete")
        # RevenueCfgData's server-owned dictionary is populated naturally by
        # the client. Its absence must not initialize it or invent a price.
        config_store = reader.fields(model.get("V_RevenueCfgData"))
        config_map = reader.dictionary_fields(config_store.get("V_DataDic"))
        config = reader.fields(config_map.get(activity_id))
        cost_type, cost_value, flower_item = (as_int(config.get(key))
                                             for key in ("costType", "costValue", "flowerItem"))
        configuration = {"available": False, "reason": "当期消费配置尚未完整加载"}
        if all(value is not None and value > 0 for value in (cost_type, cost_value, flower_item)):
            configuration = {"available": True, "currency_type": cost_type,
                             "cost_per_draw": cost_value, "gift_item_id": flower_item}
        return {**result, "ok": True, "available": True, "complete": True,
                "progress": progress, "gift_balance": flowers,
                "claimed_progress_reward_ids": claimed, "wheel_rewards": rewards,
                "configuration": configuration,
                "elapsed_seconds": perf_counter() - started,
                "evidence": {"pid": context.binding.pid,
                             "process_start_ticks": context.binding.process_start_ticks}}
    except (FanxiuRuntimeMemoryError, OSError, ValueError) as exc:
        return {**result, "ok": False, "available": False, "complete": False,
                "reason": str(exc), "code": getattr(exc, "code", "runtime_unavailable"),
                "elapsed_seconds": perf_counter() - started}
