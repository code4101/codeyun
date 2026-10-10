from __future__ import annotations

"""Read the active Magic Invasion auto-banish settings panel."""

import time
import math
from datetime import datetime, timezone
from typing import Any
from collections.abc import Mapping

from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    as_int,
    table_ref,
)
from backend.core.fanxiu.instrumentation.ui_runtime_context import (
    UiRuntimeContext,
    acquire_ui_runtime_context_fast,
    active_ui_component_objects,
    read_ui_object_field,
    read_ui_runtime_snapshot,
)


QUALITY_NAMES = {
    1: "堂主",
    2: "长老",
    3: "宗主",
    4: "太上长老",
    5: "天外魔神",
    11: "圣主",
}

_AUTO_USE_DEMON_TOKEN = 6
_TEN_BANISH = 7
_SKIP_ANIMATION = 8
_QUICK_BANISH = 9
_HIDDEN_TARGET_NAMES = {
    10: "武始",
}

_PRIMARY_ALLOWED = frozenset(
    {
        *QUALITY_NAMES,
        _AUTO_USE_DEMON_TOKEN,
        _TEN_BANISH,
        _SKIP_ANIMATION,
        _QUICK_BANISH,
        *_HIDDEN_TARGET_NAMES,
    }
)
_AUGMENT_ALLOWED = frozenset({2, 3, 4, 5, 11})
_UI_KEYS = frozenset(
    {
        "selectList",
        "selectedFourTimesList",
        "selectedSpecialList",
        "autoTimes",
        "useNum",
        "useMax",
        "SliderNum",
        "SureBtn",
        "selectBtn6",
        "closeClick",
        "isClickClose",
    }
)


def _decode_type_list(
    reader: Any,
    value: Any,
    *,
    field_name: str,
    allowed: frozenset[int],
) -> set[int]:
    ref = table_ref(value)
    if ref is None:
        raise FanxiuRuntimeMemoryError(
            f"MapActAutoTipsView.{field_name} 未加载",
            code="not_loaded",
        )
    items, declared_count = reader.list_items(ref)
    if declared_count is None:
        raise FanxiuRuntimeMemoryError(
            f"MapActAutoTipsView.{field_name} 缺少 CList count",
            code="runtime_incomplete",
        )
    decoded: list[int] = []
    for raw in items:
        item = as_int(raw)
        if item is None or item not in allowed:
            raise FanxiuRuntimeMemoryError(
                f"MapActAutoTipsView.{field_name} 含无效类型 {raw!r}",
                code="schema_mismatch",
            )
        decoded.append(int(item))
    if len(decoded) != len(set(decoded)):
        raise FanxiuRuntimeMemoryError(
            f"MapActAutoTipsView.{field_name} 含重复类型",
            code="runtime_incomplete",
        )
    return set(decoded)


def _decode_settings_panel(
    reader: Any,
    panel_address: int,
    *,
    field,
) -> dict[str, Any]:
    # These two component fields distinguish the editing panel from the
    # similarly named runtime overlay.  No Unity method is invoked.
    if table_ref(field(panel_address, "SliderNum")) is None:
        raise FanxiuRuntimeMemoryError("MapActAutoTipsView.SliderNum 未加载")
    if table_ref(field(panel_address, "SureBtn")) is None:
        raise FanxiuRuntimeMemoryError("MapActAutoTipsView.SureBtn 未加载")
    if table_ref(field(panel_address, "selectBtn6")) is None:
        raise FanxiuRuntimeMemoryError("MapActAutoTipsView.selectBtn6 未加载")

    selected = _decode_type_list(
        reader,
        field(panel_address, "selectList"),
        field_name="selectList",
        allowed=_PRIMARY_ALLOWED,
    )
    fourfold = _decode_type_list(
        reader,
        field(panel_address, "selectedFourTimesList"),
        field_name="selectedFourTimesList",
        allowed=_AUGMENT_ALLOWED,
    )
    pursuit = _decode_type_list(
        reader,
        field(panel_address, "selectedSpecialList"),
        field_name="selectedSpecialList",
        allowed=_AUGMENT_ALLOWED,
    )
    auto_times = as_int(field(panel_address, "autoTimes"))
    use_num = as_int(field(panel_address, "useNum"))
    use_max = as_int(field(panel_address, "useMax"))
    if auto_times is None or auto_times < 0:
        raise FanxiuRuntimeMemoryError(
            f"MapActAutoTipsView.autoTimes 无效：{auto_times!r}",
            code="runtime_incomplete",
        )
    if use_num is None or use_num < 0 or use_max is None or use_max < 0:
        raise FanxiuRuntimeMemoryError(
            "MapActAutoTipsView 次数边界未完整加载",
            code="runtime_incomplete",
        )
    if auto_times != use_num or use_num > use_max:
        raise FanxiuRuntimeMemoryError(
            "MapActAutoTipsView 次数状态不一致",
            code="runtime_incomplete",
        )

    qualities = {
        str(type_id): {
            "type_id": type_id,
            "name": name,
            "selected": type_id in selected,
            "fourfold_merit": type_id in fourfold,
            "pursuit_chain": type_id in pursuit,
            "augment_configurable": type_id in _AUGMENT_ALLOWED,
        }
        for type_id, name in QUALITY_NAMES.items()
    }
    options = {
        "auto_use_demon_token": _AUTO_USE_DEMON_TOKEN in selected,
        "ten_banish": _TEN_BANISH in selected,
        "skip_animation": _SKIP_ANIMATION in selected,
        "quick_banish": _QUICK_BANISH in selected,
    }
    close_latched = field(panel_address, "isClickClose")
    return {
        # Native closeClick ignores subsequent taps after latching and closes
        # only from its 0.65s tween callback. Preserve unknown as unknown.
        "exit": {"close_control_loaded": table_ref(field(panel_address, "closeClick")) is not None,
                 "close_latched": close_latched if type(close_latched) is bool else None},
        "qualities": qualities,
        "options": options,
        # Stable semantic projection consumed by the idempotent GUI planner.
        "auto_exorcism_choices": {
            "quality_hall_master": qualities["1"]["selected"],
            "quality_elder": qualities["2"]["selected"],
            "elder_quadruple_merit": qualities["2"]["fourfold_merit"],
            "elder_chase_chain": qualities["2"]["pursuit_chain"],
            "quality_sect_master": qualities["3"]["selected"],
            "sect_master_quadruple_merit": qualities["3"]["fourfold_merit"],
            "sect_master_chase_chain": qualities["3"]["pursuit_chain"],
            "quality_supreme_elder": qualities["4"]["selected"],
            "supreme_elder_quadruple_merit": qualities["4"][
                "fourfold_merit"
            ],
            "supreme_elder_chase_chain": qualities["4"]["pursuit_chain"],
            "quality_extraterrestrial_demon": qualities["5"]["selected"],
            "extraterrestrial_demon_quadruple_merit": qualities["5"][
                "fourfold_merit"
            ],
            "extraterrestrial_demon_chase_chain": qualities["5"][
                "pursuit_chain"
            ],
            "quality_saint_master": qualities["11"]["selected"],
            "saint_master_quadruple_merit": qualities["11"][
                "fourfold_merit"
            ],
            "saint_master_chase_chain": qualities["11"]["pursuit_chain"],
            "auto_use_exorcism_order": options["auto_use_demon_token"],
            "skip_animation": options["skip_animation"],
            "fast_exorcism": options["quick_banish"],
            "tenfold_exorcism": options["ten_banish"],
        },
        "times": {
            "selected": int(auto_times),
            "maximum": int(use_max),
        },
        "hidden_targets": {
            str(type_id): {
                "type_id": type_id,
                "name": name,
                "selected": type_id in selected,
                "fourfold_merit": type_id in fourfold,
                "pursuit_chain": type_id in pursuit,
            }
            for type_id, name in _HIDDEN_TARGET_NAMES.items()
        },
        "raw_type_ids": {
            "selected": sorted(selected),
            "fourfold_merit": sorted(fourfold),
            "pursuit_chain": sorted(pursuit),
        },
    }


def _active_settings_panel(context: UiRuntimeContext) -> int:
    def field(address: int, name: str) -> Any:
        return read_ui_object_field(context, address, name)

    panel_addresses = [
        component.address
        for component in active_ui_component_objects(context)
        if table_ref(field(component.address, "SliderNum")) is not None
        and table_ref(field(component.address, "SureBtn")) is not None
        and table_ref(field(component.address, "selectBtn6")) is not None
    ]
    if not panel_addresses:
        raise FanxiuRuntimeMemoryError(
            "NotLoaded: 当前未打开魔道入侵自动除魔设置弹层",
            code="not_loaded",
        )
    if len(panel_addresses) != 1:
        raise FanxiuRuntimeMemoryError(
            f"Ambiguous: 同时发现 {len(panel_addresses)} 个自动除魔设置弹层",
            code="runtime_incomplete",
        )
    return panel_addresses[0]


def _snapshot(context: UiRuntimeContext) -> dict[str, Any]:
    panel_address = _active_settings_panel(context)
    def field(address: int, name: str) -> Any:
        return read_ui_object_field(context, address, name)
    decoded = _decode_settings_panel(
        context.reader,
        panel_address,
        field=field,
    )
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "source": "active_map_act_auto_tips_view",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "captured_at_epoch": time.time(),
        **decoded,
        "performance": {
            "cache_mode": context.cache_mode,
            "stages_seconds": dict(context.timings),
        },
        "evidence": {
            "pid": context.memory.pid,
            "process_start_ticks": context.memory.process_start_ticks,
            # The active component address is the read-only identity of this
            # exact panel instance.  Callers use it only to reject a
            # before/after pair that crossed a close/reopen boundary.
            "panel_address": int(panel_address),
            "active_membership": "UIShowMgr.V_M_compDic",
            "read_only": True,
            "lua_methods_invoked": False,
        },
    }


def read_magic_invasion_auto_exit_snapshot() -> dict[str, Any]:
    """Read only active panel identity and close latch, without decoding choices.

    This observes the present instance, never reconstructs an earlier click or
    proves its hit region/tween callback. It invokes no Lua method or GUI action.
    Transient reads follow the shared bounded Runtime retry contract.
    """
    started = time.perf_counter()
    def project(context: UiRuntimeContext) -> dict[str, Any]:
        address = _active_settings_panel(context)
        latched = read_ui_object_field(context, address, "isClickClose")
        return {
            "ok": True, "available": True,
            "captured_at_epoch": time.time(),
            "exit": {
                "close_control_loaded": table_ref(read_ui_object_field(context, address, "closeClick")) is not None,
                "close_latched": latched if type(latched) is bool else None,
            },
            "evidence": {"pid": context.memory.pid,
                         "process_start_ticks": context.memory.process_start_ticks,
                         "panel_address": address, "read_only": True,
                         "lua_methods_invoked": False},
        }
    try:
        result = read_ui_runtime_snapshot(_UI_KEYS, project, fast=True)
    except FanxiuRuntimeMemoryError as exc:
        result = {"ok": False, "available": False, "error_code": exc.code,
                  "reason": str(exc), "captured_at_epoch": time.time(),
                  "evidence": {"active_membership": "UIShowMgr.V_M_compDic",
                               "read_only": True, "lua_methods_invoked": False}}
    result["elapsed_seconds"] = time.perf_counter() - started
    return result


def read_magic_invasion_auto_settings_snapshot() -> dict[str, Any]:
    """Read unsaved values shown by the active auto-banish settings panel."""

    started = time.perf_counter()
    try:
        result = read_ui_runtime_snapshot(_UI_KEYS, _snapshot, fast=True)
        result["elapsed_seconds"] = time.perf_counter() - started
        return result
    except FanxiuRuntimeMemoryError as exc:
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "active_map_act_auto_tips_view",
            "reason": str(exc),
            "error_code": exc.code,
            "qualities": {},
            "options": {},
            "times": {},
            "elapsed_seconds": time.perf_counter() - started,
            "evidence": {
                "active_membership": "UIShowMgr.V_M_compDic",
                "read_only": True,
                "lua_methods_invoked": False,
            },
        }


def read_magic_invasion_auto_count_snapshot(*, expected_binding: Mapping[str, int]) -> dict[str, Any]:
    """Fresh count-only projection bound to a previously guarded settings panel.

    No options, reward lists or exit state are decoded. Each observation still
    proves the complete active closure and unique panel, process identity and
    unchanged maximum. This query never writes, clicks or retains child bytes.
    Performance partitions cover all projection attempts; context diagnostics
    are nested and must not be added to these partitions or caller tick time.
    """
    names = ('pid', 'process_start_ticks', 'panel_address', 'maximum')
    if not isinstance(expected_binding, Mapping) or any(
        type(expected_binding.get(k)) is not int or expected_binding[k] <= 0 for k in names
    ):
        raise ValueError('完整正整数次数绑定必需')
    expected = {k: expected_binding[k] for k in names}
    started = time.perf_counter()
    phases = {'active_closure': 0.0, 'unique_panel': 0.0, 'count_identity': 0.0}
    attempts = 0

    def project(ctx):
        nonlocal attempts
        attempts += 1
        phase_start = time.perf_counter()
        try:
            components = active_ui_component_objects(ctx)
        finally:
            phases['active_closure'] += time.perf_counter() - phase_start
        phase_start = time.perf_counter()
        try:
            panels = [c.address for c in components
                      if table_ref(read_ui_object_field(ctx, c.address, 'SliderNum')) is not None
                      and table_ref(read_ui_object_field(ctx, c.address, 'SureBtn')) is not None
                      and table_ref(read_ui_object_field(ctx, c.address, 'selectBtn6')) is not None]
            if not panels:
                raise FanxiuRuntimeMemoryError('NotLoaded: 次数设置弹层不在当前active集合', code='not_loaded')
            if len(panels) != 1:
                raise FanxiuRuntimeMemoryError('Ambiguous: 多个次数设置弹层', code='runtime_incomplete')
        finally:
            phases['unique_panel'] += time.perf_counter() - phase_start
        phase_start = time.perf_counter()
        try:
            address = panels[0]
            identity = {'pid': ctx.memory.pid, 'process_start_ticks': ctx.memory.process_start_ticks,
                        'panel_address': address}
            if any(identity[k] != expected[k] for k in identity):
                raise FanxiuRuntimeMemoryError('次数设置弹层身份变化', code='identity_mismatch')
            raw = [as_int(read_ui_object_field(ctx, address, key)) for key in ('autoTimes', 'useNum', 'useMax')]
            if any(n is None or n < 0 for n in raw) or raw[0] != raw[1] or raw[1] > raw[2]:
                raise FanxiuRuntimeMemoryError('次数字段无效或状态不一致', code='runtime_incomplete')
            if raw[2] != expected['maximum']:
                raise FanxiuRuntimeMemoryError('次数上限变化', code='identity_mismatch')
            return {'ok': True, 'available': True, 'complete': True,
                    'source': 'active_map_act_auto_tips_view', 'captured_at_epoch': time.time(),
                    'times': {'selected': raw[0], 'maximum': raw[2]},
                    'evidence': {**identity, 'active_membership': 'UIShowMgr.V_M_compDic',
                                 'read_only': True, 'lua_methods_invoked': False}}
        finally:
            phases['count_identity'] += time.perf_counter() - phase_start

    try:
        result = read_ui_runtime_snapshot(
            frozenset({'SliderNum', 'SureBtn', 'selectBtn6', 'autoTimes', 'useNum', 'useMax'}),
            project, fast=True)
    except FanxiuRuntimeMemoryError as exc:
        result = {'ok': False, 'available': False, 'complete': False,
                  'source': 'active_map_act_auto_tips_view', 'error_code': exc.code, 'reason': str(exc)}
    except BaseException as exc:
        # Preserve partial phase measurements on interruption without turning
        # a read-only query into a persistence operation or swallowing control.
        elapsed = time.perf_counter() - started
        exc.magic_auto_count_performance = {
            'projection_attempts': attempts, 'wall_seconds': elapsed,
            'non_overlapping_seconds': {**phases, 'acquisition_and_recovery': max(0.0, elapsed - sum(phases.values()))}}
        raise
    elapsed = time.perf_counter() - started
    result['elapsed_seconds'] = elapsed
    result['performance'] = {'projection_attempts': attempts,
                             'non_overlapping_seconds': {**phases, 'acquisition_and_recovery': max(0.0, elapsed - sum(phases.values()))},
                             'wall_seconds': elapsed}
    return result


def magic_invasion_auto_settings_entry_admission(*, end_time_ms: int, current_time_ms: int) -> dict[str, Any]:
    """Pure client settings-entry gate, separate from challenge-start policy.

    MapMgr/MagicinvadeMgr refuse a NEW settings entry only when endTime minus
    server time is strictly less than 1,800,000 ms. Exact equality is admitted.
    Callers must obtain the exact current occurrence from fresh Runtime and
    supply an explicit current clock; this function does not resolve/caches IDs
    or authorize consumption. Existing settings observations and pending-batch
    readback do not require a new settings entry and must not use this gate.
    """
    if any(type(v) is not int or v <= 0 for v in (end_time_ms, current_time_ms)):
        raise ValueError('结束时间和当前时间必须为正整数毫秒')
    remaining = end_time_ms - current_time_ms
    allowed = remaining >= 1_800_000
    return {'allowed': allowed, 'reason': 'settings_entry_open' if allowed else 'settings_entry_window_closed',
            'remaining_ms': remaining, 'minimum_remaining_ms': 1_800_000,
            'end_time_ms': end_time_ms, 'current_time_ms': current_time_ms,
            'consumption_authorized': False}


def project_magic_invasion_entry_clock(*, clock: Mapping[str, Any], expected_binding: Mapping[str, Any],
                                      now_monotonic: float) -> dict[str, Any]:
    """Validate fresh server interval against the Runtime device/process binding.

    Upper bound advances conservatively for latency since collection. Lower is
    retained so start-boundary uncertainty cannot admit a not-yet-open activity.
    Historical persisted intervals and host clocks are never admissible inputs.
    """
    rejected = {'available': False, 'allowed': False, 'consumption_authorized': False}
    if clock.get('current_clock_complete') is not True or clock.get('clock_source') != 'device_wall_clock_minus_loaded_sync_offset':
        return {**rejected, 'reason': 'server_clock_unavailable', 'clock_reason': clock.get('reason')}
    identity = clock.get('evidence') or {}
    for key in ('pid', 'process_start_ticks', 'adb_serial'):
        expected = expected_binding.get(key)
        if not expected or expected != identity.get(key):
            return {**rejected, 'reason': 'server_clock_identity_mismatch'}
    valid_at = clock.get('valid_at_monotonic')
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (valid_at, now_monotonic)):
        return {**rejected, 'reason': 'server_clock_timing_missing'}
    age = now_monotonic - valid_at
    if age < 0 or age > 2.0:
        return {**rejected, 'reason': 'server_clock_stale', 'clock_age_seconds': age}
    lower, upper = clock.get('server_time_lower_ms'), clock.get('server_time_upper_ms')
    if any(type(v) is not int or v <= 0 for v in (lower, upper)) or upper < lower or clock.get('uncertainty_ms') != upper - lower:
        return {**rejected, 'reason': 'server_clock_interval_invalid'}
    upper += math.ceil(age * 1000)
    if upper - lower > 5000:
        return {**rejected, 'reason': 'server_clock_uncertainty_exceeded'}
    return {'available': True, 'server_time_lower_ms': lower, 'server_time_upper_ms': upper,
            'uncertainty_ms': upper - lower, 'clock_age_seconds': age, 'clock_source': clock['clock_source'],
            'clock_evidence': dict(identity), 'consumption_authorized': False}


def read_magic_invasion_auto_settings_entry_admission(*, expected_instance_key: str | None = None) -> dict[str, Any]:
    """Read entry admission; preserve phase timing on the original exception too.

    Diagnostics do not turn a rejected query into an allowed entry or retry it.
    A failed phase is included in elapsed wall time, while completed partitions
    remain separately identifiable for first-fault performance investigation.
    """
    started = time.monotonic()
    stages: dict[str, float] = {}
    try:
        return _read_magic_entry_admission(expected_instance_key=expected_instance_key,
                                           started=started, stages=stages)
    except Exception as exc:
        exc.magic_entry_performance = {"elapsed_seconds": time.monotonic() - started,
                                       "completed_stages_seconds": dict(stages)}
        raise


def _read_magic_entry_admission(*, expected_instance_key, started, stages):
    """Resolve exact fresh Runtime occurrence using a bound server-time interval.

    The schedule supplies process generation; the fresh UI context supplies its
    device binding. Both must match the clock's independently collected identity.
    Collect clock last and check monotonic age after discovery/projection. Start
    requires lower >= start; closing/30-minute policy uses the upper bound. Any
    missing, mismatched or stale clock explicitly rejects without host fallback.
    Existing #698 observations bypass this NEW-entry gate at the caller.
    """
    from backend.core.fanxiu.activity.runtime_schedule import read_fanxiu_activity_runtime_schedule
    from backend.core.fanxiu.activity.ranking_lifecycle import discover_ranking_occurrences
    from backend.core.fanxiu.instrumentation.server_clock import read_current_server_clock_snapshot
    schedule = read_fanxiu_activity_runtime_schedule(allow_discovery=True, force_refresh=True)
    schedule_done = time.monotonic()
    stages['runtime_schedule'] = schedule_done - started
    if not (schedule.get('available') is True and schedule.get('complete') is True):
        raise FanxiuRuntimeMemoryError('设置入口需要完整新鲜Runtime日程', code='runtime_incomplete')
    binding = acquire_ui_runtime_context_fast(frozenset()).binding
    stages['runtime_binding'] = time.monotonic() - schedule_done
    expected = {'pid': binding.pid, 'process_start_ticks': binding.process_start_ticks, 'adb_serial': binding.adb_serial}
    schedule_identity = schedule.get('evidence') or {}
    if any(not schedule_identity.get(k) or schedule_identity[k] != expected[k] for k in ('pid', 'process_start_ticks')):
        raise FanxiuRuntimeMemoryError('设置入口日程与设备Runtime身份不一致', code='identity_mismatch')
    binding_done = time.monotonic()
    clock = read_current_server_clock_snapshot()
    clock_done = time.monotonic()
    stages['server_clock'] = clock_done - binding_done
    occurrences = discover_ranking_occurrences(schedule)
    checked_at = time.monotonic()
    stages['occurrence_projection'] = checked_at - clock_done
    interval = project_magic_invasion_entry_clock(clock=clock, expected_binding=expected, now_monotonic=checked_at)
    performance = {'elapsed_seconds': checked_at - started,
                   'stages_seconds': {'runtime_schedule': schedule_done - started, 'runtime_binding': binding_done - schedule_done,
                                      'server_clock': clock_done - binding_done, 'occurrence_projection': checked_at - clock_done}}
    if not interval['available']:
        return {**interval, 'performance': performance}
    lower, upper = interval['server_time_lower_ms'], interval['server_time_upper_ms']
    matches = [o for o in occurrences if o.activity_type == 'magic-invasion' and o.family == 'gameplay_rank'
               and int(o.start_at.timestamp() * 1000) <= upper and lower <= int(o.end_at.timestamp() * 1000)]
    if not matches:
        raise FanxiuRuntimeMemoryError('当前完整Runtime日程无有效魔道实例', code='activity_unavailable')
    if len(matches) > 1:
        raise FanxiuRuntimeMemoryError('当前魔道实例身份存在歧义', code='identity_mismatch')
    occurrence = matches[0]
    if expected_instance_key is not None and occurrence.instance_key != expected_instance_key:
        raise FanxiuRuntimeMemoryError('设置入口当前魔道实例已变化', code='identity_mismatch')
    start_ms, end_ms = int(occurrence.start_at.timestamp() * 1000), int(occurrence.end_at.timestamp() * 1000)
    decision = magic_invasion_auto_settings_entry_admission(end_time_ms=end_ms, current_time_ms=upper)
    if lower < start_ms:
        decision.update(allowed=False, reason='settings_entry_start_boundary_uncertain')
    return {**decision, **interval, 'instance_key': occurrence.instance_key, 'runtime_id': occurrence.runtime_id,
            'runtime_captured_at': schedule.get('captured_at'), 'performance': performance}


__all__ = [
    "QUALITY_NAMES",
    "read_magic_invasion_auto_settings_snapshot",
    "read_magic_invasion_auto_count_snapshot",
    "magic_invasion_auto_settings_entry_admission",
    "read_magic_invasion_auto_settings_entry_admission",
    "project_magic_invasion_entry_clock",
]
