"""Process/ownership contracts; no simulated game or GUI actions."""
import pytest

from backend.core.fanxiu.data_annotation import kernel_scheduler_control as control


@pytest.mark.parametrize("state", ["busy", "starting", "unknown"])
def test_handoff_does_not_release_an_active_or_unconfirmed_kernel(monkeypatch, state):
    monkeypatch.setattr(control, "kernel_scheduler_status", lambda: {
        "running": False, "kernel": {"alive": True, "execution_state": state},
    })
    def forbidden(*args, **kwargs):
        pytest.fail("ownership must remain unchanged until Kernel is idle")
    monkeypatch.setattr(control, "set_scheduler_job_group_enabled", forbidden)
    monkeypatch.setattr(control, "ensure_doctor_watch_background", forbidden)
    with pytest.raises(RuntimeError, match="Cell"):
        control.resume_engineering_control()


def test_handoff_enables_dispatch_before_ensuring_its_process(monkeypatch):
    calls = []
    monkeypatch.setattr(control, "kernel_scheduler_status", lambda: {
        "running": False, "kernel": {"alive": True, "execution_state": "idle"},
    })
    def enable(value):
        calls.append(("enabled", value))
        return {"job_group_enabled": value}
    def start():
        assert calls == [("enabled", True)]
        calls.append(("watch", True))
        return {"ok": True, "started": True, "pid": 123}
    monkeypatch.setattr(control, "set_scheduler_job_group_enabled", enable)
    monkeypatch.setattr(control, "ensure_doctor_watch_background", start)
    result = control.resume_engineering_control()
    assert calls == [("enabled", True), ("watch", True)]
    assert result["job_group_enabled"] is True
    assert result["watcher"]["pid"] == 123


def test_handoff_surfaces_dispatcher_startup_failure(monkeypatch):
    monkeypatch.setattr(control, "kernel_scheduler_status", lambda: {"kernel": {"alive": False}})
    monkeypatch.setattr(control, "set_scheduler_job_group_enabled", lambda value: {"job_group_enabled": value})
    monkeypatch.setattr(control, "ensure_doctor_watch_background", lambda: {"ok": False, "message": "spawn failed"})
    with pytest.raises(RuntimeError, match="spawn failed"):
        control.resume_engineering_control()
