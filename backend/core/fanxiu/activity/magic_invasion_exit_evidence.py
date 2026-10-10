"""Non-intervening #698 close observations under exact formal attempt identity.

Queued Runtime reads can occur after the click even when requested before it.
Their actual capture time is retained; neither a latch nor panel disappearance
proves event delivery or tween callback execution. These records authorize no
action, replay, retry, completion, or ownership change.
"""
from __future__ import annotations

import hashlib
import base64
import json
import math
import threading
import time
import queue
from pathlib import Path
from filelock import FileLock
from backend.core.fanxiu.data_annotation.state import write_data_annotation_json

_queue = queue.Queue(maxsize=8)
_worker = None
_worker_lock = threading.Lock()
EVENTS = {'close_requested', 'click_api_returned', 'landing_observed', 'close_path_error'}


class _StoredFrameDecoder:
    """Pure decoding of an existing immutable frame; no shared GUI client."""
    @staticmethod
    def _decode_frame_data_url(frame):
        return base64.b64decode(frame.split(',', 1)[1], validate=True)


def _path(task_id, attempt_id, directory):
    if any(not isinstance(x, str) or not x or '\0' in x for x in (task_id, attempt_id)):
        raise ValueError('Close observations require an exact task/attempt identity')
    if directory is None:
        from backend.core.fanxiu.behavior_tree.kernel_scheduler import fanxiu_data_annotation_dir
        directory = fanxiu_data_annotation_dir() / 'magic-auto-exit-evidence'
    return Path(directory) / (hashlib.sha256((task_id + '\0' + attempt_id).encode()).hexdigest() + '.json')


def read_magic_invasion_exit_evidence(task_id: str, attempt_id: str, *, directory=None) -> dict | None:
    """Read one exact attempt; no Runtime read, missing-file creation or inference."""
    path = _path(task_id, attempt_id, directory)
    try:
        row = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    if (not isinstance(row, dict) or row.get('version') != 1
            or (row.get('task_id'), row.get('attempt_id')) != (task_id, attempt_id)
            or not isinstance(row.get('events'), list)):
        raise ValueError('Close evidence identity/schema is invalid')
    for index, event in enumerate(row['events'], 1):
        if (not isinstance(event, dict) or type(event.get('sequence')) is not int
                or event['sequence'] != index or event.get('event') not in EVENTS
                or not isinstance(event.get('facts'), dict) or not event.get('correlation_id')):
            raise ValueError('Close evidence observation is invalid')
    return row


def append_magic_invasion_exit_evidence(task_id, attempt_id, correlation_id, event, facts, *, directory=None):
    """Append observed facts atomically. A correlation id is not a native action id."""
    if not isinstance(correlation_id, str) or not correlation_id or event not in EVENTS or not isinstance(facts, dict):
        raise ValueError('Invalid close observation')
    json.dumps(facts, allow_nan=False)
    path = _path(task_id, attempt_id, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + '.lock', timeout=5):
        row = read_magic_invasion_exit_evidence(task_id, attempt_id, directory=path.parent)
        if row is None:
            row = dict(version=1, task_id=task_id, attempt_id=attempt_id, events=[])
        row['events'].append(dict(sequence=len(row['events']) + 1, correlation_id=correlation_id,
                                  event=event, recorded_at=time.time(), facts=facts))
        write_data_annotation_json(path, row)
        return row


def _capture(identity, correlation_id, event, facts, frame, runner, read_runtime):
    """Worker-only observation. All reads/serialization errors stay diagnostic."""
    try:
        facts = dict(facts, diagnostic_worker_started_at=time.time(),
                     click_hit_proven=False, callback_execution_proven=False,
                     correlation_kind='navigation_diagnostic_only', original_native_action_id=None,
                     frame_capture_epoch=None, frame_source='original_navigation_observation' if frame else None)
        if frame:
            try:
                from backend.core.fanxiu.data_annotation.scene_diagnostics import save_scene_diagnostic_frame
                facts['frame_path'] = save_scene_diagnostic_frame(
                    _StoredFrameDecoder(), frame, kind='magic_exit_evidence', label=correlation_id + '_' + event,
                    expected_scene_ids=[facts.get('target_scene_id') or 698], matched_scene_id=facts.get('scene_id'))
                if facts['frame_path'] is None:
                    facts['frame_error'] = 'frame provider returned no stored artifact'
            except Exception as exc:
                facts['frame_error'] = f'{type(exc).__name__}: {exc}'
        if read_runtime:
            facts['runtime_read_started_at'] = time.time()
            try:
                from backend.core.fanxiu.instrumentation.magic_invasion_auto_settings import read_magic_invasion_auto_exit_snapshot
                facts['runtime'] = read_magic_invasion_auto_exit_snapshot()
            except Exception as exc:
                facts['runtime_error'] = f'{type(exc).__name__}: {exc}'
            finally:
                facts['runtime_read_finished_at'] = time.time()
                facts['same_instant_as_frame'] = False
        append_magic_invasion_exit_evidence(identity['task_id'], identity['attempt_id'], correlation_id, event, facts)
    except Exception:
        # The absence of a record is unverified evidence, never game success.
        # No secondary failure may retry or interrupt the original game action.
        pass


def _drain():
    while True:
        arguments = _queue.get()
        try:
            _capture(*arguments)
        except Exception:
            pass
        finally:
            _queue.task_done()


def queue_magic_invasion_exit_observation(identity, correlation_id, event, facts, *,
                                        frame=None, runner=None, read_runtime=False) -> bool:
    """Queue a bounded independent diagnostic without delaying game waits.

    One daemon worker serializes up to eight queued diagnostics plus one active
    read. No task-end or interpreter-exit join is installed. False means a
    missing observation. Callers must not retry the game action
    or alter its deadline. Original frames are immutable strings copied from
    existing observations; no GUI frame capture is initiated by this provider.
    """
    global _worker
    try:
        with _worker_lock:
            if _worker is None or not _worker.is_alive():
                _worker = threading.Thread(target=_drain, daemon=True, name='magic-exit-evidence')
                _worker.start()
        _queue.put_nowait((dict(identity), correlation_id, event,
                          dict(facts, requested_at=time.time()), frame, None, read_runtime))
        return True
    except Exception:
        return False


def begin_magic_invasion_exit_observation(runner, shape, frame, target_scene_id):
    """Bind a new diagnostic correlation to the existing close, not a new action."""
    try:
        from uuid import uuid4
        from backend.core.fanxiu.data_annotation.subtask_execution import current_job_log_context
        identity = current_job_log_context()
        if not identity.get('task_id') or not identity.get('attempt_id'):
            return None
        probe = dict(identity=identity, correlation_id=uuid4().hex, shape_id=shape.get('id'))
        queue_magic_invasion_exit_observation(identity, probe['correlation_id'], 'close_requested',
            {'scene_id': 698, 'source': 'existing_navigation_shape', 'shape_id': shape.get('id'),
             'shape_title': shape.get('title'), 'target_scene_id': target_scene_id,
             'original_native_action_id': None, 'native_action_id_source': 'not provided by navigation contract',
             'runtime_timing': 'queued before original click; actual capture may occur after click'},
            frame=frame, runner=runner, read_runtime=True)
        return probe
    except Exception:
        return None


def observe_magic_invasion_exit(probe, event, facts, *, frame=None, runner=None, read_runtime=False):
    """Guard exact attempt context and isolate all diagnostic failures."""
    if not probe:
        return
    try:
        from backend.core.fanxiu.data_annotation.subtask_execution import current_job_log_context
        if current_job_log_context() != probe['identity']:
            return
        queue_magic_invasion_exit_observation(probe['identity'], probe['correlation_id'], event, facts,
                                             frame=frame, runner=runner, read_runtime=read_runtime)
    except Exception:
        pass


def compare_magic_exit_runtime_observations(before: dict, after: dict, *, click_started_at: float,
                                            formal_finished_at: float | None = None) -> dict:
    """Pure timing/identity guard. Never infer a hit, callback, or game effect."""
    b = (before.get('runtime') or {}).get('evidence') or {}
    a = (after.get('runtime') or {}).get('evidence') or {}
    keys = ('pid', 'process_start_ticks', 'panel_address')
    paired = (all((x.get('runtime') or {}).get('ok') is True
                  and (x.get('runtime') or {}).get('available') is True for x in (before, after))
              and all(type(b.get(k)) is int and b[k] > 0 and b[k] == a.get(k) for k in keys))
    def known_time(value):
        return type(value) in (int, float) and value > 0 and math.isfinite(value)
    timely = (known_time(before.get('runtime_read_finished_at')) and known_time(click_started_at)
              and before['runtime_read_finished_at'] <= click_started_at)
    within_attempt = (after['runtime_read_finished_at'] <= formal_finished_at
                      if known_time(formal_finished_at) and known_time(after.get('runtime_read_finished_at'))
                      else 'unknown')
    return dict(same_process_panel='proven' if paired else 'unknown',
                actual_before_click=timely, observation_within_formal_attempt=within_attempt,
                guarded_pair=paired and timely and within_attempt is True,
                click_hit_proven=False, callback_execution_proven=False)
