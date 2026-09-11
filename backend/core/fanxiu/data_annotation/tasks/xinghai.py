"""资源_星海：每天首购一次真元，按客户端默认原料顺序提纯。

#737 上方是产出预览、下方是原料，不研究方案排序。客户端进入页面和
每次提纯成功后都会选择默认第一项；实测真元不足时数量/上限仍可能为1。
以 Runtime 当前真元、已加载原料单价和选择数共同校验支付能力。
购买幂等以 #738「今日已汇聚真元」为准：>=1 跳过，0 时只准买1次100灵石。
"""
from __future__ import annotations

import re
import time
from datetime import timedelta
from typing import Any

from backend.core.fanxiu.instrumentation.xinghai_runtime import read_xinghai_ui

PURIFICATION_SCENE = 737
CHARGE_SCENE = 738


def parse_charge_state(today_text: str, cost_text: str, selected_count: int) -> dict[str, int]:
    """Fail closed on missing text; existing purchases never authorize another."""
    today = re.search(r'今日已汇聚真元\s*(\d+)\s*/\s*(\d+)\s*次', today_text)
    if today is None:
        raise ValueError(f'无法确认今日汇聚次数：{today_text!r}')
    used, limit = map(int, today.groups())
    if not 0 <= used <= limit or limit < 1:
        raise ValueError('今日汇聚次数范围异常')
    if used:
        return {'used': used, 'limit': limit, 'purchase_count': 0, 'cost': 0}
    cost = re.search(r'\d+\s*/\s*(\d+)', cost_text)
    if selected_count != 1 or cost is None or int(cost.group(1)) != 100:
        raise ValueError('只授权每日首次：1次、100灵石')
    return {'used': 0, 'limit': limit, 'purchase_count': 1, 'cost': 100}


def read_charge_state(context: Any, frame: str) -> dict[str, int]:
    status = read_xinghai_ui('charge')
    today = context.ocr_text_in_shapes(CHARGE_SCENE, ['今日次数'], frame_data_url=frame, padding=0, crop=True)
    cost = context.ocr_text_in_shapes(CHARGE_SCENE, ['消耗'], frame_data_url=frame, padding=0, crop=True)
    return parse_charge_state(today, cost, status['selected_count'])


def ensure_daily_charge(context: Any):
    """Start on the charge dialog; finish on #737, verifying purchase directly."""
    m = yield from context.wait_scene([CHARGE_SCENE], wait=8)
    state = read_charge_state(context, m.frame_data_url)
    if state['used'] >= 1:
        yield from context.wait_click(CHARGE_SCENE, '取消')
        yield from context.wait_scene([PURIFICATION_SCENE], wait=8)
        return {'purchased': False, 'today_count': state['used']}
    yield from context.wait_click(CHARGE_SCENE, '确认')
    yield from context.wait_scene([PURIFICATION_SCENE], wait=12)
    yield from context.wait_click(PURIFICATION_SCENE, '汇聚真元')
    m = yield from context.wait_scene([CHARGE_SCENE], wait=8)
    after = read_charge_state(context, m.frame_data_url)
    if after['used'] != 1:
        raise RuntimeError(f'购买后今日次数不是1，保留现场：{after}')
    yield from context.wait_click(CHARGE_SCENE, '取消')
    yield from context.wait_scene([PURIFICATION_SCENE], wait=8)
    return {'purchased': True, 'today_count': 1}


def purify_default_materials(context: Any, *, max_batches: int = 128):
    """Consume each default selection; never re-send an unconfirmed action.

    After a click the source page can remain visible before the server reply.
    Page recognition alone is therefore not success: wait for the refreshed UI
    energy to fall, then consume the client's newly selected first material.
    """
    batches = []
    for _ in range(max_batches):
        yield from context.wait_scene([PURIFICATION_SCENE], wait=12)
        before = read_xinghai_ui('purification')
        # Real #737 can retain choose/max=1 below the item's cost, even after
        # reselecting. Loaded configuration plus current energy is authoritative.
        if before['selected_config_id'] and before['energy'] < before['unit_energy_cost']:
            return {'batches': batches, 'energy': before['energy'], 'reason': '真元不足'}
        if before['selected_count'] == 0 and before['max_count'] == 0:
            return {'batches': batches, 'energy': before['energy'],
                    'reason': '真元不足' if before['selected_config_id'] else '无原料'}
        if before['selected_count'] != before['max_count'] or before['selected_count'] <= 0:
            raise RuntimeError(f'提纯没有处于默认最大数量：{before}')
        if before['selected_count'] * before['unit_energy_cost'] > before['energy']:
            raise RuntimeError(f'选择数量超过当前真元支付能力：{before}')
        yield from context.wait_click(PURIFICATION_SCENE, '提纯')
        deadline = time.monotonic() + 20
        while True:
            yield from context.wait_scene([PURIFICATION_SCENE], wait=5)
            after = read_xinghai_ui('purification')
            if after['energy'] < before['energy']:
                batches.append({'config_id': before['selected_config_id'],
                                'count': before['selected_count'],
                                'energy_before': before['energy'], 'energy_after': after['energy']})
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('提纯动作20秒未确认生效；保留现场，禁止直接重复点击')
            yield from context.wait_action_settle(0.5)
    raise RuntimeError(f'提纯达到{max_batches}批诊断上限，尚未完成')


def execute_xinghai_task(runner: Any, ctx: dict, payload: dict, stop_event: Any):
    """Replay from a stable boundary and persist next midnight before leaving."""
    from backend.core.fanxiu.data_annotation.effective_time import job_now
    context = runner._behavior_tree_context(ctx, stop_event=stop_event)
    yield from context.go_scene(259)
    yield from context.wait_click(259, '提纯', timeout=15)
    yield from context.wait_scene([PURIFICATION_SCENE], wait=15)
    yield from context.wait_click(PURIFICATION_SCENE, '汇聚真元')
    purchase = yield from ensure_daily_charge(context)
    # Charging refreshes max_count but may retain the earlier smaller choice.
    # Re-entering also restores the default list order after any prior scroll.
    yield from context.wait_click(PURIFICATION_SCENE, '返回')
    yield from context.wait_scene([259], wait=10)
    yield from context.wait_click(259, '提纯')
    yield from context.wait_scene([PURIFICATION_SCENE], wait=15)
    result = yield from purify_default_materials(context)
    next_time = (job_now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    context.set_next_time(next_time.strftime('%Y-%m-%d %H:%M:%S'))
    yield from context.go_scene(34)
    return {'result': 'success', 'message': f"资源_星海完成：{result['reason']}，剩余真元{result['energy']}；今日汇聚{purchase['today_count']}次",
            'purchase': purchase, 'purification': result}
