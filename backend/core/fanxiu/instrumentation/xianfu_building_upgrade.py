"""Read the active building upgrade tab; never load configs or invoke Lua."""
from __future__ import annotations

import re
from datetime import datetime

from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .resource_auto_use import read_runtime_config_rows
from .item_config import read_loaded_item_text
from .ui_runtime_context import (
    read_active_ui_window_panels, read_ui_object_field,
    read_ui_runtime_snapshot, read_ui_selected_tab_panel,
)


def read_xianfu_building_upgrade_panel():
    """Project the game's visible group/column order and selected upgrade.

    BuildingAllUpView owns showList and canActive. Its current-level cost row
    and BuildingData.buildMaxLevel are independently bound to the selected ID.
    Unsupported or incomplete costs fail closed before any consumer can click.
    """
    def read(c):
        def fail(message):
            raise FanxiuRuntimeMemoryError(message, code='building_upgrade_incomplete')

        def field(obj, name):
            ref = table_ref(obj)
            if ref is None:
                fail(f'仙府建筑对象未加载：{name}')
            return read_ui_object_field(c, ref.address, name)

        def sequence(obj, name):
            ref = table_ref(obj)
            if ref is None:
                fail(f'仙府建筑列表未加载：{name}')
            values, count = c.reader.list_items(ref)
            if count is None or count != len(values):
                fail(f'仙府建筑列表不完整：{name}')
            return values

        hosts = read_active_ui_window_panels(c, 'BuildingHelperMainView')
        if len(hosts) != 1:
            fail('仙府建筑升级窗口未打开或不唯一')
        selected_tab = read_ui_selected_tab_panel(c, hosts[0].address)
        if selected_tab is None or selected_tab[1] != 0:
            fail('仙府建筑当前不是等级升级页签')
        panel = selected_tab[0]
        groups = sequence(read_ui_object_field(c, panel, 'showList'), 'showList')
        ordered = [cfg for group in groups for cfg in sequence(group, 'showList.group')]
        rows = read_runtime_config_rows(c.reader, ordered,
            environment_address=c.binding.environment_address,
            group_name='XianFu', table_name='XianFuBuild', fields=('id', 'name'))
        name_ids = {as_int(row['name']) for row in rows if as_int(row['name']) is not None}
        names = read_loaded_item_text(name_ids, reader=c.reader,
            state_address=c.binding.state_address,
            environment_address=c.binding.environment_address)['texts_by_id'] if name_ids else {}
        for row in rows:
            if as_int(row['name']) is not None:
                row['name'] = names.get(as_int(row['name']))
        ids = [as_int(row['id']) for row in rows]
        if not ids or any(ident is None or ident <= 0 for ident in ids) or len(set(ids)) != len(ids):
            fail('仙府建筑列表身份缺失或重复')
        if any(not isinstance(row['name'], str) or not row['name'].strip() for row in rows):
            fail(f'仙府建筑名称投影未完成：{rows!r}')
        selected = read_ui_object_field(c, panel, 'buildCfg')
        identity = read_runtime_config_rows(c.reader, [selected],
            environment_address=c.binding.environment_address,
            group_name='XianFu', table_name='XianFuBuild', fields=('id', 'name'))[0]
        if as_int(identity['name']) is not None:
            identity['name'] = names.get(as_int(identity['name']))
        ident = as_int(identity['id'])
        if ident not in ids or identity['name'] != rows[ids.index(ident)]['name']:
            fail('仙府建筑当前选择不属于完整列表')
        current = read_runtime_config_rows(c.reader,
            [read_ui_object_field(c, panel, 'buildLevelCfg')],
            environment_address=c.binding.environment_address,
            group_name='XianFu', table_name='XianFuBase', fields=('type', 'level', 'cost'))[0]
        level = as_int(current['level'])
        if as_int(current['type']) != ident or level is None or level < 0:
            fail('仙府建筑当前消耗行与选择身份不符')
        loaded = field(c.field(c.binding.environment_address, 'package'), 'loaded')
        data = field(field(field(field(loaded, 'GameSystem.Game.Building.Mgr.BuildingMgr'), 'inst'), 'Model'), 'BuildingData')
        maxima = table_ref(field(data, 'buildMaxLevel'))
        if maxima is None:
            fail('仙府建筑等级上限未加载')
        maximum = as_int(c.reader.numeric_fields(maxima.address, frozenset({ident})).get(ident))
        if maximum is None or maximum < level:
            fail('仙府建筑等级上限不完整')
        can_active = read_ui_object_field(c, panel, 'canActive')
        if level < maximum and type(can_active) is not bool:
            fail('仙府建筑可升级条件不完整')
        costs = {}
        # SetUpMsg hides the upgrade control when prerequisites are unmet;
        # those rows need no payment projection because they cannot be clicked.
        if level < maximum and can_active is True:
            raw = current['cost']
            ref = table_ref(raw)
            if ref is None:
                fail(f'仙府建筑费用投影未完成：identity={identity}, current={current}, canActive={can_active}')
            t = c.reader.table(ref.address)
            entries = [value for value in t['array'] if value is not None]
            if not entries or t['fields']:
                fail('仙府建筑费用数组不完整')
            for value in entries:
                match = re.fullmatch(r'Item\|([1-9]\d*)_([1-9]\d*)', value) if isinstance(value, str) else None
                if match is None:
                    fail(f'仙府建筑存在未支持费用：{value}')
                item, amount = map(int, match.groups())
                if item in costs:
                    fail('仙府建筑费用存在重复材料')
                costs[item] = amount
        return {'complete': True, 'buildings': rows,
                'selected': {'id': ident, 'name': identity['name'], 'level': level,
                             'maximum': maximum, 'can_upgrade': level < maximum and can_active is True,
                             'costs': costs},
                'captured_at': datetime.now().astimezone().isoformat()}

    return read_ui_runtime_snapshot(('s_globalCfgIdx',), read)
