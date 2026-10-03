"""法身目录与服务器等级的只读投影；页面必须先由 scene 确认自然加载。

仅复用公共 UI 根；每次观察重新解析当前页面及目标，不缓存选择意图。
FieldTransformData._levelInfoDic 是完成事实，红点只提供入口线索。
"""
import re
from .runtime_memory import FanxiuRuntimeMemoryError, LuaRef, as_int, table_ref
from .ui_runtime_context import (read_ui_runtime_snapshot, read_ui_object_field,
                                read_active_ui_window_panels, read_ui_selected_tab_panel)
from .resource_auto_use import read_runtime_config_rows


def read_field_transform_state(*, page='overview'):
    """返回完整当前目录、等级和已打开详情；缺失数据失败关闭，无游戏副作用。"""
    if page not in ('overview', 'detail'):
        raise ValueError('法身观察必须传入已由 scene 确认的 overview/detail')
    def read(c):
        def ref(v):
            result = table_ref(v)
            if result is None:
                raise FanxiuRuntimeMemoryError('法身对象未加载', code='data_not_loaded')
            return result
        def f(v, key):
            return read_ui_object_field(c, ref(v).address, key)
        def items(v):
            rows, count = c.reader.list_items(ref(v))
            if count is None or count != len(rows):
                raise FanxiuRuntimeMemoryError('法身目录不完整')
            return rows
        panels = read_active_ui_window_panels(c, 'FieldTransformListPanel')
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError('法身总览未唯一加载', code='data_not_loaded')
        p = panels[0]
        data = f(f(f(f(LuaRef('table', c.binding.environment_address), 'FieldTransformMgr'), 'inst'), 'Model'), 'FieldTransformData')
        rows = read_runtime_config_rows(c.reader, items(f(p, '_allShowCfgList')),
            environment_address=c.binding.environment_address, group_name='FieldTransform',
            table_name='FieldTransform', fields=('transformId', 'sort', 'quality', 'fashionId', 'genre'))
        ids = [as_int(r.get('transformId')) for r in rows]
        if not ids or None in ids or len(set(ids)) != len(ids):
            raise FanxiuRuntimeMemoryError('法身身份缺失或重复')
        level_dic = f(data, '_levelInfoDic')
        level_storage = table_ref(f(level_dic, '_dt_'))
        if level_storage is None:
            raise FanxiuRuntimeMemoryError('法身服务器等级字典未加载')
        levels = c.reader.numeric_fields(level_storage.address, frozenset(ids))
        for row in rows:
            row['transform_id'] = as_int(row.pop('transformId'))
            row['level'] = as_int(levels.get(row['transform_id'], 0))
            if row['level'] is None or row['level'] < 0:
                raise FanxiuRuntimeMemoryError('法身服务器等级无效')
        quality_dic = f(data, '_qualityCfgDic')
        quality_storage = c.reader.dictionary_fields(ref(quality_dic))
        for row in rows:
            cfgs = quality_storage.get(row['transform_id'])
            if table_ref(cfgs) is None:
                raise FanxiuRuntimeMemoryError('法身进阶配置未加载')
            nxt = c.reader.dictionary_fields(ref(cfgs)).get(row['level']+1)
            row['maximum'] = nxt is None
            row['cost_item_id'], row['cost_count'] = None, 0
            if nxt is not None:
                cfg = read_runtime_config_rows(c.reader, [nxt], environment_address=c.binding.environment_address,
                    group_name='FieldTransform', table_name='FieldTransformAttr', fields=('transformId','rank','cleanseItem'))[0]
                if as_int(cfg['transformId']) != row['transform_id'] or as_int(cfg['rank']) != row['level']+1:
                    raise FanxiuRuntimeMemoryError('法身下一阶配置身份不一致')
                cost = re.fullmatch(r'Item\|(\d+)_(\d+)', str(cfg['cleanseItem']))
                if cost is None:
                    raise FanxiuRuntimeMemoryError(f'法身消耗格式未知：{cfg["cleanseItem"]!r}')
                row['cost_item_id'], row['cost_count'] = map(int, cost.groups())
        fashion_data = f(f(f(f(LuaRef('table',c.binding.environment_address),'FashionMgr'),'inst'),'Model'),'FashionData')
        fashion_ids = {int(r['fashionId']) for r in rows}
        fashion = {}
        for v in items(f(fashion_data,'AllFashionInfoVoList')):
            values = c.reader.string_fields(ref(v).address, frozenset({'id','serverData','level'}))
            identity = as_int(values.get('id'))
            if identity in fashion_ids:
                server = table_ref(values.get('serverData'))
                fashion[identity] = as_int(values.get('level')) if server else 0
        if set(fashion) != fashion_ids or any(v is None or v<0 for v in fashion.values()):
            raise FanxiuRuntimeMemoryError('法身衣装等级不完整')
        for row in rows:
            row['fashion_level'] = fashion[int(row['fashionId'])]
            row['free'] = not row['maximum'] and row['fashion_level']>row['level']
        detail = None
        main = table_ref(f(p, 'FieldTransformMainView'))
        def active(obj):
            go = table_ref(f(obj,'gameObject'))
            state = f(go,'isQuickActive') if go else None
            if not isinstance(state,bool): state = f(go,'ActiveState') if go else None
            if not isinstance(state,bool): raise FanxiuRuntimeMemoryError('法身面板可见状态未知')
            return state
        # Unity 包装器保留旧 ActiveState，不能据此认定当前是详情。
        selected = read_ui_selected_tab_panel(c, main.address) if page == 'detail' and main and active(main) else None
        if selected:
            d = LuaRef('table', selected[0])
            target = as_int(f(d, '_transformId'))
            if target in ids:
                item = table_ref(f(d, '_costItemCfg'))
                item_id = (as_int(read_runtime_config_rows(c.reader, [item],
                    environment_address=c.binding.environment_address, group_name='Item',
                    table_name='Item', fields=('id',))[0]['id']) if item else None)
                detail = {'transform_id': target, 'level': as_int(levels.get(target, 0)),
                    'maximum': f(d, '_isMaxLevel'), 'free': f(d, '_isFashionUpgradeMode'),
                    'cost_item_id': item_id,
                    'cost_count': as_int(f(d, '_costNum'))}
                if not isinstance(detail['maximum'], bool) or not isinstance(detail['free'], bool):
                    raise FanxiuRuntimeMemoryError('法身提升门槛未就绪')
        return {'complete': True, 'source': 'runtime', 'rows': rows, 'detail': detail,
                'fading': f(p, 'isFading') is True}
    return read_ui_runtime_snapshot([], read, fast=True)


def read_field_transform_selection():
    """已由 #915 确认详情后，只读当前身份，供有界列表定位使用。"""
    def read(c):
        panels = read_active_ui_window_panels(c, 'FieldTransformListPanel')
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError('法身窗口未唯一加载')
        main = table_ref(read_ui_object_field(c, panels[0].address, 'FieldTransformMainView'))
        selected = read_ui_selected_tab_panel(c, main.address) if main else None
        value = as_int(read_ui_object_field(c, selected[0], '_transformId')) if selected else None
        if value is None:
            raise FanxiuRuntimeMemoryError('法身详情身份未加载')
        return value
    return read_ui_runtime_snapshot([], read, fast=True)
