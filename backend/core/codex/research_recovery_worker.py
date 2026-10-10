"""Durable independent recovery execution; patrol never waits for model turns.

Observation claims protect short inspection only. Worker generations, process
identity and terminal receipts protect execution, with no expiring lease that
could authorize another recovery while the first is still alive. A lost worker
or ambiguous launch requires reconciliation; it never authorizes a replay.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

import psutil
from filelock import FileLock

from backend.core.codex.research_recovery import recover_codex_research_once, _write_document
from backend.core.services.launcher import popen_service, resolve_python
from backend.core.settings import ROOT_DIR
from backend.core.temp_paths import codeyun_temp_root
from backend.core.codex.turn_errors import NATIVE_FAILURE_RECEIPT_CONTRACT


def _path(thread_id: str, state_dir: Path | None, *, create: bool) -> Path:
    if not isinstance(thread_id, str) or not thread_id.strip() or thread_id != thread_id.strip():
        raise ValueError('Exact original thread required')
    directory = Path(state_dir) if state_dir is not None else codeyun_temp_root(
        'codex-research-recovery', create=create)
    directory = directory / 'workers'
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return directory / (hashlib.sha256(thread_id.encode()).hexdigest() + '.json')


def _read(path: Path, thread_id: str) -> dict | None:
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    if (not isinstance(record, dict) or record.get('schema_version') != 1
        or record.get('thread_id') != thread_id or not isinstance(record.get('generation'), str)
        or record.get('state') not in {'launching', 'running', 'completed', 'failed', 'stopped', 'uncertain'}):
        raise ValueError('Invalid recovery worker receipt; preserve gate')
    return record


def _identity_alive(pid: int, created: float) -> bool:
    try:
        return psutil.Process(pid).create_time() == created
    except psutil.NoSuchProcess:
        return False
    # AccessDenied or other unknown identity is not proof of exit.


def read_codex_research_recovery_worker(thread_id: str, *, state_dir: Path | None = None) -> dict:
    """Read worker evidence/liveness; never expires a claim or starts anything."""
    path = _path(thread_id, state_dir, create=False)
    record = _read(path, thread_id)
    if record is None:
        return {'status': 'missing', 'found': False, 'recovery_performed': False}
    status = record['state']
    if status in {'launching', 'running'}:
        try:
            if not record.get('pid') or record.get('process_created_at') is None:
                status = 'uncertain'
            elif not _identity_alive(record['pid'], record['process_created_at']):
                status = 'worker_lost'
        except Exception:
            status = 'uncertain'
    result = record.get('result') or {}
    return {**record, 'found': True, 'status': status, 'evidence_path': str(path),
            'recovery_performed': bool(result.get('recovery_performed', False))}


def dispatch_codex_research_recovery(thread_id: str, *, owner_authorization: str, prompt: str,
        observation: dict, dry_run: bool = True, state_dir: Path | None = None,
        observation_timeout_seconds: float = 10, stop_event=None) -> dict:
    """Launch at most one independent execution generation per original thread.

    The supplied observation only deduplicates dispatch, never admits native
    work. The worker re-reads exact native evidence under the recovery lock.
    Stop in the patrol prevents launches; it does not cancel an existing writer.
    Only the worker's explicit stop request cancels its own admitted work.
    """
    if (type(dry_run) is not bool or not isinstance(owner_authorization, str)
        or not owner_authorization.strip() or not isinstance(prompt, str) or not prompt.strip()
        or not isinstance(observation, dict) or observation.get('thread_id') != thread_id):
        raise ValueError('Exact observation, owner authorization and policy required')
    if stop_event is not None and stop_event.is_set():
        return {'status': 'stopped', 'recovery_performed': False}
    path = _path(thread_id, state_dir, create=True)
    desktop = observation.get('desktop') or {}
    signature = hashlib.sha256(json.dumps([desktop.get('latest_turn'), desktop.get('writer'),
        desktop.get('completed_tool_ids'), (observation.get('goal') or {}).get('value'),
        observation.get('source_errors')], sort_keys=True).encode()).hexdigest()
    policy_key = hashlib.sha256(json.dumps([owner_authorization, prompt, dry_run]).encode()).hexdigest()
    with FileLock(str(path.with_suffix('.lock')), timeout=1):
        previous = _read(path, thread_id)
        if previous:
            current = read_codex_research_recovery_worker(thread_id, state_dir=state_dir)
            if current['status'] in {'running', 'launching', 'worker_lost', 'uncertain'}:
                if current['status'] in {'worker_lost', 'uncertain'}:
                    previous['state'] = 'uncertain'
                    previous.setdefault('manual_intervention', {'id': previous['generation'] + ':worker-lost',
                        'created_at': time.time(), 'reason': 'worker/launch outcome unknown; no replay'})
                    _write_document(path, previous)
                return current
            if previous.get('observation_key') == signature and previous.get('policy_key') == policy_key:
                result = previous.get('result') or {}
                if result.get('status') != 'backoff' or time.time() < result.get('next_attempt_at', float('inf')):
                    return current
        generation = uuid.uuid4().hex
        record = {'schema_version': 1, 'thread_id': thread_id, 'generation': generation,
            'state': 'launching', 'claimed_at': time.time(), 'observation_key': signature,
            'policy_key': policy_key, 'pid': None, 'process_created_at': None, 'stop_requested': False,
            'policy': {'owner_authorization': owner_authorization, 'prompt': prompt, 'dry_run': dry_run,
                       'observation_timeout_seconds': observation_timeout_seconds},
            'recovery_state_dir': str(path.parent.parent)}
        _write_document(path, record)
        try:
            # Worker waits for this lifecycle lock before reading the pid receipt.
            process = popen_service([resolve_python(), '-m',
                'backend.core.codex.research_recovery_worker', str(path), generation],
                cwd=str(ROOT_DIR), close_fds=True, stdout=None, stderr=None)
            record.update(pid=process.pid, process_created_at=psutil.Process(process.pid).create_time())
            _write_document(path, record)
        except Exception as exc:
            record.update(state='uncertain', error=f'{type(exc).__name__}: {exc}')
            _write_document(path, record)
            return {**record, 'status': 'uncertain', 'recovery_performed': False}
        return {**record, 'status': 'launching', 'recovery_performed': False}


def request_codex_research_recovery_stop(thread_id: str, *, generation: str,
        state_dir: Path | None = None) -> dict:
    """Persist a stop for this exact worker generation; never kill another PID."""
    path = _path(thread_id, state_dir, create=False)
    with FileLock(str(path.with_suffix('.lock')), timeout=1):
        record = _read(path, thread_id)
        if not record or record['generation'] != generation:
            raise ValueError('Exact worker generation required')
        record['stop_requested'] = True
        _write_document(path, record)
    return {'generation': generation, 'stop_requested': True, 'completed': False}


def run_codex_research_recovery_worker(path: Path, generation: str) -> int:
    """Execute one claimed generation, persisting genuine outcome separately."""
    initial = json.loads(path.read_text(encoding='utf-8'))
    thread_id = initial['thread_id']
    with FileLock(str(path.with_suffix('.lock')), timeout=1):
        record = _read(path, thread_id)
        if record is None or record['generation'] != generation or record['state'] != 'launching':
            raise ValueError('Worker generation already running, changed or unknown')
        record.update(state='running', started_at=time.time(), pid=os.getpid(),
            process_created_at=psutil.Process(os.getpid()).create_time(),
            loaded_contracts={'native_failure_receipt': NATIVE_FAILURE_RECEIPT_CONTRACT})
        _write_document(path, record)

    class Stop:
        def is_set(self):
            try:
                current = _read(path, thread_id)
                return not current or current['generation'] != generation or current['stop_requested']
            except Exception:
                return True  # Unknown stop ownership cannot admit a new turn.

    stop = Stop()
    result, error = None, None
    try:
        if stop.is_set():
            result = {'status': 'stopped', 'recovery_performed': False}
        else:
            result = recover_codex_research_once(thread_id, **record['policy'],
                state_dir=Path(record['recovery_state_dir']), stop_event=stop)
        state = 'stopped' if result['status'] in {'stopped', 'interrupted'} else 'completed'
    except Exception as exc:
        state, error = 'failed', f'{type(exc).__name__}: {exc}'
    with FileLock(str(path.with_suffix('.lock')), timeout=1):
        current = _read(path, thread_id)
        if current is None or current['generation'] != generation:
            raise ValueError('Worker receipt ownership changed; no terminal overwrite')
        current.update(state=state, finished_at=time.time(), result=result, error=error)
        _write_document(path, current)
    return 1 if state == 'failed' else 0


def main() -> int:
    from backend.core.attendance.independent_engine_adapter import ensure_attendance_engine_importable
    ensure_attendance_engine_importable()
    import xlproject.loadenv  # noqa: F401
    return run_codex_research_recovery_worker(Path(sys.argv[1]), sys.argv[2])


if __name__ == '__main__':
    raise SystemExit(main())
