import os
from datetime import datetime
from contextlib import contextmanager

import pytest
from fastapi import HTTPException
from pyxllib.cv.rgbfmt import hash_text_to_hex_color
from pyxllib.prog.xlenv import XlEnv

from backend.api import attendance
from backend.core.attendance.service import (
    get_or_create_attendance_service_config,
    update_attendance_service_extra_config,
)
from backend.core.access.feature_access import FEATURE_ACCESS_SUBJECT_USER, save_feature_access_policy_overrides
from backend.models import AttendanceOrderRefundHistory, SheetDocument, User, UserDevice, WorkbookDocument, WorkbookSheetLink


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


def test_check_attendance_sheet_refunded_amounts_uses_order_execute_entry(session, monkeypatch):
    user = _create_superuser(session)
    entry = _create_local_device(session, user_id=user.id)
    config = get_or_create_attendance_service_config(session)
    config.execution_device_entry_id = entry.entry_id
    session.add(config)

    workbook = WorkbookDocument(id="wb-refund-check", numeric_id=9101, title="第48届觉观")
    attendance_sheet = SheetDocument(
        id="sheet-attendance-refund-check",
        numeric_id=9102,
        scope="notes",
        owner_type="course",
        owner_key="jueguan-48",
        sheet_key="attendance",
        title="考勤表",
        document_json={
            "columns": ["学号", "姓名", "已返款", "订单金额"],
            "rows": [["1_01", "甲", "14", "499"], ["1_02", "乙", "0", "499"]],
            "data_start_row": 3,
        },
    )
    registration_sheet = SheetDocument(
        id="sheet-registration-refund-check",
        numeric_id=9103,
        scope="notes",
        owner_type="course",
        owner_key="jueguan-48",
        sheet_key="registration",
        title="报名表",
        document_json={
            "columns": ["序号", "姓名", "微信支付订单号", "商户订单号", "订单金额"],
            "rows": [
                ["1_01", "甲", "4200001", "MA202607010001", "499"],
                ["1_02", "乙", "4200002", "MA202607010002", "499"],
            ],
        },
    )
    session.add(workbook)
    session.add(attendance_sheet)
    session.add(registration_sheet)
    session.add(WorkbookSheetLink(workbook_id=workbook.id, sheet_id=attendance_sheet.id, order_index=5))
    session.add(WorkbookSheetLink(workbook_id=workbook.id, sheet_id=registration_sheet.id, order_index=10))
    session.commit()

    captured = {}

    def fake_execute(entry_snapshot, execution_payload):
        captured["entry_snapshot"] = entry_snapshot
        captured["execution_payload"] = execution_payload
        return {
            "action": "inspect",
            "rows": [
                {"商户订单号": "MA202607010001", "订单金额": 499, "已返款": 14},
                {"商户订单号": "MA202607010002", "订单金额": 499, "已返款": 19},
            ],
            "summary": {"processed_count": 2},
        }

    monkeypatch.setattr(attendance, "_load_submitted_refund_amounts_from_csv", lambda _document=None: {})
    monkeypatch.setattr(attendance, "_execute_order_on_entry", fake_execute)

    result = attendance.check_attendance_sheet_refunded_amounts(
        9102,
        attendance.AttendanceSheetRefundedCheckRequest(workbook_id=9101),
        session=session,
        current_user=user,
    )

    assert captured["entry_snapshot"]["entry_id"] == entry.entry_id
    assert captured["execution_payload"]["action"] == "inspect"
    assert captured["execution_payload"]["rows"][0]["商户订单号"] == "MA202607010001"
    assert result["summary"]["matched_count"] == 1
    assert result["summary"]["mismatch_count"] == 1
    assert result["rows"][0]["status"] == "matched"
    assert result["rows"][1]["status"] == "mismatch"
    assert result["rows"][1]["payment_refunded_amount"] == "19"


def test_check_attendance_sheet_refunded_amounts_prefers_submitted_csv_history(session, monkeypatch):
    user = _create_superuser(session)
    entry = _create_local_device(session, user_id=user.id)
    config = get_or_create_attendance_service_config(session)
    config.execution_device_entry_id = entry.entry_id
    session.add(config)

    workbook = WorkbookDocument(id="wb-refund-check-csv", numeric_id=9111, title="第48届觉观")
    attendance_sheet = SheetDocument(
        id="sheet-attendance-refund-check-csv",
        numeric_id=9112,
        scope="notes",
        owner_type="course",
        owner_key="jueguan-48",
        sheet_key="attendance",
        title="考勤表",
        document_json={
            "columns": ["学号", "姓名", "已返款", "订单金额"],
            "rows": [["1_01", "甲", "14", "499"], ["1_02", "乙", "0", "499"]],
            "data_start_row": 3,
        },
    )
    registration_sheet = SheetDocument(
        id="sheet-registration-refund-check-csv",
        numeric_id=9113,
        scope="notes",
        owner_type="course",
        owner_key="jueguan-48",
        sheet_key="registration",
        title="报名表",
        document_json={
            "columns": ["序号", "姓名", "微信支付订单号", "商户订单号", "订单金额"],
            "rows": [
                ["1_01", "甲", "4200001", "MA202607010001", "499"],
                ["1_02", "乙", "4200002", "MA202607010002", "499"],
            ],
        },
    )
    session.add(workbook)
    session.add(attendance_sheet)
    session.add(registration_sheet)
    session.add(WorkbookSheetLink(workbook_id=workbook.id, sheet_id=attendance_sheet.id, order_index=5))
    session.add(WorkbookSheetLink(workbook_id=workbook.id, sheet_id=registration_sheet.id, order_index=10))
    session.commit()

    monkeypatch.setattr(
        attendance,
        "_load_submitted_refund_amounts_from_csv",
        lambda _document=None: {"MA202607010001": 14.0},
    )

    def fail_execute(*_args, **_kwargs):
        raise AssertionError("submitted refund CSV is the primary history source")

    monkeypatch.setattr(attendance, "_execute_order_on_entry", fail_execute)

    result = attendance.check_attendance_sheet_refunded_amounts(
        9112,
        attendance.AttendanceSheetRefundedCheckRequest(workbook_id=9111),
        session=session,
        current_user=user,
    )

    assert result["summary"]["matched_count"] == 2
    assert result["summary"]["mismatch_count"] == 0
    assert result["rows"][0]["payment_refunded_amount"] == "14"
    assert result["rows"][1]["payment_refunded_amount"] == "0"


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
    monkeypatch.setattr(
        attendance,
        "_execute_order_refund_details_on_entry",
        lambda entry_snapshot, execution_payload: {
            "summary": {"row_count": 1, "refund_amount_total": 479},
            "rows": [{"refund_amount": 479, "refund_status": "退款成功"}],
        },
    )

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


def test_execute_attendance_order_blocks_history_when_refund_confirmation_fails(session, monkeypatch):
    user = _create_superuser(session)
    entry = _create_local_device(session, user_id=user.id)

    monkeypatch.setattr(
        attendance,
        "_execute_order_on_entry",
        lambda entry_snapshot, execution_payload: {
            "action": "refund",
            "rows": [
                {
                    "学员名称": "王诗语",
                    "商户订单号": "TETVM2-0OZRE8O-17S8",
                    "订单金额": 620,
                    "已返款": 620,
                    "退款额度": 70,
                    "执行退款": "已退款",
                },
            ],
            "summary": {"processed_count": 1, "refunded_count": 1},
        },
    )
    monkeypatch.setattr(
        attendance,
        "_execute_order_refund_details_on_entry",
        lambda entry_snapshot, execution_payload: {
            "summary": {"row_count": 17, "refund_amount_total": 550},
            "rows": [{"refund_amount": 550, "refund_status": "退款成功"}],
        },
    )

    payload = attendance.AttendanceOrderExecuteRequest(
        action="refund",
        rows=[{"商户订单号": "TETVM2-0OZRE8O-17S8"}],
        execution_device_entry_id=entry.entry_id,
    )
    with pytest.raises(HTTPException) as exc:
        attendance.execute_attendance_order(payload, session=session, current_user=user)

    assert exc.value.status_code == 409
    assert "退款执行后支付侧确认失败" in str(exc.value.detail)
    page = attendance._build_order_refund_history_page(session, page=1, page_size=20)
    assert page.total == 0


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


def test_remote_order_requests_bypass_system_proxy(monkeypatch):
    captured = []

    class FakeResponse:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class FakeSession:
        def __init__(self):
            self.trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, *, json, headers, timeout):
            captured.append(
                {
                    "trust_env": self.trust_env,
                    "url": url,
                    "json": json,
                    "headers": headers,
                    "timeout": timeout,
                }
            )
            if url.endswith("/refund-details"):
                return FakeResponse({"summary": {"row_count": 1}, "rows": [{"refund_amount": 20}]})
            return FakeResponse({"action": "inspect", "rows": [], "summary": {"processed_count": 0}})

    monkeypatch.setattr(attendance.requests, "Session", FakeSession)
    entry_snapshot = {
        "entry_id": "remote-entry",
        "user_id": 1,
        "device_id": "remote-device",
        "name": "codepc_mi15",
        "mode": "remote",
        "server_url": "http://192.168.31.15:8000",
        "token": "device-token",
        "is_active": True,
        "order_index": 0,
        "created_at": 1.0,
        "updated_at": 2.0,
    }

    attendance._execute_order_on_entry(
        entry_snapshot,
        {"action": "inspect", "rows": [], "login_users": [], "lookup_mode": "browser_only"},
    )
    attendance._execute_order_refund_details_on_entry(
        entry_snapshot,
        {"order_id": "TBTF7N-0OZRE8O-IDY5", "query_type": "auto", "login_users": ["考勤后台"]},
    )

    assert [item["trust_env"] for item in captured] == [False, False]
    assert captured[0]["url"] == "http://192.168.31.15:8000/api/device-control/attendance/order/execute"
    assert captured[1]["url"] == "http://192.168.31.15:8000/api/device-control/attendance/order/refund-details"
    assert captured[0]["headers"]["X-Device-Token"] == "device-token"


def test_remote_order_refund_details_retries_auto_empty_with_precise_type(monkeypatch):
    captured = []

    class FakeResponse:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class FakeSession:
        def __init__(self):
            self.trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, *, json, headers, timeout):
            captured.append({"json": json, "trust_env": self.trust_env})
            if json.get("query_type") == "merchant_order":
                return FakeResponse({"summary": {"row_count": 1, "refund_amount_total": 550}, "rows": [{"refund_amount": 550}]})
            return FakeResponse({"summary": {"row_count": 0}, "rows": []})

    monkeypatch.setattr(attendance.requests, "Session", FakeSession)
    entry_snapshot = {
        "entry_id": "remote-entry",
        "user_id": 1,
        "device_id": "remote-device",
        "name": "codepc_mi15",
        "mode": "remote",
        "server_url": "http://192.168.31.15:8000",
        "token": "device-token",
        "is_active": True,
        "order_index": 0,
        "created_at": 1.0,
        "updated_at": 2.0,
    }

    result = attendance._execute_order_refund_details_on_entry(
        entry_snapshot,
        {"order_id": "TETVM2-0OZRE8O-17S8", "query_type": "auto", "login_users": ["考勤后台"]},
    )

    assert result["summary"]["refund_amount_total"] == 550
    assert [item["json"]["query_type"] for item in captured] == ["auto", "merchant_order"]
    assert [item["trust_env"] for item in captured] == [False, False]


def test_order_execute_endpoint_allows_orders_feature_without_service_grant(client, session, auth_user, monkeypatch):
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
    config.execution_device_entry_id = entry.entry_id
    config.granted_user_ids = []
    session.add(config)
    session.commit()

    _grant_feature_access(session, user_id=auth_user.id, feature_key="attendance.orders")

    monkeypatch.setattr(
        attendance,
        "_execute_order_on_entry",
        lambda entry_snapshot, execution_payload: {
            "action": "inspect",
            "rows": [{"商户订单号": "MA2026"}],
            "summary": {"processed_count": 1},
        },
    )

    response = client.post(
        "/api/attendance/order-execute",
        json={
            "action": "inspect",
            "rows": [{"商户订单号": "MA2026"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["execution_device_entry_id"] == entry.entry_id

    history_response = client.get("/api/attendance/order-refund-history")
    assert history_response.status_code == 200


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

    _grant_feature_access(session, user_id=auth_user.id, feature_key="attendance.wjx-data")

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
