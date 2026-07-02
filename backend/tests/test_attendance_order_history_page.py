import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from backend.api import attendance as attendance_api
from backend.api.attendance import (
    AttendanceOrderExecuteRequest,
    _build_order_refund_history_page,
    _sync_refund_history_from_payment_state,
)
from backend.models import AttendanceOrderRefundHistory, User, UserDevice


def test_order_refund_history_page_uses_db_pagination_order_and_strips_inline_timestamps():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine, tables=[AttendanceOrderRefundHistory.__table__])

    with Session(engine) as session:
        for index in range(25):
            session.add(
                AttendanceOrderRefundHistory(
                    id=f"row-{index:02d}",
                    operator_username="tester",
                    operator_nickname="Tester",
                    student_name=f"student-{index:02d}",
                    result_text=f"2026-07-02 10:{index:02d}:00 done {index}",
                    created_at=1000 + index,
                )
            )
        session.commit()

        page = _build_order_refund_history_page(session, page=2, page_size=10)

    assert page.total == 25
    assert page.page == 2
    assert page.page_size == 10
    assert [item.id for item in page.items] == [f"row-{index:02d}" for index in range(14, 4, -1)]
    assert all("[" not in item.result_text for item in page.items)
    assert all(item.operator_name == "Tester" for item in page.items)


def test_refund_confirmation_retries_before_blocking_history(monkeypatch):
    calls = []

    monkeypatch.setattr(attendance_api, "REFUND_CONFIRMATION_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(attendance_api, "REFUND_CONFIRMATION_RETRY_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(attendance_api.time, "sleep", lambda seconds: None)

    def fake_refund_details(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {"rows": []}
        return {"rows": [{"refund_status": "退款成功", "refund_amount": "875"}]}

    monkeypatch.setattr(attendance_api, "_execute_order_refund_details_on_entry", fake_refund_details)

    confirmation = attendance_api._verify_refund_execution_confirmed(
        {"entry_id": "device-1", "token": "stable-token"},
        {"login_users": ["考勤后台"]},
        {
            "rows": [
                {
                    "学员名称": "王悦",
                    "微信支付订单号": "4200003136202607015270577860",
                    "已返款": "875",
                    "退款额度": "875",
                    "执行退款": "已退款",
                }
            ]
        },
    )

    assert len(calls) == 2
    assert confirmation["failure_count"] == 0
    assert confirmation["checked"][0]["actual_refunded"] == 875


def test_sync_payment_refund_state_to_local_history_is_idempotent():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine, tables=[AttendanceOrderRefundHistory.__table__])
    user = User(id=1, username="tester", nickname="Tester", hashed_password="x", is_superuser=True)
    row = {
        "学员名称": "王悦",
        "微信支付订单号": "4200003136202607015270577860",
        "商户订单号": "THI1VI-0OZRE8O-HOEJ",
        "订单金额": "875",
        "已返款": "875",
        "剩余金额": "0",
        "退款额度": 0.0,
        "退款原因": "",
        "执行退款": "",
    }

    with Session(engine) as session:
        first_count = _sync_refund_history_from_payment_state(
            session,
            current_user=user,
            execution_device_entry_id="device-1",
            rows=[row],
        )
        second_count = _sync_refund_history_from_payment_state(
            session,
            current_user=user,
            execution_device_entry_id="device-1",
            rows=[row],
        )
        page = _build_order_refund_history_page(session, page=1, page_size=10)

    assert first_count == 1
    assert second_count == 0
    assert page.total == 1
    assert page.items[0].wechat_order_id == "4200003136202607015270577860"
    assert page.items[0].refund_amount == "875"
    assert page.items[0].result_text == "支付侧已退款（同步）"


def test_refund_confirmation_failure_returns_business_conflict(monkeypatch):
    entry = UserDevice(
        entry_id="device-1",
        user_id=1,
        device_id="codepc_mi15",
        name="codepc_mi15",
        server_url="http://127.0.0.1:8000",
        token="stable-token",
    )
    user = User(id=1, username="tester", hashed_password="x", is_superuser=True)

    monkeypatch.setattr(
        attendance_api,
        "get_or_create_attendance_service_config",
        lambda session: type("Config", (), {"execution_device_entry_id": entry.entry_id})(),
    )
    monkeypatch.setattr(attendance_api, "get_attendance_service_extra_config", lambda session: {})
    monkeypatch.setattr(attendance_api, "get_attendance_service_order_operation_password", lambda session: "pw")
    monkeypatch.setattr(attendance_api, "_resolve_run_device", lambda *args, **kwargs: entry)
    monkeypatch.setattr(
        attendance_api,
        "_execute_order_on_entry",
        lambda *args, **kwargs: {
            "rows": [
                {
                    "学员名称": "王悦",
                    "微信支付订单号": "4200003136202607015270577860",
                    "已返款": "875",
                    "退款额度": "875",
                    "执行退款": "已退款",
                }
            ]
        },
    )

    def raise_confirmation_failure(*args, **kwargs):
        raise RuntimeError("退款执行后支付侧确认失败，已阻止写入退款历史和后续流程推进。王悦: 支付侧0.0 / 目标875.0")

    monkeypatch.setattr(attendance_api, "_verify_refund_execution_confirmed", raise_confirmation_failure)

    with pytest.raises(HTTPException) as exc_info:
        attendance_api.execute_attendance_order(
            AttendanceOrderExecuteRequest(action="refund", rows=[{"微信支付订单号": "4200003136202607015270577860"}]),
            session=object(),
            current_user=user,
            _=user,
        )

    assert exc_info.value.status_code == 409
    assert "退款执行后支付侧确认失败" in str(exc_info.value.detail)

