"""角色天赋的只读投影。只读取已加载窗口与 TalentMgr，不调用游戏方法。

节点次序来自 TalentCell.id；三列格子包含连线和空位，不能按技能编号猜位置。
每次观察重新读取等级，配置默认值按客户端 packed 配置继承规则解析。
窗口缺失、未知条件或不完整配置均失败关闭，不等同于满级。
"""
from __future__ import annotations

import re

from .resource_auto_use import read_runtime_config_defaults
from .item_config import read_loaded_item_text
from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import (
    read_active_ui_window_panels, read_ui_object_field,
    read_ui_runtime_snapshot, read_ui_selected_tab_panel,
)


def talent_condition_met(condition, levels):
    """Client condition groups: semicolon OR, comma AND, Talent level >= N."""
    if not condition:
        return True
    groups = []
    for group in condition.split(';'):
        terms = []
        for term in group.split(','):
            match = re.fullmatch(r'Talent\|(\d+)_(\d+)', term)
            if not match:
                raise FanxiuRuntimeMemoryError(f'未知天赋条件：{term}', code='talent_condition_unknown')
            terms.append(levels.get(int(match[1]), 0) >= int(match[2]))
        groups.append(all(terms))
    return any(groups)


def read_role_talent_tree(*, all_tabs=False):
    """Return displayed tabs and current/full trees with native levels and costs.

    Only visible V_TabList entries are eligible; future entries retain their
    native type IDs and menu indexes. No cached pointers escape this snapshot.
    """
    def read(c):
        def fail(msg):
            raise FanxiuRuntimeMemoryError(msg, code='role_talent_invalid')

        def field(obj, key):
            ref = table_ref(obj)
            if ref is None:
                fail(f'天赋对象未加载：{key}')
            return read_ui_object_field(c, ref.address, key)

        def values(obj):
            ref = table_ref(obj)
            if ref is None:
                fail('天赋表未加载')
            raw = c.reader.table(ref.address)
            return {**{i: v for i, v in enumerate(raw['array']) if v is not None}, **raw['fields']}

        def sequence(obj):
            ref = table_ref(obj)
            if ref is None:
                fail('天赋列表未加载')
            items, count = c.reader.indexed_list_items(ref)
            if count is not None:
                if count != len(items):
                    fail('天赋列表不完整')
                return items
            return sorted((k, v) for k, v in values(obj).items() if isinstance(k, int) and k >= 1)

        cfg = field(c.field(c.binding.environment_address, 's_globalCfgIdx'), 'Talent')

        def rows(obj, kind):
            indexes = {k: as_int(v) for k, v in values(field(cfg, kind)).items() if isinstance(k, str)}
            items = sequence(obj)
            # Cell columns used here are explicit; its null table has no text
            # discriminator and must not be interpreted as a level-row table.
            defaults = {} if kind == 'TalentCell' else read_runtime_config_defaults(c.reader, dict(items), indexes)
            result = []
            for _, item in items:
                raw = values(item)
                result.append({k: raw.get(i, raw.get(k, defaults.get(k))) for k, i in indexes.items()})
            return result

        hosts = read_active_ui_window_panels(c, 'RoleTalentMainView')
        if len(hosts) != 1:
            fail('天赋主页未就绪')
        selected = read_ui_selected_tab_panel(c, hosts[0].address)
        if selected is None:
            fail('天赋页签未就绪')
        panel, tab_index = selected
        tabs = rows(field(hosts[0], 'V_TabList'), 'TalentType')
        text_ids = {int(t['name']) for t in tabs if as_int(t['name']) is not None}
        texts = read_loaded_item_text(text_ids, reader=c.reader,
                                      state_address=c.binding.state_address,
                                      environment_address=c.binding.environment_address)['texts_by_id']
        for tab in tabs:
            tab['name'] = texts.get(as_int(tab['name']), tab['name'])
            if not isinstance(tab['name'], str) or not tab['name']:
                fail('天赋页签名称未加载')
        selected_type = as_int(read_ui_object_field(c, panel, 'curSelectIndex'))
        if selected_type is None or not 0 <= tab_index < len(tabs) or tabs[tab_index]['id'] != selected_type + 1:
            fail('天赋页签身份尚未刷新')
        manager = field(c.field(c.binding.environment_address, 'TalentMgr'), 'inst')
        data = field(field(manager, 'Model'), 'data')
        levels = {int(k): as_int(v) for k, v in values(field(manager, 'roleTalentLevel')).items()
                  if isinstance(k, int)}
        if any(v is None or v < 0 for v in levels.values()):
            fail('天赋等级无效')
        cells_by_type = values(field(data, 'talentCellCfg'))
        groups = values(field(data, 'talentLevelNumCfg'))
        results = []
        for index, tab in enumerate(tabs):
            if not all_tabs and index != tab_index:
                continue
            cells = sorted(rows(cells_by_type.get(tab['id']), 'TalentCell'), key=lambda row: row['id'])
            if not cells or len({r['id'] for r in cells}) != len(cells):
                fail('天赋格子为空或重复')
            nodes = []
            for cell_index, cell in enumerate(cells):
                if cell['type'] != tab['id']:
                    fail('天赋格子类型不符')
                if cell['subType'] != 1:
                    continue
                ids = [as_int(v) for _, v in sequence(cell['talentId'])]
                if not ids or any(v is None or v <= 0 for v in ids) or len(set(ids)) != len(ids):
                    fail('天赋分支身份无效')
                variants = []
                for ident in ids:
                    configs = rows(groups.get(ident), 'TalentLevel')
                    configs.sort(key=lambda row: row['level'])
                    if not configs or [r['level'] for r in configs] != list(range(1, len(configs)+1)) or any(r['talentId'] != ident for r in configs):
                        fail('天赋等级配置不完整')
                    level = levels.get(ident, 0)
                    if level > len(configs):
                        fail('天赋等级超出上限')
                    nxt = configs[level] if level < len(configs) else None
                    costs = {}
                    if nxt:
                        for _, consume in sequence(nxt['consume']):
                            match = re.fullmatch(r'Item\|(\d+)_(\d+)', str(consume))
                            if not match or int(match[2]) <= 0:
                                fail(f'未知天赋消耗：{consume}')
                            costs[int(match[1])] = costs.get(int(match[1]), 0)+int(match[2])
                    condition = nxt['condition'] if nxt else ''
                    if not isinstance(condition, str):
                        fail('天赋条件未加载')
                    variants.append(dict(talent_id=ident, current_level=level, max_level=len(configs),
                                         costs=costs, condition=condition, active=level > 0,
                                         unlocked=talent_condition_met(condition, levels)))
                active = [v for v in variants if v['active']]
                if len(active) > 1:
                    fail('同一天赋节点出现多个已选分支')
                nodes.append(dict(id=cell['id'], index=cell_index+1, ordinal=len(nodes),
                                  variants=variants, requires_choice=len(ids)>1 and not active))
            results.append(dict(type=tab['id'], name=tab['name'], tab_index=index,
                                nodes=nodes, complete=True, tab_unlocked=True))
        menu = [dict(type=t['id'], name=t['name'], tab_index=i) for i, t in enumerate(tabs)]
        return dict(complete=True, selected_type=selected_type+1, menu=menu, tabs=results)

    return read_ui_runtime_snapshot(('s_globalCfgIdx', 'TalentMgr'), read)


def read_role_talent_popup(*, choice=False):
    """Read the open popup identity and action readiness; never opens a popup."""
    def read(c):
        hosts = read_active_ui_window_panels(c, 'RoleTalentChooseView' if choice else 'RoleTalentTipsView')
        if len(hosts) != 1:
            raise FanxiuRuntimeMemoryError('天赋弹窗未就绪', code='ui_snapshot_pending')
        def field(key):
            return read_ui_object_field(c, hosts[0].address, key)
        result = dict(id=as_int(field('talentID')), can_activate=field('_CanActive'))
        if choice:
            ref = table_ref(field('talentIDs'))
            if ref is None:
                raise FanxiuRuntimeMemoryError('天赋候选未加载', code='ui_snapshot_pending')
            raw = c.reader.table(ref.address)
            entries = {**{i:v for i,v in enumerate(raw['array']) if v is not None}, **raw['fields']}
            result.update(candidates=[as_int(v) for k,v in sorted(entries.items()) if isinstance(k,int) and k>=1],
                          selected_index=as_int(field('selectIndex')))
        else:
            result.update(level=as_int(field('curTalentLevel')), is_max=field('isMaxLevel'),
                          can_click=field('_CanClick'))
        return result
    return read_ui_runtime_snapshot((), read)
