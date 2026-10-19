"""仙纹升级研发入口：只打开已知强化页并读取专用材料，不消耗。"""
from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError
from backend.core.fanxiu.instrumentation.xianqiao import read_xianqiao_snapshot
from backend.core.fanxiu.instrumentation.xianqiao_upgrade import read_xianqiao_upgrade_resources_snapshot
from backend.core.fanxiu.instrumentation.xianqiao_upgrade import (
    read_xianqiao_upgrade_panel_snapshot, read_xianqiao_exp_selection_snapshot,
    read_xianqiao_strengthening_limits_snapshot,
)


STAGE_ID = 'xianqiao-dedicated-upgrade'
STAGE_VERSION = '1'
_PARTS = {1:'天璇', 2:'天玑', 3:'天权', 4:'玉衡', 5:'开阳', 6:'瑶光'}
_ELEMENT_RESULT = 967
_MAX_UPGRADE = 968


def xianqiao_stock_increment_key(stock, state):
    """Pure identity of authorized same-day stock against the worn experience.

    None means no dedicated experience is available. The key includes each
    worn instance's sumExp, so stock consumed and later replenished to the
    same count is a new increment, while unusable leftovers stay one receipt.
    """
    if stock.get('complete') is not True or state.get('complete') is not True:
        raise RuntimeError('仙纹增量准入需要完整的库存与装配快照')
    if stock['available_exp'] <= 0:
        return None
    items = ','.join(f"{row['item_id']}={row['quantity']}" for row in stock['items'] if row['quantity'])
    worn = ','.join(f"{row['equip_id']}@{row['sum_exp']}"
                    for row in sorted(state['equipped'], key=lambda row: row['part_id']))
    return f'stock:{items};worn:{worn}'


def xianqiao_upgrade_increment(previous_result):
    """Read-only admission after today's receipt; never opens a page or consumes."""
    return xianqiao_stock_increment_key(read_xianqiao_upgrade_resources_snapshot(), read_xianqiao_snapshot())


class XianqiaoUpgradePending(RuntimeError):
    """The submitted transaction has not yet reached its exact native delta."""


def inspect_xianqiao_upgrade_resources(context, *, part='天璇', timeout=30.0):
    """Observe the current 玉京天璇 upgrade page and naturally loaded materials.

    This bounded inspection supports the currently observed 玉京 system only.
    It never presses 强化、快速置入 or any material-use button.
    """
    before = read_xianqiao_snapshot()
    if before.get('complete') is not True or before.get('active_system_type') != 2:
        raise RuntimeError('仙纹强化检查当前仅支持已核实的玉京体系')
    expected = [row for row in before['equipped'] if _PARTS.get(row['part_id']) == part]
    if len(expected) != 1:
        raise RuntimeError('指定部位未唯一装配仙纹，拒绝打开未通窍部位')
    frame = context.cur_frame(update=True)
    opened = any(context.match_view(scene, frame_data_url=frame)[0] for scene in (487, _MAX_UPGRADE))
    if opened:
        current = read_xianqiao_upgrade_panel_snapshot()
        if current['target']['baseId'] == expected[0]['base_id'] and current['target']['wear'] is True:
            return dict(status='upgrade_page_observed', scene=487,
                        resources=read_xianqiao_upgrade_resources_snapshot(),
                        equipped=before['equipped'], consumed=False)
    from .bottle_menu import enter_xianqiao_page
    yield from enter_xianqiao_page(context)
    yield from context.wait_click_then_scene(484, '玉京', [485], timeout=timeout)
    yield from context.wait_click_then_scene(485, part, [486], timeout=timeout)
    yield from context.wait_click_then_scene(486, '强化', [487, _MAX_UPGRADE], timeout=timeout)
    resources = read_xianqiao_upgrade_resources_snapshot()
    panel = read_xianqiao_upgrade_panel_snapshot()
    if panel['target']['baseId'] != expected[0]['base_id'] or panel['target']['wear'] is not True:
        raise RuntimeError('当前强化目标与该部位实时装配不一致')
    return dict(status='upgrade_page_observed', scene=487, resources=resources,
                equipped=before['equipped'], consumed=False)


def inspect_xianqiao_exp_selection(context, *, part='天璇', timeout=30.0):
    """Open the first native dedicated material and verify its quantity dialog."""
    yield from inspect_xianqiao_upgrade_resources(context, part=part, timeout=timeout)
    panel = read_xianqiao_upgrade_panel_snapshot()
    resources = read_xianqiao_upgrade_resources_snapshot()
    rates = {row['item_id']: row for row in resources['items']}
    if panel['slotList'] or not panel['coreExpList']:
        raise RuntimeError('强化选择槽必须为空，且有完整专用材料列表')
    first = panel['coreExpList'][0]
    if first['equipment_material'] or first['item_id'] not in rates or first['quantity'] <= 0:
        raise RuntimeError('首格不是已核实的专用经验物品')
    yield from context.wait_click_then_scene(487, '材料列表/首项', [489], timeout=timeout)
    selection = read_xianqiao_exp_selection_snapshot()
    if (selection['itemId'] != first['item_id']
            or selection['expNum'] != rates[first['item_id']]['exp_per_item']
            or selection['itemMaxNum'] != first['quantity']
            or (selection['pid'], selection['process_start_ticks']) != (panel['pid'], panel['process_start_ticks'])):
        raise RuntimeError(f'仙纹材料选择页与原生强化列表不一致：{selection}')
    return dict(status='dedicated_quantity_dialog_verified', panel=panel,
                selection=selection, consumed=False)


def cancel_xianqiao_exp_selection(context, *, timeout=30.0):
    """Clear the default quantity before departure; no 強化 action is performed."""
    yield from context.wait_scene_exact([489], timeout=timeout, observation_scenes=[489])
    selected = read_xianqiao_exp_selection_snapshot()
    if selected['useNum'] > 1:
        yield from _set_quantity(context, selected, 0)
    elif selected['useNum'] == 1:
        yield from context.wait_click(489, '减少')
        yield from context.wait_action_settle(0.5)
    zero = read_xianqiao_exp_selection_snapshot()
    if zero['itemId'] != selected['itemId'] or zero['useNum'] != 0:
        raise RuntimeError('专用材料默认选择未清零，保留现场')
    yield from context.wait_click_then_scene(489, '使用', [487], timeout=timeout)
    panel = read_xianqiao_upgrade_panel_snapshot()
    if panel['slotList']:
        raise RuntimeError('专用材料选择槽未清空，拒绝离场')
    yield from context.go_scene(34)
    return dict(status='selection_cleared', selected=zero, scene=34, consumed=False)


def set_xianqiao_exp_selection_max(context):
    """Drag the verified dedicated quantity slider and prove its clamped value."""
    before = read_xianqiao_exp_selection_snapshot()
    yield from _set_quantity(context, before, before['canUseMax'])
    after = read_xianqiao_exp_selection_snapshot()
    if after['itemId'] != before['itemId'] or after['useNum'] != before['canUseMax']:
        raise RuntimeError(f'专用材料滑杆上限未一致确认：{after}')
    return after


def _set_quantity(context, before, target):
    from backend.core.fanxiu.data_annotation.game_context import BehaviorTreeContext
    def observe():
        current = read_xianqiao_exp_selection_snapshot()
        if (current['itemId'], current['canUseMax'], current['pid'], current['process_start_ticks']) != (
                before['itemId'], before['canUseMax'], before['pid'], before['process_start_ticks']):
            raise RuntimeError('数量滑杆目标身份变化，拒绝继续调整')
        return current['useNum']
    yield from BehaviorTreeContext.set_integer_slider_value(
        context, 489, track='数量滑杆', target=target, minimum=0,
        maximum=before['canUseMax'], read_value=observe,
        decrease='减少', increase='增加', max_attempts=3,
    )


def finish_xianqiao_element_results(context):
    """Close only the specific 五行 activation result, without another upgrade."""
    for _ in range(6):
        landed = yield from context.wait_scene_exact(
            [487, 490, _MAX_UPGRADE, _ELEMENT_RESULT], timeout=30,
            observation_scenes=[487, _MAX_UPGRADE, 490, _ELEMENT_RESULT, 839],
        )
        if landed.id in (487, _MAX_UPGRADE):
            return
        if landed.id == 490:
            yield from context.wait_click_then_scene(490, '点击空白关闭',
                [487, _MAX_UPGRADE, _ELEMENT_RESULT], timeout=30,
                retry_if_source_remains=False)
            continue
        yield from context.wait_click_then_scene(_ELEMENT_RESULT, '继续',
                                                [487, _MAX_UPGRADE, _ELEMENT_RESULT], timeout=30)
    raise RuntimeError('仙窍五行结果超过六次，保留现场')


def accept_xianqiao_upgrade_transaction(context, key):
    """Reconcile a persisted commit from current native facts; never consume."""
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import read_world_facts, record_world_discovery
    fact = (read_world_facts().get('discoveries') or {}).get(key)
    if not fact or fact.get('outcome') != 'commit_pending':
        raise RuntimeError('没有待验收的仙纹强化事务')
    yield from finish_xianqiao_element_results(context)
    panel = read_xianqiao_upgrade_panel_snapshot()
    updated = panel['target']; target = fact['target']; chosen = fact['selected']
    after = read_xianqiao_upgrade_resources_snapshot()
    if any(type(chosen.get(field)) is not int or chosen[field] <= 0
           or panel.get(field) != chosen[field]
           or after.get('evidence', {}).get(field) != chosen[field]
           for field in ('pid', 'process_start_ticks')):
        raise RuntimeError('仙纹事务的游戏进程身份变化或缺失，保留未结事实')
    counts_after = {row['item_id']:row['quantity'] for row in after['items']}
    counts_before = {int(k):v for k,v in fact['before'].items()}
    expected = min(sum(row['consumeExp'] for row in panel['levels']), target['sumExp'] + chosen['selected_exp'])
    if (updated['id'] != target['id'] or updated['sumExp'] != expected or updated['level'] < target['level']
            or any(counts_before[i]-counts_after[i] != (chosen['useNum'] if i==chosen['itemId'] else 0) for i in counts_before)):
        raise XianqiaoUpgradePending('仙纹消费、实例和经验仍未一致验收，禁止重复强化')
    receipt = dict(outcome='verified', target_before=target, target_after=updated,
                   selected=chosen, before=counts_before, after=counts_after)
    record_world_discovery(key, receipt)
    return receipt


def upgrade_xianqiao_dedicated_materials(context, *, max_transactions=None):
    """Upgrade worn 玉京仙纹 with dedicated items, preserving equipment inventory.

    Optional ``max_transactions`` bounds maintenance to complete transactions;
    reaching it returns pending, so a narrow window cannot claim daily completion.
    Every transaction is recomputed from native stock and level costs. A durable
    intent blocks repetition after an uncertain commit. Unopened parts and old
    equipment are never materials. Daily completion requires resource exhaustion
    or all currently worn parts at their native maximum.
    """
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import (
        read_world_facts, record_world_discovery,
    )
    if max_transactions is not None and (type(max_transactions) is not int or max_transactions <= 0):
        raise ValueError('max_transactions must be a positive integer or None')
    completed = []
    # Depleted materials alone cannot close a daily stage while an earlier
    # commit is unresolved. Reconcile its exact worn instance without a tap.
    pending = [(key, fact) for key, fact in (read_world_facts().get('discoveries') or {}).items()
               if key.startswith('xianqiao-dedicated-upgrade:')
               and isinstance(fact, dict) and fact.get('outcome') == 'commit_pending']
    for key, fact in pending:
        state = read_xianqiao_snapshot()
        matches = [row for row in state.get('equipped', [])
                   if row.get('equip_id') == fact['target']['id']]
        if state.get('complete') is not True or len(matches) != 1 or matches[0]['part_id'] not in _PARTS:
            raise RuntimeError('未结强化事务的装配实例已变化，禁止新强化')
        yield from inspect_xianqiao_upgrade_resources(context, part=_PARTS[matches[0]['part_id']])
        completed.append((yield from accept_xianqiao_upgrade_transaction(context, key)))
    if max_transactions is not None and len(completed) >= max_transactions:
        yield from context.go_scene(34)
        return dict(result='pending', outcome='maintenance_transaction_limit', transactions=completed, scene=34)
    for _ in range(16):
        state = read_xianqiao_snapshot()
        try:
            stock = read_xianqiao_upgrade_resources_snapshot()
        except FanxiuRuntimeMemoryError as exc:
            # Core.CoreWareItem is loaded by the client only once an upgrade
            # page has been shown. A maxed part's 强化 opens no page, so open
            # the first worn part below its native cap (read-only inspection,
            # same order as the upgrade loop) and reread from that page.
            if 'Core.CoreWareItem' not in str(exc) or state.get('complete') is not True:
                raise
            caps = read_xianqiao_strengthening_limits_snapshot(
                [row['base_id'] for row in state['equipped']])['limits']
            open_parts = [row['part_id'] for row in state['equipped']
                          if row['part_id'] in _PARTS and row['level'] < caps[row['base_id']]]
            if not open_parts:
                raise
            stock = (yield from inspect_xianqiao_upgrade_resources(
                context, part=_PARTS[open_parts[0]]))['resources']
        if state.get('complete') is not True or state['active_system_type'] != 2:
            raise RuntimeError('专用仙纹强化当前仅支持已核实的玉京体系')
        if stock['available_exp'] == 0:
            yield from context.go_scene(34)
            return dict(result='success', outcome='dedicated_materials_exhausted', transactions=completed, scene=34)
        advanced = False
        limits = read_xianqiao_strengthening_limits_snapshot(
            [row['base_id'] for row in state['equipped']])
        if any(limits[k] != state['evidence'][k] for k in ('pid', 'process_start_ticks')):
            raise RuntimeError('仙纹装配与等级上限来自不同进程')
        for equipped in state['equipped']:
            if equipped['level'] >= limits['limits'][equipped['base_id']]:
                continue
            part = _PARTS.get(equipped['part_id'])
            if part is None:
                raise RuntimeError('仙纹部位尚未映射为已标注业务入口')
            yield from inspect_xianqiao_upgrade_resources(context, part=part)
            panel = read_xianqiao_upgrade_panel_snapshot()
            target = panel['target']
            if target['level'] >= panel['max_level']:
                continue
            if target['sumExp'] is None or target['id'] is None:
                raise RuntimeError('仙纹经验或实例身份不完整')
            remaining = sum(row['consumeExp'] for row in panel['levels']) - target['sumExp']
            observed = yield from inspect_xianqiao_exp_selection(context, part=part)
            selection = observed['selection']
            chosen = yield from set_xianqiao_exp_selection_max(context)
            if (chosen['itemId'] != selection['itemId'] or chosen['useNum'] != chosen['canUseMax']
                    or chosen['useNum'] <= 0 or chosen['selected_exp'] > remaining + chosen['expNum'] - 1):
                raise RuntimeError(f'强化数量未达到原生上限或超出最小整数取整：{chosen}')
            yield from context.wait_click_then_scene(489, '使用', [487], timeout=30)
            ready = read_xianqiao_upgrade_panel_snapshot()
            rows = ready['slotList']
            if (ready['target'] != target or len(rows) != 1
                    or rows[0]['equipment_material'] or rows[0]['item_id'] != chosen['itemId']
                    or rows[0]['quantity'] != chosen['useNum']):
                raise RuntimeError(f'强化提交槽与专用材料数量不一致：{ready}')
            before = read_xianqiao_upgrade_resources_snapshot()
            counts_before = {row['item_id']:row['quantity'] for row in before['items']}
            key = f"xianqiao-dedicated-upgrade:{target['id']}:{target['sumExp']}"
            if (read_world_facts().get('discoveries') or {}).get(key):
                raise RuntimeError('已有同经验状态强化意图，需验收旧事务，禁止重复提交')
            intent = dict(outcome='commit_pending', target=target, selected=chosen, before=counts_before)
            record_world_discovery(key, intent)
            # Partial XP can stay on #487 without a level-up result window.
            # Never use a missing #490 to resubmit an already consumed batch.
            yield from context.wait_click(487, '强化', timeout=40)
            import time
            deadline = time.monotonic() + 40
            while True:
                try:
                    receipt = yield from accept_xianqiao_upgrade_transaction(context, key)
                    break
                except XianqiaoUpgradePending:
                    if time.monotonic() >= deadline:
                        raise
                    yield from context.wait_action_settle(0.5)
            completed.append(receipt)
            if max_transactions is not None and len(completed) >= max_transactions:
                yield from context.go_scene(34)
                return dict(result='pending', outcome='maintenance_transaction_limit',
                            transactions=completed, scene=34)
            advanced = True
            break
        if not advanced:
            yield from context.go_scene(34)
            return dict(result='success', outcome='all_worn_equipment_maximum', transactions=completed, scene=34)
    raise RuntimeError('仙纹专用材料强化超过16次，保留现场复核')
