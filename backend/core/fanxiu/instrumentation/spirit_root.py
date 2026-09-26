"""普通灵根（type=6）的只读事实；五行与异灵根不在本接口的操作范围。"""
from .runtime_memory import FanxiuRuntimeMemoryError, LuaRef, as_int, table_ref
from .ui_runtime_context import (read_active_ui_window_panels, read_ui_object_field,
                                 read_ui_selected_tab_panel, read_ui_runtime_snapshot)


def read_spirit_root_state():
    """读取已打开的普通灵根、服务端等级及游戏计算的可升级叶子。

    isEnough 只代表材料；LingGen_6 还包含 unlockCondition 门槛，不能仅凭
    按钮高亮/红点总组决定升级。服务器 elementInfoDic[6].level 是进展凭证；
    curStage 会在跨重后重置，不能用显示层数判断进展是否单调。
    """
    def read(c):
        def ref(value):
            result = table_ref(value)
            if result is None:
                raise FanxiuRuntimeMemoryError('普通灵根状态未加载')
            return result
        def f(obj, key):
            return read_ui_object_field(c, ref(obj).address, key)
        panels = {}
        for host in read_active_ui_window_panels(c, 'PlayerMainPanel'):
            selected = read_ui_selected_tab_panel(c, host.address)
            if selected:
                panel = LuaRef('table', selected[0])
                if f(panel, 'isSelectOneKey') is not None and table_ref(f(panel, 'DiverseLingGenView')):
                    panels[panel.address] = panel
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError('普通灵根页面不唯一')
        panel = next(iter(panels.values()))
        data = c.reader.fields(ref(f(panel, 'data')))
        env = LuaRef('table', c.binding.environment_address)
        manager = f(f(f(env, 'LinggenMgr'), 'inst'), 'Model')
        records = c.reader.numeric_fields(ref(f(f(manager, 'data'), 'elementInfoDic')).address, frozenset({6}))
        record = c.reader.fields(ref(records.get(6)))
        level = as_int(record.get('level'))
        nodes = c.reader.fields(ref(f(f(f(env, 'RedDotMgr'), 'inst'), 'Model')))
        node = c.reader.fields(ref(c.reader.fields(ref(nodes.get('redDotNodeDic'))).get('LingGen_6')))
        count = as_int(node.get('finalShowCount'))
        enough, one_key = f(panel, 'isEnough'), f(panel, 'isSelectOneKey')
        maximum = data.get('isMaxLv')
        if (level is None or level < 0 or as_int(record.get('type')) != 6
                or not isinstance(maximum, bool) or not isinstance(one_key, bool)
                or (not maximum and not isinstance(enough, bool))
                or node.get('redDotId') != 'LingGen_6' or node.get('needCalculate') is not False
                or count is None or count < 0):
            raise FanxiuRuntimeMemoryError('普通灵根状态不完整或尚未稳定')
        if count > 0 and (maximum or enough is not True):
            raise FanxiuRuntimeMemoryError('普通灵根可升级状态与页面冲突')
        return {'complete': True, 'level': level, 'stage': as_int(data.get('curStage')),
                'max_stage': as_int(data.get('maxStage')), 'max_level': maximum,
                'enough': enough, 'can_upgrade': count > 0, 'one_key': one_key,
                'item_id': as_int(f(panel, 'itemId'))}
    return read_ui_runtime_snapshot([], read)
