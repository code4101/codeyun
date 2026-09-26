"""炼体感悟只读状态；与淬炼、升阶材料消耗严格分开。"""
from .runtime_memory import FanxiuRuntimeMemoryError, LuaRef, as_int, table_ref
from .ui_runtime_context import (read_active_ui_window_panels, read_ui_object_field,
                                 read_ui_selected_tab_panel, read_ui_runtime_snapshot)


def read_physical_comprehension_state():
    """读取当前功法及服务器 comprehensionLv；不缓存地址或执行游戏函数。"""
    def read(c):
        def ref(v):
            r = table_ref(v)
            if r is None:
                raise FanxiuRuntimeMemoryError('炼体感悟对象未加载')
            return r
        def f(v, key):
            return read_ui_object_field(c, ref(v).address, key)
        hosts = read_active_ui_window_panels(c, 'PhysicalDetailMainView')
        panels = []
        for host in hosts:
            selected = read_ui_selected_tab_panel(c, host.address)
            if selected:
                panels.append(LuaRef('table', selected[0]))
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError('炼体功法详情不唯一')
        panel = panels[0]
        base_id = as_int(f(panel, 'baseId'))
        model = f(f(f(LuaRef('table', c.binding.environment_address), 'PhysicalexerciseMgr'), 'inst'), 'Model')
        records, count = c.reader.list_items(ref(f(model, 'hadLearnList')))
        if count is None or count != len(records):
            raise FanxiuRuntimeMemoryError('炼体已学功法不完整')
        found = [c.reader.fields(ref(v)) for v in records if as_int(f(v, 'baseId')) == base_id]
        can, maximum = f(panel, 'canGanWu'), f(panel, 'maxGanWu')
        level = as_int(found[0].get('comprehensionLv')) if len(found) == 1 else None
        if base_id is None or level is None or not isinstance(can, bool) or not isinstance(maximum, bool):
            raise FanxiuRuntimeMemoryError('炼体感悟状态不完整')
        return {'base_id': base_id, 'level': level, 'can_upgrade': can, 'max_level': maximum,
                'item_id': as_int(f(panel, 'ganWuItemId')), 'selected_page': as_int(f(panel, 'selectedPage'))}
    return read_ui_runtime_snapshot([], read)
