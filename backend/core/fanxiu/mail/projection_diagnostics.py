"""Bounded failure evidence for mail Session projection; no SQL or mail contents.

Wall stage timings include database waits. Per-attempt stages are sequential;
attempt totals contain stages and must not be added to them or runtime totals.
These records neither establish lock ownership nor authorize another retry.
"""
import logging
from pathlib import Path
from filelock import FileLock
from backend.core.settings import get_settings
from backend.core.fanxiu.data_annotation.state import read_data_annotation_json, write_data_annotation_json

MAX_RECORDS = 64


def mail_projection_diagnostics_path():
    return get_settings().data_dir / 'fanxiu' / 'mail_projection_diagnostics.json'


def read_mail_projection_diagnostics(*, path=None):
    """Read retained failed projection attempts, without DB/Runtime/write calls."""
    state = read_data_annotation_json(Path(path) if path is not None else mail_projection_diagnostics_path(),
                                      {'version': 1, 'records': []})
    return {**state, 'read_only': True, 'coverage': 'mail_runtime_session_projection_failures_only',
            'retention_limit': MAX_RECORDS, 'lock_holder_proven': False,
            'persistence_semantics': 'best_effort_100ms_lock_wait_loss_possible_check_exception_evidence_persisted',
            'evicted_record_count_semantics': 'only_successfully_admitted_records_removed_by_retention',
            'admission_failures_count_available': False,
            'timing_semantics': 'wall_stages_inside_attempt_total_nested_not_additive'}


def record_mail_projection_failure(record, *, path=None):
    """Persist provider-built metadata; failure cannot replace the business error."""
    try:
        target = Path(path) if path is not None else mail_projection_diagnostics_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(target) + '.lock', timeout=0.1):
            state = read_data_annotation_json(target, {'version': 1, 'records': []})
            rows = state['records'] + [record]
            state['evicted_record_count'] = int(state.get('evicted_record_count', 0)) + max(0, len(rows)-MAX_RECORDS)
            state['records'] = rows[-MAX_RECORDS:]
            write_data_annotation_json(target, state)
        return True
    except Exception as exc:
        try:
            logging.getLogger(__name__).warning('Mail projection diagnostic unavailable: %s', type(exc).__name__)
        except Exception:
            pass  # A broken logging handler is also a diagnostic failure.
        return False
