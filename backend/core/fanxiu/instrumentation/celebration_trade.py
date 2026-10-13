"""Read the naturally active 商盟争霸 main panel without trading or Lua calls."""
from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import (
    active_ui_component_objects, has_ui_object_fields, read_ui_object_field,
    read_ui_selected_tab_panel, read_ui_runtime_snapshot, read_active_ui_window_panels,
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


def read_celebration_trade_auto_result() -> dict:
    """Observe native server-confirmed auto counters and the finish window.

    UpdateAutoCount receives successful sale gains. Completeness means the
    model is decoded. A stopped native auto component plus exact full counters
    can reconcile a result whose finish window was already closed; a timeout
    or a partial count alone never proves completion.
    """
    keys = {'CelebrationTradeMgr', 'inst', 'Model', 'Data', '_CurAutoCount',
        '_MaxAutoCount', '_TmpUsePlanId', '_AddClubShengWang',
        '_SubOtherClubShengWang', '_AddExChangeCoin', 'mBigWorldInfo',
        'licenseValue', 'coinValue', 'mPanelList', 'sellTime', 'Window',
        'CTAutoFinishTipsView', 'uId', 'm_panel', 'V_uId', 'AutoActivityComponent', '_IsActive'}

    def read(c):
        def field(obj, name):
            return read_ui_object_field(c, obj.address, name) if obj else None
        manager = table_ref(c.field(c.binding.environment_address, 'CelebrationTradeMgr'))
        instance = table_ref(field(manager, 'inst'))
        data = table_ref(field(table_ref(field(instance, 'Model')), 'Data'))
        world = table_ref(field(data, 'mBigWorldInfo'))
        panel = table_ref(field(data, 'mPanelList'))
        values = {name: as_int(field(instance, key)) for name, key in
                  [('completed_count', '_CurAutoCount'), ('requested_count', '_MaxAutoCount'),
                   ('reputation_gain', '_AddClubShengWang'),
                   ('enemy_reputation_reduction', '_SubOtherClubShengWang'),
                   ('exchange_coin_gain', '_AddExChangeCoin')]}
        values.update(license_count=as_int(field(world, 'licenseValue')),
                      money_count=c.reader.long(field(world, 'coinValue')) if world else None,
                      total_sell_count=as_int(field(panel, 'sellTime')))
        if any(v is None or v < 0 for v in values.values()):
            raise FanxiuRuntimeMemoryError('商盟自动贸易结果未完整加载', code='runtime_incomplete')
        # The native Window registry owns current membership. Inspecting every
        # unrelated pooled panel can race its destruction during auto trade.
        finish = read_active_ui_window_panels(c, 'CTAutoFinishTipsView')
        auto_component = table_ref(field(instance, 'AutoActivityComponent'))
        # Stop() clears _IsActive, while _IsStart intentionally survives until
        # End(). HasStart therefore cannot diagnose a finished auto batch.
        auto_running = field(auto_component, '_IsActive')
        if auto_running is not None and type(auto_running) is not bool:
            raise FanxiuRuntimeMemoryError('商盟原生自动运行状态无效', code='runtime_incomplete')
        if len(finish) > 1 or any(not has_ui_object_fields(c, p.address,
                {'ConfirmBtn', 'ExchangeCoin', 'ShengWang', 'Club', 'title'}) for p in finish):
            raise FanxiuRuntimeMemoryError('商盟自动完成窗口身份未完整加载', code='runtime_incomplete')
        return dict(complete=True, read_only=True, **values,
            selected_plan_uid=c.reader.long(field(instance, '_TmpUsePlanId')),
            finish_window_open=len(finish) == 1,
            auto_running=auto_running,
            consumption_authorized=False, source='CelebrationTradeMgr.UpdateAutoCount + active finish window',
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


def read_celebration_trade_auto_admission_cache() -> dict:
    """Observe the native sell-count gate after opening automatic settings.

    OpenAutoSettingView compares mPanelList.sellTime with the lazily populated
    AUTO_TRADE_CONDITION cache. This query neither runs that getter nor starts
    automatic play. Passing this gate says nothing about a selected trade plan
    or its costs; those require separate UI and business validation.
    """
    from .ui_runtime_context import acquire_lua_global_runtime_context
    c = acquire_lua_global_runtime_context({'CelebrationTradeMgr', 'inst',
        'Model', 'Data', '_ConditionTradCount', 'mPanelList', 'sellTime'})
    manager = c.loaded_manager_fields('CelebrationTradeMgr',
                                     frozenset({'TryStartAuto', 'CanUseAuto'}))

    def field(obj, name):
        return c.object_field(obj.address, name) if obj else None

    instance = table_ref(manager.get('inst'))
    data = table_ref(field(table_ref(field(instance, 'Model')), 'Data'))
    panel = table_ref(field(data, 'mPanelList'))
    required = as_int(field(data, '_ConditionTradCount'))
    sold = as_int(field(panel, 'sellTime'))
    if required is None or required < 0 or sold is None or sold < 0:
        raise FanxiuRuntimeMemoryError('商盟自动贸易准入缓存未完整自然加载', code='data_not_loaded')
    return dict(complete=True, read_only=True, required_sell_count=required,
        observed_sell_count=sold, sell_count_gate_passed=sold >= required,
        activity_scope_verified=False, consumption_authorized=False,
        source='CTMapView.OpenAutoSettingView sell-count gate caches',
        evidence=dict(pid=c.binding.pid, process_start_ticks=c.binding.process_start_ticks))


def read_celebration_trade_auto_settings() -> dict:
    """Read the active native auto settings and its exact selected plan.

    Preview values are game estimates, not completed gains. The selected
    plan must join its naturally loaded model by UID before this projection
    is complete. Reading does not change the slider or start trading.
    """
    keys = {'CurPlan', 'City', 'Club', 'SliderNum', 'StartBtn', 'NonePlan',
        '_TmpPlanId', 'useNum', 'useMax', '_RewardShengWang',
        '_RewardExChangeCoin', '_CurCoinValue', 'CelebrationTradeMgr',
        'inst', 'Model', 'Data', 'mPanelList', 'programVOList', 'clubProgramVO',
        'uid', 'name', 'sellCity', 'goodsId', 'clubIdName'}

    def read(c):
        def field(obj, name):
            return read_ui_object_field(c, obj.address, name) if obj else None

        panels = [p for p in active_ui_component_objects(c) if has_ui_object_fields(c,
                  p.address, {'CurPlan', 'City', 'Club', 'SliderNum', 'StartBtn', 'NonePlan'})]
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError('商盟自动设置窗口尚未唯一自然加载', code='data_not_loaded')
        panel = panels[0]
        uid = c.reader.long(field(panel, '_TmpPlanId'))
        count, maximum = as_int(field(panel, 'useNum')), as_int(field(panel, 'useMax'))
        manager = table_ref(c.field(c.binding.environment_address, 'CelebrationTradeMgr'))
        instance = table_ref(field(manager, 'inst'))
        data = table_ref(field(table_ref(field(instance, 'Model')), 'Data'))
        model_panel = table_ref(field(data, 'mPanelList'))
        plans, declared = c.reader.list_items(field(model_panel, 'programVOList'))
        default = table_ref(field(model_panel, 'clubProgramVO'))
        matches = {}
        for candidate in [*plans, default]:
            ref = table_ref(candidate)
            if ref and c.reader.long(field(ref, 'uid')) == uid:
                matches[ref.address] = ref
        if declared != len(plans) or len(matches) != 1 or uid is None:
            raise FanxiuRuntimeMemoryError('商盟自动设置方案与原生列表未对齐', code='runtime_incomplete')
        plan = next(iter(matches.values()))
        goods, goods_count = c.reader.list_items(field(plan, 'goodsId'))
        goods = [as_int(item) for item in goods]
        city = as_int(field(plan, 'sellCity'))
        estimates = {name: as_int(field(panel, key)) for name, key in
                     [('reputation', '_RewardShengWang'), ('exchange_coin', '_RewardExChangeCoin'),
                      ('ending_trade_coin', '_CurCoinValue')]}
        if (count is None or maximum is None or not 1 <= count <= maximum
                or city is None or city <= 0 or goods_count != len(goods) or not goods
                or any(item is None or item <= 0 for item in goods)
                or any(value is None or value < 0 for value in estimates.values())):
            raise FanxiuRuntimeMemoryError('商盟自动设置数量、货物或预估未完整加载', code='runtime_incomplete')
        return dict(complete=True, read_only=True, selected_plan_uid=uid,
            plan_name=field(plan, 'name'), default_plan_selected=default is not None
                and c.reader.long(field(default, 'uid')) == uid,
            sell_city_id=city, goods_ids=goods, selected_trade_count=count,
            maximum_trade_count=maximum, game_estimates=estimates,
            consumption_authorized=False, source='active CTAutoSettingView + selected native programVO',
            evidence=dict(pid=c.binding.pid, process_start_ticks=c.binding.process_start_ticks))

    return read_ui_runtime_snapshot(keys, read, fast=True)
