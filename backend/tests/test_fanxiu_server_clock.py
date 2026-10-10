"""Scalar evidence contracts only; no simulated Runtime or game clock."""
import pytest
from backend.core.fanxiu.instrumentation.server_clock import project_game_time_clock_evidence


def test_periodic_and_stored_fields_never_masquerade_as_current_clock():
    result = project_game_time_clock_evidence(
        {'V_TimeDifference': -6780.0, 'V_PreGetSystemTime': 1791640078045.9312,
         'V_LastCheckTime': 1791640078447.324, 'V_LastSendHeratTime': 1791640077852.293,
         'V_TimeSn': 0, 'V_StartHeart': True}, stored_server_time_ms=1791640085229)
    assert result['fields']['V_PreGetSystemTime'] == 1791640078045.9312
    assert result['fields']['V_TimeDifference'] == -6780.0
    assert result['stored_server_time_ms'] == 1791640085229
    assert result['current_clock_complete'] is False
    assert result['current_server_time_ms'] is None


@pytest.mark.parametrize('value', [True, '1', float('nan'), float('inf'), None])
def test_invalid_clock_fields_are_not_numeric_evidence(value):
    result = project_game_time_clock_evidence({'V_TimeDifference': value}, stored_server_time_ms=value)
    assert result['fields']['V_TimeDifference'] is None
    assert result['stored_server_time_ms'] is None
    assert result['current_clock_complete'] is False

from backend.core.fanxiu.instrumentation.server_clock import project_server_clock_interval
from backend.core.fanxiu.client import mumu_control


def observations():
    evidence = dict(pid=1, process_start_ticks=2, login_instance_address=3, game_time_address=4, adb_serial='device')
    offset = dict(available=True, evidence=evidence, fields={'V_TimeDifference': -10.0, 'V_PreGetSystemTime': 1700000000000.0})
    return dict(offset_before=dict(offset, started_monotonic=1., finished_monotonic=1.1),
                device_before=dict(available=True, adb_serial='device', epoch_ns=1700000001000000000, started_monotonic=1.2, finished_monotonic=1.3),
                device_after=dict(available=True, adb_serial='device', epoch_ns=1700000001200000000, started_monotonic=1.4, finished_monotonic=1.5),
                offset_after=dict(offset, started_monotonic=1.6, finished_monotonic=1.7))


def test_fresh_bounded_interval_includes_return_latency():
    result = project_server_clock_interval(**observations(), now_monotonic=1.8)
    assert result['current_clock_complete'] is True
    assert result['current_server_time_ms'] is None
    assert result['server_time_lower_ms'] == 1700000001009
    assert result['server_time_upper_ms'] == 1700000001611


@pytest.mark.parametrize('case,reason', [
    ('missing_now', 'current_observation_timing_missing'),
    ('missing_timing', 'observation_timing_invalid'),
    ('overlap', 'offset_observations_do_not_bracket_device_reads'),
    ('stale', 'clock_observation_stale'),
    ('anchor', 'device_clock_precedes_sync_anchor'),
    ('offset', 'clock_sync_fields_changed'),
    ('identity', 'clock_identity_changed'),
    ('device', 'device_clock_binding_missing'),
    ('reversed', 'device_clock_reversed'),
    ('uncertainty', 'clock_uncertainty_exceeded')])
def test_invalid_observation_never_returns_current_clock(case, reason):
    samples = observations()
    now = 1.8
    extra = {}
    if case == 'missing_now': now = None
    elif case == 'missing_timing': samples['device_before'].pop('started_monotonic')
    elif case == 'overlap': samples['offset_before']['finished_monotonic'] = 1.25
    elif case == 'stale': now = 5.
    elif case == 'anchor': samples['device_before']['epoch_ns'] = 1699999999990000000
    elif case == 'offset': samples['offset_after']['fields'] = dict(samples['offset_after']['fields'], V_TimeDifference=11.)
    elif case == 'identity': samples['offset_after']['evidence'] = dict(samples['offset_after']['evidence'], pid=9)
    elif case == 'device': samples['device_after']['adb_serial'] = 'other'
    elif case == 'reversed': samples['device_after']['epoch_ns'] -= 1000000000
    elif case == 'uncertainty': extra['max_uncertainty_ms'] = 500
    result = project_server_clock_interval(**samples, now_monotonic=now, **extra)
    assert result['current_clock_complete'] is False
    assert result['reason'] == reason


@pytest.mark.parametrize('payload', [b'1700000000000000000N', b'1700000000', b'bad', b'', b'0'])
def test_device_nanosecond_precision_required(payload):
    assert not mumu_control.parse_mumu_adb_device_wall_clock(payload, expected_serial='device', metadata={'adb_serial': 'device'})['available']


def test_exact_device_identity_and_fixed_transport_contract(monkeypatch):
    assert not mumu_control.parse_mumu_adb_device_wall_clock(b'1700000000000000000', expected_serial='device', metadata={'adb_serial': 'other'})['available']
    calls = []
    def transport(command, **kwargs):
        calls.append((command, kwargs))
        return b'1700000000000000000\n', {'adb_serial': 'device'}
    monkeypatch.setattr(mumu_control, '_mumu_adb_session_shell_bytes', transport)
    result = mumu_control.read_mumu_adb_device_wall_clock(expected_serial='device')
    assert result['available']
    assert calls == [('date +%s%N', {'timeout_s': 3, 'expected_serial': 'device', 'allow_port_recovery': False})]
    assert result['finished_monotonic'] >= result['started_monotonic']


def test_readonly_transport_never_recovers_or_switches(monkeypatch):
    monkeypatch.setattr(mumu_control, 'remote_device_active', lambda: False)
    monkeypatch.setattr(mumu_control, '_mumu_readonly_serial_candidates', lambda: ['device'])
    monkeypatch.setattr(mumu_control, '_MUMU_ADB_SESSION', {'serial': 'other'})
    def forbidden(): raise AssertionError('port recovery called')
    monkeypatch.setattr(mumu_control, '_ensure_mumu_adb_port_available', forbidden)
    result = mumu_control.read_mumu_adb_device_wall_clock(expected_serial='device')
    assert not result['available']
    assert 'differs' in result['error']


def test_stale_sync_anchor_fails_closed():
    samples = observations()
    samples['offset_before']['fields'] = samples['offset_after']['fields'] = {'V_TimeDifference': -10., 'V_PreGetSystemTime': 1699999900000.}
    result = project_server_clock_interval(**samples, now_monotonic=1.8)
    assert result['reason'] == 'clock_sync_anchor_stale'
    assert not result['current_clock_complete']


def test_device_query_lock_and_transport_delay_is_timeout(monkeypatch):
    ticks = iter([1., 5.])
    monkeypatch.setattr(mumu_control.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(mumu_control, '_mumu_adb_session_shell_bytes', lambda *args, **kw: (b'1700000000000000000', {'adb_serial': 'device'}))
    result = mumu_control.read_mumu_adb_device_wall_clock(expected_serial='device', timeout_s=3)
    assert result['reason'] == 'device_clock_query_timeout'
    assert not result['available']


def test_cold_clock_transport_connects_exact_device_without_recovery(monkeypatch):
    import sys
    from types import SimpleNamespace
    calls = []
    class Transport:
        def __init__(self, host, port, **kwargs): calls.append(('connect_target', host, port))
        def connect(self, **kwargs): calls.append(('connect',))
        def shell(self, command, **kwargs):
            calls.append(('query', command))
            return b'1700000000000000000'
    monkeypatch.setitem(sys.modules, 'adb_shell.adb_device', SimpleNamespace(AdbDeviceTcp=Transport))
    monkeypatch.setattr(mumu_control, 'remote_device_active', lambda: False)
    monkeypatch.setattr(mumu_control, '_mumu_readonly_serial_candidates', lambda: ['127.0.0.1:5555', '127.0.0.1:6666'])
    monkeypatch.setattr(mumu_control, '_MUMU_ADB_SESSION', {})
    monkeypatch.setattr(mumu_control, '_mumu_adb_meta', lambda serial, **kw: {'adb_serial': serial})
    def forbidden(): raise AssertionError('port recovery called')
    monkeypatch.setattr(mumu_control, '_ensure_mumu_adb_port_available', forbidden)
    result = mumu_control.read_mumu_adb_device_wall_clock(expected_serial='127.0.0.1:6666')
    assert result['available']
    assert calls == [('connect_target', '127.0.0.1', 6666), ('connect',), ('query', 'date +%s%N')]


def test_remote_clock_retains_remote_provider_and_rejects_missing_binding(monkeypatch):
    calls = []
    monkeypatch.setattr(mumu_control, 'remote_device_active', lambda: True)
    def remote(operation, payload, **kwargs):
        calls.append((operation, payload))
        return {}
    monkeypatch.setattr(mumu_control, 'call_remote_device', remote)
    monkeypatch.setattr(mumu_control, 'remote_bytes', lambda value: (b'1700000000000000000', {}))
    result = mumu_control.read_mumu_adb_device_wall_clock(expected_serial='device')
    assert result['reason'] == 'device_clock_binding_missing'
    assert calls == [('shell_bytes', {'command': 'date +%s%N', 'timeout_seconds': 3})]


def test_clock_transport_lock_timeout_never_reaches_query(monkeypatch):
    from types import SimpleNamespace
    calls = []
    def acquire(**kwargs):
        calls.append(kwargs)
        return False
    monkeypatch.setattr(mumu_control, 'remote_device_active', lambda: False)
    monkeypatch.setattr(mumu_control, '_MUMU_ADB_SESSION_LOCK', SimpleNamespace(acquire=acquire))
    result = mumu_control.read_mumu_adb_device_wall_clock(expected_serial='device')
    assert not result['available']
    assert 'lock timeout' in result['error']
    assert calls == [{'timeout': 3}]


from copy import deepcopy
from backend.core.fanxiu.instrumentation import server_clock


def install_observation_records(monkeypatch, groups, *, slow_after=False):
    # Immutable clock-provider contracts only; no game/UI/kernel emulator.
    offsets=[];devices=[]
    for index, group in enumerate(groups):
        group=deepcopy(group)
        for name,sample in group.items():
            sample['started_monotonic']+=index
            sample['finished_monotonic']+=index
        offsets.extend([group['offset_before'],group['offset_after']])
        devices.extend([group['device_before'],group['device_after']])
    ticks={'now':.8};calls=[]
    def offset():
        sample=offsets.pop(0);calls.append('offset')
        ticks['now']=sample['finished_monotonic']
        if slow_after and len(calls)==4:ticks['now']=6.
        return sample
    def device(**kwargs):
        sample=devices.pop(0);calls.append('device')
        ticks['now']=sample['finished_monotonic'];return sample
    monkeypatch.setattr(server_clock.time,'monotonic',lambda:ticks['now'])
    monkeypatch.setattr(server_clock,'read_server_clock_runtime_evidence',offset)
    monkeypatch.setattr(mumu_control,'read_mumu_adb_device_wall_clock',device)
    return calls


def sync_crossing():
    group=observations()
    group['offset_after']['fields']=dict(group['offset_after']['fields'],V_TimeDifference=-11.)
    return group


def test_whole_group_retry_after_real_finite_sync_change_uses_only_new_group(monkeypatch):
    first=sync_crossing();second=observations()
    second['offset_before']['fields']=second['offset_after']['fields']={'V_TimeDifference':-11.,'V_PreGetSystemTime':1700000000200.}
    calls=install_observation_records(monkeypatch,[first,second])
    result=server_clock.read_current_server_clock_snapshot()
    assert result['current_clock_complete'] is True
    assert result['attempt_count']==2
    assert calls==['offset','device','device','offset']*2
    assert result['observations']['offset_before']['started_monotonic']==2.
    assert result['previous_attempts'][0]['reason']=='clock_sync_fields_changed'
    assert result['evidence']==second['offset_before']['evidence']


def test_persistent_sync_change_stops_after_two_groups(monkeypatch):
    calls=install_observation_records(monkeypatch,[sync_crossing(),sync_crossing()])
    result=server_clock.read_current_server_clock_snapshot()
    assert not result['current_clock_complete']
    assert result['reason']=='clock_sync_fields_changed'
    assert result['attempt_count']==2 and len(calls)==8


@pytest.mark.parametrize('case,reason', [('missing','clock_sync_fields_missing_or_invalid'),
    ('nonfinite','clock_sync_fields_missing_or_invalid'),('device_unavailable','device_clock_binding_missing'),
    ('wrong_serial','device_clock_binding_missing'),('reversed','device_clock_reversed'),
    ('identity','clock_identity_changed')])
def test_other_failures_take_priority_over_sync_change_and_never_retry(monkeypatch,case,reason):
    group=sync_crossing()
    if case=='missing':group['offset_after']['fields'].pop('V_TimeDifference')
    elif case=='nonfinite':group['offset_after']['fields']['V_PreGetSystemTime']=float('nan')
    elif case=='device_unavailable':group['device_before']['available']=False
    elif case=='wrong_serial':group['device_after']['adb_serial']='other'
    elif case=='reversed':group['device_after']['epoch_ns']=group['device_before']['epoch_ns']-1
    elif case=='identity':group['offset_after']['evidence']=dict(group['offset_after']['evidence'],pid=9)
    calls=install_observation_records(monkeypatch,[group])
    result=server_clock.read_current_server_clock_snapshot()
    assert result['reason']==reason
    assert not result['current_clock_complete']
    assert result['attempt_count']==1 and len(calls)<=4


def test_stable_but_over_budget_group_is_rejected_without_retry(monkeypatch):
    calls=install_observation_records(monkeypatch,[observations()],slow_after=True)
    result=server_clock.read_current_server_clock_snapshot()
    assert result['reason']=='clock_observation_budget_exhausted'
    assert not result['current_clock_complete'] and len(calls)==4


def test_retried_group_cannot_change_device_or_process(monkeypatch):
    group=observations()
    group['offset_before']['evidence']=dict(group['offset_before']['evidence'],pid=9)
    calls=install_observation_records(monkeypatch,[sync_crossing(),group])
    result=server_clock.read_current_server_clock_snapshot()
    assert result['reason']=='clock_identity_changed'
    assert not result['current_clock_complete'] and len(calls)==5


def test_expired_budget_prevents_starting_another_read(monkeypatch):
    group=observations();group['offset_before']['finished_monotonic']=6.
    calls=install_observation_records(monkeypatch,[group])
    result=server_clock.read_current_server_clock_snapshot()
    assert result['reason']=='clock_observation_budget_exhausted' and calls==['offset']
