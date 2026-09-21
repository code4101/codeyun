"""资源_星海：每天首购一次真元并提纯，再按当前 OCR 顺序处理四域外围。

#737 上方是产出预览、下方是原料，不研究方案排序。客户端进入页面和
每次提纯成功后都会选择默认第一项；实测真元不足时数量/上限仍可能为1。
以 Runtime 当前真元、已加载原料单价和选择数共同校验支付能力。
购买幂等以 #738「今日已汇聚真元」为准：>=1 跳过，0 时只准买1次100灵石。

提纯后回到 #259 处理四域：红点判定、OCR 顺序点击、落点身份、升级、
悟道树与返回链均有真实验收，正式作业已验证首购幂等与无红点收尾。
整合后的有红点分支仍需后续真实作业覆盖；不购买额外材料，不新建独立 job，
未知原因一律报错而不当作完成。
"""
from __future__ import annotations

import re
import time
from datetime import timedelta
from typing import Any

from backend.core.fanxiu.instrumentation.xinghai_runtime import read_xinghai_ui

PURIFICATION_SCENE = 737
CHARGE_SCENE = 738


def enter_next_pending_domain(context: Any):
    """星海内部子步骤：Runtime 筛选，当前 OCR 顺序点击一个外围入口。

    仅完成入口点击；落点验收和域内业务由后续子步骤负责。每次从 #259
    新取状态，不跨 Cell 保存选择游标；未知状态保留，不能当作 pass。
    """
    from backend.core.fanxiu.instrumentation.xinghai_runtime import read_xinghai_entry_states
    from backend.core.fanxiu.runtime_gui.xinghai import click_pending_xinghai_entry

    yield from context.wait_scene([259], wait=10)
    state = read_xinghai_entry_states()
    targets = [row['name'] for row in state['entries'] if row['red_dot'] is True]
    if not targets and not state['complete']:
        raise RuntimeError('星海入口状态不完整，不能判定全部完成')
    return (yield from click_pending_xinghai_entry(context, targets))


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


def execute_xinghai_domains(context: Any):
    """起止 #259，按当前 OCR 顺序处理四域，树内按行优先；每域最多一次。

    各子组件均已在真实帧逐项验收：``read_xinghai_entry_states`` 给红点与
    eligibility，``click_pending_xinghai_entry`` 按当前帧 OCR 顺序点击，
    ``read_xinghai_domain_identity`` 核对 #763 落点，``open_current_xinghai_upgrade``
    /``fill_current_xinghai_upgrade``/``fill_current_xinghai_tree`` 完成升级与悟道树；
    本函数只是新增整合，尚待主 agent 正式运行验收，故不假设满级、不购买材料、
    不新建独立 job。每次在 #259 只读一次状态：状态须 ``complete``，红点且未处理
    的域交给点击子动作按 OCR 选择（不指定域顺序）；已处理域不再进入，防止红点
    驱动循环。初始原因含未支持动作（可领取/可进阶/可觉醒）立即报错，绝不把未知
    当 pass。升级产生树点或初始即可激活节点时才进 #765 悟道树。每域结束沿明确
    返回链回到 #259 并重新读状态。残留未处理红点、残留升级或未支持/未知原因都
    报错；任何残留红点都说明仍有可处理业务，不能以首个节点材料不足作为完成。
    无红点才返回 ``all_clear=True``。
    """
    from backend.core.fanxiu.instrumentation.xinghai_runtime import (
        read_xinghai_domain_identity,
        read_xinghai_entry_states,
    )
    from backend.core.fanxiu.runtime_gui.xinghai import (
        XINGHAI_DOMAIN_VIEW,
        XINGHAI_TREE_VIEW,
        XINGHAI_UPGRADE_VIEW,
        click_pending_xinghai_entry,
        fill_current_xinghai_tree,
        fill_current_xinghai_upgrade,
        open_current_xinghai_upgrade,
    )

    supported = {'可升级', '可激活节点'}
    processed: set[str] = set()
    receipts: list[dict[str, Any]] = []

    while True:
        yield from context.wait_scene([259], wait=10, label='星海：确认外围')
        state = read_xinghai_entry_states()
        if state.get('complete') is not True:
            raise RuntimeError('星海入口状态不完整，不能判定全部完成')
        targets = [row['name'] for row in state['entries']
                   if row['red_dot'] is True and row['name'] not in processed]
        if not targets:
            break

        clicked = yield from click_pending_xinghai_entry(context, targets)
        if clicked.get('clicked') is not True:
            raise RuntimeError(f'星海待处理入口未识别却报告无点击：{clicked!r}')
        target = clicked['target']
        row = next((r for r in state['entries'] if r['name'] == target), None)
        if row is None:
            raise RuntimeError(f'星海点击目标不在当前状态：{target}')
        reasons = list(row.get('reasons') or [])
        if set(reasons) - supported:
            raise RuntimeError(f'星海 {target} 含未支持动作：{reasons}')
        if not reasons:
            raise RuntimeError(f'星海 {target} 红点原因未知：{reasons}')
        processed.add(target)

        yield from context.wait_scene([XINGHAI_DOMAIN_VIEW], wait=10, label=f'星海：确认进入{target}')
        identity = read_xinghai_domain_identity()
        if identity.get('id') != row['id'] or identity.get('name') != target:
            raise RuntimeError(f'星海 {target} 落点身份不符：{identity} vs id={row["id"]}')

        yield from open_current_xinghai_upgrade(context, row['id'])
        upgrade = None
        if '可升级' in reasons:
            upgrade = yield from fill_current_xinghai_upgrade(context)
        tree = None
        if (upgrade is not None and upgrade.get('steps', 0) > 0) or '可激活节点' in reasons:
            yield from context.wait_click(XINGHAI_UPGRADE_VIEW, '悟道树入口')
            yield from context.wait_scene([XINGHAI_TREE_VIEW], wait=10, label=f'星海 {target}：确认悟道树')
            tree = yield from fill_current_xinghai_tree(context)
            yield from context.wait_click(XINGHAI_TREE_VIEW, '返回')
            yield from context.wait_scene([XINGHAI_UPGRADE_VIEW], wait=10, label=f'星海 {target}：返回升级页')
        yield from context.wait_click(XINGHAI_UPGRADE_VIEW, '返回')
        yield from context.wait_scene([XINGHAI_DOMAIN_VIEW], wait=10, label=f'星海 {target}：返回域内展示页')
        yield from context.wait_click(XINGHAI_DOMAIN_VIEW, '返回')
        yield from context.wait_scene([259], wait=10, label='星海：返回外围')
        receipts.append({'name': target, 'id': row['id'], 'reasons': reasons,
                         'upgrade': upgrade, 'tree': tree})

    # The loop's final observation is already fresh and complete; no GUI action
    # occurred after it, so use the same snapshot for the terminal decision.
    red = [row for row in state['entries'] if row['red_dot'] is True]
    unprocessed = [row['name'] for row in red if row['name'] not in processed]
    if unprocessed:
        raise RuntimeError(f'星海仍有未处理红点：{unprocessed}')
    if not red:
        return {'all_clear': True, 'domains': receipts, 'state': state}

    raise RuntimeError(f'星海已处理域仍有红点，需核对全树可升级候选，不能视为完成：{red}')


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
    yield from context.wait_click(PURIFICATION_SCENE, '返回')
    yield from context.wait_scene([259], wait=10)
    domains = yield from execute_xinghai_domains(context)
    next_time = (job_now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    context.set_next_time(next_time.strftime('%Y-%m-%d %H:%M:%S'))
    yield from context.go_scene(34)
    if domains['all_clear']:
        message = (f"资源_星海完成：{result['reason']}，剩余真元{result['energy']}；"
                   f"今日汇聚{purchase['today_count']}次；星海四域外围已完成")
    else:
        blocked = '、'.join(f"{item['name']}({item['reason']})" for item in domains['blocked'])
        message = (f"资源_星海完成：{result['reason']}，剩余真元{result['energy']}；"
                   f"今日汇聚{purchase['today_count']}次；星海四域按节点顺序暂不可继续：{blocked}")
    return {'result': 'success', 'message': message, 'purchase': purchase,
            'purification': result, 'domains': domains}
