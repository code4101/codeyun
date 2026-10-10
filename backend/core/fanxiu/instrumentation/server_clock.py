"""Read loaded LoginMgr.GameTime evidence without invoking GetSeverTime.

Client GameTime.lua UpdateSystemTime stores timestamp and a native-clock
offset. GetSeverTime uses Round(UnityEngine.Time:GetTimestamp()*1000) minus
that offset, then mutates V_ServerTime. Reading the stored value alone is not
a current server clock. This provider deliberately exposes that missing link
instead of substituting host time, open-server time or a historical heartbeat.
"""
from __future__ import annotations

import time
import math
from typing import Any
from collections.abc import Mapping

from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError, as_int, table_ref,
)
from backend.core.fanxiu.instrumentation.ui_runtime_context import acquire_lua_global_runtime_context


def project_game_time_clock_evidence(fields: Mapping[str, Any], *, stored_server_time_ms: Any) -> dict[str, Any]:
    """Pure scalar parsing, explicitly refusing to promote periodic values.

    Fractional milliseconds are valid for local timestamp diagnostics. Boolean,
    string and nonfinite values are not clock facts. No fresh native timestamp
    input exists, so even complete synchronization fields cannot yield a
    current_server_time_ms here.
    """
    numeric = {k: fields.get(k) if type(fields.get(k)) in (int, float)
               and math.isfinite(fields[k]) else None
               for k in ('V_TimeDifference', 'V_PreGetSystemTime', 'V_LastCheckTime',
                         'V_LastSendHeratTime', 'V_TimeSn')}
    return {'current_clock_complete': False, 'current_server_time_ms': None,
            'reason': 'current_unity_timestamp_not_projected',
            'stored_server_time_ms': stored_server_time_ms
            if type(stored_server_time_ms) is int and stored_server_time_ms > 0 else None,
            'fields': {**numeric, 'V_StartHeart': fields.get('V_StartHeart')
                       if type(fields.get('V_StartHeart')) is bool else None}}


def read_server_clock_runtime_evidence() -> dict[str, Any]:
    """Read current bytes of the already-loaded login time object, no Lua calls.

    available describes evidence availability, not a usable current clock.
    current_server_time_ms remains None until the current native timestamp can
    be projected with the same clock semantics and synchronization identity.
    Neither V_LastCheckTime nor V_LastSendHeratTime proves current time: their
    updates are conditional/periodic and can stop on disconnect or pause.
    """
    started = time.monotonic()
    phases: dict[str, float] = {}
    methods = frozenset({'Inst_get', 'GetSeverTime', 'UpdateSystemTime'})
    keys = frozenset({'LoginMgr', 'inst', 'GameTime', 'V_TimeDifference', 'V_ServerTime',
                      'V_PreGetSystemTime', 'V_LastCheckTime', 'V_LastSendHeratTime',
                      'V_StartHeart', 'V_TimeSn'})
    try:
        ctx = acquire_lua_global_runtime_context(keys)
        context_done = time.monotonic()
        phases['context'] = context_done - started
        manager = ctx.loaded_manager_fields('LoginMgr', methods)
        root_done = time.monotonic()
        phases['manager_root'] = root_done - context_done
        instance = table_ref(manager.get('inst'))
        if instance is None:
            raise FanxiuRuntimeMemoryError('LoginMgr.inst 未加载', code='not_loaded')
        game_time = table_ref(ctx.object_field(instance.address, 'GameTime'))
        if game_time is None:
            raise FanxiuRuntimeMemoryError('LoginMgr.GameTime 未加载', code='not_loaded')
        fields = {k: ctx.object_field(game_time.address, k) for k in keys if k.startswith('V_')}
        stored = fields.pop('V_ServerTime')
        stored_value = ctx.reader.long(stored) if table_ref(stored) is not None else as_int(stored)
        phases['clock_fields'] = time.monotonic() - root_done
        # Never serialize raw Lua refs, manager fields or account metadata.
        # GetTimestamp()*1000 retains fractional milliseconds in native Lua;
        # do not silently turn those readable diagnostics into "missing".
        return {'available': True, **project_game_time_clock_evidence(fields, stored_server_time_ms=stored_value),
                'source': 'loaded_LoginMgr.GameTime',
                'started_monotonic': started, 'finished_monotonic': time.monotonic(),
                'captured_at_epoch': time.time(), 'elapsed_seconds': time.monotonic() - started,
                'performance': {'stages_seconds': phases, 'context_stages_seconds': dict(ctx.timings)},
                'evidence': {'pid': ctx.memory.pid, 'process_start_ticks': ctx.memory.process_start_ticks,
                             'adb_serial': ctx.binding.adb_serial,
                             'login_instance_address': instance.address, 'game_time_address': game_time.address,
                             'root_cache_hit': False, 'root_resolution': 'current_global_context',
                             'read_only': True, 'lua_methods_invoked': False}}
    except Exception as exc:
        return {'available': False, 'current_clock_complete': False, 'current_server_time_ms': None,
                'started_monotonic': started, 'finished_monotonic': time.monotonic(),
                'source': 'loaded_LoginMgr.GameTime', 'reason': str(exc), 'error_code': getattr(exc, 'code', 'clock_evidence_unavailable'),
                'performance': {'completed_stages_seconds': dict(phases)},
                'elapsed_seconds': time.monotonic() - started}


def project_server_clock_interval(*, device_before: Mapping[str, Any], device_after: Mapping[str, Any],
                                  offset_before: Mapping[str, Any], offset_after: Mapping[str, Any],
                                  now_monotonic: float | None = None, max_age_seconds: float = 2.0,
                                  max_uncertainty_ms: float = 5000.0, max_sync_age_ms: float = 30000.0) -> dict[str, Any]:
    """Pure conservative clock interval; caller brackets device reads by offsets.

    Same process, device, GameTime object and synchronization anchor/offset
    must be proven on both sides. No stored V_ServerTime participates. Device
    times must be current observations in increasing order. Round-to-ms is
    bounded by half a millisecond plus device microsecond precision; integer interval endpoints round outward.
    This is not a claim of an exact point clock or native function execution.
    """
    rejected = {'current_clock_complete': False, 'current_server_time_ms': None}
    def finite(value):
        return type(value) in (int, float) and math.isfinite(value)
    if not finite(now_monotonic) or not finite(max_age_seconds) or max_age_seconds <= 0 or not finite(max_uncertainty_ms) or max_uncertainty_ms <= 0 or not finite(max_sync_age_ms) or max_sync_age_ms <= 0:
        return {**rejected, 'reason': 'current_observation_timing_missing'}
    samples = (offset_before, device_before, device_after, offset_after)
    timings = []
    for sample in samples:
        start, end = sample.get('started_monotonic'), sample.get('finished_monotonic')
        if not finite(start) or not finite(end) or start < 0 or end < start:
            return {**rejected, 'reason': 'observation_timing_invalid'}
        timings.append((start, end))
    if any(timings[i][1] > timings[i+1][0] for i in range(3)) or now_monotonic < timings[-1][1]:
        return {**rejected, 'reason': 'offset_observations_do_not_bracket_device_reads'}
    age = now_monotonic - timings[-1][1]
    if age > max_age_seconds:
        return {**rejected, 'reason': 'clock_observation_stale', 'age_seconds': age}
    for sample in (offset_before, offset_after):
        if sample.get('available') is not True:
            return {**rejected, 'reason': 'offset_observation_unavailable'}
    first, last = offset_before.get('evidence') or {}, offset_after.get('evidence') or {}
    for key in ('pid', 'process_start_ticks', 'login_instance_address', 'game_time_address', 'adb_serial'):
        if not first.get(key) or first.get(key) != last.get(key):
            return {**rejected, 'reason': 'clock_identity_changed'}
    fields, after_fields = offset_before.get('fields') or {}, offset_after.get('fields') or {}
    sync_names = ('V_TimeDifference', 'V_PreGetSystemTime')
    for sample_fields in (fields, after_fields):
        if any(not finite(sample_fields.get(key)) for key in sync_names):
            return {**rejected, 'reason': 'clock_sync_fields_missing_or_invalid'}
        if sample_fields['V_PreGetSystemTime'] <= 0:
            return {**rejected, 'reason': 'clock_unsynchronized'}
    for sample in (device_before, device_after):
        if sample.get('available') is not True or sample.get('adb_serial') != first['adb_serial']:
            return {**rejected, 'reason': 'device_clock_binding_missing'}
        if type(sample.get('epoch_ns')) is not int or sample['epoch_ns'] <= 0:
            return {**rejected, 'reason': 'device_clock_invalid'}
    if device_after['epoch_ns'] < device_before['epoch_ns']:
        return {**rejected, 'reason': 'device_clock_reversed'}
    if any(fields[key] != after_fields[key] for key in sync_names):
        return {**rejected, 'reason': 'clock_sync_fields_changed'}
    # Native gettimeofday has microsecond precision; permit only that explicit
    # precision bound around the synchronization anchor, never historical reads.
    if device_before['epoch_ns'] / 1_000_000 + 0.001 < fields['V_PreGetSystemTime']:
        return {**rejected, 'reason': 'device_clock_precedes_sync_anchor'}
    if device_after['epoch_ns'] / 1_000_000 - fields['V_PreGetSystemTime'] > max_sync_age_ms:
        return {**rejected, 'reason': 'clock_sync_anchor_stale'}
    delta = fields['V_TimeDifference']
    low = math.floor(device_before['epoch_ns'] / 1_000_000 - delta - 0.501)
    high = math.ceil(device_after['epoch_ns'] / 1_000_000 - delta + 0.501
                     + (now_monotonic - timings[2][0]) * 1000)
    if high - low > max_uncertainty_ms:
        return {**rejected, 'reason': 'clock_uncertainty_exceeded', 'uncertainty_ms': high - low}

    return {'current_clock_complete': True, 'current_server_time_ms': None,
            'server_time_lower_ms': low, 'server_time_upper_ms': high,
            'uncertainty_ms': high - low, 'clock_source': 'device_wall_clock_minus_loaded_sync_offset',
            'reason': 'bounded_observation_interval', 'age_seconds': age,
            'valid_at_monotonic': now_monotonic, 'evidence': dict(first)}


def read_current_server_clock_snapshot(*, max_age_seconds: float = 2.0, max_uncertainty_ms: float = 5000.0,
                                       max_sync_age_ms: float = 30000.0, max_attempts: int = 2,
                                       total_timeout_seconds: float = 5.0) -> dict[str, Any]:
    """Collect at most two complete fresh groups within a monotonic admission budget.

    Only complete finite synchronization fields that actually changed permit a
    new whole-group observation. Permission, missing fields, identity changes
    and other failures stop immediately. Groups never share scalar/device bytes.
    Before every read and return, the deadline is checked: no late result can be
    complete and no further read starts after it. An in-flight lower-level read
    keeps its own transport timeout; this budget does not forcibly interrupt it.
    Failed earlier groups remain diagnostics, never current clock inputs.
    """
    from backend.core.fanxiu.client.mumu_control import read_mumu_adb_device_wall_clock
    if type(max_attempts) is not int or not 1 <= max_attempts <= 2 or type(total_timeout_seconds) not in (int, float) or not math.isfinite(total_timeout_seconds) or not 0 < total_timeout_seconds <= 5:
        raise ValueError('Clock observation requires <=2 attempts and <=5 seconds admission budget')
    started = time.monotonic()
    deadline = started + total_timeout_seconds
    previous = []
    initial_identity = None
    identity_keys = ('pid', 'process_start_ticks', 'login_instance_address', 'game_time_address', 'adb_serial')
    for index in range(1, max_attempts + 1):
        observations = {}
        result = None
        for name in ('offset_before', 'device_before', 'device_after', 'offset_after'):
            if time.monotonic() >= deadline:
                result = {'current_clock_complete': False, 'current_server_time_ms': None, 'reason': 'clock_observation_budget_exhausted'}
                break
            if name.startswith('offset'):
                sample = read_server_clock_runtime_evidence()
            else:
                remaining = deadline - time.monotonic()
                if remaining < 1:
                    result = {'current_clock_complete': False, 'current_server_time_ms': None, 'reason': 'clock_observation_budget_exhausted'}
                    break
                sample = read_mumu_adb_device_wall_clock(expected_serial=serial, timeout_s=min(3, int(remaining)))
            observations[name] = sample
            if time.monotonic() >= deadline:
                result = {'current_clock_complete': False, 'current_server_time_ms': None, 'reason': 'clock_observation_budget_exhausted'}
                break
            if sample.get('available') is not True:
                result = {'current_clock_complete': False, 'current_server_time_ms': None,
                          'reason': 'offset_observation_unavailable' if name.startswith('offset') else 'device_clock_binding_missing',
                          'sample_reason': sample.get('reason')}
                break
            if name == 'offset_before':
                evidence = sample.get('evidence') or {}
                serial = evidence.get('adb_serial')
                identity = tuple(evidence.get(key) for key in identity_keys)
                if not all(identity) or (initial_identity is not None and identity != initial_identity):
                    result = {'current_clock_complete': False, 'current_server_time_ms': None, 'reason': 'clock_identity_changed'}
                    break
                initial_identity = identity
        now = time.monotonic()
        if now >= deadline:
            result = {'current_clock_complete': False, 'current_server_time_ms': None, 'reason': 'clock_observation_budget_exhausted'}
        if result is None:
            result = project_server_clock_interval(**observations, now_monotonic=now,
                max_age_seconds=max_age_seconds, max_uncertainty_ms=max_uncertainty_ms, max_sync_age_ms=max_sync_age_ms)
        result = {**result, 'observations': observations, 'attempt_count': index,
                  'elapsed_seconds': now - started, 'previous_attempts': list(previous),
                  'attempt_limit': max_attempts, 'budget_seconds': total_timeout_seconds}
        if result.get('reason') != 'clock_sync_fields_changed' or index == max_attempts:
            return result
        previous.append({k: v for k, v in result.items() if k != 'previous_attempts'})
    raise AssertionError('unreachable')


__all__ = ['read_server_clock_runtime_evidence', 'project_game_time_clock_evidence',
           'project_server_clock_interval', 'read_current_server_clock_snapshot']
