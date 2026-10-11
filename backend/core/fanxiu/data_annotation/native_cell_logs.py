"""Exact native Cell log journal, independent of rolling status/tool output.

This records provider log calls on the executing Cell thread, not an inventory
receipt or proof that a logged intent reached the game. Historical missing
journals remain missing. Diagnostics never authorize, block or replay actions.
"""
from contextvars import ContextVar
from hashlib import sha256
import json
import math
from pathlib import Path
import time

_CURRENT = ContextVar('fanxiu_native_cell_log', default=None)


def _path(cell_id, directory):
    if not isinstance(cell_id, str) or not cell_id.strip() or '\0' in cell_id:
        raise ValueError('Exact native Cell msg_id required')
    if directory is None:
        from backend.core.temp_paths import codeyun_temp_root
        directory = codeyun_temp_root('native-cell-logs', create=False)
    return Path(directory) / (sha256(cell_id.encode()).hexdigest() + '.jsonl')


def _append(state, kind, **fields):
    try:
        row = dict(sequence=state['sequence'], kind=kind, cell_id=state['cell_id'],
                   recorded_at=time.time(), **fields)
        with state['path'].open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        state['sequence'] += 1
    except Exception:
        state['diagnostic_errors'] += 1


def begin_native_cell_log(cell_id: str, source: str, *, directory=None):
    """Bind the original Jupyter request, once per executing Cell thread."""
    state = dict(cell_id=cell_id, path=_path(cell_id, directory), sequence=0, diagnostic_errors=0)
    token = _CURRENT.set(state)
    try:
        state['path'].parent.mkdir(parents=True, exist_ok=True)
        # Exact request IDs are unique. Never append a replay to prior evidence.
        with state['path'].open('x', encoding='utf-8'):
            pass
    except Exception:
        state['diagnostic_errors'] += 1
        state['disabled'] = True
    if not state.get('disabled'):
        _append(state, 'started', version=1, source=source)
    return token


def record_native_cell_log(entry: dict):
    """Copy the provider's original structured entry without inferring effects."""
    state = _CURRENT.get()
    if state is not None and not state.get('disabled'):
        _append(state, 'log', entry=dict(entry))


def end_native_cell_log(token, *, error=None):
    state = _CURRENT.get()
    try:
        if state is not None and not state.get('disabled'):
            _append(state, 'terminal', status='error' if error else 'success',
                    error_type=type(error).__name__ if error else None,
                    error=str(error) if error else '', diagnostic_errors=state['diagnostic_errors'])
    finally:
        try:
            _CURRENT.reset(token)
        except ValueError:
            # A diagnostic context mismatch must not mask the Cell's error.
            _CURRENT.set(None)


def read_native_cell_logs(cell_id: str, *, directory=None) -> dict:
    """Read only one exact journal; no directory scan or historical backfill.

    A start without terminal is incomplete, not proof that a Cell is still
    alive. Native Kernel status owns liveness. Parse/sequence failures remain
    explicit, retaining only the intact prefix; no truncated text is repaired.
    """
    path = _path(cell_id, directory)
    result = dict(read_only=True, cell_id=cell_id, found=path.is_file(),
                  logging_complete=False, events=[], coverage='executing_cell_thread_provider_logs',
                  game_effect_verified=False)
    if not result['found']:
        return {**result, 'reason': 'native_cell_journal_missing'}
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            try:
                row = json.loads(line)
                kind, stamp = row.get('kind'), row.get('recorded_at')
                first = not result['events']
                if (row.get('cell_id') != cell_id
                        or type(row.get('sequence')) is not int or row['sequence'] != len(result['events'])
                        or type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp <= 0
                        or kind not in {'started', 'log', 'terminal'}
                        or (first and (kind != 'started' or type(row.get('version')) is not int
                                       or row['version'] != 1 or not isinstance(row.get('source'), str)))
                        or (not first and kind == 'started')
                        or (kind == 'log' and not isinstance(row.get('entry'), dict))
                        or (kind == 'terminal' and (row.get('status') not in {'success', 'error'}
                            or type(row.get('diagnostic_errors')) is not int or row['diagnostic_errors'] < 0
                            or not isinstance(row.get('error'), str)))
                        or (result['events'] and result['events'][-1].get('kind') == 'terminal')):
                    raise ValueError('Journal identity/sequence mismatch')
                result['events'].append(row)
            except (ValueError, AttributeError, TypeError, OverflowError):
                return {**result, 'reason': 'native_cell_journal_invalid'}
    terminal = result['events'][-1] if result['events'] else {}
    result['logging_complete'] = terminal.get('kind') == 'terminal' and terminal.get('diagnostic_errors') == 0
    return {**result, 'reason': 'terminal_recorded' if result['logging_complete'] else 'native_cell_journal_incomplete'}
