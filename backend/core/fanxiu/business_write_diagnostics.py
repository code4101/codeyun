"""Bounded cross-process evidence for public business-record writes.

Intervals identify overlapping instrumented operations, never prove which
connection held a SQLite lock. Other providers/direct Session writes are outside
coverage. No SQL, payloads, game calls, transaction retries or rollbacks occur.
"""
from contextlib import contextmanager
import hashlib
import logging
import os
from pathlib import Path
import threading
import time
import uuid

from filelock import FileLock
from backend.core.settings import get_settings
from backend.core.fanxiu.data_annotation.state import read_data_annotation_json, write_data_annotation_json

LOGGER = logging.getLogger(__name__)
MAX_RECORDS = 128


def business_write_diagnostics_path() -> Path:
    return get_settings().data_dir / 'fanxiu' / 'business_write_diagnostics.json'


def read_business_write_diagnostics(*, path=None) -> dict:
    """Read retained public-operation evidence; missing history stays missing."""
    state = read_data_annotation_json(Path(path) if path is not None else business_write_diagnostics_path(),
                                      {'version': 1, 'records': []})
    candidates = {}
    for failed in state['records']:
        if failed.get('error_category') != 'database_locked' or not failed.get('finished_at_epoch'):
            continue
        candidates[failed['operation_id']] = [other['operation_id'] for other in state['records']
            if other['operation_id'] != failed['operation_id']
            and other.get('database_identity') == failed.get('database_identity')
            and other['started_at_epoch'] <= failed['finished_at_epoch']
            and (other.get('finished_at_epoch') is None
                 or other['finished_at_epoch'] >= failed['started_at_epoch'])]
    return {**state, 'read_only': True, 'coverage': 'upsert_fanxiu_business_records_only',
            'lock_holder_proven': False, 'retention_limit': MAX_RECORDS,
            'overlapping_operation_candidates': candidates,
            'overlap_semantics': 'operation_interval_only_not_lock_ownership'}


def _publish(record, path):
    # Diagnostic failure cannot replace a business exception or pretend that
    # a committed write failed. Explicit warning exposes lost evidence.
    try:
        target = Path(path) if path is not None else business_write_diagnostics_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(target) + '.lock', timeout=0.1):
            state = read_data_annotation_json(target, {'version': 1, 'records': []})
            rows = [r for r in state['records'] if r['operation_id'] != record['operation_id']]
            rows.append(dict(record))
            rows.sort(key=lambda r: r['started_at_epoch'])
            state['evicted_record_count'] = int(state.get('evicted_record_count', 0)) + max(0, len(rows) - MAX_RECORDS)
            state['records'] = rows[-MAX_RECORDS:]
            write_data_annotation_json(target, state)
        return True
    except Exception as exc:
        LOGGER.warning('Business write diagnostic unavailable: %s', type(exc).__name__)
        return False


@contextmanager
def observe_business_write(session, rows, *, source: dict, path=None):
    """Observe one existing provider call without changing Session ownership."""
    started = time.perf_counter()
    database = str(session.get_bind().url)
    record = {'operation_id': uuid.uuid4().hex, 'pid': os.getpid(),
              'thread_id': threading.get_ident(), 'source': source,
              'database_identity': hashlib.sha256(database.encode()).hexdigest(),
              'started_at_epoch': time.time(), 'finished_at_epoch': None, 'status': 'running',
              'row_count': len(rows), 'domains': sorted({str(r.get('domain', ''))[:160]
                  for r in rows if isinstance(r, dict)})[:20],
              'record_keys': [str(r.get('record_key', ''))[:160]
                  for r in rows[:20] if isinstance(r, dict)],
              'session_transaction_already_active': session.in_transaction(),
              'pending_before': {'new': len(session.new), 'dirty': len(session.dirty), 'deleted': len(session.deleted)},
              'phase': 'begin', 'phase_seconds': {}}
    record['start_evidence_published'] = _publish(record, path)
    try:
        yield record
    except BaseException as exc:
        original = getattr(exc, 'orig', None)
        record.update(status='error', error_type=type(exc).__name__,
                      driver_error_type=type(original).__name__ if original is not None else None,
                      driver_error_code=getattr(original, 'sqlite_errorcode', None),
                      driver_error_name=getattr(original, 'sqlite_errorname', None),
                      error_category='database_locked' if original is not None
                      and 'locked' in str(original).lower() else 'other')
        raise
    else:
        record['status'] = 'success'
    finally:
        record.update(finished_at_epoch=time.time(), elapsed_seconds=time.perf_counter() - started)
        _publish(record, path)
