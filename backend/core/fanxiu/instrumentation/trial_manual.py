"""试炼手册 Newbattlepass 的只读状态；与 ActivityBattlePass 活动战令分离。

只读取已打开页面、完整奖励列表和服务器同步记录。不缓存页面地址，
每次沿当前选中页重新绑定；换期、退出重进或进程重启均重新校验身份。
初始化和循环奖励领取尚待对应真实状态验收，异常保留现场检查此适配层。
"""
from datetime import datetime, timezone

from .runtime_memory import (FanxiuRuntimeMemoryError, LuaRef, as_int,
                             manager_index_fields, resolve_lua_global_manager_root, table_ref)
from .ui_runtime_context import (active_ui_component_objects, read_ui_object_field,
                                 read_ui_selected_tab_panel, read_ui_runtime_snapshot)
from .resource_auto_use import read_runtime_config_rows


def read_trial_manual_state():
    """先确认 #863 就绪，再读取免费轨完整状态；不执行游戏方法。

    维修索引：NewbattlepassData._battlePassInfo.buys 决定免费轨是否激活；
    gotNormalRewardIds 是已领事实；newBattlePassVO.refreshIds 与页面奖励
    ID 全集校验周期，normalMap 提供循环箱已领次数。页面 reached 与
    _normalCanGet 提供游戏已计算的达标状态，不能用高亮/红点替代。

    研发曾误选名称相似的 ActivityBattlePassMgr，它属于另一类活动战令，
    其数据可完整加载却不是当前手册。后续字段变动先核对页面实际使用的
    NewbattlepassMgr，不能放宽活动/周期校验来兼容错误管理器。
    Lua claimed_normal 缺失可以表示 nil，须与完整服务器已领集合合并判断；
    必要列表、布尔状态或配置缺失则报错，不能都归一成 false/0。

    配置 data 是带默认值的 packed row，由 read_runtime_config_rows 解码；
    直接读字段得到空表不表示无奖励。不持有跨观察的 UI 地址或字节缓存。
    页面同步冲突时先等待真实更新并新读取，勿修改内存或缓存伪造成功。
    """
    def read(c):
        def fail(reason):
            raise FanxiuRuntimeMemoryError('试炼手册：' + reason, code='runtime_incomplete')

        def ref(value):
            result = table_ref(value)
            if result is None:
                fail('所需对象未加载')
            return result

        def field(obj, name):
            return read_ui_object_field(c, ref(obj).address, name)

        def items(value):
            values, count = c.reader.list_items(ref(value))
            if count is None or len(values) != count or not 0 <= count <= 256:
                fail('列表不完整')
            return values

        def ids(value):
            values = [as_int(v) for v in items(value)]
            if any(v is None or v <= 0 for v in values) or len(set(values)) != len(values):
                fail('ID 列表无效')
            return values

        panels = {}
        for component in active_ui_component_objects(c):
            host = table_ref(field(component, 'm_panel')) or component
            if as_int(field(host, 'V_ActivityId')) is None:
                continue
            selected = read_ui_selected_tab_panel(c, host.address)
            if selected is None:
                continue
            panel = LuaRef('table', selected[0])
            if field(panel, 'V_contentKey') == 'BattlePass_type1_':
                if field(panel, 'V_IsShow') is not True:
                    fail('手册页未显示')
                activity_id = as_int(field(panel, 'V_ActivityId'))
                if activity_id != as_int(field(host, 'V_ActivityId')):
                    fail('父子活动不一致')
                panels[panel.address] = (panel, activity_id)
        if len(panels) != 1:
            fail('当前手册页不唯一')
        panel, activity_id = next(iter(panels.values()))

        methods = frozenset({'Inst_get', 'LuaNewbattlepassMgr'})
        root, _, environment = resolve_lua_global_manager_root(
            c.memory, manager_key='trial-manual-new', state_address=c.binding.state_address,
            global_name='NewbattlepassMgr', required_methods=methods,
            validate=lambda reader, address: manager_index_fields(reader, address, methods))
        manager = manager_index_fields(c.reader, root, methods)
        data = field(field(manager['inst'], 'Model'), 'NewbattlepassData')
        info = field(data, '_battlePassInfo')
        vo = field(info, 'newBattlePassVO')
        bought = ids(field(info, 'buys'))
        claimed = set(ids(field(info, 'gotNormalRewardIds')))
        refresh_ids = ids(field(vo, 'refreshIds'))
        level, round_ = as_int(field(vo, 'level')), as_int(field(vo, 'round'))
        if level is None or round_ is None or not refresh_ids:
            fail('周期身份不完整')
        activated = 1 in bought
        view_items = items(field(panel, '_viewItemList'))
        if not view_items:
            fail('奖励列表未加载')
        raw_rows = [c.reader.fields(ref(item)) for item in view_items]
        normal = [row for row in raw_rows if row.get('_isBoxCfg') is False]
        boxes = [row for row in raw_rows if row.get('_isBoxCfg') is True]
        if len(normal) + len(boxes) != len(raw_rows) or len(boxes) != 1:
            fail('奖励类型不完整')
        configs = read_runtime_config_rows(c.reader, [ref(row.get('data')) for row in normal],
            environment_address=environment, group_name='NewBattlePass',
            table_name='NewBattlePassReward', fields=('id', 'score'))
        rows = []
        current_index = as_int(field(panel, '_curScoreIndex'))
        for row, cfg in zip(normal, configs):
            ident, score = as_int(cfg['id']), as_int(cfg['score'])
            reached = row.get('reached')
            if (ident != as_int(row.get('rewardId')) or ident is None or score is None
                    or not isinstance(reached, bool)):
                fail('奖励配置与实例不一致')
            if row.get('claimed_normal') is not None and row['claimed_normal'] != (ident in claimed):
                fail('领取记录尚未同步到页面')
            rows.append({'reward_id': ident, 'score': score, 'claimed': ident in claimed,
                         'current_marker': as_int(row.get('_index')) == current_index,
                         'claimable': activated and reached and ident not in claimed})
        if len({r['reward_id'] for r in rows}) != len(rows) or set(refresh_ids) != {r['reward_id'] for r in rows}:
            fail('服务器奖励周期与页面不一致')
        box = boxes[0]
        box_id = as_int(box.get('rewardId'))
        can_get = box.get('_normalCanGet')
        if box_id is None or not isinstance(can_get, bool):
            fail('底部循环奖励状态缺失')
        counts = c.reader.dictionary_fields(ref(field(vo, 'normalMap')))
        box_count = as_int(counts.get(box_id, 0))
        if box_count is None or box_count < 0:
            fail('循环奖励领取次数无效')
        return {'complete': True, 'activity_id': activity_id, 'cycle': [level, round_, refresh_ids],
                'observed_at': datetime.now(timezone.utc).isoformat(), 'activated': activated,
                'claimed_ids': sorted(claimed), 'rewards': rows,
                'box': {'reward_id': box_id, 'claimable': activated and can_get, 'claimed_count': box_count}}
    return read_ui_runtime_snapshot([], read)
