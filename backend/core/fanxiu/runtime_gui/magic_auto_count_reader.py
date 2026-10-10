"""Optional Runtime feedback for the existing integer slider controller.

No GUI, Kernel, scheduling, retry or consumption is implemented here. The
controller still owns fresh visible-scene guards, two stable samples, exact
targets, action budgets and OCR fallback. Integration is deliberately separate.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import math
import time
from typing import Any


class MagicAutoCountPanelRejected(Exception):
    """Hard stop for identity, ambiguity or invalid/stale observation evidence.

    Deliberately not RuntimeError: the existing controller catches that class
    for OCR fallback. The same scene does not prove the same panel instance.
    """


class MagicAutoCountObservationUnavailable(RuntimeError):
    """A transient projection failure permits the controller's guarded OCR."""


@dataclass(frozen=True)
class MagicAutoCountBinding:
    pid: int
    process_start_ticks: int
    panel_address: int
    maximum: int


def _positive_int(value: Any, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise MagicAutoCountPanelRejected(f"魔道次数字段无效：{field}={value!r}")
    return value


def validate_magic_auto_count_snapshot(
    snapshot: Mapping[str, Any], *, now: float,
    expected: MagicAutoCountBinding | None = None, max_age_seconds: float = 15.0,
) -> tuple[MagicAutoCountBinding, int]:
    """Pure projection of one fresh, authoritative active-panel observation.

    The existing settings provider proves autoTimes == useNum <= useMax before
    publishing complete=True. Its selected/maximum projection is consumed here;
    no second native decoder or invented raw field is used. A selected value
    need not equal the target during reconciliation; the controller proves that.
    """
    if not math.isfinite(now) or not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
        raise ValueError("观测时间和新鲜度预算必须有限且预算为正")
    if not isinstance(snapshot, Mapping) or snapshot.get('source') != 'active_map_act_auto_tips_view':
        raise MagicAutoCountPanelRejected("魔道次数反馈来源不是active设置面板")
    if snapshot.get('complete') is not True:
        if expected is not None and snapshot.get('error_code') in {
            'memory_address_unmapped', 'memory_read_failed',
        }:
            # Return no cached count. The existing controller may use fresh OCR
            # only after its own visible-scene guard. runtime_incomplete may
            # mean multiple active panels or autoTimes != useNum: fail closed.
            raise MagicAutoCountObservationUnavailable(str(snapshot.get('reason') or '数量观测暂不可用'))
        raise MagicAutoCountPanelRejected("魔道次数反馈缺少完整active面板事实")
    if snapshot.get('ok') is not True or snapshot.get('available') is not True:
        raise MagicAutoCountPanelRejected("魔道次数反馈状态不完整")
    evidence = snapshot.get('evidence')
    if not isinstance(evidence, Mapping) or (
        evidence.get('active_membership') != 'UIShowMgr.V_M_compDic'
        or evidence.get('read_only') is not True
        or evidence.get('lua_methods_invoked') is not False
    ):
        raise MagicAutoCountPanelRejected("魔道次数反馈缺少active成员/只读证据")
    times = snapshot.get('times')
    if not isinstance(times, Mapping):
        raise MagicAutoCountPanelRejected("魔道次数反馈缺少times")
    binding = MagicAutoCountBinding(
        _positive_int(evidence.get('pid'), 'pid'),
        _positive_int(evidence.get('process_start_ticks'), 'process_start_ticks'),
        _positive_int(evidence.get('panel_address'), 'panel_address'),
        _positive_int(times.get('maximum'), 'maximum'),
    )
    count = _positive_int(times.get('selected'), 'selected')
    if count > binding.maximum or (expected is not None and binding != expected):
        raise MagicAutoCountPanelRejected("魔道次数越界或active面板身份/上限已变化")
    captured = snapshot.get('captured_at_epoch')
    if type(captured) not in (int, float) or not math.isfinite(captured) or not 0 <= now - captured <= max_age_seconds:
        raise MagicAutoCountPanelRejected("魔道次数反馈过期或观测时间无效")
    return binding, count


def build_magic_auto_runtime_count_reader(
    baseline: Mapping[str, Any], *, snapshot_reader: Callable[[], Mapping[str, Any]] | None = None,
    clock: Callable[[], float] = time.time, max_age_seconds: float = 15.0,
) -> Callable[[], dict[str, int]]:
    """Bind once after a visible guard; each callback obtains a NEW snapshot.

    Pass the callback as runtime_count_reader to the existing slider controller.
    A close/reopen, process change, maximum change, malformed count or reused
    observation hard-stops. Only structured temporary memory observation
    errors permit the controller's guarded fresh OCR fallback.
    No snapshot or quantity is retained as future feedback. Provider errors
    outside its structured contract propagate rather than guessing identity.
    """
    binding, _ = validate_magic_auto_count_snapshot(
        baseline, now=clock(), max_age_seconds=max_age_seconds)
    last_captured = baseline['captured_at_epoch']
    if snapshot_reader is None:
        from backend.core.fanxiu.instrumentation.magic_invasion_auto_settings import (
            read_magic_invasion_auto_settings_snapshot,
        )
        snapshot_reader = read_magic_invasion_auto_settings_snapshot

    def read_count() -> dict[str, int]:
        nonlocal last_captured
        observation = snapshot_reader()
        _, count = validate_magic_auto_count_snapshot(
            observation, now=clock(), expected=binding, max_age_seconds=max_age_seconds)
        captured = observation['captured_at_epoch']
        if captured <= last_captured:
            raise MagicAutoCountPanelRejected("魔道次数反馈不是新的递增观测，禁止缓存快照冒充稳定采样")
        last_captured = captured
        return {'current': count, 'maximum': binding.maximum}

    return read_count
