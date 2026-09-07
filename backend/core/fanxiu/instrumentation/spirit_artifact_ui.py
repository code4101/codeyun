"""只读当前灵器窗口的选中部件；不把储物袋中的最高阶部件当成 UI 选择。

SpiritWareView.v_wareId → tabPanelGroup 当前页 → SpiritWareWashPanel。
仅走这条窗口成员链，避免展开世界页全部子组件；所有地址每次重新取得。
"""

from __future__ import annotations

from typing import Any

from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import read_ui_object_field, read_ui_runtime_snapshot


def read_spirit_artifact_auto_open_rule() -> dict[str, Any]:
    """只读客户端自动洗炼开放表达式；不执行 CheckCondition 或改变活动状态。"""
    from .runtime_memory import manager_index_fields, resolve_lua_global_manager_root

    def read(ctx):
        reader = ctx.reader
        methods = frozenset({'DBMgr', 'GetConfigTable', 'GetConfigTableByIdWithLog', 'Inst_get'})
        def tables(current, address):
            manager = manager_index_fields(current, address, methods)
            return current.dictionary_fields(current.fields(manager.get('inst')).get('ConfigDic'))
        root, _, environment = resolve_lua_global_manager_root(
            ctx.memory, manager_key='spirit-artifact-auto-rule', state_address=ctx.binding.state_address,
            global_name='DBMgr', required_methods=methods, validate=tables)
        row = table_ref(reader.fields(tables(reader, root).get('SpiritWare.ConfigValue')).get('OpenAutoRefresh'))
        indexes = reader.fields(reader.fields(reader.fields(reader.string_fields(environment,
            frozenset({'s_globalCfgIdx'})).get('s_globalCfgIdx')).get('SpiritWare')).get('ConfigValue'))
        value_index = as_int(indexes.get('value'))
        if row is None or value_index is None:
            raise FanxiuRuntimeMemoryError('自动洗炼开放配置尚未加载')
        data = reader.table(row.address)
        condition = data['fields'].get('value', data['fields'].get(value_index,
            data['array'][value_index] if 0 <= value_index < len(data['array']) else None))
        if not isinstance(condition, str) or not condition:
            raise FanxiuRuntimeMemoryError('自动洗炼开放表达式无效')
        return {'condition': condition, 'minimum_quality': 5,
                'source': 'runtime_dbmgr_spiritware_auto_open_rule',
                'pid': ctx.memory.pid, 'process_start_ticks': ctx.memory.process_start_ticks}

    return read_ui_runtime_snapshot([], read)


def locate_spirit_artifact_name(tokens: list[dict], names: tuple[str, ...]) -> tuple[float, float] | None:
    """按 OCR 原始行序连接竖排字，返回唯一名称包络中心；不借用图标或序号。"""
    lines: dict[str, list[dict]] = {}
    for token in tokens:
        lines.setdefault(str(token['parent_line_id']), []).append(token)
    boxes = set()
    for line in lines.values():
        line.sort(key=lambda token: token.get('order', 0))
        text = ''.join(str(token['text']) for token in line)
        for name in names:
            start = text.find(name)
            if start < 0:
                continue
            if text.find(name, start + 1) >= 0:
                raise FanxiuRuntimeMemoryError('同一 OCR 行出现重复灵器名称')
            end, offset, selected = start + len(name), 0, []
            for token in line:
                next_offset = offset + len(str(token['text']))
                if offset < end and next_offset > start:
                    selected.append(token)
                offset = next_offset
            boxes.add((min(t['x'] for t in selected), min(t['y'] for t in selected),
                       max(t['x'] + t['w'] for t in selected), max(t['y'] + t['h'] for t in selected)))
    if len(boxes) > 1:
        raise FanxiuRuntimeMemoryError('灵器名称 OCR 候选不唯一')
    if not boxes:
        return None
    x1, y1, x2, y2 = boxes.pop()
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def bind_spirit_artifact_ui_effects(rows: list[dict], committed: list[dict]) -> list[dict]:
    """保留 UI 行序，采用实时属性；拒绝重复或不同集合的词条。"""
    current = {effect['cleanse_id']: effect for effect in committed}
    order = [effect['cleanse_id'] for effect in rows]
    if len(current) != len(committed) or len(set(order)) != len(order) or set(current) != set(order):
        raise FanxiuRuntimeMemoryError('洗炼 UI 属性列表与当前实例不一致，请等待页面刷新')
    return [{'row': effect['row'], **current[effect['cleanse_id']]} for effect in rows]


def read_spirit_artifact_ui_snapshot() -> dict[str, Any]:
    """返回当前灵器、页签、洗炼选中实例与成本；缺失或歧义直接报错。"""

    from ..catalog.spirit_artifact_wash_rules import spirit_artifact_ware_ids
    supported_ware_ids = spirit_artifact_ware_ids()

    def read(ctx):
        reader = ctx.reader
        field = lambda obj, key: read_ui_object_field(ctx, obj.address, key)
        storage = reader.table(ctx.binding.component_storage_address)
        candidates = {}
        for raw in [*storage['array'], *storage['fields'].values()]:
            window = table_ref(raw)
            if window is None:
                continue
            members, window_count = reader.list_items(window)
            if not window_count or len(members) != window_count:
                continue
            component = table_ref(members[-1])
            outer = table_ref(field(component, 'm_panel')) if component else None
            if outer is None:
                continue
            ware_id = as_int(field(outer, 'v_wareId'))
            group = table_ref(field(outer, 'tabPanelGroup'))
            if ware_id not in supported_ware_ids or group is None:
                continue
            index = as_int(field(group, 'curTabIndex'))
            panels = table_ref(field(group, 'panelShowComps'))
            items, panel_count = reader.list_items(panels) if panels else ([], None)
            if index is None or not panel_count or not 0 <= index < len(items):
                raise FanxiuRuntimeMemoryError('灵器当前页签成员链不完整')
            comp = table_ref(items[index])
            panel = table_ref(field(comp, 'm_panel')) if comp else None
            if panel is None:
                raise FanxiuRuntimeMemoryError('灵器当前页签面板未加载')
            result = {'ware_id': ware_id, 'tab_index': index,
                      'panel_address': hex(panel.address), 'is_wash': False}
            if all(table_ref(field(panel, key)) for key in ('curAttrScroll', 'nextAttrScroll', 'SpiritWareScroll')):
                item_id = reader.long(field(panel, '_CurSelectSlot'))
                if not item_id:
                    raise FanxiuRuntimeMemoryError('洗炼页没有明确选中实例')
                result.update(is_wash=True, item_id=str(item_id),
                              material_id=as_int(field(panel, 'V_CostItemId')),
                              material_cost=as_int(field(panel, 'totalCostNum')),
                              needs_auto_warning=field(panel, 'needAutoTips'))
                for key, output in [('curAttrScroll', 'effects'), ('nextAttrScroll', 'pending_effects')]:
                    scroll = table_ref(field(panel, key))
                    values, count = reader.list_items(field(scroll, 'ItemInfoList'))
                    if count is None or count > 6 or len(values) != count:
                        raise FanxiuRuntimeMemoryError(f'{key} 属性顺序不完整')
                    result[output] = []
                    for row, value in enumerate(values):
                        data = reader.fields(value)
                        result[output].append({'row': row, 'cleanse_id': as_int(data.get('cleanseId')),
                                               'value': as_int(data.get('value')), 'locked': data.get('isLock'),
                                               'quality': as_int(data.get('quality'))})
                values, count = reader.list_items(field(panel, 'V_PartList'))
                if count is None or count > 6 or len(values) != count:
                    raise FanxiuRuntimeMemoryError('洗炼部件列表不完整')
                result['parts'] = [{str(k): str(reader.long(v)) if k == 'itemUid' else v
                                    for k, v in reader.fields(value).items()}
                                   for value in values]
                result['selected_index'] = as_int(field(panel, '_CurIndex'))
                from .spirit_artifact import read_spirit_artifact_item_runtime
                committed = read_spirit_artifact_item_runtime(str(item_id))
                if (committed['pid'], committed['process_start_ticks']) != (ctx.memory.pid, ctx.memory.process_start_ticks):
                    raise FanxiuRuntimeMemoryError('洗炼 UI 与部件数据进程不一致')
                # 背包读取期间用户可能手动切部件/页签/关闭窗口。重读现场链，
                # 不能把上一部件的数据附到新页面上，也不自动导航恢复旧位置。
                live_members, live_count = reader.list_items(window)
                live_storage = reader.table(ctx.binding.component_storage_address)
                registered = any(table_ref(value) == window for value in [
                    *live_storage['array'], *live_storage['fields'].values()])
                if (not registered or live_count != window_count or not live_members or table_ref(live_members[-1]) != component
                        or as_int(field(outer, 'v_wareId')) != ware_id
                        or as_int(field(group, 'curTabIndex')) != index
                        or str(reader.long(field(panel, '_CurSelectSlot'))) != str(item_id)):
                    raise FanxiuRuntimeMemoryError('读取洗炼属性期间页面或选中部件发生变化，请重新观察当前现场')
                for output in ('effects', 'pending_effects'):
                    result[output] = bind_spirit_artifact_ui_effects(result[output], committed[output])
                result['refine_num'] = committed['refine_num']
                result['base_id'] = committed['base_id']
                result['part'] = committed['part']
                result['is_break'] = committed.get('is_break')
            candidates[outer.address] = result
        if len(candidates) != 1:
            raise FanxiuRuntimeMemoryError(f'当前灵器窗口数量必须为 1，实际 {len(candidates)}')
        return {**next(iter(candidates.values())), 'source': 'active_spiritware_view',
                'pid': ctx.memory.pid, 'process_start_ticks': ctx.memory.process_start_ticks}

    return read_ui_runtime_snapshot([], read)
