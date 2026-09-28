from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.core.fanxiu.data_annotation.login_recovery import (
    LoginProgress, clear_login_recovery, loading_progress, reserve_login_recovery,
)


def test_progress_deadline_ignores_missing_and_regressed_ocr():
    progress = LoginProgress()
    assert progress.observe("loading", 0, 69) == 0
    assert progress.observe("loading", 299, None) == 299
    assert progress.observe("loading", 300, 68) == 300
    assert progress.observe("loading", 301, 70) == 0
    assert progress.observe("loading", 601, 70) == 300
    assert progress.observe("login", 602) == 0


@pytest.mark.parametrize(("text", "expected"), [
    ("资源 69%", 69), ("资源 69.5％", 69.5), ("AppVer:2 ResVer:3", None),
    ("101%", None), ("69% 70%", None),
])
def test_loading_percentage(text, expected):
    assert loading_progress(text) == expected


def test_budget_survives_new_callers_and_only_login_success_resets(tmp_path):
    path = tmp_path / "login_recovery.json"
    assert reserve_login_recovery(path, now=1000)["attempts"] == 1
    with pytest.raises(RuntimeError, match="冷却"):
        reserve_login_recovery(path, now=1299)
    assert reserve_login_recovery(path, now=1300)["attempts"] == 2
    with pytest.raises(RuntimeError, match="2 次"):
        reserve_login_recovery(path, now=100000)
    clear_login_recovery(path)
    assert reserve_login_recovery(path, now=100001)["attempts"] == 1


def test_concurrent_callers_cannot_reserve_two_restarts(tmp_path):
    path = tmp_path / "login_recovery.json"

    def reserve():
        try:
            reserve_login_recovery(path, now=1000)
            return True
        except RuntimeError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sum(executor.map(lambda _: reserve(), range(2))) == 1


def test_invalid_budget_never_grants_restart(tmp_path):
    path = tmp_path / "login_recovery.json"
    path.write_text("broken", encoding="utf-8")
    with pytest.raises(ValueError):
        reserve_login_recovery(path, now=1000)


def test_scheduler_recovers_only_after_terminal_under_same_lease(monkeypatch, tmp_path):
    """Exercise attempt ownership, not a simulated game/scene sequence."""
    from backend.core.fanxiu.data_annotation import kernel_scheduler_control as control
    from backend.core.fanxiu.client import mumu_control
    from backend.core.fanxiu.behavior_tree.kernel import FanxiuKernel

    events = []

    def terminal(**kwargs):
        events.append(("attempt", kwargs["scheduled_attempt"]))
        if len(events) == 1:
            return {"status": "error", "error_type": "FanxiuLoginStalled", "error": "stalled"}
        return {"status": "success", "phase": "done"}

    def restart_game(**kwargs):
        assert kwargs["dispatch_lease"].is_locked
        events.append("game")

    def restart_kernel(self, **kwargs):
        events.append("kernel")
        return {"ok": True}

    monkeypatch.setattr(control, "_run_scheduler_task_cell_and_record_terminal_owned", terminal)
    monkeypatch.setattr(mumu_control, "restart_fanxiu_game_after_ui_failure", restart_game)
    monkeypatch.setattr(FanxiuKernel, "restart", restart_kernel)
    monkeypatch.setattr(control, "ensure_scheduler_kernel_code_current", lambda **kw: {"ready": True})
    result = control._run_scheduler_task_cell_and_record_terminal(
        entry=None, entry_id="test", task={"task_type": "login_game"},
        scheduler_state_path=tmp_path / "scheduler.json", scheduled_attempt=True,
    )
    assert result["status"] == "success"
    assert events == [("attempt", True), "game", "kernel", ("attempt", False)]
    assert not (tmp_path / "login_recovery.json").exists()


@pytest.mark.parametrize("error_type", ["RuntimeError", "SceneWaitTimeout", None])
def test_other_errors_never_authorize_restart(monkeypatch, tmp_path, error_type):
    from backend.core.fanxiu.data_annotation import kernel_scheduler_control as control

    failure = {"status": "error", "error_type": error_type}
    monkeypatch.setattr(control, "_run_scheduler_task_cell_and_record_terminal_owned", lambda **kw: failure)
    result = control._run_scheduler_task_cell_and_record_terminal(
        entry=None, entry_id="test", task={"task_type": "login_game"},
        scheduler_state_path=tmp_path / "scheduler.json",
    )
    assert result == failure
    assert not (tmp_path / "login_recovery.json").exists()


def test_exhausted_budget_is_reported_without_another_restart(monkeypatch, tmp_path):
    from backend.core.fanxiu.data_annotation import kernel_scheduler_control as control
    from backend.core.fanxiu.client import mumu_control

    path = tmp_path / "login_recovery.json"
    reserve_login_recovery(path, now=1000)
    reserve_login_recovery(path, now=1300)
    failure = {"status": "error", "error_type": "FanxiuLoginStalled", "error": "stalled"}
    monkeypatch.setattr(control, "_run_scheduler_task_cell_and_record_terminal_owned", lambda **kw: failure)
    monkeypatch.setattr(control, "read_scheduler_tasks", lambda **kw: [])
    monkeypatch.setattr(control, "read_scheduler_settings", lambda **kw: {"job_group_enabled": False})
    incidents = []
    monkeypatch.setattr(control, "record_scheduler_incident", lambda **kw: incidents.append(kw))

    def forbidden(**kwargs):
        pytest.fail("exhausted budget must not restart")

    monkeypatch.setattr(mumu_control, "restart_fanxiu_game_after_ui_failure", forbidden)
    result = control._run_scheduler_task_cell_and_record_terminal(
        entry=None, entry_id="test", task={"task_type": "login_game"},
        scheduler_state_path=tmp_path / "scheduler.json",
    )
    assert result["recovery_status"] == "diagnosis_required"
    assert "2 次" in result["message"]
    assert incidents[0]["incident"]["kind"] == "login_recovery_failed"


def test_diagnosis_dispatch_is_deduplicated_until_success(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from backend.core import codex
    from backend.core.fanxiu.data_annotation.login_recovery import request_login_recovery_diagnosis

    calls = []

    def dispatch(request):
        calls.append(request)
        return SimpleNamespace(model_dump=lambda: {"dispatch_id": "one"})

    monkeypatch.setattr(codex, "escalate_to_codex", dispatch)
    path = tmp_path / "login_recovery.json"
    for _ in range(2):
        assert request_login_recovery_diagnosis(path, detail="exhausted", entry_id="test", task_id="login")["dispatch_id"] == "one"
    assert len(calls) == 1
