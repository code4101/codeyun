import json
from datetime import datetime

from backend.core.codex.token_usage import read_codex_token_usage


def counts(n):
    return {'input_tokens': n * 10, 'cached_input_tokens': n * 8,
            'output_tokens': n * 2, 'reasoning_output_tokens': n,
            'total_tokens': n * 12}


def event(stamp, cumulative, last):
    return {'timestamp': stamp, 'type': 'event_msg', 'payload': {
        'type': 'token_count', 'info': {'total_token_usage': counts(cumulative),
                                      'last_token_usage': counts(last)}}}


def write(root, name, rows):
    folder = root / 'sessions'
    folder.mkdir(exist_ok=True)
    meta = {'type': 'session_meta', 'payload': {'id': name, 'model_provider': 'openai'}}
    (folder / (name + '.jsonl')).write_text('\n'.join(map(json.dumps, [meta, *rows])), encoding='utf-8')


def query(root):
    return read_codex_token_usage(root_dir=str(root), start_date='2026-09-25',
        end_date='2026-10-13', cutoff=datetime.fromisoformat('2026-10-13T20:00:00+08:00'))


def test_date_boundary_stale_updates_and_fork_copies(tmp_path):
    rows = [event('2026-09-24T15:59:59Z', 1, 1),
            event('2026-09-24T16:00:00Z', 3, 2),
            event('2026-09-24T16:00:01Z', 3, 2),
            event('2026-10-13T11:59:59Z', 6, 3),
            event('2026-10-13T12:00:00Z', 10, 4)]
    write(tmp_path, 'root', rows)
    write(tmp_path, 'fork', rows[:3] + [event('2026-09-25T00:00:00Z', 4, 1)])
    r = query(tmp_path)
    assert r['totals'] == counts(6)
    assert r['diagnostics']['copied_events'] == 1
    assert r['daily']['2026-09-25']['total_tokens'] == 36


def test_truncated_prefix_reset_and_other_provider(tmp_path):
    write(tmp_path, 'truncated', [event('2026-09-26T00:00:00Z', 100, 2),
                                   event('2026-09-26T00:01:00Z', 1, 1)])
    r = query(tmp_path)
    assert r['totals'] == counts(3)
    assert r['diagnostics']['counter_resets'] == 1
    assert r['diagnostics']['unattributed_initial_snapshots'] == 1


def test_missing_rate_only_and_invalid_usage_are_not_zero_claims(tmp_path):
    bad = event('2026-09-26T00:00:00Z', 1, 1)
    bad['payload']['info']['total_token_usage']['total_tokens'] = 999
    write(tmp_path, 'bad', [bad, {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': None}}])
    r = query(tmp_path)
    assert r['totals']['total_tokens'] == 0
    assert r['diagnostics']['invalid_usage_records'] == 1


def test_cumulative_only_initial_history_is_unattributed(tmp_path):
    initial = event('2026-09-26T00:00:00Z', 100, 1)
    del initial['payload']['info']['last_token_usage']
    write(tmp_path, 'initial', [initial, event('2026-09-26T01:00:00Z', 102, 2)])
    r = query(tmp_path)
    assert r['totals'] == counts(2)
    assert r['unattributed_initial_prefix_tokens'] == counts(100)
