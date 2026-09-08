"""只读自然加载的灵器升阶页；不执行 Lua、导航、升阶或隐式刷新。

客户端 SpiritWareGradePanel 的 V_PartList 是已拥有部件优先、partId
升序的完整 UI 顺序，_CurIndex 为零基索引。不能用 _CurPartIndex 判断
当前部位：客户端 OnPartSelect 只更新 _CurSelectSlot 和 _CurIndex。
"""

from __future__ import annotations

import time
from typing import Any

from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import read_ui_object_field, read_ui_runtime_snapshot


def _read_grade_panel() -> dict[str, Any]:
    """Read only the active panel; the public observer owns cross-model checks."""
    from ..catalog.spirit_artifact_wash_rules import spirit_artifact_ware_ids
    supported_ware_ids = spirit_artifact_ware_ids()

    def read(ctx):
        reader = ctx.reader

        def field(obj, name):
            return read_ui_object_field(ctx, obj.address, name) if obj else None

        def observe():
            storage = reader.table(ctx.binding.component_storage_address)
            candidates = {}
            for raw in [*storage['array'], *storage['fields'].values()]:
                window = table_ref(raw)
                if window is None:
                    continue
                members, count = reader.list_items(window)
                if not count or len(members) != count:
                    continue
                component = table_ref(members[-1])
                outer = table_ref(field(component, 'm_panel'))
                ware_id = as_int(field(outer, 'v_wareId'))
                group = table_ref(field(outer, 'tabPanelGroup'))
                if ware_id not in supported_ware_ids or group is None:
                    continue
                tab_index = as_int(field(group, 'curTabIndex'))
                # TabPanelGroup.ShowTab only materializes the selected index;
                # unlike V_PartList, its CList intentionally has lazy holes.
                panels = reader.fields(field(group, 'panelShowComps'))
                panel_count = as_int(panels.get('count'))
                panel_storage = table_ref(panels.get('_dt_'))
                if (tab_index is None or panel_count is None
                        or not 0 <= tab_index < panel_count or panel_storage is None):
                    raise FanxiuRuntimeMemoryError('灵器当前页签链不完整')
                selected = reader.numeric_fields(panel_storage.address, frozenset({tab_index + 1}))
                component = table_ref(selected.get(tab_index + 1))
                panel = table_ref(field(component, 'm_panel'))
                if panel is None:
                    raise FanxiuRuntimeMemoryError('灵器当前升阶页签尚未加载')
                # GradePanel 专属成员组合，不能只凭页签编号认定当前页。
                if not all(table_ref(field(panel, name)) for name in (
                        'SpiritWareScroll', 'curGradeTF', 'nextGradeTF',
                        'AttrScroll', 'attachAttrScroll')):
                    raise FanxiuRuntimeMemoryError('当前灵器页不是已加载的升阶页')
                if as_int(field(panel, 'V_SpiritWare')) != ware_id:
                    raise FanxiuRuntimeMemoryError('升阶页灵器身份与窗口不一致')
                values, part_count = reader.list_items(field(panel, 'V_PartList'))
                if part_count != 6 or len(values) != 6:
                    raise FanxiuRuntimeMemoryError('升阶部件有序列表不是完整六部位')
                parts = []
                for index, value in enumerate(values):
                    data = reader.fields(value)
                    uid = reader.long(data.get('itemUid'))
                    parts.append({'index': index, 'part': as_int(data.get('partId')),
                                  'item_id': str(uid) if uid else None})
                if {row['part'] for row in parts} != set(range(1, 7)):
                    raise FanxiuRuntimeMemoryError('升阶部件列表存在重复或未知部位')
                selected_index = as_int(field(panel, '_CurIndex'))
                uid = reader.long(field(panel, '_CurSelectSlot'))
                if (selected_index is None or not 0 <= selected_index < 6 or not uid
                        or parts[selected_index]['item_id'] != str(uid)):
                    raise FanxiuRuntimeMemoryError('升阶选中实例与列表索引不一致')
                info = reader.fields(field(panel, '_SpiritWareItemInfo'))
                grade = as_int(info.get('grade'))
                if grade is None or grade < 1:
                    raise FanxiuRuntimeMemoryError('升阶选中实例阶数尚未加载')
                candidates[outer.address] = {
                    'ware_id': ware_id, 'tab_index': tab_index, 'is_grade': True,
                    'item_id': str(uid), 'part': parts[selected_index]['part'],
                    'grade': grade, 'selected_index': selected_index, 'parts': parts,
                    'material_id': as_int(field(panel, 'V_CostItemId')),
                    'can_upgrade': field(panel, '_IsCanGradeUpgrade'),
                    'panel_address': hex(panel.address),
                }
            if len(candidates) != 1:
                raise FanxiuRuntimeMemoryError(f'当前升阶窗口必须唯一，实际 {len(candidates)}')
            return next(iter(candidates.values()))

        return {**observe(), 'pid': ctx.memory.pid,
                'process_start_ticks': ctx.memory.process_start_ticks}

    return read_ui_runtime_snapshot([], read)


def project_spirit_artifact_grade_observation(before, after, inventory, *, equipped=None):
    """纯互证：两次独立 UI 观察夹住完整库存，可选严格装备引用互证。"""
    if before != after:
        raise FanxiuRuntimeMemoryError('读取期间升阶页面或进程变化')
    identity = (before['pid'], before['process_start_ticks'])
    if (inventory.get('complete') is not True
            or (inventory['pid'], inventory['process_start_ticks']) != identity):
        raise FanxiuRuntimeMemoryError('升阶库存不完整或进程变化')
    matches = [row for row in inventory['items'] if row['item_id'] == before['item_id']]
    if len(matches) != 1:
        raise FanxiuRuntimeMemoryError('升阶选中实例在完整库存中不唯一')
    item = matches[0]
    if any(item[key] != before[key] for key in ('ware_id', 'part', 'grade')):
        raise FanxiuRuntimeMemoryError('升阶选中部件身份或阶数与库存不一致')
    if equipped is not None:
        slots = [row for row in equipped['items']
                 if (row['ware_id'], row['part']) == (before['ware_id'], before['part'])]
        if ((equipped['pid'], equipped['process_start_ticks']) != identity
                or len(slots) != 1 or slots[0]['item_id'] != before['item_id']):
            raise FanxiuRuntimeMemoryError('升阶选中实例与精确装配引用不一致')
    result = dict(before)
    result.pop('panel_address')
    return {**result, 'base_id': item['base_id'], 'read_only': True,
            'captured_at': time.time(), 'source': 'active_spiritware_grade_panel'}


def read_spirit_artifact_grade_snapshot() -> dict[str, Any]:
    """独立读取升阶 UI 与完整库存；前后 UI 使用不同读上下文核验。

    返回契约不变；can_upgrade 仅是客户端提示，不代表材料消耗授权。
    """
    from .spirit_artifact import read_spirit_artifact_inventory_runtime
    before = _read_grade_panel()
    inventory = read_spirit_artifact_inventory_runtime()
    after = _read_grade_panel()
    return project_spirit_artifact_grade_observation(before, after, inventory)


def read_spirit_artifact_grade_owned_snapshot() -> dict[str, Any]:
    """单轮联合读取当前升阶目标与完整库存/精确装配，不接收旧快照。

    新 UI 前态→公开 owned(当前灵器)→新 UI 后态；只读取一次全库存。
    两个 UI 上下文各自从活动组件重新确认页面成员，校验进程、页面、
    UID/部位/阶数及装备引用；变化即拒绝，不跨业务动作复用。
    返回 grade、owned 及分段 timings_seconds；尚待真实对照与性能验收。
    """
    from .spirit_artifact_equipped import read_spirit_artifact_owned_runtime
    started = time.perf_counter()
    before = _read_grade_panel()
    panel_at = time.perf_counter()
    owned = read_spirit_artifact_owned_runtime([before['ware_id']])
    owned_at = time.perf_counter()
    after = _read_grade_panel()
    grade = project_spirit_artifact_grade_observation(
        before, after, owned['inventory'], equipped=owned['equipped'])
    return {'grade': grade, 'owned': owned, 'timings_seconds': {
        'panel_before': panel_at - started, 'owned': owned_at - panel_at,
        'panel_after': time.perf_counter() - owned_at,
        'total': time.perf_counter() - started}}
