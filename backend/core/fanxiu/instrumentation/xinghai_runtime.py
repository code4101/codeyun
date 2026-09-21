"""星海已加载界面的只读事实：提纯、汇聚、四域红点与中央页签。

调用方先进入对应界面；公共 UI provider 负责进程根绑定和恢复。子对象地址与
业务值不跨快照缓存，面板缺失、歧义或域身份冲突均报错，不调用游戏 Lua。
"""
from __future__ import annotations

from typing import Any, Literal

from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import (
    active_ui_component_objects, has_ui_object_fields, read_ui_object_field,
    read_ui_runtime_snapshot, read_ui_selected_tab_panel,
)

_XINGHAI_DOMAIN_NAMES = {2: '淬灵域', 3: '淬锋域', 4: '幻灵域', 5: '轮回域'}
_XINGHAI_CENTRAL_TAB_SCHEMAS = (
    ('level', ('levelUpBtn', 'costTxt', 'LevelTxt', 'TreeBtn')),
    ('wake', ('WakeBtn', 'CostTxt', 'DescTxt', 'TipTxt')),
    ('stage', ('levelUpBtn', 'costTxt', 'StageTxt', 'UpStagePanel')),
)


def read_xinghai_domain_identity() -> dict[str, Any]:
    """Read the active domain display panel as a read-only landing proof.

    The active UI registry plus its unique DisplayView schema is the ready
    evidence. No stale selected ID from a global manager is accepted.
    """
    def read(ctx):
        panels = [r for r in active_ui_component_objects(ctx)
                  if has_ui_object_fields(ctx, r.address,
                      ('BlueStarSeaDisplayItem', 'CenterBtn', 'UpgradeBtn', '_FaqiId'))]
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError(f'星海域内展示页不唯一：{len(panels)}')
        fid = as_int(read_ui_object_field(ctx, panels[0].address, '_FaqiId'))
        if fid not in _XINGHAI_DOMAIN_NAMES:
            raise FanxiuRuntimeMemoryError(f'星海域内身份无效：{fid}')
        return {'id': fid, 'name': _XINGHAI_DOMAIN_NAMES[fid], 'page': 'domain_display', 'read_only': True}
    return read_ui_runtime_snapshot((), read)


def _read_xinghai_selected_panel(ctx) -> tuple[int, int, int]:
    """Resolve the unique four-domain central host and its selected tab panel.

    File-private provider shared by the level and central-tab readers. Identity
    starts from the unique active host schema
    (``_FaqiId``/``tabPanelGroup``/``_CurTabList``/``_LuaPathDic``), the host id
    must be one of the four domains, and the host's currently selected tab must
    resolve through ``read_ui_selected_tab_panel``. Returns
    ``(fid, panel_address, tab_index)``; a missing/ambiguous host or an
    unselected tab fails closed. The returned addresses are temporary and valid
    only for the current snapshot.
    """
    hosts = [r for r in active_ui_component_objects(ctx)
             if has_ui_object_fields(ctx, r.address,
                 ('_FaqiId', 'tabPanelGroup', '_CurTabList', '_LuaPathDic'))]
    if len(hosts) != 1:
        raise FanxiuRuntimeMemoryError(f'星海四域升级宿主不唯一：{len(hosts)}')
    fid = as_int(read_ui_object_field(ctx, hosts[0].address, '_FaqiId'))
    if fid not in _XINGHAI_DOMAIN_NAMES:
        raise FanxiuRuntimeMemoryError(f'星海升级宿主域身份无效：{fid}')
    selected = read_ui_selected_tab_panel(ctx, hosts[0].address)
    if selected is None:
        raise FanxiuRuntimeMemoryError('星海升级页当前页签未选中')
    panel, tab_index = selected
    return fid, panel, tab_index


def read_xinghai_upgrade_state() -> dict[str, Any]:
    """Read the unique active four-domain central upgrade page as a read-only fact.

    The level view is a tab of a host component, not a direct registry child;
    ``_read_xinghai_selected_panel`` resolves the unique host and its selected
    tab. The selected panel must carry the level-view schema
    (levelUpBtn/costTxt/LevelTxt/TreeBtn), and the host ``_FaqiId``, the panel
    ``V_FaqiId`` and ``V_RitualImplementVO.faqiId`` must all agree.
    Missing/ambiguous hosts, a non-selected upgrade tab, or non-bool level flags
    fail closed; no global selected id or stale value is trusted. This reads no
    cost config and does not depend on backpack/wallet; the GUI pairs the returned
    identity/level with real OCR.
    """
    def read(ctx):
        host_fid, panel, _tab_index = _read_xinghai_selected_panel(ctx)
        if not has_ui_object_fields(ctx, panel,
                                    ('levelUpBtn', 'costTxt', 'LevelTxt', 'TreeBtn')):
            raise FanxiuRuntimeMemoryError('星海当前页签不是四域升级页')
        fid = as_int(read_ui_object_field(ctx, panel, 'V_FaqiId'))
        if fid != host_fid:
            raise FanxiuRuntimeMemoryError(f'星海升级页域身份与宿主不一致：{fid} != {host_fid}')
        vo = table_ref(read_ui_object_field(ctx, panel, 'V_RitualImplementVO'))
        if vo is None:
            raise FanxiuRuntimeMemoryError('星海升级页法器 VO 未加载')
        vo_fid = as_int(read_ui_object_field(ctx, vo.address, 'faqiId'))
        level = as_int(read_ui_object_field(ctx, vo.address, 'level'))
        if vo_fid != fid:
            raise FanxiuRuntimeMemoryError(f'星海升级页 VO 域身份不一致：{vo_fid} != {fid}')
        if level is None or level < 0:
            raise FanxiuRuntimeMemoryError(f'星海升级页等级无效：{level}')
        is_max = read_ui_object_field(ctx, panel, 'V_IsMaxLevel')
        if type(is_max) is not bool:
            raise FanxiuRuntimeMemoryError('星海升级页 V_IsMaxLevel 必须为布尔')
        if is_max:
            # 满级时 V_IsEnough 可能残留旧值，不信任它。
            can_upgrade = False
        else:
            is_enough = read_ui_object_field(ctx, panel, 'V_IsEnough')
            if type(is_enough) is not bool:
                raise FanxiuRuntimeMemoryError('星海升级页 V_IsEnough 必须为布尔')
            can_upgrade = is_enough
        return {'page': 'domain_upgrade', 'id': int(fid), 'name': _XINGHAI_DOMAIN_NAMES[fid],
                'level': int(level), 'is_max_level': is_max,
                'can_upgrade': can_upgrade, 'read_only': True}
    return read_ui_runtime_snapshot((), read)


def read_xinghai_central_tab() -> dict[str, Any]:
    """Read which central tab (level/wake/stage) the four-domain page shows.

    Identifies the currently selected tab by schema, not by tab index: the level,
    wake and stage panes are told apart by their unique field sets, so the caller
    can re-enter the default landing tab when upgrade materials run short rather
    than assuming ``tab_index`` equals a semantic. ``_read_xinghai_selected_panel``
    supplies the unique host/selected panel, and the pane
    ``V_RitualImplementVO.faqiId`` must equal the host ``_FaqiId``. Exactly one of
    the three known schemas must match; an unknown or multiply-matching tab fails
    closed. No Lua is executed and no address is cached. Wake and level have
    live acceptance; stage remains schema-only and is not an enabled GUI path.
    """
    def read(ctx):
        fid, panel, tab_index = _read_xinghai_selected_panel(ctx)
        vo = table_ref(read_ui_object_field(ctx, panel, 'V_RitualImplementVO'))
        if vo is None:
            raise FanxiuRuntimeMemoryError('星海中央页法器 VO 未加载')
        vo_fid = as_int(read_ui_object_field(ctx, vo.address, 'faqiId'))
        if vo_fid != fid:
            raise FanxiuRuntimeMemoryError(f'星海中央页 VO 域身份不一致：{vo_fid} != {fid}')
        matches = [tab for tab, schema in _XINGHAI_CENTRAL_TAB_SCHEMAS
                   if has_ui_object_fields(ctx, panel, schema)]
        if len(matches) != 1:
            raise FanxiuRuntimeMemoryError(f'星海中央页签身份不唯一或未知：{matches}')
        return {'id': fid, 'name': _XINGHAI_DOMAIN_NAMES[fid], 'page': 'domain_central',
                'tab': matches[0], 'tab_index': tab_index, 'read_only': True}
    return read_ui_runtime_snapshot((), read)


def read_xinghai_entry_states() -> dict[str, Any]:
    """Read the four entries while #259 is open, without executing Lua.

    Current BlueStarSeaPartItem.UpdateTabRedDot uses per-faqi checks, NOT the
    legacy BlueStarSea_PartItem_2 aggregate. Reconstruct its decision from the
    loaded configs, display-result cache and fresh item-resource counts (backpack
    ItemVoDic first, wallet currency fallback). Missing
    evidence yields red_dot=None, never permission to pass. No child address
    or business value is cached across calls. Reopening after a process restart,
    upgrade-to-tree transitions and all-four-negative completion have live evidence.
    """
    import re
    import time
    from datetime import datetime
    from .item_resources import read_item_available_counts

    names = _XINGHAI_DOMAIN_NAMES

    def read(ctx):
        def field(obj, key):
            ref = table_ref(obj)
            if ref is None:
                raise FanxiuRuntimeMemoryError(f'星海对象未加载：{key}')
            return read_ui_object_field(ctx, ref.address, key)

        def values(obj):
            ref = table_ref(obj)
            if ref is None:
                raise FanxiuRuntimeMemoryError('星海配置/状态表未加载')
            table = ctx.reader.table(ref.address)
            return {**{i: v for i, v in enumerate(table['array']) if v is not None},
                    **table['fields']}

        panels = [r for r in active_ui_component_objects(ctx)
                  if has_ui_object_fields(ctx, r.address, ('_PartItemList', '_OrbitCfgList'))]
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError(f'星海主界面不唯一：{len(panels)}')
        loaded = field(ctx.field(ctx.binding.environment_address, 'package'), 'loaded')
        indices = field(ctx.field(ctx.binding.environment_address, 's_globalCfgIdx'), 'BlueStarSea')
        model = field(field(field(loaded, 'GameSystem.Game.BlueStarSea.Mgr.BlueStarSeaMgr'), 'inst'), 'Model')
        data = field(model, 'BlueStarSeaData')

        def config(obj, category):
            raw = values(obj)
            return {k: raw.get(as_int(i)) for k, i in values(field(indices, category)).items()}

        entries, count = ctx.reader.list_items(field(panels[0], '_PartItemList'))
        bases = [config(field(item, 'V_Data'), 'Base') for item in entries]
        if count != 4 or {row.get('id') for row in bases} != set(names):
            raise FanxiuRuntimeMemoryError('星海四域入口身份或数量不完整')
        faqis = ctx.reader.list_items(field(field(field(data, '_SyncInfo'), 'vo'), 'faqiList'))[0]
        faqis = {as_int(field(vo, 'faqiId')): vo for vo in faqis}
        display_cache = values(field(model, '_displayRedDotCache'))
        group_maps = values(field(data, 'V_StarTreeGroupMapCache'))
        open_configs = values(field(loaded, 'Generate.Cfg.BlueStarSea.ConfigValue'))
        activated = values(field(data, '_ActivatedTalentTreeIds'))
        tree_groups = values(field(data, 'V_TreeCfgByGroup'))
        maps = {category: values(field(data, map_name)) for category, map_name in (
            ('Level', 'V_RitualImplementLevelMap'), ('Star', 'V_RitualImplementStageMap'),
            ('Wake', 'V_RitualImplementWakeMap'))}

        def proven_open(condition, fid, fusion):
            # Both predicates true prove this client's compound gate regardless
            # of its grouping. Unsupported/unsatisfied gates remain unknown.
            if not condition:
                return True
            parts = str(condition).split(';')
            for part in parts:
                if part == f'FAQITUIMERGE|{fid},CL|25' and fusion is True:
                    continue
                match = re.fullmatch(r'DateAfter\|(\d{8})(?:,CL\|25)?', part)
                if match and datetime.now().strftime('%Y%m%d') > match.group(1):
                    continue
                return False
            return True

        result = []
        for base in bases:
            fid = int(base['id'])
            vo = faqis.get(fid)
            if vo is None:
                raise FanxiuRuntimeMemoryError(f'星海法器 {fid} 未同步')
            groups = values(group_maps.get(fid))
            cached = [display_cache.get(f'{fid}_{int(group)}') for group in groups]
            claim = True if any(v is True for v in cached) else (
                False if cached and all(v is False for v in cached) else None)
            fusion = field(vo, 'fusionUpgraded')
            condition = config(open_configs.get(f'OPENCONDITION{fid}'), 'ConfigValue').get('value')
            gate = condition is not None and proven_open(condition, fid, fusion)
            row = {'id': fid, 'name': names[fid], 'claim': claim,
                   'gate_proven': gate, 'checks': {}, 'tree_candidates': []}
            for category, attr in (('Level', 'level'), ('Star', 'star'), ('Wake', 'wake')):
                current = as_int(field(vo, attr))
                if current is None:
                    raise FanxiuRuntimeMemoryError(f'星海 {fid} 缺少 {attr}')
                row[attr] = current
                source = maps[category].get(fid)
                if table_ref(source) is None:
                    raise FanxiuRuntimeMemoryError(f'星海 {fid} 的 {category} 配置未加载')
                configs = [config(v, category) for v in ctx.reader.list_items(source)[0]]
                selected = [v for v in configs if v.get('Wake' if category == 'Wake' else attr) == current + 1]
                if len(selected) > 1:
                    raise FanxiuRuntimeMemoryError('星海下一阶配置存在歧义')
                row['checks'][category] = selected[0].get('cost') if selected else None
            row['tree_gate_proven'] = gate and proven_open(base.get('showcondition'), fid, fusion) and proven_open(base.get('opencondition'), fid, fusion)
            for group, li in values(tree_groups.get(fid)).items():
                for obj in ctx.reader.list_items(li)[0]:
                    cfg = config(obj, 'Tree')
                    if activated.get(cfg['id']) is True:
                        continue
                    pre = [int(v) for v in str(cfg.get('preId') or '').split(',') if v and int(v) > 0]
                    if all(activated.get(v) is True for v in pre):
                        row['tree_candidates'].append(cfg.get('cost') or '')
                    break  # Client checks the first inactive node per group.
            result.append(row)
        return result

    rows = read_ui_runtime_snapshot(('s_globalCfgIdx',), read)
    costs: dict[str, list[tuple[int, int]]] = {}
    for row in rows:
        for value in [*row['checks'].values(), *row['tree_candidates']]:
            if value is None:
                continue
            parsed = []
            for part in str(value).split(',') if value else []:
                match = re.fullmatch(r'(?i:item)\|(\d+)_(\d+)', part)
                if not match:
                    raise FanxiuRuntimeMemoryError(f'不支持的星海红点消耗：{value}')
                parsed.append((int(match.group(1)), int(match.group(2))))
            costs[value] = parsed
    counts, evidence = read_item_available_counts(
        {item for pairs in costs.values() for item, _ in pairs}, manager_key='xinghai-entry-red-dots')

    def enough(value):
        return value is not None and all(counts[item] >= need for item, need in costs[value])

    for row in rows:
        affordable = {k: enough(v) for k, v in row.pop('checks').items()}
        tree = any(enough(v) for v in row.pop('tree_candidates'))
        gate, tree_gate = row.pop('gate_proven'), row.pop('tree_gate_proven')
        reasons = (['可领取'] if row['claim'] is True else [])
        if gate:
            reasons += [label for key, label in (('Level', '可升级'), ('Star', '可进阶'), ('Wake', '可觉醒')) if affordable[key]]
        if tree_gate and tree:
            reasons.append('可激活节点')
        row['reasons'] = reasons
        row['red_dot'] = True if reasons else (False if row['claim'] is False and gate and tree_gate else None)
        row['can_pass'] = row['red_dot'] is False
    return {'entries': rows, 'complete': all(r['red_dot'] is not None for r in rows),
            'source': 'loaded_blue_star_sea_rules_and_item_resources', 'read_only': True,
            'observed_at': evidence.get('observed_at'), 'completed_at': time.time(),
            'materials': evidence.get('items')}


def read_xinghai_ui(page: Literal['purification', 'charge']) -> dict[str, Any]:
    """Return current selection/energy, with no game writes or hidden loading."""
    if page not in {'purification', 'charge'}:
        raise ValueError(page)
    markers = {'purification': ('PurifyBtn', 'sliderContent', '_breakItemId'),
               'charge': ('ChooseCountTxt', 'CostTxt', 'SureBtn')}[page]

    def read(ctx):
        candidates = [r for r in active_ui_component_objects(ctx)
                      if has_ui_object_fields(ctx, r.address, markers)]
        if len(candidates) != 1:
            raise FanxiuRuntimeMemoryError(f'星海 {page} 活动面板不唯一：{len(candidates)}')
        panel = candidates[0].address
        def number(address, name):
            value = as_int(read_ui_object_field(ctx, address, name))
            if value is None or value < 0:
                raise FanxiuRuntimeMemoryError(f'星海字段无效：{name}')
            return value
        result = {'page': page, 'selected_count': number(panel, '_chooseCount')}
        if page == 'purification':
            slider = table_ref(read_ui_object_field(ctx, panel, 'sliderContent'))
            if slider is None:
                raise FanxiuRuntimeMemoryError('星海真元组件未加载')
            result.update(selected_config_id=number(panel, '_breakItemId'),
                          max_count=number(panel, '_maxCount'),
                          energy=number(slider.address, '_CurrentVal'),
                          energy_limit=number(slider.address, '_MaxVal'))
            if result['selected_config_id']:
                def field(ref, key):
                    if ref is None:
                        raise FanxiuRuntimeMemoryError('星海原料配置尚未加载')
                    return ctx.field(ref.address, key)
                package = ctx.field(ctx.binding.environment_address, 'package')
                configs = field(field(package, 'loaded'), 'Generate.Cfg.BlueStarSea.BreakItem')
                indexes = field(field(ctx.field(ctx.binding.environment_address, 's_globalCfgIdx'), 'BlueStarSea'), 'BreakItem')
                index = as_int(ctx.reader.fields(indexes).get('energyConsume'))
                row = table_ref(ctx.reader.fields(configs).get(result['selected_config_id']))
                values = ctx.reader.table(row.address)['array'] if row else []
                cost = as_int(values[index]) if index is not None and 0 <= index < len(values) else None
                if cost is None or cost <= 0:
                    raise FanxiuRuntimeMemoryError('星海原料真元消耗无效')
                result['unit_energy_cost'] = cost
            else:
                result['unit_energy_cost'] = 0
        return result
    return read_ui_runtime_snapshot(('Generate.Cfg.BlueStarSea.BreakItem', 's_globalCfgIdx', 'BlueStarSea', 'BreakItem') if page == 'purification' else (), read)
