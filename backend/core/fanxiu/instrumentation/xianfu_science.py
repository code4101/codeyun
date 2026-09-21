"""玄机阁树与节点详情的只读投影，不调用游戏 Lua 或触发配置加载。"""
from __future__ import annotations

import re

from .runtime_memory import FanxiuRuntimeMemoryError, LuaRef, as_int, table_ref
from .resource_auto_use import read_runtime_config_defaults
from .ui_runtime_context import (
    read_active_ui_window_panels, has_ui_object_fields, read_ui_object_field,
    read_ui_runtime_snapshot, read_ui_selected_tab_panel,
)


def read_xianfu_science(*, detail=False):
    """Return a complete positioned graph, or the active science detail.

    Positions come from each VO's client-calculated posInfo (y is negated for
    screen ordering). Costs use the current-level row, as BuildScienceTipsView
    does. Only the observed XianFuScience predecessor condition and Item costs
    are supported; other condition/cost types fail closed. Config defaults are
    read from the loaded closure, including the omitted level-zero value.
    """
    def read(c):
        def fail(message):
            raise FanxiuRuntimeMemoryError(message, code='science_snapshot_invalid')

        def values(obj):
            ref = table_ref(obj)
            if ref is None:
                fail('玄机阁状态表未加载')
            t = c.reader.table(ref.address)
            return {**{i: v for i, v in enumerate(t['array']) if v is not None}, **t['fields']}

        def field(obj, key):
            ref = table_ref(obj)
            if ref is None:
                fail(f'玄机阁对象未加载：{key}')
            return read_ui_object_field(c, ref.address, key)

        loaded = field(c.field(c.binding.environment_address, 'package'), 'loaded')
        data = field(field(field(field(loaded, 'GameSystem.Game.Building.Mgr.BuildingMgr'), 'inst'), 'Model'), 'BuildingData')
        vos = values(field(data, 'BuildScienceInfoVoTb'))
        maxima = values(field(data, 'MaxScienceLvCfgTb'))
        idx = values(field(field(c.field(c.binding.environment_address, 's_globalCfgIdx'), 'XianFu'), 'XianFuScienceLvResource'))
        indexes = {k: as_int(v) for k, v in idx.items()}
        if not indexes or any(i is None or i < 1 for i in indexes.values()):
            fail('玄机阁等级配置索引不完整')
        defaults = read_runtime_config_defaults(c.reader, maxima, indexes)

        def config(obj):
            raw = values(obj)
            return {k: raw.get(i, defaults.get(k)) for k, i in indexes.items()}

        # A detail observation needs only its node and named predecessors.
        # Reading all 68 live VOs before checking a single upgrade needlessly
        # widens the non-atomic observation window across client GC/UI updates.
        levels = {}

        def level(ident):
            if ident not in vos:
                fail('玄机阁前置节点不在完整树中')
            if ident not in levels:
                levels[ident] = as_int(field(vos[ident], 'ScienceLevel'))
            if levels[ident] is None or levels[ident] < 0:
                fail('玄机阁当前等级不完整')
            return levels[ident]

        def eligibility(current, result):
            if (as_int(current['scienceId']), as_int(current['level'])) != (result['id'], result['level']):
                fail('玄机阁消耗配置尚未刷新')
            result.update(costs={}, unlocked=True)
            condition = current['condition']
            if not isinstance(condition, str):
                fail('玄机阁前置条件缺失')
            for part in condition.split(',') if condition else []:
                match = re.fullmatch(r'XianFuScience\|(\d+)_(\d+)', part)
                if not match or int(match[1]) not in vos:
                    fail(f'玄机阁前置条件尚不支持：{part}')
                result['unlocked'] &= level(int(match[1])) >= int(match[2])
            for part in values(current['consume']).values():
                match = re.fullmatch(r'Item\|(\d+)_(\d+)', str(part))
                if not match or int(match[1]) <= 0 or int(match[2]) <= 0:
                    fail(f'玄机阁消耗尚不支持：{part}')
                item, amount = int(match[1]), int(match[2])
                result['costs'][item] = result['costs'].get(item, 0)+amount
            if not result['costs']:
                fail('玄机阁消耗不完整')
            return result

        def node(vo, *, with_eligibility=True):
            ident = as_int(field(vo, 'id'))
            if ident not in vos or table_ref(vos[ident]) != table_ref(vo) or ident not in maxima:
                fail('玄机阁节点身份与完整树不符')
            maximum = config(maxima[ident])
            mx = as_int(maximum['level'])
            if as_int(maximum['scienceId']) != ident or mx is None or mx <= 0 or not 0 <= level(ident) <= mx:
                fail('玄机阁节点等级越界')
            pos = values(field(vo, 'posInfo'))
            result = {'id': ident, 'x': float(pos['posX']), 'y': -float(pos['posY']),
                      'level': levels[ident], 'max_level': mx, 'costs': {}, 'unlocked': True}
            if with_eligibility and result['level'] < mx:
                # CheckShowRed uses this exact current-level row. Reuse the VO
                # cache only if its identity/level match; otherwise decode the
                # already-loaded native list without invoking its Lua getter.
                cached = field(vo, 'curLevelCfg')
                current = config(cached) if table_ref(cached) else {}
                if (as_int(current.get('scienceId')), as_int(current.get('level'))) != (ident, result['level']):
                    groups = values(field(data, 'XianFuScienceLvResourceTb'))
                    rows, count = c.reader.list_items(groups.get(ident))
                    if len(rows) != count:
                        fail('玄机阁当前等级配置列表不完整')
                    matches = [cfg for cfg in map(config, rows)
                               if as_int(cfg.get('scienceId')) == ident and as_int(cfg.get('level')) == result['level']]
                    if len(matches) != 1:
                        fail('玄机阁当前等级配置不唯一')
                    current = matches[0]
                eligibility(current, result)
            return result

        if detail:
            panels = read_active_ui_window_panels(c, 'BuildScienceTipsView')
            if not panels:
                raise FanxiuRuntimeMemoryError('玄机阁详情正在刷新或已关闭', code='ui_snapshot_pending')
            if len(panels) != 1:
                fail(f'玄机阁详情不唯一：{len(panels)}')
            panel = panels[0]
            if not has_ui_object_fields(c, panel.address, ('scienceInfoVo', 'SkillLevelTF', 'ExtraUseBtnTxt', 'curLevel')):
                fail('玄机阁详情字段未就绪')
            if field(panel, 'isChoose') not in (None, False):
                fail('当前为技能选择页，不能升级')
            vo = field(panel, 'scienceInfoVo')
            result = node(vo, with_eligibility=False)
            if (as_int(field(panel, 'curLevel')), as_int(field(panel, 'maxLevel'))) != (result['level'], result['max_level']):
                raise FanxiuRuntimeMemoryError('玄机阁详情尚未刷新', code='ui_snapshot_pending')
            result.update(costs={}, unlocked=True, can_activate=field(panel, '_CanActive'))
            if result['level'] == result['max_level']:
                return result
            current = config(field(panel, 'curLevelCfg'))
            if (as_int(current['scienceId']), as_int(current['level'])) != (result['id'], result['level']):
                raise FanxiuRuntimeMemoryError('玄机阁详情消耗尚未刷新', code='ui_snapshot_pending')
            eligibility(current, result)
            if not result['costs'] or not isinstance(result['can_activate'], bool):
                fail('玄机阁消耗或升级状态不完整')
            return result

        hosts = read_active_ui_window_panels(c, 'BuildScienceMainView')
        if len(hosts) != 1:
            fail(f'玄机阁树面板不唯一：{len(hosts)}')
        if not has_ui_object_fields(c, hosts[0].address, ('CultivationTypeCfgList', 'PanelContent', 'tabPanelGroup')):
            fail('玄机阁树字段未就绪')
        selected = read_ui_selected_tab_panel(c, hosts[0].address)
        if selected is None:
            fail('玄机阁页签未加载')
        panel = LuaRef('table', selected[0])
        selected_data = field(panel, 'selectData')
        if field(selected_data, 'luaPath') != 'GameSystem.Game.Building.Model.Science.BuildSciencePanel':
            fail('当前选中页签不是玄机阁树')
        items, count = c.reader.list_items(field(panel, 'nodeItemList'))
        nodes = [node(field(item, '_NodeData')) for item in items]
        if count != len(nodes) or len(nodes) != len(vos) or {n['id'] for n in nodes} != set(vos):
            fail('玄机阁 UI 节点与完整树不一致')
        return {'nodes': nodes, 'complete': True}

    return read_ui_runtime_snapshot(('s_globalCfgIdx',), read)
