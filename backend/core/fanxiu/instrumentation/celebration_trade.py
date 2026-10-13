"""Read the naturally active 商盟争霸 main panel without trading or Lua calls."""
from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import (
    active_ui_component_objects, has_ui_object_fields, read_ui_object_field,
    read_ui_selected_tab_panel, read_ui_runtime_snapshot,
)


def read_celebration_trade_main_panel(*, expected_activity_id: int,
                                    expected_runtime_id: int | None = None) -> dict:
    """Observe CTMainPanel identity and its server-derived entry availability.

    IsOpen is populated by UpdatePanel from mPanelList.canJoin > 0. It only
    means the main map entry is available; it grants no trading, automatic
    play, purchases, attacks or guild authority. A closed/unloaded panel fails
    rather than initializing the manager or issuing a game request.

    resources.complete separately describes the naturally loaded wallet and
    license projection. Recovery may still be unknown when its cached getter
    has never run; neither completeness flag proves cross-period retention.
    """
    if type(expected_activity_id) is not int or expected_activity_id <= 0:
        raise ValueError('商盟页面取证需要正整数活动ID')
    if expected_runtime_id is not None and (type(expected_runtime_id) is not int or expected_runtime_id <= 0):
        raise ValueError('商盟周期取证需要正整数Runtime ID')
    keys = {'planeBtn', 'courseBtn', 'commandBtn', 'qualifyBtn', 'OpenNode',
            'ReadyNode', 'costTip', '_MapAreaList', 'V_ActivityId',
            'V_MainActivityVO', 'IsOpen', 'state', 'id',
            'CelebrationTradeMgr', 'inst', 'Model', 'Data', '_coinIdList',
            '_CTMoneyId', '_CTLicenseCoinId', '_CTMaxLicense',
            '_LicenseRecoverTime', 'mBigWorldInfo', 'licenseValue',
            'coinValue', 'lastTime', 'currId'}

    def read(c):
        panels = {}
        seen = set()
        for component in active_ui_component_objects(c):
            panel = table_ref(read_ui_object_field(c, component.address, 'm_panel')) or component
            for _ in range(4):
                if panel.address in seen:
                    break
                seen.add(panel.address)
                if has_ui_object_fields(c, panel.address,
                        {'planeBtn', 'courseBtn', 'commandBtn', 'qualifyBtn', 'OpenNode', 'ReadyNode'}):
                    if as_int(read_ui_object_field(c, panel.address, 'V_ActivityId')) == expected_activity_id:
                        panels[panel.address] = panel
                child = read_ui_selected_tab_panel(c, panel.address)
                if child is None:
                    break
                from .runtime_memory import LuaRef
                panel = LuaRef('table', child[0])
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError(f'当前商盟活动面板数量为{len(panels)}',
                code='data_not_loaded' if not panels else 'runtime_incomplete')
        panel = next(iter(panels.values()))
        enabled = read_ui_object_field(c, panel.address, 'IsOpen')
        activity = table_ref(read_ui_object_field(c, panel.address, 'V_MainActivityVO'))
        state = as_int(read_ui_object_field(c, activity.address, 'state')) if activity else None
        runtime_id = c.reader.long(read_ui_object_field(c, activity.address, 'id')) if activity else None
        if type(enabled) is not bool or state is None or runtime_id is None or runtime_id <= 0:
            raise FanxiuRuntimeMemoryError('商盟参与状态尚未完整加载', code='runtime_incomplete')
        if expected_runtime_id is not None and runtime_id != expected_runtime_id:
            raise FanxiuRuntimeMemoryError('商盟主页面并非指定周期实例', code='runtime_incomplete')
        # Only naturally populated manager caches are observed. CoinTypeID's
        # ordinal is an index, never a wallet ID. A missing cache stays unknown;
        # do not call its getter (which initializes config or requests data).
        def field(obj, name):
            return read_ui_object_field(c, obj.address, name) if obj else None

        manager = table_ref(c.field(c.binding.environment_address, 'CelebrationTradeMgr'))
        instance = table_ref(field(manager, 'inst'))
        model = table_ref(field(instance, 'Model'))
        data = table_ref(field(model, 'Data'))
        coins = table_ref(field(data, '_coinIdList'))
        coin_ids = c.reader.numeric_fields(coins.address, frozenset({1, 2})) if coins else {}
        license_id = as_int(coin_ids.get(1)) or as_int(field(data, '_CTLicenseCoinId'))
        money_id = as_int(coin_ids.get(2)) or as_int(field(data, '_CTMoneyId'))
        world = table_ref(field(data, 'mBigWorldInfo'))
        count = as_int(field(world, 'licenseValue'))
        maximum = as_int(field(data, '_CTMaxLicense'))
        money = c.reader.long(field(world, 'coinValue')) if world else None
        last_time = c.reader.long(field(world, 'lastTime')) if world else None
        recover_ms = as_int(field(instance, '_LicenseRecoverTime'))
        resources_complete = (all(type(v) is int and v > 0 for v in
                                  (license_id, money_id, maximum))
                              and type(count) is int and 0 <= count <= maximum
                              and type(money) is int and money >= 0)
        resources = dict(complete=resources_complete, license_item_id=license_id,
            money_item_id=money_id, license_count=count, license_maximum=maximum,
            money_count=money, last_license_update_ms=last_time,
            license_recover_interval_ms=recover_ms,
            recovery_complete=type(recover_ms) is int and recover_ms > 0
                              and type(last_time) is int and last_time > 0,
            cross_period_retention_verified=False,
            source='CelebrationTradeMgr.inst.Model.Data naturally loaded caches')
        return dict(complete=True, read_only=True, activity_id=expected_activity_id,
            runtime_id=runtime_id,
            resources=resources,
            entry_enabled=enabled, activity_state=state, consumption_authorized=False,
            source='active CTMainPanel.V_ActivityId + V_MainActivityVO.id + IsOpen',
            evidence=dict(pid=c.binding.pid, process_start_ticks=c.binding.process_start_ticks))

    return read_ui_runtime_snapshot(keys, read, fast=True)


def read_celebration_trade_recovery_cache() -> dict:
    """Read the recovery model naturally populated by the license tooltip.

    This manager cache does not identify an activity occurrence. The caller
    must separately verify its current main page; the result authorizes no
    spending and does not establish ticket retention across periods.
    """
    from .ui_runtime_context import acquire_lua_global_runtime_context

    c = acquire_lua_global_runtime_context({'CelebrationTradeMgr', 'inst',
        'Model', 'Data', '_LicenseRecoverTime', '_CTMaxLicense', '_ctInfo',
        'licenseValue', 'lastTime', '_EnergyTipList', 'text', 'time'})
    manager = c.loaded_manager_fields('CelebrationTradeMgr',
                                     frozenset({'GetLicenseRecoverTime', 'GetEnergyTipList'}))

    def field(obj, name):
        return c.object_field(obj.address, name) if obj else None

    instance = table_ref(manager.get('inst'))
    data = table_ref(field(table_ref(field(instance, 'Model')), 'Data'))
    info = table_ref(field(data, '_ctInfo'))
    interval = as_int(field(instance, '_LicenseRecoverTime'))
    maximum = as_int(field(data, '_CTMaxLicense'))
    count = as_int(field(info, 'licenseValue'))
    last_time = c.reader.long(field(info, 'lastTime')) if info else None
    rows, declared = c.reader.list_items(field(instance, '_EnergyTipList'))
    tips = [dict(text=field(table_ref(row), 'text'),
                 remaining_ms=as_int(field(table_ref(row), 'time'))) for row in rows]
    if (not interval or interval <= 0 or not maximum or maximum <= 0
            or count is None or not 0 <= count <= maximum or not last_time
            or declared != len(rows) or not tips
            or any(not isinstance(t['text'], str) or t['remaining_ms'] is None for t in tips)):
        raise FanxiuRuntimeMemoryError('商盟文书恢复说明缓存未完整自然加载', code='data_not_loaded')
    return dict(complete=True, read_only=True, license_count=count,
        license_maximum=maximum, license_recover_interval_ms=interval,
        last_license_update_ms=last_time, tooltip_rows=tips,
        countdown_is_tooltip_open_time_snapshot=True,
        activity_scope_verified=False, cross_period_retention_verified=False,
        consumption_authorized=False, source='CelebrationTradeMgr.GetEnergyTipList cached model',
        evidence=dict(pid=c.binding.pid, process_start_ticks=c.binding.process_start_ticks))
