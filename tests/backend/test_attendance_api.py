from __future__ import annotations

import re

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import select

from backend.app import app
from backend.api import attendance as attendance_api
from backend.core.auth import get_current_user_from_token, get_optional_current_user_from_token
from backend.core.attendance_service import (
    encrypt_attendance_secret,
    get_attendance_course_data_flow_config,
    get_attendance_service_extra_config,
    get_attendance_service_order_operation_password,
    get_or_create_attendance_service_config,
)
from backend.core.feature_access import (
    FEATURE_ACCESS_SUBJECT_ANONYMOUS,
    FEATURE_ACCESS_SUBJECT_USER,
    save_feature_access_policy_overrides,
)
from backend.models import (
    AttendanceAccountAsset,
    AttendanceOrderRefundHistory,
    AttendanceWjxDataEntry,
    ResourceAccessGrant,
    SheetDocument,
    User,
    UserDevice,
    WorkbookDocument,
    WorkbookSheetLink,
)


def _override_user(user: User):
    app.dependency_overrides[get_current_user_from_token] = lambda: user
    app.dependency_overrides[get_optional_current_user_from_token] = lambda: user


def _clear_user_override():
    app.dependency_overrides.pop(get_current_user_from_token, None)
    app.dependency_overrides.pop(get_optional_current_user_from_token, None)


def _create_admin_user(session) -> User:
    user = User(
        username="admin-user",
        email="admin@example.com",
        hashed_password="pw",
        is_active=True,
        is_superuser=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _grant_feature_access(session, *, user_id: int, feature_key: str) -> None:
    save_feature_access_policy_overrides(
        session,
        subject_type=FEATURE_ACCESS_SUBJECT_USER,
        subject_user_id=user_id,
        overrides={feature_key: "allow"},
    )


def _create_attendance_summary_course_sheet(
    session,
    *,
    rows: list[list[object]],
    updated_at: float = 1234.0,
    cell_meta: dict[str, object] | None = None,
) -> None:
    sheet = SheetDocument(
        numeric_id=4,
        scope="notes",
        owner_type="note_sheet",
        owner_key="attendance-summary",
        sheet_key="courses",
        title="课程",
        document_json={
            "schema_version": 1,
            "columns": [
                "课程类型",
                "课程名称",
                "在线考勤表",
                "考勤负责人",
                "课次链接",
                "打卡链接",
                "备注",
                "返款频次",
                "课程开始日期",
                "课程结束日期",
                "考勤实际完成结点",
            ],
            "rows": rows,
            "cell_meta": cell_meta or {},
        },
        updated_at=updated_at,
    )
    session.add(sheet)
    session.commit()


def _create_attendance_workbook(session, owner: User) -> WorkbookDocument:
    workbook = WorkbookDocument(
        numeric_id=2,
        title="武陵禅寺网课考勤汇总",
        owner_user_id=owner.id,
        created_by_user_id=owner.id,
        updated_by_user_id=owner.id,
    )
    session.add(workbook)
    session.commit()
    session.refresh(workbook)
    return workbook


def _get_anonymous_sheet_grant(session, sheet: SheetDocument) -> ResourceAccessGrant | None:
    return session.exec(
        select(ResourceAccessGrant)
        .where(ResourceAccessGrant.resource_type == "sheet")
        .where(ResourceAccessGrant.resource_id == str(sheet.numeric_id))
        .where(ResourceAccessGrant.subject_key == "anonymous")
    ).first()


def _course_row(course_name: str, online_sheet: str, completed_date: object = "") -> list[object]:
    return ["", course_name, online_sheet, "", "", "", "", "", "", "", completed_date]


def _shift_cell_meta_rows(cell_meta: dict[str, object], offset: int) -> dict[str, object]:
    shifted: dict[str, object] = {}
    for key, value in cell_meta.items():
        row_text, separator, column_text = key.partition(":")
        if separator != ":" or not row_text.isdigit():
            shifted[key] = value
            continue
        shifted[f"{int(row_text) + offset}:{column_text}"] = value
    return shifted


def test_attendance_config_requires_attendance_access(client: TestClient, auth_user):
    response = client.get("/api/attendance/config")
    assert response.status_code == 403


def test_attendance_config_and_account_crud(client: TestClient, session, test_device):
    admin_user = _create_admin_user(session)
    _override_user(admin_user)

    try:
        add_device_resp = client.post(
            "/api/devices/add",
            json={
                "mode": "local",
                "token": "attendance-local-token",
                "alias": "当前考勤设备",
            },
        )
        assert add_device_resp.status_code == 200
        entry_id = add_device_resp.json()["id"]

        account_resp = client.post(
            "/api/attendance/accounts",
            json={
                "login_username": "18850000000",
                "password": "plain-pass",
            },
        )
        assert account_resp.status_code == 200
        account = account_resp.json()
        assert account["password"] == "plain-pass"
        assert account["name"] == "18850000000"

        config_resp = client.put(
            "/api/attendance/config",
            json={
                "current_wjx_account_id": account["id"],
                "execution_device_entry_id": entry_id,
                "scan_reminder_users": ["考勤后台", "文件传输助手"],
                "order_lookup_mode": "db_only",
                "order_operation_password": "refund-pass",
            },
        )
        assert config_resp.status_code == 200
        config = config_resp.json()
        assert config["service"]["current_wjx_account_id"] == account["id"]
        assert config["service"]["execution_device_entry_id"] == entry_id
        assert "data_device_entry_id" not in config["service"]
        assert "step_runners" not in config["service"]
        assert config["service"]["scan_reminder_users"] == ["考勤后台", "文件传输助手"]
        assert config["service"]["order_lookup_mode"] == "db_only"
        assert config["service"]["order_operation_password_configured"] is True
        assert "password" not in config["current_account"]
        assert config["current_execution_device"]["entry_id"] == entry_id
        assert "fixed_wjx_template" not in config
        assert get_attendance_service_extra_config(session)["scan_reminder_users"] == ["考勤后台", "文件传输助手"]
        assert get_attendance_service_extra_config(session)["order_lookup_mode"] == "db_only"
        assert get_attendance_service_extra_config(session)["order_operation_password_configured"] is True
        assert get_attendance_service_order_operation_password(session) == "refund-pass"
    finally:
        _clear_user_override()


def test_attendance_course_data_flow_config_supports_course_devices_and_step_runners(client: TestClient, session):
    admin_user = _create_admin_user(session)
    _override_user(admin_user)

    execution_device = UserDevice(
        user_id=admin_user.id,
        device_id="codepc-mi15",
        name="mi15 浏览器执行",
        mode="remote",
        server_url="http://mi15.local",
        token="mi15-token",
        is_active=True,
    )
    data_device = UserDevice(
        user_id=admin_user.id,
        device_id="codepc-mf",
        name="mf 数据主机",
        mode="remote",
        server_url="http://mf.local",
        token="mf-token",
        is_active=True,
    )
    custom_device = UserDevice(
        user_id=admin_user.id,
        device_id="codepc-worker",
        name="独立运行设备",
        mode="remote",
        server_url="http://worker.local",
        token="worker-token",
        is_active=True,
    )
    session.add_all([execution_device, data_device, custom_device])
    session.commit()
    session.refresh(execution_device)
    session.refresh(data_device)
    session.refresh(custom_device)

    try:
        response = client.put(
            "/api/attendance/course-data-flow/config",
            json={
                "browser_device_entry_id": execution_device.entry_id,
                "data_device_entry_id": data_device.entry_id,
                "step_device_entry_ids": {
                    "1": "",
                    "2": custom_device.entry_id,
                    "6": execution_device.entry_id,
                },
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["course_data_flow"]["browser_device_entry_id"] == execution_device.entry_id
        assert payload["course_data_flow"]["effective_browser_device_entry_id"] == execution_device.entry_id
        assert payload["course_data_flow"]["data_device_entry_id"] == data_device.entry_id
        assert payload["current_browser_device"]["entry_id"] == execution_device.entry_id
        assert payload["current_data_device"]["entry_id"] == data_device.entry_id
        assert payload["course_data_flow"]["step_device_entry_ids"] == {
            "2": custom_device.entry_id,
            "6": execution_device.entry_id,
        }

        step_runners = {item["step"]: item for item in payload["course_data_flow"]["step_runners"]}
        assert step_runners[1]["default_role"] == "browser_device"
        assert step_runners[1]["effective_role"] == "browser_device"
        assert step_runners[1]["effective_device_entry_id"] == execution_device.entry_id
        assert step_runners[2]["default_role"] == "data_host"
        assert step_runners[2]["effective_role"] == "custom_device"
        assert step_runners[2]["effective_device_entry_id"] == custom_device.entry_id
        assert step_runners[3]["effective_role"] == "data_host"
        assert step_runners[3]["effective_device_entry_id"] == data_device.entry_id
        assert step_runners[6]["effective_role"] == "custom_device"
        assert step_runners[6]["effective_device_entry_id"] == execution_device.entry_id

        service_config_response = client.get("/api/attendance/config")
        assert service_config_response.status_code == 200
        assert "step_runners" not in service_config_response.json()["service"]

        data_flow_config = get_attendance_course_data_flow_config(session)
        assert data_flow_config["browser_device_entry_id"] == execution_device.entry_id
        assert data_flow_config["data_device_entry_id"] == data_device.entry_id
        assert data_flow_config["step_device_entry_ids"] == {
            "2": custom_device.entry_id,
            "6": execution_device.entry_id,
        }
    finally:
        _clear_user_override()


def test_attendance_course_data_flow_config_rejects_invalid_step_runner_key(client: TestClient, session):
    admin_user = _create_admin_user(session)
    _override_user(admin_user)
    device = UserDevice(
        user_id=admin_user.id,
        device_id="codepc-worker",
        name="独立运行设备",
        mode="remote",
        server_url="http://worker.local",
        token="worker-token",
        is_active=True,
    )
    session.add(device)
    session.commit()
    session.refresh(device)

    try:
        response = client.put(
            "/api/attendance/course-data-flow/config",
            json={"step_device_entry_ids": {"7": device.entry_id}},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "课程数据 step_device_entry_ids 只支持 step1-step6"
    finally:
        _clear_user_override()


def test_attendance_feedback_form_meta_reads_unfinished_courses_from_summary_sheet(client: TestClient, session):
    initial_meta = client.get("/api/attendance/wjx-feedback-form")
    assert initial_meta.status_code == 200
    assert initial_meta.json()["course_names"] == []
    assert initial_meta.json()["data_sheet_url"] == ""

    _create_attendance_summary_course_sheet(
        session,
        rows=[
            _course_row("2025念住闯关第2部分", "20250106念住闯关"),
            _course_row("第40届念住", "20260501第40届念住"),
            _course_row("第39届念住", "20260401第39届念住", "46142"),
            _course_row("第46届觉观", "20260501第46届觉观"),
            _course_row("重复课程", "20260501第46届觉观"),
            _course_row("梵呗初阶", ""),
        ],
        cell_meta={
            "0:2": {"link": {"url": "https://www.kdocs.cn/l/nianzhu"}},
            "1:2": {"link": {"url": "https://www.kdocs.cn/l/nianzhu40"}},
            "3:2": {"link": {"url": "https://www.kdocs.cn/l/jueguan46"}},
        },
        updated_at=5678.0,
    )

    updated_meta = client.get("/api/attendance/wjx-feedback-form")
    assert updated_meta.status_code == 200
    updated_payload = updated_meta.json()
    assert updated_payload["course_names"] == [
        "20250106念住闯关",
        "20260501第40届念住",
        "20260501第46届觉观",
        "梵呗初阶",
    ]
    assert updated_payload["course_options"] == [
        {"name": "20250106念住闯关", "attendance_sheet_url": "https://www.kdocs.cn/l/nianzhu"},
        {"name": "20260501第40届念住", "attendance_sheet_url": "https://www.kdocs.cn/l/nianzhu40"},
        {"name": "20260501第46届觉观", "attendance_sheet_url": "https://www.kdocs.cn/l/jueguan46"},
        {"name": "梵呗初阶", "attendance_sheet_url": ""},
    ]
    assert updated_payload["course_names_updated_at"] == 5678.0
    assert updated_payload["data_sheet_url"] == ""
    assert "template" not in updated_payload
    assert "summary_sheet_url" not in updated_payload
    source_sheet = session.exec(select(SheetDocument).where(SheetDocument.numeric_id == 4)).one()
    assert _get_anonymous_sheet_grant(session, source_sheet) is None


def test_attendance_feedback_form_meta_reads_links_from_grid_row_cell_meta(client: TestClient, session):
    _create_attendance_summary_course_sheet(
        session,
        rows=[
            _course_row("2025念住闯关第2部分", "20250106念住闯关"),
            _course_row("梵呗初阶", "20260601梵呗初阶"),
            _course_row("禅宗1至3期5阶", "20260308禅宗1至3期五阶"),
        ],
        cell_meta=_shift_cell_meta_rows(
            {
                "0:2": {"link": {"url": "https://www.kdocs.cn/l/nianzhu"}},
                "2:2": {"link": {"url": "https://www.kdocs.cn/l/zen-five"}},
            },
            1,
        ),
        updated_at=5678.0,
    )
    source_sheet = session.exec(select(SheetDocument).where(SheetDocument.numeric_id == 4)).one()
    document_json = dict(source_sheet.document_json or {})
    columns = list(document_json["columns"])
    rows = list(document_json["rows"])
    source_sheet.document_json = {
        **document_json,
        "grid_rows": [columns, *rows],
        "data_start_row": 1,
        "field_row_index": 0,
    }
    session.add(source_sheet)
    session.commit()

    response = client.get("/api/attendance/wjx-feedback-form")

    assert response.status_code == 200
    assert response.json()["course_options"] == [
        {"name": "20250106念住闯关", "attendance_sheet_url": "https://www.kdocs.cn/l/nianzhu"},
        {"name": "20260601梵呗初阶", "attendance_sheet_url": ""},
        {"name": "20260308禅宗1至3期五阶", "attendance_sheet_url": "https://www.kdocs.cn/l/zen-five"},
    ]


def test_attendance_header_tool_builds_zen_week_headers(monkeypatch):
    monkeypatch.setattr(
        attendance_api,
        "_query_attendance_header_clockins",
        lambda course_name: [
            {"clockin_id": 201, "name": f"{course_name}-共学打卡", "url": "https://example.com/clockin-study"},
            {"clockin_id": 202, "name": f"{course_name}-共修打卡", "url": "https://example.com/clockin-practice"},
        ],
    )
    monkeypatch.setattr(
        attendance_api,
        "_query_attendance_header_lessons",
        lambda course_name: [
            {
                "lesson_id": 11,
                "lesson_name": f"{course_name}-第1周=佛教史1",
                "lesson_id2": "lesson-token-1",
            },
            {
                "lesson_id": 12,
                "lesson_name": f"{course_name}-第1周=佛教史2",
                "lesson_id2": "https://example.com/lesson2",
            },
            {
                "lesson_id": 13,
                "lesson_name": f"{course_name}-第2周=心经",
                "lesson_id2": "",
            },
        ],
    )

    payload = attendance_api._build_attendance_header_tool_response("d260308禅宗1至3期五阶")

    assert [group.label for group in payload.groups] == ["打卡数据", "第1周", "第2周"]
    assert [group.colspan for group in payload.groups] == [2, 2, 1]
    assert payload.rows == [
        ["打卡数据", "", "第1周", "", "第2周"],
        ["共学打卡", "共修打卡", "佛教史1", "佛教史2", "心经"],
    ]
    assert payload.cells[2].url == (
        "https://admin.xiaoe-tech.com/t/live_management#/userOperation?id=lesson-token-1&tabName=UserManage"
    )
    assert payload.cells[3].url == "https://example.com/lesson2"
    assert payload.document_json["merged_cells"] == [
        {"row": 0, "col": 0, "rowspan": 1, "colspan": 2},
        {"row": 0, "col": 2, "rowspan": 1, "colspan": 2},
    ]
    assert payload.document_json["cell_meta"]["1:2"]["link"]["url"] == payload.cells[2].url


def test_attendance_header_tool_accepts_plain_date_prefix(monkeypatch):
    queried_course_names: list[str] = []

    def fake_query_clockins(course_name: str):
        queried_course_names.append(course_name)
        return []

    def fake_query_lessons(course_name: str):
        queried_course_names.append(course_name)
        return [
            {
                "lesson_id": 11,
                "lesson_name": f"{course_name}-第1周=佛教史1",
                "lesson_id2": "lesson-token-1",
            },
        ]

    monkeypatch.setattr(attendance_api, "_query_attendance_header_clockins", fake_query_clockins)
    monkeypatch.setattr(attendance_api, "_query_attendance_header_lessons", fake_query_lessons)

    payload = attendance_api._build_attendance_header_tool_response("20260308禅宗1至3期五阶")

    assert payload.course_name == "d260308禅宗1至3期五阶"
    assert queried_course_names == ["d260308禅宗1至3期五阶", "d260308禅宗1至3期五阶"]
    assert payload.rows == [
        ["第1周"],
        ["佛教史1"],
    ]


def test_attendance_header_tool_rejects_unsupported_course_type():
    try:
        attendance_api._build_attendance_header_tool_response("d260501第40届念住")
    except Exception as exc:
        assert isinstance(exc, HTTPException)
        assert exc.status_code == 400
        assert "暂不支持" in exc.detail
    else:
        raise AssertionError("unsupported course type should fail")


def test_attendance_feedback_form_meta_exposes_public_readonly_data_sheet_link(client: TestClient, session):
    admin_user = _create_admin_user(session)
    _create_attendance_workbook(session, admin_user)
    _create_attendance_summary_course_sheet(
        session,
        rows=[_course_row("第45届觉观", "20260401第45届觉观")],
    )

    response = client.get("/api/attendance/wjx-feedback-form")
    assert response.status_code == 200
    payload = response.json()
    assert "summary_sheet_url" not in payload
    assert re.fullmatch(r"/sheet/\d+", payload["data_sheet_url"])

    summary_sheet = session.exec(select(SheetDocument).where(SheetDocument.numeric_id == 4)).one()
    data_sheet_id = int(payload["data_sheet_url"].rsplit("/", 1)[-1])
    data_sheet = session.exec(select(SheetDocument).where(SheetDocument.numeric_id == data_sheet_id)).one()
    assert _get_anonymous_sheet_grant(session, summary_sheet) is None
    assert _get_anonymous_sheet_grant(session, data_sheet).role == "viewer"

    public_summary_response = client.get("/api/note-sheets/sheets/4")
    assert public_summary_response.status_code == 403
    public_data_response = client.get(f"/api/note-sheets/sheets/{data_sheet_id}")
    assert public_data_response.status_code == 200
    assert public_data_response.json()["access"]["role"] == "viewer"


def test_attendance_feedback_form_meta_is_read_only(client: TestClient, session, auth_user):
    response = client.put(
        "/api/attendance/wjx-feedback-form",
        json={"course_names": ["20260415第46届觉观"]},
    )
    assert response.status_code == 405


def test_attendance_feedback_public_endpoints_bypass_feature_access_policy(client: TestClient, session):
    _create_attendance_summary_course_sheet(
        session,
        rows=[_course_row("第45届觉观", "20260401第45届觉观")],
    )
    save_feature_access_policy_overrides(
        session,
        subject_type=FEATURE_ACCESS_SUBJECT_ANONYMOUS,
        overrides={
            "attendance-tools": "deny",
            "attendance.wjx-feedback": "deny",
        },
    )

    meta_response = client.get("/api/attendance/wjx-feedback-form")
    assert meta_response.status_code == 200
    assert meta_response.json()["course_names"] == ["20260401第45届觉观"]

    submit_response = client.post(
        "/api/attendance/wjx-feedback/submissions",
        json={
            "course_name": "20260401第45届觉观",
            "student_id_text": "1",
            "student_name": "游客测试",
            "correction_request": "公开入口仍可提交",
            "extra_note": "",
        },
    )
    assert submit_response.status_code == 200
    assert submit_response.json()["student_name"] == "游客测试"

    history_response = client.get(
        "/api/attendance/wjx-feedback/history",
        params={
            "course_name": "20260401第45届觉观",
            "student_id_text": "1",
            "student_name": "游客测试",
        },
    )
    assert history_response.status_code == 200
    assert history_response.json()["items"][0]["student_name"] == "游客测试"


def test_order_refund_history_strips_timestamps_but_keeps_result_text(client: TestClient, session):
    admin_user = _create_admin_user(session)
    _grant_feature_access(session, user_id=admin_user.id, feature_key="attendance.orders")
    _override_user(admin_user)

    try:
        session.add_all(
            [
                AttendanceOrderRefundHistory(
                    requested_by_user_id=admin_user.id,
                    operator_username=admin_user.username,
                    operator_nickname=admin_user.nickname,
                    student_name="张三",
                    wechat_order_id="wx-1",
                    merchant_order_id="mch-1",
                    order_amount="300",
                    refunded_amount="300",
                    remaining_amount="0",
                    refund_amount="300",
                    refund_reason="测试",
                    result_text="2026/02/28 22:55:53 已退款",
                    raw_row_json={"执行退款": "2026/02/28 22:55:53 已退款"},
                    created_at=1.0,
                ),
                AttendanceOrderRefundHistory(
                    requested_by_user_id=admin_user.id,
                    operator_username=admin_user.username,
                    operator_nickname=admin_user.nickname,
                    student_name="李四",
                    wechat_order_id="wx-2",
                    merchant_order_id="mch-2",
                    order_amount="300",
                    refunded_amount="300",
                    remaining_amount="0",
                    refund_amount="300",
                    refund_reason="测试",
                    result_text="已退还全部促学金\n2026/02/16 16:58:35",
                    raw_row_json={"执行退款": "已退还全部促学金\n2026/02/16 16:58:35"},
                    created_at=2.0,
                ),
            ]
        )
        session.commit()

        response = client.get("/api/attendance/order-refund-history")
        assert response.status_code == 200
        items = response.json()["items"]

        assert items[0]["result_text"] == "已退款"
        assert items[1]["result_text"] == "已退还全部促学金"
        assert items[0]["created_at"] > items[1]["created_at"]
    finally:
        _clear_user_override()


def test_strip_order_history_result_timestamps_handles_only_timestamp_and_suffix_colon():
    assert attendance_api._strip_order_history_result_timestamps("2026/02/28 22:55:53 已退款") == "已退款"
    assert attendance_api._strip_order_history_result_timestamps("已处理:\n2026/02/28 22:55:53") == "已处理"
    assert attendance_api._strip_order_history_result_timestamps("2026/02/28 22:55:53") == ""


def test_attendance_config_rejects_inactive_execution_device(client: TestClient, session, test_device):
    admin_user = _create_admin_user(session)
    _override_user(admin_user)

    try:
        add_device_resp = client.post(
            "/api/devices/add",
            json={
                "mode": "local",
                "token": "attendance-local-token",
                "alias": "当前考勤设备",
            },
        )
        assert add_device_resp.status_code == 200
        entry_id = add_device_resp.json()["id"]

        disable_resp = client.put(
            f"/api/devices/{entry_id}",
            json={
                "is_active": False,
            },
        )
        assert disable_resp.status_code == 200

        config_resp = client.put(
            "/api/attendance/config",
            json={
                "execution_device_entry_id": entry_id,
            },
        )
        assert config_resp.status_code == 400
        assert config_resp.json()["detail"] == "当前执行设备已停用"
    finally:
        _clear_user_override()


def test_attendance_account_is_singleton_and_uses_login_as_name(client: TestClient, session):
    admin_user = _create_admin_user(session)
    _override_user(admin_user)

    try:
        first_resp = client.post(
            "/api/attendance/accounts",
            json={
                "login_username": "18850000002",
                "password": "plain-pass",
            },
        )
        assert first_resp.status_code == 200
        first_account = first_resp.json()
        assert first_account["name"] == "18850000002"

        second_resp = client.post(
            "/api/attendance/accounts",
            json={
                "login_username": "18850000003",
                "password": "other-pass",
            },
        )
        assert second_resp.status_code == 400
        assert second_resp.json()["detail"] == "问卷星账号只支持一个，请直接编辑现有账号"

        update_resp = client.put(
            f"/api/attendance/accounts/{first_account['id']}",
            json={
                "login_username": "18850000009",
            },
        )
        assert update_resp.status_code == 200
        updated = update_resp.json()
        assert updated["login_username"] == "18850000009"
        assert updated["name"] == "18850000009"
    finally:
        _clear_user_override()


def test_attendance_config_allows_order_user_without_service_grant(client: TestClient, session, auth_user):
    account = AttendanceAccountAsset(
        name="18850000088",
        login_username="18850000088",
        password_encrypted=encrypt_attendance_secret("plain-pass"),
        created_by_user_id=auth_user.id,
        updated_by_user_id=auth_user.id,
    )
    device = UserDevice(
        user_id=auth_user.id,
        device_id="shared-order-device",
        name="共享订单设备",
        mode="local",
        token="shared-order-token",
        is_active=True,
        order_index=0,
    )
    session.add(account)
    session.add(device)
    session.commit()
    session.refresh(account)
    session.refresh(device)

    config = get_or_create_attendance_service_config(session)
    config.current_wjx_account_id = account.id
    config.execution_device_entry_id = device.entry_id
    session.add(config)
    session.commit()

    _grant_feature_access(session, user_id=auth_user.id, feature_key="attendance.orders")

    response = client.get("/api/attendance/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["current_account"]["login_username"] == "18850000088"
    assert "password" not in payload["current_account"]
    assert payload["current_execution_device"]["entry_id"] == device.entry_id
    assert payload["service"]["scan_reminder_users"] == []
    assert payload["service"]["order_lookup_mode"] == "browser_only"
    assert payload["service"]["order_operation_password_configured"] is False


def test_attendance_config_update_requires_configs_feature(client: TestClient, session, auth_user):
    device = UserDevice(
        user_id=auth_user.id,
        device_id="user-device-for-config",
        name="用户自有设备",
        mode="local",
        token="user-device-token",
        is_active=True,
        order_index=0,
    )
    session.add(device)
    session.commit()
    session.refresh(device)

    config = get_or_create_attendance_service_config(session)
    config.granted_user_ids = [auth_user.id]
    session.add(config)
    session.commit()

    _grant_feature_access(session, user_id=auth_user.id, feature_key="attendance.orders")

    response = client.put(
        "/api/attendance/config",
        json={"execution_device_entry_id": device.entry_id},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "当前账号无权访问该功能"


def test_attendance_wjx_data_readonly_listing_is_public(client: TestClient, session):
    save_feature_access_policy_overrides(
        session,
        subject_type=FEATURE_ACCESS_SUBJECT_ANONYMOUS,
        overrides={"attendance-tools": "deny", "attendance.wjx-data": "deny"},
    )
    session.add(
        AttendanceWjxDataEntry(
            activity_id="264266843",
            seq=800,
            submitted_at_text="2026/4/18 08:00:00",
            course_name="20260401第45届觉观",
            student_id_text="39",
            student_name="吴菲",
            correction_request="补第2课",
            process_status="",
            synced_at=1713426373.0,
            created_at=1713426373.0,
            updated_at=1713426373.0,
        )
    )
    session.commit()

    public_response = client.get("/api/attendance/wjx-data")
    assert public_response.status_code == 200
    payload = public_response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["seq"] == 800
    assert payload["items"][0]["student_name"] == "吴菲"
    assert payload["items"][0]["correction_request"] == "补第2课"


def test_attendance_feedback_history_matches_course_and_identity(client: TestClient, session):
    session.add_all(
        [
            AttendanceWjxDataEntry(
                activity_id="264266843",
                seq=810,
                submitted_at_text="2026/5/12 12:55:00",
                course_name="20260509梵呗初阶",
                student_id_text="6组-33号",
                student_name="范鹏",
                correction_request="日志打卡已经3次了",
                process_status="已处理",
                synced_at=1715490000.0,
                created_at=1715490000.0,
                updated_at=1715490000.0,
            ),
            AttendanceWjxDataEntry(
                activity_id="264266843",
                seq=808,
                submitted_at_text="2026/5/11 09:28:00",
                course_name="20260509梵呗初阶",
                student_id_text="6组-33号",
                student_name="范鹏",
                correction_request="日志打卡每次都按时完成了",
                process_status="已处理",
                synced_at=1715400000.0,
                created_at=1715400000.0,
                updated_at=1715400000.0,
            ),
            AttendanceWjxDataEntry(
                activity_id="264266843",
                seq=807,
                submitted_at_text="2026/5/10 08:00:00",
                course_name="20260509梵呗初阶",
                student_id_text="6组-34号",
                student_name="范鹏",
                correction_request="姓名相同但学号不同也应该能作为疑似历史显示",
                process_status="",
                synced_at=1715310000.0,
                created_at=1715310000.0,
                updated_at=1715310000.0,
            ),
            AttendanceWjxDataEntry(
                activity_id="264266843",
                seq=806,
                submitted_at_text="2026/5/09 08:00:00",
                course_name="20260510其他课程",
                student_id_text="6组-33号",
                student_name="范鹏",
                correction_request="不同课程不显示",
                process_status="",
                synced_at=1715220000.0,
                created_at=1715220000.0,
                updated_at=1715220000.0,
            ),
        ]
    )
    session.commit()

    response = client.get(
        "/api/attendance/wjx-feedback/history",
        params={
            "course_name": "20260509梵呗初阶",
            "student_id_text": "6 组 - 33 号",
            "student_name": "范鹏",
            "limit": 2,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert [item["seq"] for item in payload["items"]] == [810, 808]
    assert payload["items"][0]["correction_request"] == "日志打卡已经3次了"


def test_attendance_wjx_data_public_listing_returns_full_rows(client: TestClient, session):
    session.add_all(
        [
            AttendanceWjxDataEntry(
                activity_id="264266843",
                seq=801,
                submitted_at_text="2026/4/18 08:00:00",
                duration_text="80秒",
                source="微信",
                source_detail="福建福州",
                source_ip="1.1.1.1",
                course_name="20260401第45届觉观",
                student_id_text="39",
                student_name="吴菲",
                correction_request="补第2课",
                extra_note="备注1",
                process_status="",
                process_note="",
                raw_row_json={"2、学号": "39"},
                synced_at=1713426373.0,
                created_at=1713426373.0,
                updated_at=1713426373.0,
            ),
            AttendanceWjxDataEntry(
                activity_id="codeyun-attendance-feedback",
                seq=802,
                submitted_at_text="2026/4/18 08:05:00",
                duration_text="90秒",
                source="采集系统",
                source_detail="CodeYun反馈表",
                source_ip="2.2.2.2",
                course_name="20260408第39届念住",
                student_id_text="2-17",
                student_name="王五",
                correction_request="第3课没返款",
                extra_note="备注2",
                process_status="",
                process_note="",
                raw_row_json={"2、学号": "2-17"},
                synced_at=1713426473.0,
                created_at=1713426473.0,
                updated_at=1713426473.0,
            ),
            AttendanceWjxDataEntry(
                activity_id="264266843",
                seq=803,
                submitted_at_text="2026/4/18 08:10:00",
                duration_text="70秒",
                source="微信",
                source_detail="上海上海",
                source_ip="3.3.3.3",
                course_name="20260415梵呗初阶",
                student_id_text="18",
                student_name="赵六",
                correction_request="已处理记录",
                extra_note="备注3",
                process_status="已处理",
                process_note="已补登",
                raw_row_json={"2、学号": "18"},
                synced_at=1713426573.0,
                created_at=1713426573.0,
                updated_at=1713426573.0,
            ),
        ]
    )
    session.commit()

    response = client.get("/api/attendance/wjx-data")
    assert response.status_code == 200
    payload = response.json()

    assert payload["total"] == 3
    assert payload["sync_state"] is None
    assert [item["seq"] for item in payload["items"]] == [803, 802, 801]
    assert [item["course_name"] for item in payload["items"]] == [
        "20260415梵呗初阶",
        "20260408第39届念住",
        "20260401第45届觉观",
    ]

    first = payload["items"][0]
    assert first["student_id_text"] == "18"
    assert first["student_name"] == "赵六"
    assert first["source"] == "微信"
    assert first["source_ip"] == "3.3.3.3"
    assert first["correction_request"] == "已处理记录"
    assert first["extra_note"] == "备注3"
    assert first["raw_row"] == {"2、学号": "18"}
    assert first["foreground_colors"]["course"] is not None
    assert first["foreground_colors"]["student"] is not None

    pending_response = client.get("/api/attendance/wjx-data", params={"process_status": "__empty__"})
    assert pending_response.status_code == 200
    assert pending_response.json()["total"] == 2
    assert [item["seq"] for item in pending_response.json()["items"]] == [802, 801]


def test_attendance_wjx_data_sheet_location_creates_standard_sheet_and_seeds_entries(client: TestClient, session):
    admin_user = _create_admin_user(session)
    workbook = _create_attendance_workbook(session, admin_user)
    session.add(
        AttendanceWjxDataEntry(
            activity_id="264266843",
            seq=801,
            submitted_at_text="2026/4/18 08:00:00",
            source="微信",
            course_name="20260401第45届觉观",
            student_id_text="39",
            student_name="吴菲",
            correction_request="补第2课",
            extra_note="备注1",
            process_status="已处理",
            process_note="已补登",
            synced_at=1713426373.0,
            created_at=1713426373.0,
            updated_at=1713426373.0,
        )
    )
    session.commit()
    _override_user(admin_user)

    try:
        response = client.get("/api/attendance/wjx-data/sheet")
        assert response.status_code == 200
        payload = response.json()
    finally:
        _clear_user_override()

    assert payload["workbook_id"] == 2
    assert payload["path"] == f"/workbook/2?sheet={payload['sheet_id']}"
    sheet = session.exec(select(SheetDocument).where(SheetDocument.numeric_id == payload["sheet_id"])).one()
    assert sheet.title == "问卷数据"
    assert sheet.scope == "notes"
    assert sheet.document_json["columns"] == [
        "序号",
        "提交时间",
        "来源",
        "课程",
        "考勤负责人",
        "学号",
        "姓名",
        "修正需求",
        "补充说明",
        "处理状态",
    ]
    assert "处理说明" not in sheet.document_json["columns"]
    assert all(config.get("hidden") is not True for config in sheet.document_json["column_configs"].values())
    assert sheet.document_json["view_settings"]["row_marker_numbering"] == "global"
    assert sheet.document_json["view_settings"]["row_marker_origin"] == "sheet"
    assert sheet.document_json["view_settings"]["column_marker_style"] == "letters"
    assert sheet.document_json["rows"] == [[
        "801",
        "2026/4/18 08:00:00",
        "微信",
        "20260401第45届觉观",
        "",
        "39",
        "吴菲",
        "补第2课",
        "备注1",
        "已补登",
    ]]
    link = session.exec(
        select(WorkbookSheetLink)
        .where(WorkbookSheetLink.workbook_id == str(workbook.numeric_id))
        .where(WorkbookSheetLink.sheet_id == str(sheet.numeric_id))
    ).one()
    assert link.order_index == 10
    public_grant = _get_anonymous_sheet_grant(session, sheet)
    assert public_grant is not None
    assert public_grant.role == "viewer"


def test_attendance_feedback_submission_uses_questionnaire_sheet_max_seq(client: TestClient, session):
    admin_user = _create_admin_user(session)
    _create_attendance_workbook(session, admin_user)
    _override_user(admin_user)
    try:
        location = client.get("/api/attendance/wjx-data/sheet").json()
    finally:
        _clear_user_override()

    sheet = session.exec(select(SheetDocument).where(SheetDocument.numeric_id == location["sheet_id"])).one()
    columns = sheet.document_json["columns"]
    sheet.document_json = {
        **sheet.document_json,
        "rows": [[
            "900",
            "2026/4/18 08:00:00",
            "微信",
            "20260401第45届觉观",
            "",
            "39",
            "吴菲",
            "旧问题",
            "",
            "人工已处理",
        ]],
    }
    assert columns == sheet.document_json["columns"]
    session.add(sheet)
    session.commit()

    submit_response = client.post(
        "/api/attendance/wjx-feedback/submissions",
        json={
            "course_name": "20260408第39届念住",
            "student_id_text": "2-17",
            "student_name": "薛伟",
            "correction_request": "今天没有收到退款",
            "extra_note": "来自公开采集页",
        },
    )
    assert submit_response.status_code == 200
    assert submit_response.json()["seq"] == 901

    sheet = session.exec(select(SheetDocument).where(SheetDocument.numeric_id == location["sheet_id"])).one()
    assert [row[0] for row in sheet.document_json["rows"]] == ["901", "900"]
    rows_by_seq = {row[0]: row for row in sheet.document_json["rows"]}
    assert rows_by_seq["901"] == [
        "901",
        submit_response.json()["submitted_at_text"],
        "采集系统",
        "20260408第39届念住",
        "",
        "2-17",
        "薛伟",
        "今天没有收到退款",
        "来自公开采集页",
        "",
    ]


def test_attendance_feedback_submission_links_course_cell_from_summary_sheet(client: TestClient, session):
    admin_user = _create_admin_user(session)
    _create_attendance_workbook(session, admin_user)
    _create_attendance_summary_course_sheet(
        session,
        rows=[
            _course_row("第39届念住", "20260408第39届念住"),
        ],
        cell_meta={
            "0:2": {"link": {"url": "https://www.kdocs.cn/l/nianzhu39"}},
        },
    )

    submit_response = client.post(
        "/api/attendance/wjx-feedback/submissions",
        json={
            "course_name": "20260408第39届念住",
            "student_id_text": "2-17",
            "student_name": "薛伟",
            "correction_request": "今天没有收到退款",
            "extra_note": "来自公开采集页",
        },
    )
    assert submit_response.status_code == 200

    sheet = session.exec(
        select(SheetDocument).where(SheetDocument.owner_type == attendance_api.ATTENDANCE_WJX_DATA_OWNER_TYPE)
    ).one()
    assert sheet.document_json["rows"][0][3] == "20260408第39届念住"
    assert sheet.document_json["cell_meta"]["0:3"]["link"]["url"] == "https://www.kdocs.cn/l/nianzhu39"


def test_attendance_wjx_sheet_upsert_inserts_by_seq_desc_and_shifts_cell_meta():
    document = attendance_api._create_default_attendance_wjx_sheet_document()
    document["rows"] = [
        ["900", "2026/4/18 08:00:00", "微信", "旧课程1", "", "39", "吴菲", "旧问题1", "", ""],
        ["800", "2026/4/17 08:00:00", "微信", "旧课程2", "", "40", "王五", "旧问题2", "", ""],
    ]
    document["cell_meta"] = {
        "0:3": {"link": {"url": "https://example.com/old-900"}},
        "1:3": {"link": {"url": "https://example.com/old-800"}},
    }

    next_document, inserted, changed = attendance_api._upsert_attendance_wjx_sheet_values(
        document,
        {
            "序号": 901,
            "提交时间": "2026/4/19 09:00:00",
            "来源": "采集系统",
            "课程": "新课程",
            "学号": "41",
            "姓名": "赵六",
            "修正需求": "新问题",
            "补充说明": "",
            "处理状态": "",
        },
        preserve_process_status=False,
        course_link_map={"新课程": "https://example.com/new"},
    )

    assert inserted is True
    assert changed is True
    assert [row[0] for row in next_document["rows"]] == ["901", "900", "800"]
    assert next_document["cell_meta"]["0:3"]["link"]["url"] == "https://example.com/new"
    assert next_document["cell_meta"]["1:3"]["link"]["url"] == "https://example.com/old-900"
    assert next_document["cell_meta"]["2:3"]["link"]["url"] == "https://example.com/old-800"


def test_attendance_wjx_data_sheet_sync_preserves_manual_process_status(client: TestClient, session):
    admin_user = _create_admin_user(session)
    _create_attendance_workbook(session, admin_user)
    _create_attendance_summary_course_sheet(
        session,
        rows=[
            _course_row("第39届念住", "20260408第39届念住"),
        ],
        cell_meta={
            "0:2": {"link": {"url": "https://www.kdocs.cn/l/nianzhu39"}},
        },
    )
    _override_user(admin_user)
    try:
        location = client.get("/api/attendance/wjx-data/sheet").json()
    finally:
        _clear_user_override()

    sheet = session.exec(select(SheetDocument).where(SheetDocument.numeric_id == location["sheet_id"])).one()
    sheet.document_json = {
        **sheet.document_json,
        "rows": [[
            "10",
            "2026/4/18 08:00:00",
            "微信",
            "旧课程",
            "",
            "39",
            "吴菲",
            "旧问题",
            "",
            "人工已处理",
        ]],
    }
    session.add(sheet)
    session.commit()

    attendance_api._upsert_attendance_wjx_sheet_raw_rows(
        session,
        rows=[{
            "序号": 10,
            "提交答卷时间": "2026/4/19 08:00:00",
            "来源": "微信",
            "1、所属课程": "20260408第39届念住",
            "2、学号": "2-17",
            "3、姓名": "薛伟",
            "4、修正需求": "更新后的问题",
            "5、其他补充说明": "补充",
        }],
        actor=admin_user,
    )

    sheet = session.exec(select(SheetDocument).where(SheetDocument.numeric_id == location["sheet_id"])).one()
    assert sheet.document_json["rows"] == [[
        "10",
        "2026/4/19 08:00:00",
        "微信",
        "20260408第39届念住",
        "",
        "2-17",
        "薛伟",
        "更新后的问题",
        "补充",
        "人工已处理",
    ]]
    assert sheet.document_json["cell_meta"]["0:3"]["link"]["url"] == "https://www.kdocs.cn/l/nianzhu39"


def test_attendance_feedback_submission_persists_and_keeps_existing_rows(client: TestClient, session):
    existing_entry = AttendanceWjxDataEntry(
        activity_id="264266843",
        seq=644,
        submitted_at_text="2026/4/17 15:51:36",
        duration_text="84秒",
        source="微信",
        source_detail="福建福州",
        source_ip="1.1.1.1",
        course_name="20260401第45届觉观",
        student_id_text="2-17",
        student_name="薛伟",
        correction_request="今天没有收到退款",
        extra_note="",
        synced_at=1713426373.0,
        created_at=1713426373.0,
        updated_at=1713426373.0,
    )
    session.add(existing_entry)
    session.commit()

    submit_response = client.post(
        "/api/attendance/wjx-feedback/submissions",
        json={
            "course_name": "20260408第39届念住",
            "student_id_text": "39",
            "student_name": "吴菲",
            "correction_request": "共学已打满10次(截止第五周)",
            "extra_note": "来自公开采集页",
        },
    )
    assert submit_response.status_code == 200
    submit_payload = submit_response.json()
    assert submit_payload["source"] == "采集系统"
    assert submit_payload["course_name"] == "20260408第39届念住"
    assert submit_payload["student_name"] == "吴菲"

    admin_user = _create_admin_user(session)
    _grant_feature_access(session, user_id=admin_user.id, feature_key="attendance.wjx-data")
    _create_attendance_workbook(session, admin_user)
    _override_user(admin_user)

    try:
        listing = client.get("/api/attendance/wjx-data")
        assert listing.status_code == 200
        page = listing.json()
        assert page["total"] == 2
        assert [item["source"] for item in page["items"]] == ["采集系统", "微信"]
        assert [item["seq"] for item in page["items"]] == [645, 644]
        assert page["items"][0]["course_name"] == "20260408第39届念住"
        assert page["items"][1]["seq"] == 644
        assert page["items"][1]["course_name"] == "20260401第45届觉观"
    finally:
        _clear_user_override()


def test_attendance_wjx_data_delete_keeps_seq_history_for_next_feedback(client: TestClient, session):
    existing_entry = AttendanceWjxDataEntry(
        activity_id="264266843",
        seq=700,
        submitted_at_text="2026/4/18 15:51:36",
        duration_text="84秒",
        source="微信",
        source_detail="福建福州",
        source_ip="1.1.1.1",
        course_name="20260401第45届觉观",
        student_id_text="2-17",
        student_name="薛伟",
        correction_request="今天没有收到退款",
        extra_note="",
        synced_at=1713426373.0,
        created_at=1713426373.0,
        updated_at=1713426373.0,
    )
    session.add(existing_entry)
    session.commit()
    session.refresh(existing_entry)

    admin_user = _create_admin_user(session)
    _grant_feature_access(session, user_id=admin_user.id, feature_key="attendance.wjx-data")
    _override_user(admin_user)

    try:
        delete_response = client.delete(f"/api/attendance/wjx-data/{existing_entry.id}")
        assert delete_response.status_code == 200
        assert delete_response.json()["seq"] == 700

        listing = client.get("/api/attendance/wjx-data")
        assert listing.status_code == 200
        assert listing.json()["items"] == []
    finally:
        _clear_user_override()

    submit_response = client.post(
        "/api/attendance/wjx-feedback/submissions",
        json={
            "course_name": "20260408第39届念住",
            "student_id_text": "39",
            "student_name": "吴菲",
            "correction_request": "共学已打满10次(截止第五周)",
            "extra_note": "删除高序号后的再次提交",
        },
    )
    assert submit_response.status_code == 200
    assert submit_response.json()["seq"] == 701


def test_attendance_wjx_data_delete_requires_superuser(client: TestClient, session, auth_user):
    entry = AttendanceWjxDataEntry(
        activity_id="264266843",
        seq=710,
        submitted_at_text="2026/4/18 15:51:36",
        course_name="20260401第45届觉观",
        student_id_text="2-17",
        student_name="薛伟",
        correction_request="今天没有收到退款",
        synced_at=1713426373.0,
        created_at=1713426373.0,
        updated_at=1713426373.0,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    config = get_or_create_attendance_service_config(session)
    config.granted_user_ids = [auth_user.id]
    session.add(config)
    session.commit()

    _grant_feature_access(session, user_id=auth_user.id, feature_key="attendance.wjx-data")

    response = client.delete(f"/api/attendance/wjx-data/{entry.id}")
    assert response.status_code == 403
    assert response.json()["detail"] == "只有超级管理员可以删除问卷数据"


def test_attendance_wjx_data_feedback_list_and_update(client: TestClient, session):
    admin_user = _create_admin_user(session)
    _grant_feature_access(session, user_id=admin_user.id, feature_key="attendance.wjx-data")
    _override_user(admin_user)

    try:
        submitted = []
        for payload in [
            {
                "course_name": "20260401第45届觉观",
                "student_id_text": "2-17",
                "student_name": "薛伟",
                "correction_request": "第3课没有返款",
                "extra_note": "",
            },
            {
                "course_name": "20260408第39届念住",
                "student_id_text": "39",
                "student_name": "吴菲",
                "correction_request": "补第1课",
                "extra_note": "备注",
            },
            {
                "course_name": "20260415梵呗初阶",
                "student_id_text": "13",
                "student_name": "伍琳",
                "correction_request": "补第2课",
                "extra_note": "",
            },
        ]:
            response = client.post("/api/attendance/wjx-feedback/submissions", json=payload)
            assert response.status_code == 200
            submitted.append(response.json())

        listing = client.get("/api/attendance/wjx-data")
        assert listing.status_code == 200
        page = listing.json()
        assert page["total"] == 3
        assert [item["seq"] for item in page["items"]] == sorted(
            [item["seq"] for item in submitted],
            reverse=True,
        )

        first_item_id = page["items"][0]["id"]
        update_resp = client.patch(
            f"/api/attendance/wjx-data/{first_item_id}",
            json={
                "process_status": "已处理",
                "process_note": "已补登",
                "match_result": {"user_id": "u123"},
                "revision_result": {"status": "ok"},
            },
        )
        assert update_resp.status_code == 200
        updated_item = update_resp.json()
        assert updated_item["process_status"] == "已处理"
        assert updated_item["process_note"] == "已补登"
        assert updated_item["match_result"]["user_id"] == "u123"
        assert updated_item["revision_result"]["status"] == "ok"

        filtered = client.get("/api/attendance/wjx-data", params={"process_status": "已处理"})
        assert filtered.status_code == 200
        filtered_page = filtered.json()
        assert filtered_page["total"] == 1
        assert filtered_page["items"][0]["id"] == first_item_id
    finally:
        _clear_user_override()


def test_attendance_wjx_data_listing_includes_hashed_foreground_colors(client: TestClient, session):
    admin_user = _create_admin_user(session)
    _grant_feature_access(session, user_id=admin_user.id, feature_key="attendance.wjx-data")
    _override_user(admin_user)

    try:
        rows = [
            AttendanceWjxDataEntry(
                activity_id="264266843",
                seq=701,
                submitted_at_text="2026/4/18 09:00:00",
                duration_text="80秒",
                source="微信",
                source_detail="福建福州",
                source_ip="1.1.1.1",
                course_name="20260401第45届觉观",
                student_id_text="39",
                student_name="吴菲",
                correction_request="补第2课",
                extra_note="",
                synced_at=1713426373.0,
                created_at=1713426373.0,
                updated_at=1713426373.0,
            ),
            AttendanceWjxDataEntry(
                activity_id="264266843",
                seq=702,
                submitted_at_text="2026/4/17 10:00:00",
                duration_text="90秒",
                source="微信",
                source_detail="江苏无锡",
                source_ip="2.2.2.2",
                course_name="20260401第45届觉观",
                student_id_text="18",
                student_name="王五",
                correction_request="补第3课",
                extra_note="",
                synced_at=1713426374.0,
                created_at=1713426374.0,
                updated_at=1713426374.0,
            ),
            AttendanceWjxDataEntry(
                activity_id="264266843",
                seq=703,
                submitted_at_text="2026/4/18 12:00:00",
                duration_text="70秒",
                source="微信",
                source_detail="上海上海",
                source_ip="3.3.3.3",
                course_name="20260408第39届念住",
                student_id_text="2-17",
                student_name="赵六",
                correction_request="登记有误",
                extra_note="",
                synced_at=1713426375.0,
                created_at=1713426375.0,
                updated_at=1713426375.0,
            ),
            AttendanceWjxDataEntry(
                activity_id="264266843",
                seq=704,
                submitted_at_text="2026/4/16 18:30:00",
                duration_text="77秒",
                source="微信",
                source_detail="广东广州",
                source_ip="4.4.4.4",
                course_name="20260415梵呗初阶",
                student_id_text="13",
                student_name="吴菲",
                correction_request="返款没到账",
                extra_note="",
                synced_at=1713426376.0,
                created_at=1713426376.0,
                updated_at=1713426376.0,
            ),
        ]
        for row in rows:
            session.add(row)
        session.commit()

        response = client.get("/api/attendance/wjx-data")
        assert response.status_code == 200
        page = response.json()
        assert page["total"] == 4

        items_by_seq = {item["seq"]: item for item in page["items"]}
        assert items_by_seq[701]["foreground_colors"]["submitted"] == items_by_seq[703]["foreground_colors"]["submitted"]
        assert items_by_seq[701]["foreground_colors"]["course"] == items_by_seq[702]["foreground_colors"]["course"]
        assert items_by_seq[701]["foreground_colors"]["student"] == items_by_seq[704]["foreground_colors"]["student"]
        assert re.fullmatch(r"#[0-9A-F]{6}", items_by_seq[701]["foreground_colors"]["submitted"])
        assert re.fullmatch(r"#[0-9A-F]{6}", items_by_seq[701]["foreground_colors"]["course"])
        assert re.fullmatch(r"#[0-9A-F]{6}", items_by_seq[701]["foreground_colors"]["student"])
    finally:
        _clear_user_override()


def test_attendance_wjx_data_listing_defaults_to_seq_desc(client: TestClient, session):
    admin_user = _create_admin_user(session)
    _grant_feature_access(session, user_id=admin_user.id, feature_key="attendance.wjx-data")
    _override_user(admin_user)

    try:
        session.add_all(
            [
                AttendanceWjxDataEntry(
                    activity_id="264266843",
                    seq=705,
                    submitted_at_text="2026/4/18 09:00:00",
                    course_name="20260401第45届觉观",
                    student_id_text="39",
                    student_name="吴菲",
                    correction_request="补第2课",
                    synced_at=1713426373.0,
                    created_at=1713426373.0,
                    updated_at=1713426373.0,
                ),
                AttendanceWjxDataEntry(
                    activity_id="264266843",
                    seq=704,
                    submitted_at_text="2026/4/18 10:00:00",
                    course_name="20260408第39届念住",
                    student_id_text="18",
                    student_name="王五",
                    correction_request="补第3课",
                    synced_at=1713427373.0,
                    created_at=1713427373.0,
                    updated_at=1713427373.0,
                ),
            ]
        )
        session.commit()

        response = client.get("/api/attendance/wjx-data")
        assert response.status_code == 200
        page = response.json()
        assert [item["seq"] for item in page["items"]] == [705, 704]
    finally:
        _clear_user_override()
