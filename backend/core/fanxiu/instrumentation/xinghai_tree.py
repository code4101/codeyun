"""悟道树实时事实；仅读取已加载的树/详情及配置，不调用游戏 Lua。

Only process roots are cached by the shared UI provider. Every observation
revalidates active membership, domain and group. Reopening after a game process
restart has been verified; missing membership still fails closed.
"""
from __future__ import annotations

import re
from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import active_ui_component_objects, has_ui_object_fields, read_ui_object_field, read_ui_runtime_snapshot


def read_xinghai_tree(*, detail=False):
    """Return complete graph, or the active detail and its next exact cost."""
    def read(c):
        def field(obj, key):
            ref = table_ref(obj)
            if ref is None:
                raise FanxiuRuntimeMemoryError(f'悟道树对象未加载：{key}')
            return read_ui_object_field(c, ref.address, key)

        def values(obj):
            ref = table_ref(obj)
            if ref is None:
                raise FanxiuRuntimeMemoryError('悟道树状态表未加载')
            t = c.reader.table(ref.address)
            return {**{i: v for i, v in enumerate(t['array']) if v is not None}, **t['fields']}

        markers = ('ActiveBtn', '_Group', '_FaqiId', 'LevelTxt') if detail else ('_TreeItemList', '_TreeIdToGroup', 'ScrollView')
        panels = [r for r in active_ui_component_objects(c) if has_ui_object_fields(c, r.address, markers)]
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError(f'悟道树活动面板不唯一：{len(panels)}')
        panel = panels[0]
        fid = as_int(field(panel, '_FaqiId' if detail else 'faqiId'))
        loaded = field(c.field(c.binding.environment_address, 'package'), 'loaded')
        model = field(field(field(loaded, 'GameSystem.Game.BlueStarSea.Mgr.BlueStarSeaMgr'), 'inst'), 'Model')
        data = field(model, 'BlueStarSeaData')
        activated = values(field(data, '_ActivatedTalentTreeIds'))
        index = values(field(field(c.field(c.binding.environment_address, 's_globalCfgIdx'), 'BlueStarSea'), 'Tree'))
        groups = values(values(field(data, 'V_TreeCfgByGroup')).get(fid))

        def config(obj):
            raw = values(obj)
            return {k: raw.get(as_int(i)) for k, i in index.items() if k in ('id', 'group', 'faqiId', 'level', 'point', 'preId', 'cost')}

        def node(group):
            configs, count = c.reader.list_items(groups.get(group))
            if not configs or count != len(configs):
                raise FanxiuRuntimeMemoryError('悟道树节点配置不完整')
            cfgs = [config(v) for v in configs]
            if [r['level'] for r in cfgs] != list(range(1, len(cfgs)+1)):
                raise FanxiuRuntimeMemoryError('悟道树节点等级序列异常')
            states = [activated.get(row['id']) is True for row in cfgs]
            level = next((i for i, active in enumerate(states) if not active), len(states))
            if any(states[level:]):
                raise FanxiuRuntimeMemoryError('悟道树激活序列不连续')
            nxt = cfgs[level] if level < len(cfgs) else None
            costs = {}
            if nxt:
                for part in (nxt['cost'] or '').split(','):
                    match = re.fullmatch(r'(?i:item)\|(\d+)_(\d+)', part)
                    if not match or int(match[2]) <= 0:
                        raise FanxiuRuntimeMemoryError(f'悟道树消耗格式不支持：{part}')
                    item, amount = int(match[1]), int(match[2])
                    costs[item] = costs.get(item, 0)+amount
            pre = [int(v) for v in str(nxt['preId'] or '').split(',') if v] if nxt else []
            x, y = map(float, cfgs[0]['point'].split(','))
            # 配置 point 的 y 向上为正，而公共树策略约定屏幕 y 向下为正，故取反保持上到下排序。
            return {'id': int(group), 'faqi_id': fid, 'x': x, 'y': -y,
                    'level': level, 'max_level': len(cfgs), 'next_id': nxt['id'] if nxt else None,
                    'unlocked': all(activated.get(i) is True for i in pre), 'costs': costs}

        if detail:
            result = node(as_int(field(panel, '_Group')))
            nxt = field(panel, '_NextCfg')
            if (config(nxt)['id'] if nxt else None) != result['next_id']:
                raise FanxiuRuntimeMemoryError('悟道树详情尚未刷新')
            return result
        items = values(field(panel, '_TreeItemList'))
        if set(items) != set(groups):
            raise FanxiuRuntimeMemoryError('悟道树 UI 节点与完整配置不一致')
        return {'faqi_id': fid, 'nodes': [node(group) for group in groups], 'complete': True}
    return read_ui_runtime_snapshot(('s_globalCfgIdx',), read)
