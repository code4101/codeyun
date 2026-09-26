"""神印当前地图、已解锁部位与升级事实；全部只读。"""
from .runtime_memory import FanxiuRuntimeMemoryError, LuaRef, as_int, table_ref
from .ui_runtime_context import (read_active_ui_window_panels, read_ui_object_field,
                                 read_ui_selected_tab_panel, read_ui_runtime_snapshot)
from .resource_auto_use import read_runtime_config_rows
from .item_config import read_loaded_item_text


def _loaded_levels(c):
    """共享只读解码；UI 与储物袋调用同一份层数事实。"""
    def ref(v):
        r = table_ref(v)
        if r is None:
            raise FanxiuRuntimeMemoryError('神印层数数据未加载，不能视为零级')
        return r
    def f(v, k):
        return read_ui_object_field(c, ref(v).address, k)
    def items(v):
        rows, count = c.reader.list_items(ref(v))
        if count is None or count != len(rows):
            raise FanxiuRuntimeMemoryError('神印层数列表不完整')
        return rows
    def decode(rows, table, fields):
        return read_runtime_config_rows(c.reader, rows,
            environment_address=c.binding.environment_address,
            group_name='GodWrath', table_name=table, fields=fields)
    model = f(f(f(LuaRef('table', c.binding.environment_address), 'GodwrathMgr'), 'inst'), 'Model')
    data = f(model, 'GodwrathData')
    maps = decode(items(f(data, 'GodWrathMapDataList')), 'GodWrathMap', ('pointType',))
    types = frozenset(as_int(row.get('pointType')) for row in maps)
    if not types or None in types:
        raise FanxiuRuntimeMemoryError('神印分组配置不完整')
    groups = c.reader.numeric_fields(ref(f(data, 'GodWrathBaseTb')).address, types)
    records = []
    for point_type in sorted(types):
        for v in items(groups.get(point_type)):
            cfg = decode([f(v, 'configData')], 'GodWrathBase', ('pointType', 'parts', 'name', 'itemId'))[0]
            part = as_int(cfg.get('parts'))
            if as_int(cfg.get('pointType')) != point_type or part is None:
                raise FanxiuRuntimeMemoryError('神印分组与部位配置冲突')
            server = table_ref(f(v, 'baseVo'))
            level = as_int(f(server, 'level')) if server else 0
            if level is None or level < 0:
                raise FanxiuRuntimeMemoryError('神印服务器等级缺失')
            if level == 0:
                # 未激活/未开放部位可能尚无等级表，不要求不存在的配置。
                records.append({'point_type': point_type, 'part': part, 'level': 0,
                    'activated': False, 'stage': 0, 'turn': 0,
                    'item_id': as_int(cfg.get('itemId')), 'name_id': as_int(cfg.get('name'))})
                continue
            key = point_type*10000+part
            levels = c.reader.numeric_fields(ref(f(data, 'AllGodWrathLevelTb')).address, frozenset({key}))
            configs = decode(items(levels.get(key)), 'GodWrathLevel', ('lv', 'grade', 'initPos'))
            current = [row for row in configs if as_int(row.get('lv')) == max(1, level)]
            if len(current) != 1 or as_int(current[0].get('grade')) is None:
                raise FanxiuRuntimeMemoryError('神印当前层级配置缺失或重复')
            current = current[0]
            grade = as_int(current['grade'])
            # 与 GetCurGrade()-1 一致：无 initPos 的行是跨重突破节点。
            has_node = table_ref(current.get('initPos')) is not None
            stage = (grade-1 if has_node else grade) if level else 0
            nodes = [row for row in configs if as_int(row.get('grade')) == grade
                     and table_ref(row.get('initPos')) is not None
                     and as_int(row.get('lv')) is not None and int(row['lv']) <= level]
            turns = len(nodes) if level and has_node else 0
            records.append({'point_type': point_type, 'part': part, 'level': level,
                'activated': level > 0, 'stage': stage, 'turn': turns,
                'item_id': as_int(cfg.get('itemId')), 'name_id': as_int(cfg.get('name'))})
    texts = read_loaded_item_text([r['name_id'] for r in records], reader=c.reader,
        state_address=c.binding.state_address, environment_address=c.binding.environment_address)
    for row in records:
        row['name'] = texts['texts_by_id'].get(row.pop('name_id'))
        if not row['name']:
            raise FanxiuRuntimeMemoryError('神印名称缺失')
    return records


def read_god_seal_levels():
    """读取全部已加载分组的神印层数，供储物袋自选策略使用，无 GUI 副作用。

    返回 point_type/part 唯一身份、name、item_id（对应自选材料）、原始
    level、activated、stage（重）、turn（转）。未激活与零重已激活明确
    区分；配置未加载直接报错，不能当成零重来选择奖励。重/转按原生
    GodWrathLevel 配置和 GetCurGrade 解码，禁止假设每重固定十级。
    这是即时 Runtime 快照，不落盘陈旧层数，也不决定选哪种神印。
    目录包含尚未显示的分组；activated 不代表解锁条件，选择奖励仍需
    按后续业务策略判断。实测原始等级 121→11重0转，82→7重5转，2→0重2转。
    """
    return read_ui_runtime_snapshot([], lambda c: {
        'complete': True, 'source': 'runtime', 'parts': _loaded_levels(c)})


def read_god_seal_state():
    """以挂载的 GodWrathMainPanel 为入口，校验部位与服务端等级。

    isOpen 来自原生解锁判断；详情 isEnough/isMax 控制升级，不以发光推断。
    baseVo.level 跨“突破”仍单调，用作防重复提交凭证。
    """
    def read(c):
        def ref(v):
            r = table_ref(v)
            if r is None:
                raise FanxiuRuntimeMemoryError('神印对象未加载')
            return r
        def f(v, k):
            return read_ui_object_field(c, ref(v).address, k)
        def items(v):
            rows, count = c.reader.list_items(ref(v))
            if count is None or count != len(rows):
                raise FanxiuRuntimeMemoryError('神印列表不完整')
            return rows
        panels = []
        for host in read_active_ui_window_panels(c, 'PlayerMainPanel'):
            selected = read_ui_selected_tab_panel(c, host.address)
            if selected:
                obj = LuaRef('table', selected[0])
                if table_ref(f(obj, 'SmallGodWrathInfoPanel')):
                    panels.append(obj)
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError('神印面板不唯一')
        p = panels[0]
        index, max_index = as_int(f(p, 'curSelectIndex')), as_int(f(p, 'maxIndex'))
        if index is None or max_index is None or not 0 <= index <= max_index:
            raise FanxiuRuntimeMemoryError('神印地图索引无效')
        def vo(v):
            base = table_ref(f(v, 'baseVo'))
            level = as_int(f(base, 'level')) if base else 0
            point_type, part = as_int(f(v, 'pointType')), as_int(f(v, 'part'))
            if None in (level, point_type, part):
                raise FanxiuRuntimeMemoryError('神印部位状态不完整')
            return {'point_type': point_type, 'part': part, 'level': level}
        parts = []
        for big in items(f(p, 'GodWrathItemList')):
            for small in items(f(big, 'smallItemList')):
                row = vo(f(small, 'data'))
                row['open'] = f(small, 'isOpen')
                if not isinstance(row['open'], bool):
                    raise FanxiuRuntimeMemoryError('神印解锁状态未加载')
                cfg = read_runtime_config_rows(c.reader, [f(small, 'configData')],
                    environment_address=c.binding.environment_address,
                    group_name='GodWrath', table_name='GodWrathBase', fields=('parts', 'name', 'initPos'))[0]
                row['name_id'] = as_int(cfg.get('name'))
                pos = c.reader.numeric_fields(ref(cfg.get('initPos')).address, frozenset({1, 2}))
                row['position'] = [pos.get(1), pos.get(2)]
                parts.append(row)
        texts = read_loaded_item_text([v['name_id'] for v in parts], reader=c.reader,
            state_address=c.binding.state_address, environment_address=c.binding.environment_address)
        for row in parts:
            row['name'] = texts['texts_by_id'].get(row['name_id'])
        levels = {(r['point_type'], r['part']): r for r in _loaded_levels(c)}
        for row in parts:
            value = levels.get((row['point_type'], row['part']))
            if value is None or value['level'] != row['level']:
                raise FanxiuRuntimeMemoryError('神印层数与页面状态冲突')
            row.update(stage=value['stage'], turn=value['turn'], item_id=value['item_id'],
                       activated=value['activated'])
        keys = {(v['point_type'], v['part']) for v in parts}
        if (not parts or len(keys) != len(parts)
                or len({v['point_type'] for v in parts}) != 1
                or any(not v['name'] or any(not isinstance(n, (int, float)) for n in v['position']) for v in parts)):
            raise FanxiuRuntimeMemoryError('神印部位目录或布局不完整')
        detail = None
        if as_int(f(p, 'panelState')) == 3:
            d = f(p, 'SmallGodWrathInfoPanel')
            detail = vo(f(d, 'data'))
            value = levels.get((detail['point_type'], detail['part']))
            if value is None or value['level'] != detail['level']:
                raise FanxiuRuntimeMemoryError('神印详情层数与服务器冲突')
            detail.update(stage=value['stage'], turn=value['turn'], item_id=value['item_id'],
                          activated=value['activated'])
            detail.update(enough=f(d, 'isEnough'), maximum=f(d, 'isMax'),
                          breakthrough=f(d, 'isNeedUpGrade'),
                          animating=f(d, 'isPlayEffect') is True and f(d, 'isBreak') is True)
            if not isinstance(detail['enough'], bool) or not isinstance(detail['maximum'], bool):
                raise FanxiuRuntimeMemoryError('神印升级门槛未加载')
        return {'index': index, 'max_index': max_index, 'parts': parts, 'detail': detail,
                'moving': f(p, 'isMoving'), 'panel_state': as_int(f(p, 'panelState'))}
    return read_ui_runtime_snapshot([], read)
