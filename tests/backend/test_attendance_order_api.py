import os
from datetime import datetime
from contextlib import contextmanager

import pytest
from fastapi import HTTPException
from pyxllib.cv.rgbfmt import hash_text_to_hex_color
from pyxllib.prog.xlenv import XlEnv

from backend.api import attendance
from backend.core.attendance_service import (
    get_or_create_attendance_service_config,
    update_attendance_service_extra_config,
)
from backend.core.feature_access import FEATURE_ACCESS_SUBJECT_USER, save_feature_access_policy_overrides
from backend.models import AttendanceOrderRefundHistory, User, UserDevice


def _create_superuser(session):
    user = User(
        username="attendance-admin",
        email="attendance-admin@example.com",
        hashed_password="pw",
        is_active=True,
        is_superuser=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _create_local_device(session, *, user_id: int):
    entry = UserDevice(
        user_id=user_id,
        device_id="device-local-1",
        name="本机执行器",
        mode="local",
        server_url=None,
        token="device-token",
        is_active=True,
        order_index=0,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _grant_feature_access(session, *, user_id: int, feature_key: str) -> None:
    save_feature_access_policy_overrides(
        session,
        subject_type=FEATURE_ACCESS_SUBJECT_USER,
        subject_user_id=user_id,
        overrides={feature_key: "allow"},
    )


def test_execute_attendance_order_dispatches_and_persists_selection(session, monkeypatch):
    user = _create_superuser(session)
    entry = _create_local_device(session, user_id=user.id)
    captured = {}

    def fake_execute(entry_snapshot, execution_payload):
        captured["entry_snapshot"] = entry_snapshot
        captured["execution_payload"] = execution_payload
        return {
            "action": "inspect",
            "rows": [{"商户订单号": "MA2026"}],
            "summary": {"processed_count": 1},
        }

    monkeypatch.setattr(attendance, "_execute_order_on_entry", fake_execute)

    payload = attendance.AttendanceOrderExecuteRequest(
        action="inspect",
        rows=[{"商户订单号": "MA2026"}],
        execution_device_entry_id=entry.entry_id,
        login_users=["考勤后台"],
        order_lookup_mode="browser_only",
        persist_global_selection=True,
    )
    result = attendance.execute_attendance_order(payload, session=session, current_user=user)

    assert result["execution_device_entry_id"] == entry.entry_id
    assert captured["entry_snapshot"]["entry_id"] == entry.entry_id
    assert captured["execution_payload"] == {
        "action": "inspect",
        "rows": [{"商户订单号": "MA2026"}],
        "login_users": ["考勤后台"],
        "lookup_mode": "browser_only",
    }

    config = attendance.get_or_create_attendance_service_config(session)
    assert config.execution_device_entry_id == entry.entry_id


def test_execute_attendance_order_maps_business_error(session, monkeypatch):
    user = _create_superuser(session)
    entry = _create_local_device(session, user_id=user.id)

    def fake_execute(entry_snapshot, execution_payload):
        raise attendance.OrderAutomationError("退款额度非法")

    monkeypatch.setattr(attendance, "_execute_order_on_entry", fake_execute)

    payload = attendance.AttendanceOrderExecuteRequest(
        action="refund",
        rows=[{"商户订单号": "MA2026"}],
        execution_device_entry_id=entry.entry_id,
    )
    with pytest.raises(HTTPException) as exc:
        attendance.execute_attendance_order(payload, session=session, current_user=user)

    assert exc.value.status_code == 400
    assert exc.value.detail == "退款额度非法"


def test_execute_attendance_order_uses_global_scan_reminder_users(session, monkeypatch):
    user = _create_superuser(session)
    entry = _create_local_device(session, user_id=user.id)
    captured = {}

    config = get_or_create_attendance_service_config(session)
    config.execution_device_entry_id = entry.entry_id
    session.add(config)
    session.commit()
    update_attendance_service_extra_config(
        session,
        scan_reminder_users=["考勤后台", "文件传输助手"],
        order_lookup_mode="db_only",
    )

    def fake_execute(entry_snapshot, execution_payload):
        captured["execution_payload"] = execution_payload
        return {
            "action": "inspect",
            "rows": [{"商户订单号": "MA2026"}],
            "summary": {"processed_count": 1},
        }

    monkeypatch.setattr(attendance, "_execute_order_on_entry", fake_execute)

    payload = attendance.AttendanceOrderExecuteRequest(
        action="inspect",
        rows=[{"商户订单号": "MA2026"}],
    )
    attendance.execute_attendance_order(payload, session=session, current_user=user)

    assert captured["execution_payload"] == {
        "action": "inspect",
        "rows": [{"商户订单号": "MA2026"}],
        "login_users": ["考勤后台", "文件传输助手"],
        "lookup_mode": "db_only",
    }


def test_execute_attendance_order_includes_configured_operation_password_for_refund(session, monkeypatch):
    user = _create_superuser(session)
    entry = _create_local_device(session, user_id=user.id)
    captured = {}

    config = get_or_create_attendance_service_config(session)
    config.execution_device_entry_id = entry.entry_id
    session.add(config)
    session.commit()
    update_attendance_service_extra_config(
        session,
        order_operation_password="refund-pass",
    )

    def fake_execute(entry_snapshot, execution_payload):
        captured["execution_payload"] = execution_payload
        return {
            "action": "refund",
            "rows": [{"商户订单号": "MA2026", "执行退款": "已退款"}],
            "summary": {"processed_count": 1, "refunded_count": 1},
        }

    monkeypatch.setattr(attendance, "_execute_order_on_entry", fake_execute)

    payload = attendance.AttendanceOrderExecuteRequest(
        action="refund",
        rows=[{"商户订单号": "MA2026"}],
    )
    attendance.execute_attendance_order(payload, session=session, current_user=user)

    assert captured["execution_payload"] == {
        "action": "refund",
        "rows": [{"商户订单号": "MA2026"}],
        "login_users": [],
        "lookup_mode": "browser_only",
        "operation_password": "refund-pass",
    }


def test_execute_attendance_order_records_refund_history(session, monkeypatch):
    user = _create_superuser(session)
    entry = _create_local_device(session, user_id=user.id)

    def fake_execute(entry_snapshot, execution_payload):
        return {
            "action": "refund",
            "rows": [
                {
                    "学员名称": "石金峰",
                    "微信支付订单号": "`4200003006202602263330752835",
                    "商户订单号": "MA2026022616592804207819",
                    "订单金额": 499,
                    "已返款": 479,
                    "退款额度": 20,
                    "退款原因": "补差退款",
                    "执行退款": "已退款",
                },
                {
                    "学员名称": "李艳玲",
                    "微信支付订单号": "`4200003073202603232072084026",
                    "商户订单号": "TCCDN4-0OZRE8O-EI4L",
                    "订单金额": 620,
                    "已返款": 620,
                    "退款额度": 0,
                    "退款原因": "",
                    "执行退款": "无需退款",
                },
            ],
            "summary": {"processed_count": 2, "refunded_count": 1},
        }

    monkeypatch.setattr(attendance, "_execute_order_on_entry", fake_execute)

    payload = attendance.AttendanceOrderExecuteRequest(
        action="refund",
        rows=[{"商户订单号": "MA2026"}, {"商户订单号": "TCCDN4-0OZRE8O-EI4L"}],
        execution_device_entry_id=entry.entry_id,
    )
    result = attendance.execute_attendance_order(payload, session=session, current_user=user)

    page = attendance._build_order_refund_history_page(session, page=1, page_size=20)

    assert result["rows"][0]["微信支付订单号"] == "4200003006202602263330752835"
    assert result["rows"][1]["微信支付订单号"] == "4200003073202603232072084026"
    assert page.total == 2
    assert page.items[0].merchant_order_id == "TCCDN4-0OZRE8O-EI4L"
    assert page.items[0].operator_username == user.username
    assert page.items[0].operator_name == user.username
    assert page.items[0].foreground_colors.operator == hash_text_to_hex_color(user.username, tone="dark")
    assert page.items[1].wechat_order_id == "4200003006202602263330752835"
    assert page.items[1].refund_reason == "补差退款"
    assert page.items[1].remaining_amount == "20"


def test_execute_attendance_order_refund_history_uses_result_timestamps_and_interpolation(session, monkeypatch):
    user = _create_superuser(session)
    entry = _create_local_device(session, user_id=user.id)

    def fake_execute(entry_snapshot, execution_payload):
        return {
            "action": "refund",
            "rows": [
                {
                    "学员名称": "甲",
                    "商户订单号": "MA-1",
                    "执行退款": "2026/04/18 10:00:00 已退款",
                },
                {
                    "学员名称": "乙",
                    "商户订单号": "MA-2",
                    "执行退款": "已退款",
                },
                {
                    "学员名称": "丙",
                    "商户订单号": "MA-3",
                    "执行退款": "2026/04/18 10:00:20 已退款",
                },
            ],
            "summary": {"processed_count": 3, "refunded_count": 3},
        }

    monkeypatch.setattr(attendance, "_execute_order_on_entry", fake_execute)

    payload = attendance.AttendanceOrderExecuteRequest(
        action="refund",
        rows=[{"商户订单号": "MA-1"}, {"商户订单号": "MA-2"}, {"商户订单号": "MA-3"}],
        execution_device_entry_id=entry.entry_id,
    )
    attendance.execute_attendance_order(payload, session=session, current_user=user)

    page = attendance._build_order_refund_history_page(session, page=1, page_size=20)

    assert [item.merchant_order_id for item in page.items] == ["MA-3", "MA-2", "MA-1"]
    assert page.items[0].created_at == pytest.approx(datetime(2026, 4, 18, 10, 0, 20).timestamp())
    assert page.items[1].created_at == pytest.approx(datetime(2026, 4, 18, 10, 0, 10).timestamp())
    assert page.items[2].created_at == pytest.approx(datetime(2026, 4, 18, 10, 0, 0).timestamp())
    assert page.items[0].foreground_colors.created_day == hash_text_to_hex_color("2026/04/18", tone="dark")


def test_execute_attendance_order_does_not_record_history_for_inspect(session, monkeypatch):
    user = _create_superuser(session)
    entry = _create_local_device(session, user_id=user.id)

    monkeypatch.setattr(
        attendance,
        "_execute_order_on_entry",
        lambda entry_snapshot, execution_payload: {
            "action": "inspect",
            "rows": [{"商户订单号": "MA2026", "订单金额": 499, "已返款": 0}],
            "summary": {"processed_count": 1},
        },
    )

    payload = attendance.AttendanceOrderExecuteRequest(
        action="inspect",
        rows=[{"商户订单号": "MA2026"}],
        execution_device_entry_id=entry.entry_id,
    )
    attendance.execute_attendance_order(payload, session=session, current_user=user)

    page = attendance._build_order_refund_history_page(session, page=1, page_size=20)
    assert page.total == 0


def test_order_refund_history_page_is_sorted_by_latest_first(session):
    session.add_all(
        [
            AttendanceOrderRefundHistory(
                operator_username="tester",
                operator_nickname="",
                student_name="甲",
                merchant_order_id="MA-1",
                created_at=101.0,
            ),
            AttendanceOrderRefundHistory(
                operator_username="tester",
                operator_nickname="",
                student_name="乙",
                merchant_order_id="MA-2",
                created_at=202.0,
            ),
            AttendanceOrderRefundHistory(
                operator_username="tester",
                operator_nickname="",
                student_name="丙",
                merchant_order_id="MA-3",
                created_at=303.0,
            ),
        ]
    )
    session.commit()

    page = attendance._build_order_refund_history_page(session, page=2, page_size=1)

    assert page.total == 3
    assert page.page == 2
    assert page.page_size == 1
    assert len(page.items) == 1
    assert page.items[0].merchant_order_id == "MA-2"


def test_order_refund_history_page_normalizes_legacy_prefixed_order_ids(session):
    session.add(
        AttendanceOrderRefundHistory(
            operator_username="tester",
            operator_nickname="",
            student_name="甲",
            wechat_order_id="`420000000000000000000001",
            merchant_order_id="`MA-1",
            created_at=101.0,
        )
    )
    session.commit()

    page = attendance._build_order_refund_history_page(session, page=1, page_size=20)

    assert page.items[0].wechat_order_id == "420000000000000000000001"
    assert page.items[0].merchant_order_id == "MA-1"


def test_execute_order_on_entry_initializes_ui_automation_context_for_local_device(monkeypatch):
    events = []

    @contextmanager
    def fake_ctx():
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    monkeypatch.setattr(attendance, "ensure_ui_automation_thread_context", fake_ctx)
    monkeypatch.setattr(attendance, "get_device_id", lambda: "device-local-1")
    monkeypatch.delenv("XL_KQ_PAY_PASSWORD", raising=False)

    def fake_execute_order_action(**kwargs):
        return {
            "ok": kwargs["lookup_mode"],
            "operation_password": XlEnv.get("XL_KQ_PAY_PASSWORD", decoding=True),
        }

    monkeypatch.setattr(attendance, "execute_order_action", fake_execute_order_action)

    result = attendance._execute_order_on_entry(
        {"mode": "local", "device_id": "device-local-1"},
        {"action": "refund", "rows": [], "login_users": [], "lookup_mode": "hybrid", "operation_password": "refund-pass"},
    )

    assert result == {"ok": "hybrid", "operation_password": "refund-pass"}
    assert events == ["enter", "exit"]
    assert os.getenv("XL_KQ_PAY_PASSWORD") is None


def test_order_execute_endpoint_requires_orders_feature(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="device-local-orders",
        name="本机订单设备",
        mode="local",
        token="device-token",
        is_active=True,
        order_index=0,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    config = get_or_create_attendance_service_config(session)
    config.granted_user_ids = [auth_user.id]
    session.add(config)
    session.commit()

    _grant_feature_access(session, user_id=auth_user.id, feature_key="attendance.wjx-templates")

    monkeypatch.setattr(
        attendance,
        "_execute_order_on_entry",
        lambda entry_snapshot, execution_payload: {"action": "inspect", "rows": [], "summary": {"processed_count": 0}},
    )

    response = client.post(
        "/api/attendance/order-execute",
        json={
            "action": "inspect",
            "rows": [{"商户订单号": "MA2026"}],
            "execution_device_entry_id": entry.entry_id,
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "当前账号无权访问该功能"
