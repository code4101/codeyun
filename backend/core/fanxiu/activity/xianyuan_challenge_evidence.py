"""Attempt-scoped observations, never authority to replay a challenge.

Routes describe the task's observed branch. Even ``challenge_completed`` is
not a standalone proof of #198 recognition or a scheduler terminal. Consumers
must correlate the original frames/events and the exact formal attempt.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from filelock import FileLock
from backend.core.fanxiu.data_annotation.state import write_data_annotation_json

ROUTES = {'already_done_runtime', 'already_done_daily', 'no_remaining_challenges', 'challenge_completed', 'unknown'}


def _receipt_path(task_id, attempt_id, directory):
    if (not isinstance(task_id, str) or not task_id or '\0' in task_id
            or not isinstance(attempt_id, str) or not attempt_id or '\0' in attempt_id):
        raise ValueError('Evidence requires an exact task and attempt identity')
    if directory is None:
        from backend.core.fanxiu.behavior_tree.kernel_scheduler import fanxiu_data_annotation_dir
        directory = fanxiu_data_annotation_dir() / 'xianyuan-challenge-evidence'
    key = hashlib.sha256((task_id + '\0' + attempt_id).encode()).hexdigest()
    return Path(directory) / (key + '.json')


def read_xianyuan_challenge_evidence(task_id: str, attempt_id: str, *, directory=None) -> dict | None:
    """Read exactly one attempt, without querying the game or creating receipts."""
    path = _receipt_path(task_id, attempt_id, directory)
    try:
        row = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    if not isinstance(row, dict) or (row.get('task_id'), row.get('attempt_id')) != (task_id, attempt_id):
        raise ValueError('Challenge evidence identity mismatch')
    if row.get('version') != 1 or not isinstance(row.get('events'), list) or row.get('route') not in ROUTES:
        raise ValueError('Challenge evidence is malformed')
    for index, observation in enumerate(row['events'], 1):
        if (not isinstance(observation, dict) or observation.get('sequence') != index
                or not isinstance(observation.get('facts'), dict)):
            raise ValueError('Challenge evidence event sequence is malformed')
    return row


def append_xianyuan_challenge_evidence(task_id: str, attempt_id: str, event: str, facts: dict, *, directory=None) -> dict:
    """Persist only caller observations; no inferred actions, completion or backfill.

    A diagnostic write failure propagates to the task's diagnostic isolation
    boundary, never silently becomes a success receipt. The existing atomic
    JSON provider and a per-attempt lock serialize observations across processes.
    """
    if not isinstance(facts, dict):
        raise ValueError('Challenge evidence requires observation facts')
    json.dumps(facts, allow_nan=False)  # reject invalid serialization before any write
    if event not in {'runtime_before', 'detail_observation', 'action_submitted',
                     'detail_transition', 'challenge_result', 'battle_observation', 'runtime_after',
                     'route', 'world_return', 'task_return'}:
        raise ValueError('Unknown challenge evidence event')
    if event == 'route' and facts.get('route') not in ROUTES:
        raise ValueError('Unknown challenge route')
    path = _receipt_path(task_id, attempt_id, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + '.lock', timeout=5):
        row = read_xianyuan_challenge_evidence(task_id, attempt_id, directory=path.parent)
        if row is None:
            row = dict(version=1, task_id=task_id, attempt_id=attempt_id, route='unknown', events=[])
        row['events'].append(dict(sequence=len(row['events']) + 1, recorded_at=time.time(),
                                  event=event, facts=facts))
        if event == 'route':
            row['route'] = facts['route']
        write_data_annotation_json(path, row)
        return row
