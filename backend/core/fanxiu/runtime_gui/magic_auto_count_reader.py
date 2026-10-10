"""Optional Runtime feedback for the existing integer slider controller.

No GUI, Kernel, scheduling, retry or consumption is implemented here. The
controller still owns fresh visible-scene guards, two stable samples, exact
targets, action budgets and OCR fallback. Integration is deliberately separate.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
import math
import time
import uuid
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
    stats_recorder: Callable[[Mapping[str, Any]], None] | None = None,
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
    from backend.core.fanxiu.instrumentation.magic_invasion_auto_settings import (
        read_magic_invasion_auto_settings_snapshot, read_magic_invasion_auto_count_snapshot,
    )
    # Existing formal assembly passes the canonical full settings reader.
    # Select the provider's bound count projection here, without changing GUI
    # ownership. Explicit custom readers retain their deterministic contract.
    if snapshot_reader is None or snapshot_reader is read_magic_invasion_auto_settings_snapshot:
        snapshot_reader = lambda: read_magic_invasion_auto_count_snapshot(expected_binding=asdict(binding))
    recorder = stats_recorder or persist_magic_auto_count_feedback
    from backend.core.fanxiu.data_annotation.subtask_execution import current_job_log_context
    stats = {'reader_id': uuid.uuid4().hex, 'binding': asdict(binding),
             'job_context': current_job_log_context(),
             'measurement_scope': 'query+validation wall excludes telemetry writes and caller scene guards; provider partitions nest within query',
             'started': 0, 'succeeded': 0, 'failed': 0, 'fallback_eligible': 0,
             'interrupted': 0, 'events': [],
             'completed_query_timing': {'n': 0, 'sum_seconds': 0.0, 'sum_squares': 0.0}}

    def publish(event):
        # Persist before querying and after each result, not only after the
        # controller finishes. A hard process loss leaves an explicit started
        # event; KeyboardInterrupt is recorded and re-raised unchanged.
        stats['events'].append(event)
        stats['events'] = stats['events'][-64:]
        try:
            recorder(stats)
        except Exception:
            _log_feedback_failure('Magic count feedback recorder failed')

    def retain_query_timing(seconds):
        if seconds is None:
            return
        timing = stats['completed_query_timing']
        timing['n'] += 1
        timing['sum_seconds'] += seconds
        timing['sum_squares'] += seconds * seconds
        mean = timing['sum_seconds'] / timing['n']
        timing['mean_seconds'] = mean
        timing['population_std_seconds'] = math.sqrt(max(0.0, timing['sum_squares'] / timing['n'] - mean * mean))

    def read_count() -> dict[str, int]:
        nonlocal last_captured
        stats['started'] += 1
        seq = stats['started']
        publish({'sequence': seq, 'status': 'started', 'at_epoch': clock()})
        started = time.perf_counter()
        observation = None
        query_seconds = None
        try:
            observation = snapshot_reader()
            query_seconds = time.perf_counter() - started
            retain_query_timing(query_seconds)
            _, count = validate_magic_auto_count_snapshot(
                observation, now=clock(), expected=binding, max_age_seconds=max_age_seconds)
            captured = observation['captured_at_epoch']
            if captured <= last_captured:
                raise MagicAutoCountPanelRejected("魔道次数反馈不是新的递增观测，禁止缓存快照冒充稳定采样")
            last_captured = captured
        except BaseException as exc:
            stats['failed'] += 1
            fallback = isinstance(exc, MagicAutoCountObservationUnavailable)
            stats['fallback_eligible'] += int(fallback)
            stats['interrupted'] += int(isinstance(exc, (KeyboardInterrupt, SystemExit)))
            publish({'sequence': seq, 'status': 'fallback_eligible' if fallback else 'failed',
                     'error_type': type(exc).__name__, 'wall_seconds': time.perf_counter() - started,
                     'query_seconds': query_seconds,
                     'provider_performance': observation.get('performance') if isinstance(observation, Mapping)
                     else getattr(exc, 'magic_auto_count_performance', None)})
            raise
        stats['succeeded'] += 1
        publish({'sequence': seq, 'status': 'succeeded', 'current': count,
                 'query_seconds': query_seconds, 'wall_seconds': time.perf_counter() - started,
                 'provider_performance': observation.get('performance')})
        return {'current': count, 'maximum': binding.maximum}

    return read_count


def _log_feedback_failure(message: str) -> None:
    """Logging is secondary too: never replace a count or its original error."""
    try:
        import logging
        logging.getLogger(__name__).exception(message)
    except Exception:
        # A broken file/transport handler cannot authorize retries or discard
        # an otherwise valid fresh observation. Do not recursively log it.
        pass


def persist_magic_auto_count_feedback(stats: Mapping[str, Any]) -> None:
    """Provider-owned durable telemetry; never submits a Cell or sends a message.

    fallback_eligible counts Runtime failures allowing fresh OCR, not observed
    OCR success: that remains the unchanged controller's responsibility.
    Per-reader keys preserve interrupted/replaced readers; latest is a pointer.
    Telemetry failure is explicit and must not alter an observation exception.
    """
    from backend.core.messaging.codex_results import result_store
    try:
        store = result_store()
        key = 'fanxiu_magic_count_feedback:' + str(stats['reader_id'])
        store.set_meta(key, dict(stats))
        store.set_meta('fanxiu_magic_count_feedback_latest', {'meta_key': key})
    except Exception:
        _log_feedback_failure('Magic count feedback persistence failed')


def read_magic_auto_count_feedback(*, reader_id: str | None = None) -> dict[str, Any]:
    """Read durable callback statistics, including interrupted readers.

    No Runtime/game access. Use the exact reader ID for historical acceptance;
    the latest pointer may already belong to a later attempt. Phase partitions
    are nested within query wall time, never additive to callback/tick walls.
    """
    from backend.core.messaging.codex_results import result_store
    store = result_store()
    if reader_id is not None:
        if not isinstance(reader_id, str) or len(reader_id) != 32 or any(c not in '0123456789abcdef' for c in reader_id):
            raise ValueError('reader_id must be a lowercase UUID hex')
        key = 'fanxiu_magic_count_feedback:' + reader_id
    else:
        key = (store.get_meta('fanxiu_magic_count_feedback_latest') or {}).get('meta_key')
    value = store.get_meta(key) if key else None
    return {'found': value is not None, 'statistics': value}
