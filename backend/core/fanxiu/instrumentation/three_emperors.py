"""三皇灵威当前节点只读投影；仅在原生页面就绪后读取。"""
import re
from .runtime_memory import table_ref, as_int, FanxiuRuntimeMemoryError
from .ui_runtime_context import read_ui_runtime_snapshot, read_active_ui_window_panels, read_ui_object_field
from .resource_auto_use import read_runtime_config_defaults


def _node(c, panel):
    node = table_ref(read_ui_object_field(c, panel.address, 'nodeItemData'))
    root = table_ref(c.field(c.binding.environment_address, 's_globalCfgIdx'))
    group = table_ref(read_ui_object_field(c, root.address, 'Spirit')) if root else None
    if node is None or group is None:
        raise FanxiuRuntimeMemoryError('三皇节点配置未加载', code='data_not_loaded')
    indexes = c.reader.fields(read_ui_object_field(c, group.address, 'SpiritLevel'))
    defaults = read_runtime_config_defaults(c.reader, {1: node}, indexes)
    wanted = ('level', 'consume', 'skillGroup', 'nodeType', 'id')
    raw = c.reader.numeric_fields(node.address, frozenset(int(indexes[k]) for k in wanted))
    result = {k: raw.get(indexes[k], defaults.get(k)) for k in wanted}
    cost = re.fullmatch(r'Item\|36_(\d+)', result['consume'])
    if not cost or int(cost[1]) <= 0:
        raise FanxiuRuntimeMemoryError('三皇灵威出现未知消耗', code='unknown_cost')
    return result, int(cost[1])


def read_three_emperors():
    def read(c):
        hosts = read_active_ui_window_panels(c, 'CelestialdemonMainView')
        if len(hosts) != 1:
            raise FanxiuRuntimeMemoryError('三皇灵威主界面未唯一加载', code='data_not_loaded')
        h = hosts[0]
        result = {key: read_ui_object_field(c, h.address, key)
                  for key in ('career', 'curLevel', 'nodeState', 'isEnough', 'isMax')}
        if result['isMax'] is True:
            return result
        result['node'], result['cost'] = _node(c, h)
        if as_int(result['curLevel']) is None or result['nodeState'] != 1:
            raise FanxiuRuntimeMemoryError('三皇灵威未选中下一可领悟节点', code='runtime_incomplete')
        return result
    return read_ui_runtime_snapshot(('s_globalCfgIdx',), read)


def read_three_emperors_choice():
    """Read native list order and selection identity without retaining pointers.

    realShowList follows the client's ascending config ID order and horizontal
    layout. The first data object is the leftmost option; selection identity
    and activation were verified on the two-option skill group 28.
    """
    def read(c):
        hosts = read_active_ui_window_panels(c, 'CelestialdemonSkillChangeView')
        if len(hosts) != 1:
            raise FanxiuRuntimeMemoryError('三皇抉择页未唯一加载', code='data_not_loaded')
        h = hosts[0]
        result = {k: read_ui_object_field(c, h.address, k) for k in
                  ('nodeState', 'skillGroup', 'initSelectedSkill', 'isCanClick', 'isEnough')}
        result['node'], result['cost'] = _node(c, h)
        rows, count = c.reader.indexed_list_items(read_ui_object_field(c, h.address, 'realShowList'))
        if count is None or count != len(rows) or not 1 <= count <= 4:
            raise FanxiuRuntimeMemoryError('三皇抉择选项不完整', code='runtime_incomplete')
        rows.sort(key=lambda row: row[0])
        first = table_ref(rows[0][1])
        selected = table_ref(read_ui_object_field(c, h.address, 'selectData'))
        if first is None or selected is None:
            raise FanxiuRuntimeMemoryError('三皇抉择选择状态缺失', code='runtime_incomplete')
        result['selected_first'] = first == selected
        result['option_count'] = count
        return result
    return read_ui_runtime_snapshot(('s_globalCfgIdx',), read)
