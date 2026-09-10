from datetime import datetime
from types import SimpleNamespace
import pytest
from backend.core.fanxiu.data_annotation.tasks import tianjige_forum_quiz as task
from backend.core.fanxiu.tianjige_forum_quiz import TianjigeQuizProbe

@pytest.mark.parametrize("launch_fails", [False, True])
def test_review_dispatch_preserves_owner_and_deduplicates(monkeypatch, launch_fails):
    import backend.core.codex as codex
    from backend.core.fanxiu.data_annotation import kernel_scheduler_control as control
    state = {}
    calls = []
    monkeypatch.setattr(task, "_read_submission_ledger", lambda: dict(state))
    monkeypatch.setattr(task, "_write_submission_ledger", lambda value: state.update(value))
    monkeypatch.setattr(control, "read_scheduler_settings", lambda: {"job_group_enabled": True})
    def forbidden(*args, **kwargs):
        raise AssertionError("engineering must not change owner")
    monkeypatch.setattr(control, "set_scheduler_job_group_enabled", forbidden)
    def dispatch(request):
        calls.append(request)
        if launch_fails:
            raise OSError("launch failed")
        return SimpleNamespace(dispatch_id="one")
    monkeypatch.setattr(codex, "escalate_to_codex", dispatch)
    logger = SimpleNamespace(_log=lambda *args: None)
    for _ in range(2):
        task._request_missing_thread_review(logger, datetime(2026, 9, 10, 18, 11), TianjigeQuizProbe(status="waiting_thread"))
    assert len(calls) == (2 if launch_fails else 1)
    assert bool(state.get("missing_thread_review_date")) is not launch_fails

def test_agent_review_requires_evidence_and_preserves_submission(monkeypatch):
    state = {"thread_key": "old", "state": "submitted"}
    monkeypatch.setattr(task, "_read_submission_ledger", lambda: dict(state))
    monkeypatch.setattr(task, "_write_submission_ledger", lambda value: state.update(value))
    with pytest.raises(ValueError):
        task.record_tianjige_missing_thread_review("2026-09-10", "")
    task.record_tianjige_missing_thread_review("2026-09-10", "Latest visible post is yesterday")
    assert state["thread_key"] == "old"
    assert state["missing_thread_review_date"] == "2026-09-10"
