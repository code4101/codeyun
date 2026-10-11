"""Real journal I/O contracts, no simulated game or Kernel execution."""
import json
import pytest
from backend.core.fanxiu.data_annotation.native_cell_logs import (
    begin_native_cell_log, record_native_cell_log, end_native_cell_log, read_native_cell_logs,
)


def test_exact_journal_preserves_large_source_logs_and_first_error(tmp_path):
    source = 'original source\n' * 6000
    token = begin_native_cell_log('native-original', source, directory=tmp_path)
    record_native_cell_log({'kind': 'click', 'message': '关闭', 'source_expr': 'wait_click(943, 关闭)'})
    end_native_cell_log(token, error=RuntimeError('original cost gate'))
    r = read_native_cell_logs('native-original', directory=tmp_path)
    assert r['logging_complete'] and not r['game_effect_verified']
    assert r['events'][0]['source'] == source
    assert r['events'][1]['entry']['message'] == '关闭'
    assert r['events'][-1]['error'] == 'original cost gate'
    assert not read_native_cell_logs('different', directory=tmp_path)['found']


def test_partial_journal_is_not_liveness_or_terminal_proof(tmp_path):
    token = begin_native_cell_log('partial', 'original', directory=tmp_path)
    r = read_native_cell_logs('partial', directory=tmp_path)
    assert not r['logging_complete'] and 'alive' not in r
    end_native_cell_log(token)


def test_duplicate_request_never_appends_to_original_journal(tmp_path):
    token = begin_native_cell_log('same', 'first', directory=tmp_path)
    end_native_cell_log(token)
    original = read_native_cell_logs('same', directory=tmp_path)
    token = begin_native_cell_log('same', 'replayed', directory=tmp_path)
    record_native_cell_log({'message': 'never append'})
    end_native_cell_log(token)
    assert read_native_cell_logs('same', directory=tmp_path) == original


def test_truncated_append_retains_only_intact_prefix(tmp_path):
    token = begin_native_cell_log('damaged', 'original', directory=tmp_path)
    end_native_cell_log(token)
    path = next(tmp_path.glob('*.jsonl'))
    with path.open('a', encoding='utf-8') as stream:
        stream.write('{"truncated"')
    r = read_native_cell_logs('damaged', directory=tmp_path)
    assert not r['logging_complete'] and r['reason'] == 'native_cell_journal_invalid'
    assert len(r['events']) == 2


def test_unavailable_storage_never_raises_in_cell_logger(tmp_path):
    directory = tmp_path / 'blocked'
    directory.write_text('not a directory', encoding='utf-8')
    token = begin_native_cell_log('unavailable', 'original', directory=directory)
    record_native_cell_log({'message': 'business continues'})
    end_native_cell_log(token, error=RuntimeError('business first fault'))


@pytest.mark.parametrize('index,change', [
    (0, {'sequence': False}), (0, {'version': True}), (0, {'recorded_at': 0}),
    (0, {'recorded_at': float('inf')}), (0, {'recorded_at': float('nan')}),
    (1, {'entry': None}), (1, {'kind': 'started'}), (1, {'sequence': 1.0}),
    (2, {'status': None}), (2, {'status': 'running'}), (2, {'diagnostic_errors': False}),
    (2, {'diagnostic_errors': -1}), (2, {'recorded_at': None}), (2, {'error': None}),
    (2, {'cell_id': 'different'}),
])
def test_corrupt_schema_never_reports_complete_and_retains_intact_prefix(tmp_path, index, change):
    token = begin_native_cell_log('schema', 'original', directory=tmp_path)
    record_native_cell_log({'message': 'original entry'})
    end_native_cell_log(token)
    path = next(tmp_path.glob('*.jsonl'))
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    rows[index].update(change)
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    result = read_native_cell_logs('schema', directory=tmp_path)
    assert not result['logging_complete'] and result['reason'] == 'native_cell_journal_invalid'
    assert len(result['events']) == index
