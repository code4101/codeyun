"""Runtime guards for the real CoreWareEquipView replacement panel."""
from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import (
    active_ui_component_objects, has_ui_object_fields,
    read_ui_object_field, read_ui_runtime_snapshot,
)


def read_xianqiao_equipment_panel_snapshot() -> dict:
    """Read the actual part, complete candidate order and selected instance.

    Selection is preparatory. Only the panel's 更换 action equips an instance.
    A pooled stale curSelectCoreWare alone cannot authorize that action: its
    index and the selected row must agree in this fresh snapshot.
    """
    def observe(context):
        panels = [obj for obj in active_ui_component_objects(context)
                  if has_ui_object_fields(context, obj.address,
                      {'mapType', 'partType', 'showList', 'noWearList',
                       'curSelectIndex', 'upDownBtn', 'wareShowItemRight'})]
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError('仙纹更换面板未唯一打开', code='data_not_loaded')
        panel = panels[0]
        field = lambda key: read_ui_object_field(context, panel.address, key)
        system, part, selected_index = (as_int(field(key)) for key in
                                       ('mapType', 'partType', 'curSelectIndex'))
        if system is None or system <= 0 or part not in range(1, 7) or selected_index is None:
            raise FanxiuRuntimeMemoryError('仙纹更换部位身份不完整', code='runtime_incomplete')
        def equipment(value):
            ref = table_ref(value)
            if ref is None:
                return None
            raw = lambda key: read_ui_object_field(context, ref.address, key)
            ident = context.reader.long(raw('equipId'))
            base, level = as_int(raw('baseId')), as_int(raw('level'))
            exp, worn = context.reader.long(raw('sumExp')), raw('wear')
            if (ident is None or ident <= 0 or base is None or base <= 0
                    or level is None or level < 0 or exp is None or exp < 0
                    or type(worn) is not bool):
                raise FanxiuRuntimeMemoryError('仙纹候选实例字段不完整', code='runtime_incomplete')
            return {'equip_id': ident, 'base_id': base, 'level': level,
                    'sum_exp': exp, 'worn': worn}
        values, count = context.reader.list_items(field('showList'))
        if count is None or count != len(values):
            raise FanxiuRuntimeMemoryError('仙纹候选完整列表读取竞态', code='snapshot_incoherent')
        rows = []
        # Native curSelectIndex and Cipairs are zero-based (first live row
        # is selected with curSelectIndex=0); Shape labels remain one-based.
        for index, value in enumerate(values):
            ref = table_ref(value)
            if ref is None:
                raise FanxiuRuntimeMemoryError('仙纹候选行缺失', code='runtime_incomplete')
            item = equipment(read_ui_object_field(context, ref.address, 'coreWare'))
            if item is None:
                raise FanxiuRuntimeMemoryError('仙纹候选内容缺失', code='runtime_incomplete')
            selected = read_ui_object_field(context, ref.address, 'isSelect')
            if type(selected) is not bool:
                raise FanxiuRuntimeMemoryError('仙纹候选选择标识不完整', code='runtime_incomplete')
            rows.append({**item, 'index': index, 'selected': selected})
        selected = equipment(field('curSelectCoreWare'))
        selected_rows = [r for r in rows if r['selected']]
        ready = (0 <= selected_index < len(rows) and len(selected_rows) == 1
                 and selected_rows[0]['index'] == selected_index and selected is not None
                 and selected_rows[0]['equip_id'] == selected['equip_id'])
        return {'complete': True, 'read_only': True, 'system_type': system,
                'part_id': part, 'selected_index': selected_index,
                'selected': selected, 'selection_verified': ready, 'candidates': rows,
                'pid': context.binding.pid, 'process_start_ticks': context.binding.process_start_ticks}
    return read_ui_runtime_snapshot((), observe)
