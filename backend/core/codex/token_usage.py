"""Read-only historical Codex token accounting, independent of quota percentages."""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_FIELDS = ('input_tokens', 'cached_input_tokens', 'output_tokens',
           'reasoning_output_tokens', 'total_tokens')


def _counts(value):
    if not isinstance(value, dict):
        return None
    result = tuple(value.get(key, 0) for key in _FIELDS)
    if any(type(n) is not int or n < 0 for n in result):
        return None
    if result[0] + result[2] != result[4] or result[1] > result[0] or result[3] > result[2]:
        return None
    return result


def read_codex_token_usage(*, start_date: str, end_date: str,
                          timezone: str = 'Asia/Shanghai', root_dir: str | None = None,
                          cutoff: datetime | None = None) -> dict:
    """Sum recorded model usage for inclusive calendar dates, through cutoff/now.

    Owns native session/archived-session discovery and format adaptation. Reads
    JSONL only; never opens a DB, resumes a writer, or makes a model request.
    Cumulative snapshots are differenced BEFORE date filtering; repeated quota
    notifications with unchanged totals contribute zero. Exact copied events
    across forked histories are deduplicated globally. Cached input is a subset
    of input; reasoning is a subset of output, never added twice.

    Only current native session directories are covered (not backups, deleted
    history, other machines or cloud-only tasks). Unattributable initial totals,
    counter resets and unreadable/malformed usage are explicitly reported;
    this is local observed usage, not a final account billing statement.
    """
    tz = ZoneInfo(timezone)
    first, last = date.fromisoformat(start_date), date.fromisoformat(end_date)
    if first > last:
        raise ValueError('start_date must be no later than end_date')
    now = cutoff or datetime.now(tz)
    if now.tzinfo is None:
        raise ValueError('cutoff must have a timezone')
    start = datetime.combine(first, time.min, tz)
    stop = min(datetime.combine(last + timedelta(days=1), time.min, tz), now)
    root = Path(root_dir or os.environ.get('CODEX_HOME') or Path.home() / '.codex').expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    totals = Counter()
    daily = defaultdict(Counter)
    providers = defaultdict(Counter)
    diagnostics = Counter()
    last_call_totals = Counter()
    unattributed_prefix = Counter()
    seen = set()
    thread_ids = set()
    latest = None
    files = sorted({p.resolve() for folder in ('sessions', 'archived_sessions')
                    for p in (root / folder).rglob('*.jsonl')})
    for path in files:
        previous = (0,) * len(_FIELDS)
        provider = 'unknown'
        thread_id = None
        try:
            # Snapshot the readable extent; a live writer's later append belongs
            # to the next query. No writer locks or destructive operations.
            with path.open('rb') as source:
                extent = os.fstat(source.fileno()).st_size
                while source.tell() < extent:
                    raw = source.readline()
                    if source.tell() > extent:
                        break
                    if not any(marker in raw for marker in (b'"token_count"', b'"session_meta"')):
                        continue
                    try:
                        event = json.loads(raw)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        diagnostics['malformed_records'] += 1
                        continue
                    payload = event.get('payload') or {}
                    if event.get('type') == 'session_meta':
                        provider = payload.get('model_provider') or 'unknown'
                        thread_id = payload.get('id')
                        continue
                    if event.get('type') != 'event_msg' or payload.get('type') != 'token_count':
                        continue
                    info = payload.get('info')
                    if not isinstance(info, dict):
                        continue  # Rate-limit-only notifications have no usage.
                    cumulative = _counts(info.get('total_token_usage'))
                    last_usage = _counts(info.get('last_token_usage'))
                    try:
                        stamp = datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00'))
                        if stamp.tzinfo is None:
                            raise ValueError('naive timestamp')
                    except (KeyError, ValueError, TypeError):
                        diagnostics['invalid_timestamps'] += 1
                        continue
                    in_range = start <= stamp < stop
                    if cumulative is None:
                        if in_range:
                            diagnostics['invalid_usage_records'] += 1
                        continue
                    if cumulative == previous:
                        if in_range:
                            diagnostics['unchanged_snapshots'] += 1
                        continue
                    delta = tuple(n - p for n, p in zip(cumulative, previous))
                    if not any(previous) and last_usage is None:
                        # A cumulative-only first snapshot has no date-scoped
                        # baseline. Do not assign its entire history to now.
                        if in_range:
                            diagnostics['unattributed_initial_snapshots'] += 1
                            for key, n in zip(_FIELDS, cumulative):
                                unattributed_prefix[key] += n
                        previous = cumulative
                        continue
                    if any(n < 0 for n in delta):
                        delta = last_usage
                        if in_range:
                            diagnostics['counter_resets'] += 1
                    elif not any(previous) and last_usage is not None and cumulative != last_usage:
                        # An imported/truncated history cannot assign the missing
                        # cumulative prefix to this event's timestamp.
                        delta = last_usage
                        if in_range:
                            diagnostics['unattributed_initial_snapshots'] += 1
                            for key, n, p in zip(_FIELDS, cumulative, last_usage):
                                unattributed_prefix[key] += max(0, n - p)
                    previous = cumulative
                    if not in_range or delta is None:
                        continue
                    fingerprint = (event['timestamp'], cumulative, last_usage)
                    if fingerprint in seen:
                        diagnostics['copied_events'] += 1
                        continue
                    seen.add(fingerprint)
                    thread_ids.add(thread_id or str(path))
                    diagnostics['usage_events'] += 1
                    if last_usage is None:
                        diagnostics['missing_last_call_usage'] += 1
                    else:
                        if delta != last_usage:
                            diagnostics['delta_last_call_mismatches'] += 1
                        for key, n in zip(_FIELDS, last_usage):
                            last_call_totals[key] += n
                    day = stamp.astimezone(tz).date().isoformat()
                    for key, n in zip(_FIELDS, delta):
                        totals[key] += n
                        daily[day][key] += n
                        providers[provider][key] += n
                    latest = max(latest, stamp) if latest else stamp
        except OSError:
            diagnostics['unreadable_files'] += 1
    return {'source': 'local_codex_recorded_usage', 'root_dir': str(root),
            'start': start.isoformat(), 'end_exclusive': stop.isoformat(),
            'latest_usage_at': latest.astimezone(tz).isoformat() if latest else None,
            'totals': {key: totals[key] for key in _FIELDS},
            'total_hundred_million_tokens': totals['total_tokens'] / 100_000_000,
            'daily': {day: dict(values) for day, values in sorted(daily.items())},
            'providers': {key: dict(values) for key, values in sorted(providers.items())},
            'files_scanned': len(files), 'threads_with_usage': len(thread_ids),
            'diagnostics': dict(diagnostics),
            'validation_last_call_totals': dict(last_call_totals),
            'unattributed_initial_prefix_tokens': dict(unattributed_prefix),
            'scope': 'Local sessions and archived_sessions only; includes cached input and reasoning output.'}
