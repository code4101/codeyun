from backend.core import attendance_behavior_tree_service as service


def test_ensure_attendance_service_reuses_running_process(monkeypatch):
    status = {"running": True, "pid": 123, "state": "running"}
    monkeypatch.setattr(service, "get_attendance_behavior_tree_status", lambda: status)
    monkeypatch.setattr(
        service,
        "start_attendance_behavior_tree_service",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not restart a running scheduler")),
    )

    result = service.ensure_attendance_behavior_tree_service()

    assert result == {"status": "already_running", "pid": 123, "service": status}


def test_ensure_attendance_service_starts_missing_process_without_replacing(monkeypatch):
    calls = []
    monkeypatch.setattr(
        service,
        "get_attendance_behavior_tree_status",
        lambda: {"running": False, "pid": None, "state": "stopped"},
    )
    monkeypatch.setattr(
        service,
        "start_attendance_behavior_tree_service",
        lambda **kwargs: calls.append(kwargs) or {"status": "started", "pid": 456},
    )

    result = service.ensure_attendance_behavior_tree_service()

    assert calls == [{"replace_existing": False}]
    assert result == {"status": "started", "pid": 456}
