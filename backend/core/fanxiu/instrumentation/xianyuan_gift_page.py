"""Exact active NPC gift selection; reads UI bytes without invoking game Lua."""
import time

from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import read_active_ui_window_panels, read_ui_object_field, read_ui_runtime_snapshot


def require_xianyuan_gift_selection(snapshot, batch, *, expected_process):
    """Before Submit, require precisely one authorized item on the exact NPC.

    Quantity-modal confirmation alone cannot satisfy this guard. Fresh bytes and
    same-process identity also prevent an old selection from another page/session
    authorizing consumption, including unrelated prayer flowers.
    """
    if not snapshot.get('complete') or snapshot.get('quantity_modal') is not None:
        raise RuntimeError('赠礼主页面尚未就绪')
    captured = snapshot.get('captured_at')
    if not isinstance(captured, (int, float)) or not 0 <= time.time() - captured <= 5:
        raise RuntimeError('赠礼选择观察已过期')
    evidence = snapshot.get('evidence', {})
    if any(not expected_process.get(k) or expected_process[k] != evidence.get(k)
           for k in ['pid', 'process_start_ticks']):
        raise RuntimeError('赠礼选择与计划进程不符')
    if snapshot.get('npc_id') != batch['npc_id'] or snapshot.get('native_npc_id') != batch['npc_id']:
        raise RuntimeError('赠礼收礼人物与计划不符')
    selected = snapshot.get('selected_items', [])
    if batch.get('action') != 'gift' or len(selected) != 1 or (
        selected[0].get('base_id'), selected[0].get('quantity')
    ) != (batch['base_id'], batch['quantity']) or batch['quantity'] <= 0:
        raise RuntimeError('赠礼选中物品或数量与唯一授权批次不符')
    return snapshot


def read_xianyuan_gift_page_snapshot():
    """Observe the recipient, available gifts, selected items and quantity modal.

    Window membership is revalidated on every observation. An empty selected list
    means no gift was selected; a missing list never means an empty selection.
    Choosing a quantity is separate from submitting the gift on the main page.
    """
    def observe(ctx):
        def fail(message):
            raise FanxiuRuntimeMemoryError('仙缘赠礼：' + message, code='runtime_incomplete')

        def field(obj, name):
            ref = table_ref(obj)
            if ref is None:
                fail(name + '所属对象缺失')
            return read_ui_object_field(ctx, ref.address, name)

        def number(obj, name, minimum=0):
            value = as_int(field(obj, name))
            if value is None or value < minimum:
                fail(name + '缺失或无效')
            return value

        def gifts(value):
            if table_ref(value) is None:
                fail('礼品列表缺失')
            entries, count = ctx.reader.indexed_list_items(value)
            if count is None or count != len(entries):
                fail('礼品列表不完整')
            return [{'runtime_index': index, 'base_id': number(row, 'baseId', 1),
                     'quantity': number(row, 'num')} for index, row in entries]

        panels = read_active_ui_window_panels(ctx, 'NpcGiftGivingView')
        modals = read_active_ui_window_panels(ctx, 'NpcGiftSelectedNumView')
        if not panels and not modals:
            return {'ok': True, 'complete': False, 'reason': 'gift_page_closed'}
        if len(panels) != 1 or len(modals) > 1:
            fail('赠礼主页面或数量窗口不唯一')
        panel = panels[0]
        npc_id = number(panel, '_CurNpcId', 1)
        native_id = number(field(panel, 'npcInfoVo'), 'id', 1)
        if npc_id != native_id:
            fail('收礼人物身份尚未对齐')
        selected = gifts(field(panel, '_CurSelectItemList'))
        if any(item['quantity'] <= 0 for item in selected):
            fail('选中礼品数量无效')
        modal = None
        if modals:
            obj = modals[0]
            data = field(obj, 'data')
            modal = {'base_id': number(data, 'baseId', 1), 'inventory_quantity': number(data, 'num', 1),
                     'quantity': number(obj, 'useNum', 1), 'maximum': number(obj, 'useMax', 1),
                     'favor_per_item': number(obj, 'perValue', 1)}
            if not modal['quantity'] <= modal['maximum'] <= modal['inventory_quantity']:
                fail('数量选择超出实际库存或当前容量')
        return {'ok': True, 'complete': True, 'npc_id': npc_id, 'native_npc_id': native_id,
                'available_items': gifts(field(panel, 'hobbyList')), 'selected_items': selected,
                'selected_favor_gain': number(panel, '_CurWillAddValue'), 'quantity_modal': modal,
                'captured_at': time.time(), 'source': 'runtime_memory',
                'evidence': {'pid': ctx.binding.pid, 'process_start_ticks': ctx.binding.process_start_ticks}}

    return read_ui_runtime_snapshot((), observe, fast=True)


def read_xianyuan_gift_bag_snapshot():
    """Read the active NPC bag's native reset eligibility and loaded reset cost."""
    import re
    from .ui_runtime_context import active_ui_component_objects, has_ui_object_fields
    from .resource_auto_use import read_runtime_config_rows
    def observe(ctx):
        def fail(message):
            raise FanxiuRuntimeMemoryError('仙缘回礼：' + message, code='runtime_incomplete')
        def field(obj, name):
            ref = table_ref(obj)
            if ref is None:
                fail(name + '对象缺失')
            return read_ui_object_field(ctx, ref.address, name)
        panels = [obj for obj in active_ui_component_objects(ctx) if has_ui_object_fields(ctx, obj.address,
                  {'ResetBtn', 'GiftBtn', 'npcInfoVo', 'rewardList'})]
        if len(panels) != 1:
            fail('人物储物袋未唯一加载')
        panel = panels[0]
        ident = as_int(field(field(panel, 'npcInfoVo'), 'id'))
        if not ident or ident <= 0:
            fail('人物身份缺失')
        steps, count = ctx.reader.list_items(field(panel, 'resetStepList'))
        # No reset list means not eligible, while a partial declared list fails.
        if count is None and field(panel, 'resetStepList') is None:
            steps, count = [], 0
        if count is None or count != len(steps) or any(as_int(step) is None for step in steps):
            fail('重置阶段不完整')
        over, over_count = ctx.reader.list_items(field(panel, 'resetOverList'))
        if over_count is None or over_count != len(over):
            fail('重置未领取列表不完整')
        package = read_ui_object_field(ctx, ctx.binding.environment_address, 'package')
        row = field(field(field(package, 'loaded'), 'Generate.Cfg.Npc.ConfigValue'), 'NPC_GIFT_RESET_ITEM')
        config = read_runtime_config_rows(ctx.reader, [row], environment_address=ctx.binding.environment_address,
            state_address=ctx.binding.state_address, group_name='Npc', table_name='ConfigValue', fields=('id', 'content'))[0]
        match = re.fullmatch(r'Item\|(\d+)_(\d+)', str(config.get('content') or ''))
        if config.get('id') != 'NPC_GIFT_RESET_ITEM' or match is None:
            fail('重置费用配置未加载或不可识别')
        enough = field(panel, 'isEnough')
        if not isinstance(enough, bool):
            fail('重置费用资格缺失')
        return {'complete': True, 'npc_id': ident, 'reset_steps': [as_int(step) for step in steps],
                'remaining_reset_rewards': over_count, 'is_enough': enough,
                'reset_cost_item_id': int(match[1]), 'reset_cost_quantity': int(match[2]),
                'captured_at': time.time(), 'evidence': {'pid': ctx.binding.pid,
                'process_start_ticks': ctx.binding.process_start_ticks}}
    return read_ui_runtime_snapshot((), observe, fast=True)


def require_free_single_step_npc_reset(bag, person, *, expected_process):
    """Authorize only the loaded, free single-stage reset of a fully claimed NPC."""
    if (not bag.get('complete') or bag.get('npc_id') != person.get('npc_id')
            or bag.get('reset_steps') != [1]
            or bag.get('remaining_reset_rewards') != 0
            or bag.get('reset_cost_quantity') != 0 or bag.get('is_enough') is not True):
        raise RuntimeError('人物回礼重置资格、阶段或免费配置不符')
    if any(not expected_process.get(key) or bag.get('evidence', {}).get(key) != expected_process[key]
           for key in ('pid', 'process_start_ticks')):
        raise RuntimeError('人物回礼重置前进程身份变化')
    rewards = person.get('rewards') or []
    if not rewards or any(row.get('state') != 2 for row in rewards):
        raise RuntimeError('人物回礼尚未全部领取，禁止重置')
    captured = bag.get('captured_at')
    if not isinstance(captured, (int, float)) or not 0 <= time.time() - captured <= 5:
        raise RuntimeError('人物回礼重置资格观察已过期')
    return bag
