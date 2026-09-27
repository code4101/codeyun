"""丹灵已加载页面的只读事实；不调用 Lua、不发送游戏协议。

每次从活动窗口重新解析所选丹灵和页签，地址不跨观察缓存。
升阶使用原生 isEnough/isMax，升级使用 isCanUp/isMaxLevel；红点会被
客户端的“已查看”标志清除，不能据此证明培养已经完成。
"""
from .runtime_memory import FanxiuRuntimeMemoryError, LuaRef, as_int, table_ref
from .ui_runtime_context import (
    read_active_ui_window_panels, read_ui_object_field,
    read_ui_runtime_snapshot, read_ui_selected_tab_panel,
)
from .resource_auto_use import read_runtime_config_rows
from .item_config import read_loaded_item_text


def read_danling_state():
    """返回当前目录或详情的身份、服务器阶数/等级/经验与原生消耗门槛。

目录顺序由客户端决定；详情 ShowVoList 是已通过开放条件的完整集合。
缺失字段失败关闭，不能把未加载当成材料不足。升级页动画期间的消耗
字段只作诊断；调用方须等待 animating=False 后再决定下一次操作。
"""
    def read(c):
        def fail(message):
            raise FanxiuRuntimeMemoryError('丹灵：' + message, code='danling_incomplete')

        def ref(value):
            result = table_ref(value)
            if result is None:
                fail('对象未加载')
            return result

        def field(obj, key):
            return read_ui_object_field(c, ref(obj).address, key)

        def number(obj, key):
            value = as_int(field(obj, key))
            if value is None or value < 0:
                fail(f'{key} 缺失或无效')
            return value

        def boolean(obj, key):
            value = field(obj, key)
            if not isinstance(value, bool):
                fail(f'{key} 缺失')
            return value

        def items(obj):
            values, count = c.reader.list_items(ref(obj))
            if count is None or count != len(values):
                fail('列表不完整')
            return values

        def vo(obj):
            server = field(obj, 'serverData')
            ident = number(obj, 'id')
            if number(server, 'elfId') != ident:
                fail('服务端身份不符')
            return dict(id=ident, stage=number(server, 'stage'),
                        level=number(server, 'level'), exp=number(server, 'exp'))

        def config(row, table, fields):
            return read_runtime_config_rows(c.reader, [ref(row)],
                environment_address=c.binding.environment_address,
                group_name='MedicalElf', table_name=table, fields=fields)[0]

        detail = read_active_ui_window_panels(c, 'MedicalelfLookMainView')
        main = read_active_ui_window_panels(c, 'MedicalelfMainView')
        if len(detail) > 1 or len(main) > 1:
            fail('活动窗口不唯一')
        host = detail[0] if detail else main[0] if main else None
        if host is None:
            fail('页面未打开')
        selected = read_ui_selected_tab_panel(c, host.address)
        if selected is None:
            fail('页签未加载')
        panel, tab = LuaRef('table', selected[0]), selected[1]
        result = {'page': 'detail' if detail else 'list', 'tab': tab}
        if detail:
            values = items(field(host, 'ShowVoList'))
            index = number(host, 'curShowIndex')
            maximum = number(host, 'maxIndex')
            if maximum != len(values)-1 or not 0 <= index <= maximum:
                fail('详情目录或索引不完整')
            rows = [vo(v) for v in values]
            current = vo(field(host, 'curShowVo'))
            if current != rows[index] or current != vo(field(panel, 'curShowVo')):
                fail('页签与丹灵身份不符')
            result.update(index=index, maximum_index=maximum, current=current)
            if tab == 0:
                raw_maximum = field(panel, 'isMax')
                if raw_maximum is None and current['stage'] == 0:
                    maximum = False
                elif isinstance(raw_maximum, bool):
                    maximum = raw_maximum
                else:
                    fail('isMax 缺失')
                enough = boolean(panel, 'isEnough')
                item_id = as_int(field(panel, 'costItemId'))
                # UpdateSureBtnShow clears costItemId before its free/max branch,
                # but can leave an older costInfo object on the pooled panel.
                cost = table_ref(field(panel, 'costInfo')) if item_id else None
                result.update(maximum=maximum, actionable=not maximum and enough,
                    animating=False, active=boolean(panel, 'isActive'),
                    cost_item_id=item_id,
                    owned=number(cost, 'hadNum') if cost else None,
                    cost=number(cost, 'itemNum') if cost else None)
            elif tab == 1:
                # CheckIsMaxLevel returns nil before the first level; Lua's
                # cached predicate only materializes a boolean for level > 0.
                raw_maximum = field(panel, 'isMaxLevel')
                if raw_maximum is None and current['level'] == 0:
                    maximum = False
                elif isinstance(raw_maximum, bool):
                    maximum = raw_maximum
                else:
                    fail('isMaxLevel 缺失')
                # A max-level branch hides the button before initializing costs.
                result.update(maximum=maximum,
                    actionable=current['stage'] > 0 and not maximum and boolean(panel, 'isCanUp')
                               and number(panel, 'hadNum') > 0,
                    animating=field(panel, 'needPlayExpTween') is True or field(panel, 'isUseExpItem') is True,
                    cost_item_id=as_int(field(panel, 'itemId')),
                    owned=number(panel, 'hadNum') if not maximum else None,
                    cost=number(panel, 'needNum') if not maximum else None)
            else:
                fail(f'未知详情页签 {tab}')
        else:
            if tab != 0:
                fail('当前不是丹灵列表页签')
            values = items(field(panel, 'showDataList'))
            rows = [vo(v) for v in values]
        if not rows or len({r['id'] for r in rows}) != len(rows):
            fail('丹灵目录缺失或重复')
        configs = [config(field(v, 'configData'), 'MedicalElf', ('name',)) for v in values]
        name_ids = [as_int(cfg['name']) for cfg in configs]
        if None in name_ids:
            fail('名称配置缺失')
        texts = read_loaded_item_text(name_ids, reader=c.reader,
            state_address=c.binding.state_address, environment_address=c.binding.environment_address)
        for row, name_id in zip(rows, name_ids):
            row['name'] = texts['texts_by_id'].get(name_id)
            if not row['name']:
                fail('丹灵名称未加载')
        if detail:
            result['current']['name'] = rows[index]['name']
        result['spirits'] = rows
        return result
    return read_ui_runtime_snapshot([], read)


def read_danling_result_window():
    """Identify native cultivation/bloodline result overlays, without clicks.

Shared reward artwork can be classified as another reward scene. Membership
in UIShowMgr, and the underlying operation's monotonic result, disambiguate
the overlay; the action layer still waits for the visible continue control.
"""
    def read(c):
        found = [name for name in ('MedicalelfActiveNewTipsView',
            'MedicalelfUpLevelView', 'MedicalelfActiveView', 'MedicalelfActivateSuitView')
            if read_active_ui_window_panels(c, name)]
        if len(found) > 1:
            raise FanxiuRuntimeMemoryError('丹灵结果窗口重叠', code='danling_incomplete')
        return found[0] if found else None
    return read_ui_runtime_snapshot([], read)


def read_danling_bloodline():
    """读取当前血脉下一节点、已激活节点和门槛；仅支持已核验的原生条件。

    GetStarMasterNum 的输入取自客户端刚刷新过的 activeList，按 StarMaster
    配置质量集合汇总服务器阶数。未知条件报错，不把读取失败当作不能激活。
    """
    import re

    def read(c):
        def fail(message):
            raise FanxiuRuntimeMemoryError('丹灵血脉：' + message, code='danling_incomplete')

        def field(obj, key):
            obj = table_ref(obj)
            if obj is None:
                fail(f'{key} 对象缺失')
            return read_ui_object_field(c, obj.address, key)

        def items(obj):
            obj = table_ref(obj)
            if obj is None:
                fail('列表缺失')
            values, count = c.reader.list_items(obj)
            if count is None or count != len(values):
                fail('列表不完整')
            return values

        def config(rows, table, keys):
            return read_runtime_config_rows(c.reader, rows,
                environment_address=c.binding.environment_address,
                group_name='MedicalElf', table_name=table, fields=keys)

        hosts = read_active_ui_window_panels(c, 'MedicalelfMainView')
        if len(hosts) != 1:
            fail('主窗口未就绪')
        main = read_ui_selected_tab_panel(c, hosts[0].address)
        if main is None or main[1] != 1:
            fail('未选中血脉')
        host = LuaRef('table', main[0])
        selected = read_ui_selected_tab_panel(c, host.address)
        if selected is None:
            fail('品质页未加载')
        tabs = items(field(host, 'tabList'))
        tab_rows = config([field(t, 'cfg') for t in tabs], 'MedicalElfQuality', ('qualityName',))
        text_ids = [as_int(t['qualityName']) for t in tab_rows]
        if None in text_ids:
            fail('品质名称缺失')
        texts = read_loaded_item_text(text_ids, reader=c.reader,
            state_address=c.binding.state_address, environment_address=c.binding.environment_address)['texts_by_id']
        tab_names = [''.join(texts.get(i, '').split()) for i in text_ids]
        if any(not isinstance(n, str) or not n for n in tab_names):
            fail('品质名称未加载')
        popup = read_active_ui_window_panels(c, 'MedicalelfSuitActiveView')
        result = dict(tab=selected[1], tabs=tab_names, popup=bool(popup))
        if len(popup) > 1:
            fail('节点窗口不唯一')
        # Cached quality tabs do not reopen their tooltip. The native panel's
        # selected next node remains authoritative with or without that tooltip.
        cfg = config([field(LuaRef('table', selected[0]), 'selectCountCfg')], 'MedicalElfCombination',
                     ('id', 'group', 'condition', 'groupAttrCondition', 'pointName', 'bigPoint'))[0]
        data = field(field(field(c.field(c.binding.environment_address, 'MedicalelfMgr'), 'inst'),
                           'Model'), 'MedicalelfData')
        effects = c.reader.dictionary_fields(field(data, 'effects'))
        active = as_int(effects.get(as_int(cfg['group']), 0))
        if active is None:
            fail('服务端激活状态缺失')
        has_active = as_int(cfg['id']) <= active
        popup_id = None
        if popup:
            popup_id = config([field(popup[0], 'cfg')], 'MedicalElfCombination', ('id',))[0]['id']
            if as_int(field(popup[0], 'curActiveId')) != active:
                fail('窗口与服务端激活状态不一致')
        node_text_id = as_int(cfg['pointName'])
        if node_text_id is None:
            fail('节点名称配置缺失')
        node_name = read_loaded_item_text([node_text_id], reader=c.reader,
            state_address=c.binding.state_address, environment_address=c.binding.environment_address)['texts_by_id'].get(node_text_id)
        if not node_name:
            fail('节点名称未加载')
        stars = config(items(field(data, 'starMasterList')), 'StarMaster', ('id', 'quality'))
        vos = items(field(data, 'activeList'))
        vo_cfgs = config([field(v, 'configData') for v in vos], 'MedicalElf', ('id', 'quality'))
        totals = {}
        for star in stars:
            raw = c.reader.table(table_ref(star['quality']).address)
            qualities = {as_int(v) for v in raw['array'] if v is not None}
            qualities.update(as_int(v) for k, v in raw['fields'].items() if isinstance(k, int))
            if not qualities or None in qualities:
                fail('品质集合缺失')
            total = 0
            for vo, vo_cfg in zip(vos, vo_cfgs):
                stage = as_int(field(field(vo, 'serverData'), 'stage'))
                if stage is None or stage < 1:
                    fail('已激活丹灵阶数缺失')
                if as_int(vo_cfg['quality']) in qualities:
                    total += stage
            totals[as_int(star['id'])] = total
        conditions = []
        for term in str(cfg['condition']).split(','):
            count = re.fullmatch(r'MedicalElfStarMasterNum\|(\d+)_(\d+)', term)
            previous = re.fullmatch(r'MedicalElfComLevel\|(\d+)', term)
            if count and int(count[1]) in totals:
                conditions.append(totals[int(count[1])] >= int(count[2]))
            elif previous:
                conditions.append(active >= int(previous[1]))
            else:
                fail(f'未知激活条件 {term}')
        result.update(id=as_int(cfg['id']), group=as_int(cfg['group']), active_id=active,
                      actionable=not has_active and all(conditions), condition=cfg['condition'], totals=totals,
                      popup_id=as_int(popup_id), node_name=node_name, big_point=as_int(cfg['bigPoint']) == 1)
        return result
    return read_ui_runtime_snapshot(['MedicalelfMgr'], read)
