"""Persistent diagnostic/identity contracts, not simulated game closure."""
import json
import queue
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
from backend.core.fanxiu.activity import magic_invasion_exit_evidence as evidence
from backend.core.fanxiu.data_annotation.subtask_execution import job_log_context


def write(tmp_path, attempt='a', facts=None):
    return evidence.append_magic_invasion_exit_evidence('ranking-lifecycle', attempt, 'diagnostic-correlation',
        'close_requested', facts or {'original_native_action_id': None, 'local_navigation_retry': 2},
        directory=tmp_path)


def test_exact_attempt_and_local_retry_are_separate_and_read_is_non_creating(tmp_path):
    assert evidence.read_magic_invasion_exit_evidence('ranking-lifecycle', 'a', directory=tmp_path) is None
    assert not list(tmp_path.iterdir())
    write(tmp_path)
    write(tmp_path, 'b')
    row = evidence.read_magic_invasion_exit_evidence('ranking-lifecycle', 'a', directory=tmp_path)
    assert row['attempt_id'] == 'a'
    assert row['events'][0]['facts']['local_navigation_retry'] == 2
    assert row['events'][0]['facts']['original_native_action_id'] is None
    assert 'completed' not in row and 'verified' not in row


@pytest.mark.parametrize('broken', ['{', 'null', '[]', '{"version":0}'])
def test_corrupt_receipt_cannot_be_replaced(tmp_path, broken):
    write(tmp_path)
    path = next(tmp_path.glob('*.json'))
    path.write_text(broken, encoding='utf-8')
    original = path.read_bytes()
    with pytest.raises(ValueError):
        write(tmp_path)
    assert path.read_bytes() == original


def test_concurrent_durable_append_keeps_sequences_and_correlations(tmp_path):
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: write(tmp_path, facts={'original_index': i}), range(16)))
    row = evidence.read_magic_invasion_exit_evidence('ranking-lifecycle', 'a', directory=tmp_path)
    assert [x['sequence'] for x in row['events']] == list(range(1, 17))
    assert {x['facts']['original_index'] for x in row['events']} == set(range(16))


def sample(pid=1, panel=3, finished=1):
    return {'runtime_read_finished_at': finished, 'runtime': {'ok': True, 'available': True,
        'exit': {'close_latched': None}, 'evidence': {'pid': pid, 'process_start_ticks': 2, 'panel_address': panel}}}


@pytest.mark.parametrize('case', ['late_before', 'after_task_end', 'changed_pid', 'changed_panel', 'not_loaded'])
def test_late_or_unbound_observations_never_form_guarded_pair(case):
    before, after = sample(), sample(finished=4)
    if case == 'late_before': before['runtime_read_finished_at'] = 3
    if case == 'after_task_end': after['runtime_read_finished_at'] = 6
    if case == 'changed_pid': after = sample(pid=10, finished=4)
    if case == 'changed_panel': after = sample(panel=10, finished=4)
    if case == 'not_loaded': after['runtime']['available'] = False
    result = evidence.compare_magic_exit_runtime_observations(before, after, click_started_at=2, formal_finished_at=5)
    assert result['guarded_pair'] is False
    assert result['click_hit_proven'] is False and result['callback_execution_proven'] is False


def test_nullable_latch_is_retained_and_never_means_click_hit():
    result = evidence.compare_magic_exit_runtime_observations(sample(), sample(finished=4), click_started_at=2, formal_finished_at=5)
    assert result['guarded_pair'] is True
    assert result['click_hit_proven'] is False and result['callback_execution_proven'] is False


@pytest.mark.parametrize('finished', [None, False, 0, float('nan')])
def test_unknown_formal_end_is_not_proven_within_attempt(finished):
    result = evidence.compare_magic_exit_runtime_observations(sample(), sample(finished=4),
        click_started_at=2, formal_finished_at=finished)
    assert result['observation_within_formal_attempt'] == 'unknown'
    assert result['guarded_pair'] is False


def test_missing_observation_time_cannot_form_guarded_pair():
    after = sample(finished=4)
    after.pop('runtime_read_finished_at')
    result = evidence.compare_magic_exit_runtime_observations(sample(), after, click_started_at=2, formal_finished_at=5)
    assert result['observation_within_formal_attempt'] == 'unknown' and result['guarded_pair'] is False


def test_full_queue_does_not_block_or_retry(monkeypatch):
    full = queue.Queue(maxsize=1)
    full.put('already queued')
    monkeypatch.setattr(evidence, '_queue', full)
    monkeypatch.setattr(evidence, '_worker', SimpleNamespace(is_alive=lambda: True))
    assert evidence.queue_magic_invasion_exit_observation({'task_id': 't', 'attempt_id': 'a'},
        'correlation', 'close_requested', {}) is False
    assert full.qsize() == 1


def test_frame_failure_preserves_original_event_and_runtime_error(tmp_path, monkeypatch):
    from backend.core.fanxiu.data_annotation import scene_diagnostics
    from backend.core.fanxiu.instrumentation import magic_invasion_auto_settings
    def failed(*a, **k): raise OSError('diagnostic failure')
    monkeypatch.setattr(scene_diagnostics, 'save_scene_diagnostic_frame', failed)
    monkeypatch.setattr(magic_invasion_auto_settings, 'read_magic_invasion_auto_exit_snapshot', failed)
    original = evidence.append_magic_invasion_exit_evidence
    monkeypatch.setattr(evidence, 'append_magic_invasion_exit_evidence',
        lambda *a, **k: original(*a, **k, directory=tmp_path))
    evidence._capture({'task_id': 't', 'attempt_id': 'a'}, 'correlation', 'close_requested',
                      {'scene_id': 698}, 'existing-frame', None, True)
    row = evidence.read_magic_invasion_exit_evidence('t', 'a', directory=tmp_path)
    facts = row['events'][0]['facts']
    assert 'OSError' in facts['frame_error'] and 'OSError' in facts['runtime_error']
    assert facts['runtime_read_started_at'] <= facts['runtime_read_finished_at']
    assert facts['same_instant_as_frame'] is False
    assert facts['original_native_action_id'] is None


def test_missing_or_changed_formal_context_cannot_queue_other_attempt(monkeypatch):
    calls = []
    monkeypatch.setattr(evidence, 'queue_magic_invasion_exit_observation', lambda *a, **k: calls.append(a))
    assert evidence.begin_magic_invasion_exit_observation(None, {'id': 'shape', 'title': '返回'}, None, 34) is None
    with job_log_context('t', 'a'):
        probe = evidence.begin_magic_invasion_exit_observation(None, {'id': 'shape', 'title': '返回'}, None, 34)
    with job_log_context('t', 'b'):
        evidence.observe_magic_invasion_exit(probe, 'landing_observed', {})
    assert len(calls) == 1
    assert calls[0][0] == {'task_id': 't', 'attempt_id': 'a'}
