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


def project_prize_wheel_milestones(progress, milestones, claimed_ids):
    """Project the native four-slot bar from its ordered, complete config.

    The client uses the number of claimed IDs to page/mark slots. Only a
    verified prefix of the native order may use that geometry. Claims live in
    RevenuePlayVO.draws; FlowerInfo.draws alone can lag a just-completed claim.
    """
    if type(progress) is not int or progress < 0:
        raise ValueError('宝盘累计次数不可读')
    if not milestones or len(milestones) > 1000:
        raise ValueError('宝盘累计奖励配置缺失或越界')
    ids = [r.get('id') for r in milestones]
    thresholds = [r.get('progress') for r in milestones]
    if (any(type(v) is not int or v <= 0 for v in ids + thresholds)
            or len(set(ids)) != len(ids)
            or any(a >= b for a, b in zip(thresholds, thresholds[1:]))
            or any(not isinstance(r.get('reward'), str) or not r['reward'] for r in milestones)):
        raise ValueError('宝盘累计奖励顺序或条目不完整')
    if (any(type(v) is not int or v <= 0 for v in claimed_ids)
            or len(set(claimed_ids)) != len(claimed_ids)
            or set(claimed_ids) != set(ids[:len(claimed_ids)])):
        raise ValueError('宝盘领取ID不是原生档位前缀，不能按数量定位')
    start = min(len(claimed_ids) // 4 * 4, max(0, len(ids) - 4))
    rows = [{**r, 'claimed': r['id'] in claimed_ids,
             'claimable': r['progress'] <= progress and r['id'] not in claimed_ids}
            for r in milestones]
    return {'milestones': rows, 'claimed_reward_ids': list(claimed_ids),
            'claimable_reward_ids': [r['id'] for r in rows if r['claimable']],
            'visible_milestones': [{**r, 'visible_slot': slot + 1}
                                   for slot, r in enumerate(rows[start:start + 4])]}


def read_prize_wheel_cumulative_rewards(activity_id: int) -> dict:
    """Read loaded wheel progress + canonical RevenuePlayVO claimed IDs.

    Does not open a page or request a refresh. A claim must be checked through
    this canonical list, not the independently refreshed FlowerInfo snapshot.
    """
    if type(activity_id) is not int or activity_id <= 0:
        raise ValueError('需要精确活动ID')
    started = perf_counter()
    base = {'activity_id': activity_id, 'read_only': True,
            'source': 'RevenueMgr.prizeWheelDic.progress+RevenuePlayVO.draws',
            'captured_at': datetime.now().astimezone().isoformat(timespec='seconds')}
    try:
        context = acquire_lua_global_runtime_context({'RevenueMgr'})
        reader = context.reader
        def fields(value, label):
            if table_ref(value) is None:
                raise FanxiuRuntimeMemoryError(label + '尚未加载', code='data_not_loaded')
            return reader.fields(value)
        def dictionary(value, label):
            row = fields(value, label)
            return reader.dictionary_fields(value) if '_dt_' in row else row
        def values(value, label):
            fields(value, label)
            indexed, count = reader.indexed_list_items(value)
            if (count is None or not 0 <= count <= 1000 or len(indexed) != count
                    or [k for k, _ in indexed] != list(range(1, count + 1))):
                raise FanxiuRuntimeMemoryError(label + '列表不完整', code='runtime_incomplete')
            return [v for _, v in indexed]
        manager = context.loaded_manager_fields('RevenueMgr', _REVENUE_METHODS)
        model = fields(fields(manager.get('inst'), 'Revenue实例').get('Model'), 'Revenue模型')
        data = fields(model.get('RevenueData'), 'Revenue数据')
        wheel = fields(dictionary(data.get('prizeWheelDic'), '宝盘列表').get(activity_id), '当期宝盘')
        activity = fields(dictionary(data.get('V_ActivityDic'), 'Revenue活动列表').get(activity_id), '当期累计奖励')
        play = fields(activity.get('revenuePlayVO'), '当期玩家领奖记录')
        configs = fields(activity.get('rateConfigs'), '当期累计奖励配置')
        milestones = []
        for value in values(configs.get('itemList'), '累计奖励档位'):
            row = fields(value, '累计奖励条目')
            milestones.append({'id': as_int(row.get('id')), 'progress': as_int(row.get('progress')),
                               'reward': row.get('reward')})
        claimed = [as_int(v) for v in values(play.get('draws'), '累计奖励已领取ID')]
        projection = project_prize_wheel_milestones(reader.long(wheel.get('progress')), milestones, claimed)
        return {**base, 'complete': True, 'available': True, 'progress': reader.long(wheel.get('progress')),
                **projection, 'elapsed_seconds': perf_counter() - started,
                'evidence': {'pid': context.binding.pid, 'process_start_ticks': context.binding.process_start_ticks}}
    except (FanxiuRuntimeMemoryError, ValueError, OSError) as exc:
        return {**base, 'complete': False, 'available': False, 'reason': str(exc),
                'code': getattr(exc, 'code', 'runtime_unavailable'), 'elapsed_seconds': perf_counter() - started}


def read_prize_wheel_reward_window() -> dict:
    """Observe the shared native reward window's timer/animation, without clicks.

    A first root click may merely skip item animations; only a later click
    closes it. Positive auto-close timers must be allowed to close naturally
    so a delayed click cannot penetrate into the activity behind the popup.
    """
    from .ui_runtime_context import (read_active_ui_window_panels,
                                    read_ui_object_field, read_ui_runtime_snapshot)

    def read(context):
        panels = read_active_ui_window_panels(context, 'WinAlertRewardView')
        base = {'read_only': True, 'source': 'UIShowMgr.WinAlertRewardView',
                'evidence': {'pid': context.binding.pid,
                            'process_start_ticks': context.binding.process_start_ticks}}
        if not panels:
            return {**base, 'is_open': False}
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError('奖励窗口不唯一', code='runtime_incomplete')
        field = lambda k: read_ui_object_field(context, panels[0].address, k)
        return {**base, 'is_open': True, 'panel_address': f'0x{panels[0].address:x}',
                'auto_close_seconds': as_int(field('V_autoCloseTime')),
                'auto_close_timer_id': as_int(field('V_AutoCloseTimerId')),
                'auto_close_end_time_ms': context.reader.long(field('V_AutoJumpEndTime')),
                'quick_hit_end': field('quickHitEnd')}

    return read_ui_runtime_snapshot((), read, fast=True)


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
        read_ui_object_field, read_ui_runtime_snapshot, read_ui_selected_tab_panel,
    )

    def read(context):
        candidates = {}
        for obj in active_ui_component_objects(context):
            selected = read_ui_selected_tab_panel(context, obj.address)
            addresses = {obj.address}
            if selected:
                addresses.add(selected[0])
            for address in addresses:
                if (has_ui_object_fields(context, address, {"arrowImg", "tenTimeToggle", "itemList"})
                        and as_int(read_ui_object_field(context, address, "V_ActivityId")) == activity_id):
                    candidates[address] = address
        base = {"activity_id": activity_id, "read_only": True,
                "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "source": "active_PrizeWheelView.prizeIndex"}
        if not candidates:
            return {**base, "complete": False, "reason": "当期宝盘页面未打开"}
        if len(candidates) != 1:
            raise FanxiuRuntimeMemoryError("当期宝盘页面不唯一", code="runtime_incomplete")
        panel_address = next(iter(candidates))
        field = lambda key: read_ui_object_field(context, panel_address, key)
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
        queued, queued_count = context.reader.list_items(field('rewardsList'))
        reward_wait_frames = as_int(field('rewardWaitFrameCount'))
        if queued_count is None or len(queued) != queued_count or reward_wait_frames is None or reward_wait_frames < 0:
            raise FanxiuRuntimeMemoryError('宝盘待显示奖励状态不完整', code='runtime_incomplete')
        return {**base, "complete": True, "wheel_rewards": rewards,
                "panel_address": f"0x{panel_address:x}",
                "runtime_id": runtime_id, "start_time_ms": start_ms, "end_time_ms": end_ms,
                "ten_draw": ten_draw, "displayed_currency_amount": currency_amount,
                "cost_item_id": cost_item_id,
                "last_response_target": target,
                "waiting_response": field("isWaitingMsg"),
                "animation_running": field("needTween"),
                "queued_reward_count": queued_count,
                "reward_wait_frame_count": reward_wait_frames,
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
