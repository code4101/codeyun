from datetime import datetime
import json
import pytest
from backend.core.fanxiu.data_annotation.external_login_handoff import (
    observe_external_login_notice, read_external_login_handoff,
    require_external_login_wait_finished, FanxiuExternalLoginWait,
)
from backend.core.fanxiu.data_annotation.kernel_scheduler_control import defer_scheduler_tasks_until


def test_notice_fixed_deadline_survives_reobserve_and_expiry(tmp_path):
    path = tmp_path / "handoff.json"
    first = observe_external_login_notice(path=path, now=1000)
    assert first["resume_at"] == 2800
    assert observe_external_login_notice(path=path, now=2000)["resume_at"] == 2800
    with pytest.raises(FanxiuExternalLoginWait):
        require_external_login_wait_finished(path=path, now=2799)
    state = read_external_login_handoff(path=path, now=2800)
    assert not state["blocked"] and state["waiting_for_due_job"]
    assert observe_external_login_notice(path=path, now=9000)["resume_at"] == 2800


def test_invalid_receipt_cannot_silently_allow_login(tmp_path):
    path = tmp_path / "handoff.json"
    path.write_text('{broken', encoding="utf-8")
    with pytest.raises(ValueError):
        read_external_login_handoff(path=path)
    path.write_text(json.dumps({"status":"waiting", "detected_at":1000, "resume_at":1100}), encoding="utf-8")
    with pytest.raises(RuntimeError):
        read_external_login_handoff(path=path)


def test_operator_can_confirm_elapsed_wait_without_completing_notice(tmp_path, monkeypatch):
    from backend.core.fanxiu.data_annotation import external_login_handoff as api
    path = tmp_path / "handoff.json"
    api.observe_external_login_notice(path=path, now=1000)
    monkeypatch.setattr(api.time, "time", lambda: 1100)
    state = api.confirm_external_login_wait_elapsed(path=path, evidence={"source": "operator"})
    assert state["status"] == "waiting" and state["waiting_for_due_job"]
    assert not state["blocked"] and state["resume_at"] == 2800
    assert api.observe_external_login_notice(path=path, now=1200)["resume_at"] == 2800
    api.complete_external_login_handoff(path=path)
    assert api.read_external_login_handoff(path=path)["status"] == "completed"
    fresh = api.observe_external_login_notice(path=path, now=4000)
    assert fresh["blocked"] and "operator_confirmed_elapsed_at" not in fresh


def test_batch_defers_only_existing_earlier_triggers_and_preserves_attempts(tmp_path):
    path = tmp_path / "tasks.json"
    tasks = [
        {"id":"overdue", "next_time":"2026-10-01 10:00:00", "attempt_id":"original"},
        {"id":"soon", "next_time":"2026-10-01 11:10:00"},
        {"id":"later", "next_time":"2026-10-01 12:00:00"},
        {"id":"unscheduled", "next_time":None},
    ]
    path.write_text(json.dumps(tasks),encoding="utf-8")
    deadline = datetime(2026,10,1,11,20)
    assert defer_scheduler_tasks_until(deadline,scheduler_state_path=path) == ["overdue","soon"]
    result = json.loads(path.read_text(encoding="utf-8"))
    assert [t["next_time"] for t in result] == ["2026-10-01 11:20:00"]*2+["2026-10-01 12:00:00",None]
    assert result[0]["attempt_id"] == "original"
    assert defer_scheduler_tasks_until(deadline,scheduler_state_path=path) == []


def test_new_fact_trigger_cannot_bypass_an_active_handoff(tmp_path):
    import time
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import (
        set_scheduler_task_next_time, advance_scheduler_task_from_fact,
    )
    path = tmp_path / "tasks.json"
    current = time.time()
    gate = observe_external_login_notice(path=tmp_path / "external_login_handoff.json", now=current)
    deadline = datetime.fromtimestamp(gate["resume_at"]).strftime("%Y-%m-%d %H:%M:%S")
    path.write_text(json.dumps([{"id":"a","next_time":None}]),encoding="utf-8")
    assert set_scheduler_task_next_time("a",datetime.fromtimestamp(current),scheduler_state_path=path) == deadline
    set_scheduler_task_next_time("a",None,scheduler_state_path=path)
    assert advance_scheduler_task_from_fact("a",datetime.fromtimestamp(current),scheduler_state_path=path) == deadline


def test_dismissed_notice_ends_handoff_and_next_notice_starts_fresh_cd(tmp_path,monkeypatch):
    from backend.core.fanxiu.data_annotation import external_login_handoff as api
    path=tmp_path/"handoff.json"
    api.observe_external_login_notice(path=path,now=1000)
    monkeypatch.setattr(api.time,"time",lambda:2000)
    with pytest.raises(api.FanxiuExternalLoginWait):
        api.complete_external_login_handoff(path=path)
    monkeypatch.setattr(api.time,"time",lambda:2800)
    api.complete_external_login_handoff(path=path)
    assert api.read_external_login_handoff(path=path)["status"] == "completed"
    fresh=api.observe_external_login_notice(path=path,now=4000)
    assert fresh["detected_at"] == 4000 and fresh["resume_at"] == 5800
