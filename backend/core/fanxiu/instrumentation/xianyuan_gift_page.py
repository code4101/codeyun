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
