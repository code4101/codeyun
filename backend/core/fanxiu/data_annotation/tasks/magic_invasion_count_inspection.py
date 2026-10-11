"""Real settings/count performance inspection, without starting auto-exorcism."""
from statistics import mean, pstdev
import logging
import time
import uuid

from backend.core.fanxiu.instrumentation.magic_invasion_auto_settings import (
    read_magic_invasion_auto_settings_entry_admission,
    read_magic_invasion_auto_settings_snapshot,
    read_magic_invasion_auto_count_snapshot,
)
from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError
from backend.core.fanxiu.runtime_gui.magic_auto_count_reader import (
    build_magic_auto_runtime_count_reader, validate_magic_auto_count_snapshot,
)
from .magic_invasion_native_auto import open_magic_invasion_auto_settings


def execute_magic_invasion_count_inspection(runner, ctx, payload, stop_event):
    """Measure native reads through the normal entry, never save/start/consume.

    The Cell result retains every observation and independent timing groups.
    Initial/reopened full bindings are separate from count-only feedback; GUI
    navigation and telemetry writes are excluded. An unavailable occurrence
    returns an explicit unaccepted result before obtaining a game context.
    Closing a panel must invalidate its count projection before reopening.
    Every reopened cycle binds freshly within the original occurrence.
    """
    evidence = {'inspection_id': uuid.uuid4().hex, 'accepted': False,
                'samples': [], 'timings': {'initial_window_reader_binding': [],
                                         'continuous_count': [], 'reopened_reader_binding': []},
                'consumption_performed': False, 'settings_changed': False,
                'game_actions_performed': False}
    try:
        result = yield from _inspect(runner, ctx, payload, stop_event, evidence)
        evidence.update(result)
        return evidence
    except BaseException as exc:
        evidence.update(outcome='inspection_failed', accepted=False,
                        error_type=type(exc).__name__, reason=str(exc))
        evidence['entry_performance'] = getattr(exc, 'magic_entry_performance', None)
        if evidence.get('query_started') is not None:
            evidence['incomplete_query_seconds'] = time.perf_counter() - evidence['query_started']
        exc.magic_count_inspection = evidence
        raise
    finally:
        # Persist partial observations with the original failure, as the tail
        # inspector does. Diagnostic writes never change the Cell's first error.
        try:
            from ..kernel_scheduler_control import record_world_discovery
            record_world_discovery('magic-count-inspection:' + evidence['inspection_id'], evidence)
        except Exception as diagnostic_error:
            evidence['diagnostic_write_error_type'] = type(diagnostic_error).__name__
            try:
                logging.getLogger(__name__).exception('Magic count inspection evidence write failed')
            except Exception:
                pass  # A failing logging transport cannot replace the first fault.


def _inspect(runner, ctx, payload, stop_event, evidence):
    cycles = payload.get('cycles', 3)
    samples = payload.get('samples_per_cycle', 5)
    if type(cycles) is not int or not 2 <= cycles <= 5:
        raise ValueError('cycles requires 2..5')
    if type(samples) is not int or not 2 <= samples <= 10:
        raise ValueError('samples_per_cycle requires 2..10')
    try:
        admission = read_magic_invasion_auto_settings_entry_admission()
    except FanxiuRuntimeMemoryError as exc:
        if exc.code != 'activity_unavailable':
            raise
        return {'outcome': 'awaiting_activity', 'accepted': False,
                'samples': [], 'error_code': exc.code, 'reason': str(exc),
                'entry_performance': getattr(exc, 'magic_entry_performance', None),
                'consumption_performed': False, 'game_actions_performed': False}
    if not admission['allowed']:
        return {'outcome': 'entry_not_admitted', 'accepted': False,
                'samples': [], 'admission': admission,
                'consumption_performed': False, 'game_actions_performed': False}
    context = runner._behavior_tree_context(ctx, stop_event=stop_event)
    evidence['instance_key'] = admission['instance_key']
    observations = evidence['samples']
    timings = evidence['timings']
    for cycle in range(cycles):
        if stop_event.is_set():
            raise InterruptedError()
        guard = read_magic_invasion_auto_settings_entry_admission(
            expected_instance_key=admission['instance_key'])
        if not guard['allowed']:
            raise RuntimeError('次数性能验收入口窗口已关闭，保留已完成样本')
        evidence.update(stage='open_settings', game_actions_performed=True)
        yield from open_magic_invasion_auto_settings(context)
        binding = None
        baselines = []
        counts = []
        feedback = {}
        observation = {'cycle': cycle, 'baselines': baselines, 'counts': counts,
                       'feedback': feedback, 'complete': False}
        observations.append(observation)
        for _ in range(samples):
            if stop_event.is_set():
                raise InterruptedError()
            started = time.perf_counter()
            evidence.update(stage='bind_reader', query_started=started)
            baseline = read_magic_invasion_auto_settings_snapshot()
            baselines.append(baseline)
            binding, count = validate_magic_auto_count_snapshot(
                baseline, now=time.time(), expected=binding)
            # Each baseline is a fresh native query and creates an independent
            # production reader lifecycle. Shared provider caches stay intact;
            # these are initial-window/reopened bindings, not cold-process times.
            read_count = build_magic_auto_runtime_count_reader(baseline, stats_recorder=feedback.update)
            timings['initial_window_reader_binding' if cycle == 0 else 'reopened_reader_binding'].append(time.perf_counter() - started)
            evidence['query_started'] = None
        for _ in range(samples):
            if stop_event.is_set():
                raise InterruptedError()
            evidence.update(stage='read_count', query_started=time.perf_counter())
            counts.append(read_count())
            timings['continuous_count'].append(feedback['events'][-1]['wall_seconds'])
            evidence['query_started'] = None
        # Navigation owns the annotated close route. No count, option, confirm,
        # save or native-start Shape is clicked anywhere in this component.
        evidence['stage'] = 'close_settings'
        yield from context.go_scene(34)
        evidence.update(stage='validate_closed_panel', query_started=time.perf_counter())
        closed = read_magic_invasion_auto_count_snapshot(expected_binding={
            'pid': binding.pid, 'process_start_ticks': binding.process_start_ticks,
            'panel_address': binding.panel_address, 'maximum': binding.maximum})
        observation['closed_projection'] = closed
        if closed.get('complete') or closed.get('error_code') != 'not_loaded':
            raise RuntimeError('关闭设置后的次数面板失效未被唯一确认')
        evidence['query_started'] = None
        observation.update(baseline_count=count, complete=True)
    return {'outcome': 'inspection_complete', 'accepted': True,
            'instance_key': admission['instance_key'], 'samples': observations,
            'timing_scope': 'query+validation wall; excludes GUI navigation and telemetry writes; provider partitions are nested',
            'timing_summary': {key: {'n': len(values), 'mean_seconds': mean(values),
                             'population_std_seconds': pstdev(values), 'raw_seconds': values}
                        for key, values in timings.items()},
            'consumption_performed': False, 'settings_changed': False,
            'game_actions_performed': True, 'returned_scene': 34}
