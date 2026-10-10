from __future__ import annotations

import pytest
from types import SimpleNamespace

from backend.core.fanxiu.instrumentation import magic_invasion_auto_settings as runtime
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaRef,
)


class _Reader:
    def __init__(self, lists: dict[int, list[int]]) -> None:
        self.lists = lists

    def list_items(self, value: LuaRef):
        items = self.lists[value.address]
        return list(items), len(items)


def _panel_fields(**overrides):
    values = {
        "SliderNum": LuaRef("table", 0x10),
        "SureBtn": LuaRef("table", 0x11),
        "selectBtn6": LuaRef("table", 0x12),
        "selectList": LuaRef("table", 0x20),
        "selectedFourTimesList": LuaRef("table", 0x21),
        "selectedSpecialList": LuaRef("table", 0x22),
        "autoTimes": 37,
        "useNum": 37,
        "useMax": 120,
    }
    values.update(overrides)
    return lambda _address, name: values.get(name)


@pytest.mark.parametrize("latched", [None, True, False, 1])
def test_exit_diagnostic_preserves_unknown_latch_and_loaded_control(latched):
    result = runtime._decode_settings_panel(
        _Reader({0x20: [4], 0x21: [], 0x22: []}), 0x100,
        field=_panel_fields(closeClick=LuaRef("table", 0x13), isClickClose=latched),
    )
    assert result["exit"]["close_control_loaded"] is True
    assert result["exit"]["close_latched"] is (latched if type(latched) is bool else None)


def test_decode_active_auto_settings_exposes_all_business_switches() -> None:
    result = runtime._decode_settings_panel(
        _Reader(
            {
                0x20: [4, 5, 6, 8, 9, 10, 11],
                0x21: [4, 5, 11],
                0x22: [4, 5, 11],
            }
        ),
        0x100,
        field=_panel_fields(),
    )

    assert result["qualities"] == {
        "1": {
            "type_id": 1,
            "name": "堂主",
            "selected": False,
            "fourfold_merit": False,
            "pursuit_chain": False,
            "augment_configurable": False,
        },
        "2": {
            "type_id": 2,
            "name": "长老",
            "selected": False,
            "fourfold_merit": False,
            "pursuit_chain": False,
            "augment_configurable": True,
        },
        "3": {
            "type_id": 3,
            "name": "宗主",
            "selected": False,
            "fourfold_merit": False,
            "pursuit_chain": False,
            "augment_configurable": True,
        },
        "4": {
            "type_id": 4,
            "name": "太上长老",
            "selected": True,
            "fourfold_merit": True,
            "pursuit_chain": True,
            "augment_configurable": True,
        },
        "5": {
            "type_id": 5,
            "name": "天外魔神",
            "selected": True,
            "fourfold_merit": True,
            "pursuit_chain": True,
            "augment_configurable": True,
        },
        "11": {
            "type_id": 11,
            "name": "圣主",
            "selected": True,
            "fourfold_merit": True,
            "pursuit_chain": True,
            "augment_configurable": True,
        },
    }
    assert result["options"] == {
        "auto_use_demon_token": True,
        "ten_banish": False,
        "skip_animation": True,
        "quick_banish": True,
    }
    assert result["times"] == {"selected": 37, "maximum": 120}
    assert result["hidden_targets"] == {
        "10": {
            "type_id": 10,
            "name": "武始",
            "selected": True,
            "fourfold_merit": False,
            "pursuit_chain": False,
        },
    }
    assert result["auto_exorcism_choices"] == {
        "quality_hall_master": False,
        "quality_elder": False,
        "elder_quadruple_merit": False,
        "elder_chase_chain": False,
        "quality_sect_master": False,
        "sect_master_quadruple_merit": False,
        "sect_master_chase_chain": False,
        "quality_supreme_elder": True,
        "supreme_elder_quadruple_merit": True,
        "supreme_elder_chase_chain": True,
        "quality_extraterrestrial_demon": True,
        "extraterrestrial_demon_quadruple_merit": True,
        "extraterrestrial_demon_chase_chain": True,
        "quality_saint_master": True,
        "saint_master_quadruple_merit": True,
        "saint_master_chase_chain": True,
        "auto_use_exorcism_order": True,
        "skip_animation": True,
        "fast_exorcism": True,
        "tenfold_exorcism": False,
    }


def test_decode_auto_settings_fails_closed_on_unknown_type() -> None:
    with pytest.raises(FanxiuRuntimeMemoryError, match="无效类型") as exc_info:
        runtime._decode_settings_panel(
            _Reader({0x20: [4, 99], 0x21: [4], 0x22: [4]}),
            0x100,
            field=_panel_fields(),
        )

    assert exc_info.value.code == "schema_mismatch"


def test_decode_auto_settings_fails_closed_on_incoherent_count() -> None:
    with pytest.raises(FanxiuRuntimeMemoryError, match="次数状态不一致"):
        runtime._decode_settings_panel(
            _Reader({0x20: [4], 0x21: [4], 0x22: [4]}),
            0x100,
            field=_panel_fields(autoTimes=36),
        )


def test_snapshot_exposes_exact_active_panel_identity(monkeypatch) -> None:
    context = SimpleNamespace(
        reader=_Reader({0x20: [4], 0x21: [4], 0x22: [4]}),
        memory=SimpleNamespace(pid=7, process_start_ticks=11),
        cache_mode="layered",
        timings={},
    )
    fields = _panel_fields(autoTimes=1, useNum=1)
    monkeypatch.setattr(
        runtime,
        "active_ui_component_objects",
        lambda _context: [SimpleNamespace(address=0x698)],
    )
    monkeypatch.setattr(
        runtime,
        "read_ui_object_field",
        lambda _context, address, name: fields(address, name),
    )

    result = runtime._snapshot(context)

    assert result["evidence"] == {
        "pid": 7,
        "process_start_ticks": 11,
        "panel_address": 0x698,
        "active_membership": "UIShowMgr.V_M_compDic",
        "read_only": True,
        "lua_methods_invoked": False,
    }


@pytest.mark.parametrize('change,error', [
    ({}, None), ({'pid': 2}, 'identity_mismatch'),
    ({'maximum': 121}, 'identity_mismatch'),
    ({'useNum': 36}, 'runtime_incomplete'),
    ({'useNum': True}, 'runtime_incomplete'),
    ({'panels': 2}, 'runtime_incomplete'), ({'panels': 0}, 'not_loaded'),
])
def test_count_projection_preserves_bound_identity_uniqueness_and_count_contract(monkeypatch, change, error):
    # Deterministic decoding/public dispatch contract, not simulated game I/O.
    ctx = SimpleNamespace(memory=SimpleNamespace(pid=change.get('pid', 1), process_start_ticks=10))
    fields = _panel_fields(useNum=change.get('useNum', 37), useMax=change.get('maximum', 120))
    requested = []
    def field(context, address, name):
        requested.append(name)
        return fields(address, name)
    monkeypatch.setattr(runtime, 'active_ui_component_objects',
                        lambda ctx: tuple(LuaRef('table', 100 + i) for i in range(change.get('panels', 1))))
    monkeypatch.setattr(runtime, 'read_ui_object_field', field)
    monkeypatch.setattr(runtime, 'read_ui_runtime_snapshot', lambda keys, project, fast: project(ctx))
    result = runtime.read_magic_invasion_auto_count_snapshot(
        expected_binding={'pid': 1, 'process_start_ticks': 10, 'panel_address': 100, 'maximum': 120})
    if error:
        assert result['complete'] is False
        assert result['error_code'] == error
    else:
        assert result['times'] == {'selected': 37, 'maximum': 120}
        assert result['evidence']['panel_address'] == 100
        assert result['complete'] is True
    assert not set(requested) & {'selectList', 'selectedFourTimesList', 'selectedSpecialList', 'closeClick', 'isClickClose'}
    phases = result['performance']['non_overlapping_seconds']
    assert sum(phases.values()) == pytest.approx(result['elapsed_seconds'])


@pytest.mark.parametrize('fault', [KeyboardInterrupt, FanxiuRuntimeMemoryError])
def test_count_projection_retains_partial_phase_metrics_on_failure(monkeypatch, fault):
    def fail(ctx):
        if fault is FanxiuRuntimeMemoryError:
            raise fault('transport failed', code='memory_read_failed')
        raise fault()
    monkeypatch.setattr(runtime, 'active_ui_component_objects', fail)
    monkeypatch.setattr(runtime, 'read_ui_runtime_snapshot', lambda keys, project, fast: project(None))
    binding = {'pid': 1, 'process_start_ticks': 10, 'panel_address': 100, 'maximum': 120}
    if fault is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt) as caught:
            runtime.read_magic_invasion_auto_count_snapshot(expected_binding=binding)
        performance = caught.value.magic_auto_count_performance
    else:
        result = runtime.read_magic_invasion_auto_count_snapshot(expected_binding=binding)
        assert result['error_code'] == 'memory_read_failed'
        performance = result['performance']
    assert performance['projection_attempts'] == 1
    assert sum(performance['non_overlapping_seconds'].values()) == pytest.approx(performance['wall_seconds'])


@pytest.mark.parametrize('remaining,allowed', [(1800001, True), (1800000, True), (1799999, False), (0, False), (-1, False), (3600000, True)])
def test_settings_entry_gate_matches_strict_native_thirty_minute_boundary(remaining, allowed):
    result = runtime.magic_invasion_auto_settings_entry_admission(end_time_ms=10000000, current_time_ms=10000000-remaining)
    assert result['allowed'] is allowed
    assert result['remaining_ms'] == remaining
    assert result['consumption_authorized'] is False


@pytest.mark.parametrize('value', [True, 0, -1, 1.5, '1800000'])
def test_settings_entry_gate_rejects_invalid_clock(value):
    with pytest.raises(ValueError):
        runtime.magic_invasion_auto_settings_entry_admission(end_time_ms=10000000, current_time_ms=value)



def test_entry_admission_rediscovers_exact_runtime_identity_each_call(monkeypatch):
    from backend.core.fanxiu.activity import runtime_schedule, ranking_lifecycle
    from datetime import datetime, timezone
    now = 10000000
    occurrence = SimpleNamespace(activity_type='magic-invasion', family='gameplay_rank',
                                 start_at=datetime.fromtimestamp((now-1000000)/1000, timezone.utc),
                                 end_at=datetime.fromtimestamp((now+1800000)/1000, timezone.utc),
                                 instance_key='exact', runtime_id='123')
    reads = []
    def schedule(**kwargs):
        reads.append(kwargs)
        return {'available': True, 'complete': True, 'captured_at': 'fresh', 'evidence': {'pid': 1, 'process_start_ticks': 2}}
    monkeypatch.setattr(runtime_schedule, 'read_fanxiu_activity_runtime_schedule', schedule)
    monkeypatch.setattr(ranking_lifecycle, 'discover_ranking_occurrences', lambda s: [occurrence])
    from backend.core.fanxiu.instrumentation import server_clock
    monkeypatch.setattr(runtime, 'acquire_ui_runtime_context_fast', lambda keys: SimpleNamespace(binding=SimpleNamespace(pid=1, process_start_ticks=2, adb_serial='device')))
    monkeypatch.setattr(runtime.time, 'monotonic', lambda: 1.)
    monkeypatch.setattr(server_clock, 'read_current_server_clock_snapshot', lambda: entry_clock(now, now, 1.))
    assert runtime.read_magic_invasion_auto_settings_entry_admission()['allowed'] is True
    assert runtime.read_magic_invasion_auto_settings_entry_admission(expected_instance_key='exact')['allowed'] is True
    occurrence.instance_key = 'rotated'
    with pytest.raises(FanxiuRuntimeMemoryError) as caught:
        runtime.read_magic_invasion_auto_settings_entry_admission(expected_instance_key='exact')
    assert caught.value.code == 'identity_mismatch'
    assert caught.value.magic_entry_performance['elapsed_seconds'] == 0
    assert set(caught.value.magic_entry_performance['completed_stages_seconds']) == {
        'runtime_schedule', 'runtime_binding', 'server_clock', 'occurrence_projection'}
    monkeypatch.setattr(ranking_lifecycle, 'discover_ranking_occurrences', lambda s: [])
    with pytest.raises(FanxiuRuntimeMemoryError) as absent:
        runtime.read_magic_invasion_auto_settings_entry_admission()
    assert absent.value.code == 'activity_unavailable'
    assert 'runtime_schedule' in absent.value.magic_entry_performance['completed_stages_seconds']
    assert reads == [{'allow_discovery': True, 'force_refresh': True}] * 4


def entry_clock(lower=10000000, upper=10000000, valid_at=1.):
    return {'current_clock_complete': True, 'clock_source': 'device_wall_clock_minus_loaded_sync_offset',
            'server_time_lower_ms': lower, 'server_time_upper_ms': upper, 'uncertainty_ms': upper-lower,
            'valid_at_monotonic': valid_at, 'evidence': {'pid': 1, 'process_start_ticks': 2, 'adb_serial': 'device'}}


def test_entry_clock_upper_includes_age_and_never_uses_midpoint():
    result = runtime.project_magic_invasion_entry_clock(clock=entry_clock(10000000,10000100),
            expected_binding={'pid':1,'process_start_ticks':2,'adb_serial':'device'}, now_monotonic=1.5)
    assert result['server_time_upper_ms'] == 10000600
    assert result['server_time_lower_ms'] == 10000000
    assert result['uncertainty_ms'] == 600


@pytest.mark.parametrize('case,reason', [('unavailable','server_clock_unavailable'), ('host','server_clock_unavailable'),
        ('device','server_clock_identity_mismatch'), ('pid','server_clock_identity_mismatch'),
        ('generation','server_clock_identity_mismatch'), ('stale','server_clock_stale'),
        ('future','server_clock_stale'), ('missing','server_clock_timing_missing'),
        ('invalid','server_clock_interval_invalid'), ('uncertainty','server_clock_uncertainty_exceeded')])
def test_entry_clock_fails_closed_for_bad_or_historical_contract(case, reason):
    clock = entry_clock()
    if case == 'unavailable': clock['current_clock_complete'] = False
    elif case == 'host': clock['clock_source'] = 'host_wall_clock'
    elif case == 'device': clock['evidence']['adb_serial'] = 'other'
    elif case == 'pid': clock['evidence']['pid'] = 3
    elif case == 'generation': clock['evidence']['process_start_ticks'] = 3
    elif case == 'stale': clock['valid_at_monotonic'] = -10.
    elif case == 'future': clock['valid_at_monotonic'] = 3.
    elif case == 'missing': clock.pop('valid_at_monotonic')
    elif case == 'invalid': clock['server_time_upper_ms'] = 9999999
    elif case == 'uncertainty': clock.update(server_time_upper_ms=10006000,uncertainty_ms=6000)
    result = runtime.project_magic_invasion_entry_clock(clock=clock, expected_binding={'pid':1,'process_start_ticks':2,'adb_serial':'device'}, now_monotonic=1.)
    assert result['allowed'] is False
    assert result['reason'] == reason
    assert result['consumption_authorized'] is False


@pytest.mark.parametrize('lower,upper,start,end,allowed,reason', [
    (9999900,10000100,10000000,15000000,False,'settings_entry_start_boundary_uncertain'),
    (10000000,10000000,10000000,15000000,True,'settings_entry_open'),
    (9999900,10000100,9000000,11800000,False,'settings_entry_window_closed'),
    (10000000,10000000,9000000,11800000,True,'settings_entry_open')])
def test_entry_admission_server_interval_boundaries(monkeypatch,lower,upper,start,end,allowed,reason):
    from backend.core.fanxiu.activity import runtime_schedule, ranking_lifecycle
    from backend.core.fanxiu.instrumentation import server_clock
    from datetime import datetime,timezone
    occurrence=SimpleNamespace(activity_type='magic-invasion',family='gameplay_rank',
            start_at=datetime.fromtimestamp(start/1000,timezone.utc),end_at=datetime.fromtimestamp(end/1000,timezone.utc),instance_key='exact',runtime_id='123')
    monkeypatch.setattr(runtime_schedule,'read_fanxiu_activity_runtime_schedule',lambda **kw: {'available':True,'complete':True,'evidence':{'pid':1,'process_start_ticks':2}})
    monkeypatch.setattr(ranking_lifecycle,'discover_ranking_occurrences',lambda s:[occurrence])
    monkeypatch.setattr(runtime,'acquire_ui_runtime_context_fast',lambda keys:SimpleNamespace(binding=SimpleNamespace(pid=1,process_start_ticks=2,adb_serial='device')))
    monkeypatch.setattr(runtime.time,'monotonic',lambda:1.)
    def forbidden(): raise AssertionError('host wall clock used')
    monkeypatch.setattr(runtime.time,'time',forbidden)
    monkeypatch.setattr(server_clock,'read_current_server_clock_snapshot',lambda:entry_clock(lower,upper))
    result=runtime.read_magic_invasion_auto_settings_entry_admission(expected_instance_key='exact')
    assert result['allowed'] is allowed
    assert result['reason'] == reason
    assert result['current_time_ms'] == upper
    assert result['clock_source'] == 'device_wall_clock_minus_loaded_sync_offset'
    assert result['consumption_authorized'] is False


@pytest.mark.parametrize('case', ['slow_projection','schedule_identity','clock_unavailable'])
def test_entry_admission_rejects_query_delay_and_identity_before_any_navigation(monkeypatch,case):
    from backend.core.fanxiu.activity import runtime_schedule, ranking_lifecycle
    from backend.core.fanxiu.instrumentation import server_clock
    ticks={'now':1.}
    monkeypatch.setattr(runtime.time,'monotonic',lambda:ticks['now'])
    monkeypatch.setattr(runtime_schedule,'read_fanxiu_activity_runtime_schedule',lambda **kw:{'available':True,'complete':True,'evidence':{'pid':3 if case=='schedule_identity' else 1,'process_start_ticks':2}})
    monkeypatch.setattr(runtime,'acquire_ui_runtime_context_fast',lambda keys:SimpleNamespace(binding=SimpleNamespace(pid=1,process_start_ticks=2,adb_serial='device')))
    def projection(schedule):
        if case=='slow_projection': ticks['now']=3.1
        return []
    monkeypatch.setattr(ranking_lifecycle,'discover_ranking_occurrences',projection)
    calls=[]
    def clock():
        calls.append('clock')
        return {'current_clock_complete':False,'reason':'offset_changed'} if case=='clock_unavailable' else entry_clock()
    monkeypatch.setattr(server_clock,'read_current_server_clock_snapshot',clock)
    if case=='schedule_identity':
        with pytest.raises(FanxiuRuntimeMemoryError) as exc:
            runtime.read_magic_invasion_auto_settings_entry_admission()
        assert exc.value.code=='identity_mismatch'
        assert calls==[]
    else:
        result=runtime.read_magic_invasion_auto_settings_entry_admission()
        assert result['allowed'] is False
        assert result['reason']==('server_clock_stale' if case=='slow_projection' else 'server_clock_unavailable')
        assert result['performance']['elapsed_seconds']==pytest.approx(2.1 if case=='slow_projection' else 0.)
