"""当前 Revenue 模板礼包商店的只读身份、显示顺序及服务器轮次配置。"""
from .runtime_memory import FanxiuRuntimeMemoryError, LuaRef, table_ref, as_int
from .ui_runtime_context import (read_ui_runtime_snapshot, read_ui_object_field,
    read_active_ui_window_panels, read_ui_selected_tab_panel)


def read_revenue_activity_template_binding(activity_id: int):
    """Read one already-loaded RevenueMainView server template association.

    Native GetActivityCfgData(activityId) indexes V_RevenueCfgData.V_DataDic;
    RevenueMainView uses its templateId to select Activity.Template panels.
    This observation never creates the singleton, loads config, or opens UI.
    Missing data remains unknown; a template alone does not establish a period
    or authorize item consumption.
    """
    if type(activity_id) is not int or activity_id <= 0:
        raise ValueError('需要正整数活动ID')

    def read(c):
        def field(value, key):
            ref = table_ref(value)
            return read_ui_object_field(c, ref.address, key) if ref else None

        env = LuaRef('table', c.binding.environment_address)
        model = field(field(field(env, 'RevenueMgr'), 'inst'), 'Model')
        dictionary = field(field(model, 'V_RevenueCfgData'), 'V_DataDic')
        entries = table_ref(field(dictionary, '_dt_'))
        row = c.reader.numeric_fields(entries.address, frozenset({activity_id})).get(activity_id) if entries else None
        template_id = as_int(field(row, 'templateId'))
        if row is not None and (table_ref(row) is None or not template_id or template_id <= 0):
            raise FanxiuRuntimeMemoryError('服务器活动模板配置不完整', code='runtime_incomplete')
        return {'activity_id': activity_id,
                'source': 'RevenueMgr.Model.V_RevenueCfgData.V_DataDic',
                'status': 'loaded' if template_id and template_id > 0 else 'not_loaded',
                'template_id': template_id if template_id and template_id > 0 else None,
                'pid': c.memory.pid, 'process_start_ticks': c.memory.process_start_ticks,
                'read_only': True, 'activity_period_verified': False,
                'consumption_authorized': False}
    return read_ui_runtime_snapshot([], read, fast=True)


def read_revenue_activity_round_config(activity_id):
    """只读已加载的服务器 openGrade；监测时无需打开商店或切换运行权。"""
    def read(c):
        def f(v, key):
            r = table_ref(v)
            if r is None:
                raise FanxiuRuntimeMemoryError('服务器活动配置未加载')
            return read_ui_object_field(c, r.address, key)
        env = LuaRef('table', c.binding.environment_address)
        data = f(f(f(f(env, 'RevenueMgr'), 'inst'), 'Model'), 'RevenueData')
        dic = table_ref(f(data, 'V_ActivityDic'))
        if dic is None:
            raise FanxiuRuntimeMemoryError('服务器活动配置字典未加载')
        vo = c.reader.dictionary_fields(dic).get(int(activity_id))
        if as_int(f(vo, 'activityId')) != int(activity_id):
            raise FanxiuRuntimeMemoryError('服务器活动配置身份不一致')
        grade = f(f(vo, 'revenueBaseVO'), 'openGrade')
        if not isinstance(grade, str) or not grade:
            raise FanxiuRuntimeMemoryError('服务器活动轮次配置未加载')
        return dict(complete=True, activity_id=int(activity_id), open_grade=grade)
    return read_ui_runtime_snapshot([], read, fast=True)


def read_activity_template_store():
    """调用前用商店 scene 确认页面；不加载游戏，也不调用游戏 Lua。"""
    def read(c):
        def ref(v):
            r = table_ref(v)
            if r is None:
                raise FanxiuRuntimeMemoryError('活动模板商店数据未加载')
            return r
        def f(v, key):
            return read_ui_object_field(c, ref(v).address, key)
        panels = read_active_ui_window_panels(c, 'RevenueMainView')
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError('活动模板窗口未唯一加载')
        selection = read_ui_selected_tab_panel(c, panels[0].address)
        p = LuaRef('table', selection[0]) if selection else None
        if p is None or f(p, 'V_IsShow') is not True or f(p, 'InitType') != 'ACTIVITY_GIFT':
            raise FanxiuRuntimeMemoryError('当前模板页不是活动礼包商店')
        identity = as_int(f(p, 'V_ActivityId'))
        turn_id = as_int(f(p, 'V_ActivityTurnActivityID'))
        rows, count = c.reader.list_items(ref(f(p, 'V_ShowList')))
        ids = [as_int(f(r, 'id')) for r in rows]
        if not identity or not turn_id or count != len(ids) or not ids or None in ids or len(set(ids)) != len(ids):
            raise FanxiuRuntimeMemoryError('活动商店身份或显示顺序不完整')
        env = LuaRef('table', c.binding.environment_address)
        data = f(f(f(f(env, 'RevenueMgr'), 'inst'), 'Model'), 'RevenueData')
        vo = c.reader.dictionary_fields(ref(f(data, 'V_ActivityDic'))).get(turn_id)
        grade = f(f(vo, 'revenueBaseVO'), 'openGrade')
        if not isinstance(grade, str) or not grade:
            raise FanxiuRuntimeMemoryError('服务器轮次配置未加载')
        bindings = []
        scroll = ref(f(p, 'scrollView'))
        for obj in c.reader.dictionary_fields(ref(f(scroll, 'ItemClassDic'))).values():
            r = table_ref(obj)
            if r is None:
                raise FanxiuRuntimeMemoryError('商店已渲染商品对象不完整')
            ident = as_int(f(r, 'V_Id'))
            if ident in ids:
                config = ref(f(r,'V_Data'))
                if as_int(f(config,'id')) != ident:
                    raise FanxiuRuntimeMemoryError('已渲染礼包配置身份不一致')
                bindings.append(dict(id=ident, can_buy=f(r,'canBuy'),
                    enough=f(r,'V_IsEnough'), sold_out=f(r,'isEmpty'), index=as_int(f(r,'index')),
                    pay_id=as_int(f(config,'payId')) or 0, costs=f(config,'costs') or '',
                    activity_id=as_int(f(config,'activityId'))))
        return dict(complete=True, activity_id=identity, turn_activity_id=turn_id,
                    ordered_gift_ids=ids, open_grade=grade, gift_bindings=bindings,
                    source='active_template_store')
    return read_ui_runtime_snapshot([], read, fast=True)
