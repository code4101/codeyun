"""Persistence/dispatch contracts only; no simulated game or GUI."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlmodel import SQLModel, Session, create_engine
from backend.core.fanxiu.activity.ranking_lifecycle import RankingCheckpoint
from backend.core.fanxiu.data_annotation import ranking_escalation as escalation


@pytest.fixture
def incident(monkeypatch, tmp_path):
    engine = create_engine('sqlite:///:memory:')
    SQLModel.metadata.create_all(engine)
    checkpoint = RankingCheckpoint('instance', 'xutian-palace', 'gameplay_rank', '123', 8080001,
                                   'exchange_tail', '2026-09-25', datetime.now(timezone.utc))
    dispatched = []
    def dispatch(request):
        dispatched.append(request)
        return SimpleNamespace(dispatch_id='abc123', request_path='request.json')
    monkeypatch.setattr(escalation, 'request_ai_assistance', dispatch)
    monkeypatch.setattr(escalation, 'read_scheduler_settings', lambda: {'job_group_enabled': True})
    monkeypatch.setattr(escalation, 'inspect_codex_dispatch', lambda ident: SimpleNamespace(status='running', error=None))
    monkeypatch.setattr(escalation, 'codeyun_temp_root', lambda *args: tmp_path)
    with Session(engine) as session:
        def report(error):
            return escalation.report_ranking_failure(session, checkpoint=checkpoint, occurrence='exact occurrence',
                error=error, task_id='ranking-lifecycle', entry_id='entry')
        yield report, dispatched


def test_missing_executor_escalates_immediately_and_deduplicates(incident):
    report, dispatched = incident
    assert report(escalation.RankingCapabilityMissing('missing'))['status'] == 'dispatched'
    report(escalation.RankingCapabilityMissing('missing'))
    assert len(dispatched) == 1
    assert dispatched[0].evidence


def test_same_technical_failure_retries_once_then_escalates(incident):
    report, dispatched = incident
    assert report(RuntimeError('contract broken'))['status'] == 'retry_once'
    assert not dispatched
    assert report(RuntimeError('contract broken'))['status'] == 'dispatched'
    assert len(dispatched) == 1


def test_ai_owner_prevents_recursive_agent_dispatch(incident, monkeypatch):
    report, dispatched = incident
    monkeypatch.setattr(escalation, 'read_scheduler_settings', lambda: {'job_group_enabled': False})
    assert report(escalation.RankingCapabilityMissing('missing'))['status'] == 'current_ai_owner'
    assert not dispatched


def test_failed_delivery_is_visible_and_rate_limited(incident, monkeypatch):
    report, dispatched = incident
    def fail(request):
        dispatched.append(request)
        raise RuntimeError('transport unavailable')
    monkeypatch.setattr(escalation, 'request_ai_assistance', fail)
    state = report(escalation.RankingCapabilityMissing('missing'))
    assert state['status'] == 'dispatch_failed'
    assert 'transport unavailable' in state['dispatch_error']
    report(escalation.RankingCapabilityMissing('missing'))
    assert len(dispatched) == 1
