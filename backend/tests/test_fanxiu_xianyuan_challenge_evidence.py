"""Real durable observations and ownership contexts; no game simulation."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from backend.core.fanxiu.activity import xianyuan_challenge_evidence as evidence
from backend.core.fanxiu.data_annotation.subtask_execution import job_log_context
from backend.core.fanxiu.data_annotation.tasks.xianyuan_execution import DailyXianyuanTaskMixin


def append(tmp_path, attempt='first', event='route', facts=None):
    return evidence.append_xianyuan_challenge_evidence('legacy-daily-xianyuan', attempt, event,
        facts or {'route': 'unknown'}, directory=tmp_path)


def test_exact_attempt_read_has_no_creation_or_cross_attempt_reuse(tmp_path):
    assert evidence.read_xianyuan_challenge_evidence('task', 'missing', directory=tmp_path) is None
    assert not list(tmp_path.iterdir())
    append(tmp_path, facts={'route': 'already_done_runtime'})
    append(tmp_path, 'second')
    a = evidence.read_xianyuan_challenge_evidence('legacy-daily-xianyuan', 'first', directory=tmp_path)
    b = evidence.read_xianyuan_challenge_evidence('legacy-daily-xianyuan', 'second', directory=tmp_path)
    assert a['route'] == 'already_done_runtime' and b['route'] == 'unknown'
    assert a['attempt_id'] != b['attempt_id']
    assert 'completed' not in b and 'verified' not in b


@pytest.mark.parametrize('broken', ['{', 'null', '[]', '{"version":999}'])
def test_corrupt_existing_receipt_is_never_overwritten(tmp_path, broken):
    append(tmp_path)
    path = next(tmp_path.glob('*.json'))
    path.write_text(broken, encoding='utf-8')
    original = path.read_bytes()
    with pytest.raises((ValueError, json.JSONDecodeError)):
        append(tmp_path, facts={'route': 'challenge_completed'})
    assert path.read_bytes() == original


def test_unreadable_existing_receipt_is_never_treated_as_missing(tmp_path, monkeypatch):
    append(tmp_path)
    path = next(tmp_path.glob('*.json'))
    original = path.read_bytes()
    read = Path.read_text
    def denied(self, *args, **kwargs):
        if self == path:
            raise PermissionError('not readable')
        return read(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', denied)
    with pytest.raises(PermissionError):
        append(tmp_path)
    assert path.read_bytes() == original


def test_concurrent_append_has_unique_ordered_durable_sequence(tmp_path):
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: append(tmp_path, event='detail_transition',
                                      facts={'observation_index': i}), range(20)))
    row = evidence.read_xianyuan_challenge_evidence('legacy-daily-xianyuan', 'first', directory=tmp_path)
    assert [x['sequence'] for x in row['events']] == list(range(1, 21))
    assert {x['facts']['observation_index'] for x in row['events']} == set(range(20))


def test_original_runtime_identity_and_override_source_are_not_filled(tmp_path):
    facts = {'source': 'supplied_override', 'snapshot': {'task_id': 1008, 'done': True}}
    append(tmp_path, event='runtime_before', facts=facts)
    row = evidence.read_xianyuan_challenge_evidence('legacy-daily-xianyuan', 'first', directory=tmp_path)
    assert row['events'][0]['facts'] == facts
    assert row['route'] == 'unknown'


def test_invalid_route_or_serialization_does_not_modify_original(tmp_path):
    append(tmp_path)
    path = next(tmp_path.glob('*.json'))
    original = path.read_bytes()
    for facts in [{'route': 'success'}, {'route': 'unknown', 'invalid': object()}]:
        with pytest.raises((ValueError, TypeError)):
            append(tmp_path, facts=facts)
        assert path.read_bytes() == original


def test_diagnostic_failure_and_logger_failure_cannot_escape_task_boundary(monkeypatch):
    def failure(*args, **kwargs):
        raise OSError('diagnostic storage unavailable')
    monkeypatch.setattr(evidence, 'append_xianyuan_challenge_evidence', failure)
    class Recorder(DailyXianyuanTaskMixin):
        _log = staticmethod(failure)
    with job_log_context('legacy-daily-xianyuan', 'owned'):
        Recorder()._xianyuan_evidence('route', {'route': 'unknown'})


def test_task_context_is_bound_and_missing_context_never_records(monkeypatch):
    calls = []
    monkeypatch.setattr(evidence, 'append_xianyuan_challenge_evidence',
                        lambda task, attempt, event, facts: calls.append((task, attempt, event, facts)))
    recorder = DailyXianyuanTaskMixin()
    recorder._xianyuan_evidence('route', {'route': 'unknown'})
    assert not calls
    for attempt in ['a', 'b']:
        with job_log_context('legacy-daily-xianyuan', attempt):
            recorder._xianyuan_evidence('route', {'route': 'unknown'})
    assert [x[1] for x in calls] == ['a', 'b']
