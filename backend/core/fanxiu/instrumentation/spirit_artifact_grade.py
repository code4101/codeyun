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


def read_spirit_artifact_grade_snapshot() -> dict[str, Any]:
    """返回升阶页 ware_id/part/item_id/grade 和零基有序 parts 列表。

    仅接受唯一当前灵器窗口及其正在显示的升阶页。parts 保留未拥有的
    槽位（item_id 为 None）；selected_index 对应此列表。部位身份再与
    当前背包实例互证，读取中页面变化或列表不完整时抛错，不自行恢复。
    can_upgrade 是客户端升阶提示，不能作为重置或消耗授权。
    """
    from .spirit_artifact import read_spirit_artifact_inventory_runtime

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
                panels, panel_count = reader.list_items(field(group, 'panelShowComps'))
                if (tab_index is None or panel_count != len(panels)
                        or not 0 <= tab_index < len(panels)):
                    raise FanxiuRuntimeMemoryError('灵器当前页签链不完整')
                panel = table_ref(field(table_ref(panels[tab_index]), 'm_panel'))
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

        result = observe()
        inventory = read_spirit_artifact_inventory_runtime()
        matches = [item for item in inventory['items'] if item['item_id'] == result['item_id']]
        if len(matches) != 1:
            raise FanxiuRuntimeMemoryError('升阶选中实例在背包中不唯一')
        item = matches[0]
        if ((inventory['pid'], inventory['process_start_ticks'])
                != (ctx.memory.pid, ctx.memory.process_start_ticks)
                or (item['ware_id'], item['part'], item['grade'])
                != (result['ware_id'], result['part'], result['grade'])):
            raise FanxiuRuntimeMemoryError('升阶选中部件身份或阶数与当前背包不一致，请等待页面刷新')
        if observe() != result:
            raise FanxiuRuntimeMemoryError('读取期间升阶页或选中部件变化，请重新观察')
        # 地址只参与本次一致性检查，不作为业务身份或可复用定位信息。
        result.pop('panel_address')
        return {**result, 'base_id': item['base_id'], 'read_only': True,
                'captured_at': time.time(), 'source': 'active_spiritware_grade_panel',
                'pid': ctx.memory.pid, 'process_start_ticks': ctx.memory.process_start_ticks}

    return read_ui_runtime_snapshot([], read)
