from contextlib import contextmanager

import pytest
from fastapi import HTTPException

from backend.api import device_control


def test_execute_attendance_order_delegates_to_shared_service(monkeypatch):
    captured = {}
    events = []

    @contextmanager
    def fake_ctx():
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    def fake_execute_order_action(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(device_control, "ensure_ui_automation_thread_context", fake_ctx)
    monkeypatch.setattr(device_control, "execute_order_action", fake_execute_order_action)

    req = device_control.AttendanceOrderExecuteRequest(
        action="inspect",
        rows=[{"商户订单号": "MA2026"}],
        login_users=["考勤后台"],
        lookup_mode="browser_only",
    )

    result = device_control.execute_attendance_order(req)

    assert result == {"ok": True}
    assert captured == {
        "action": "inspect",
        "rows": [{"商户订单号": "MA2026"}],
        "weipay_login_users": ["考勤后台"],
        "lookup_mode": "browser_only",
    }
    assert events == ["enter", "exit"]


def test_execute_attendance_order_maps_business_error(monkeypatch):
    @contextmanager
    def fake_ctx():
        yield

    monkeypatch.setattr(device_control, "ensure_ui_automation_thread_context", fake_ctx)

    def fake_execute_order_action(**kwargs):
        raise device_control.OrderAutomationError("订单动作非法")

    monkeypatch.setattr(device_control, "execute_order_action", fake_execute_order_action)

    req = device_control.AttendanceOrderExecuteRequest(action="refund", rows=[])
    with pytest.raises(HTTPException) as exc:
        device_control.execute_attendance_order(req)

    assert exc.value.status_code == 400
    assert exc.value.detail == "订单动作非法"


def test_device_control_order_request_defaults_to_browser_only():
    req = device_control.AttendanceOrderExecuteRequest(action="inspect", rows=[])

    assert req.lookup_mode == "browser_only"
