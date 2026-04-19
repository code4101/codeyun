from __future__ import annotations

import re

from fastapi.testclient import TestClient
from sqlmodel import select

from backend.app import app
from backend.api import attendance as attendance_api
from backend.core.auth import get_current_user_from_token, get_optional_current_user_from_token
from backend.core.attendance_service import (
    DEFAULT_FEEDBACK_COURSE_NAMES,
    encrypt_attendance_secret,
    get_attendance_service_extra_config,
    get_attendance_service_order_operation_password,
    get_or_create_attendance_service_config,
)
from backend.core.feature_access import (
    FEATURE_ACCESS_SUBJECT_ANONYMOUS,
    FEATURE_ACCESS_SUBJECT_USER,
    save_feature_access_policy_overrides,
)
from backend.models import AttendanceAccountAsset, AttendanceOrderRefundHistory, AttendanceWjxDataEntry, User, UserDevice


class ImmediateThread:
    def __init__(self, *, target, kwargs=None, daemon=None):
        self._target = target
        self._kwargs = kwargs or {}
        self.daemon = daemon

    def start(self):
        self._target(**self._kwargs)


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


def test_attendance_config_requires_attendance_access(client: TestClient, auth_user):
    response = client.get("/api/attendance/config")
    assert response.status_code == 403


def test_attendance_config_and_template_crud(client: TestClient, session, test_device):
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

        template_resp = client.post(
            "/api/attendance/templates",
            json={
                "name": "问题反馈表",
                "activity_id": "264266843",
                "is_active": True,
            },
        )
        assert template_resp.status_code == 200
        template = template_resp.json()
        assert template["activity_id"] == "264266843"

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
        assert config["service"]["scan_reminder_users"] == ["考勤后台", "文件传输助手"]
        assert config["service"]["order_lookup_mode"] == "db_only"
        assert config["service"]["order_operation_password_configured"] is True
        assert "password" not in config["current_account"]
        assert config["current_execution_device"]["entry_id"] == entry_id
        assert config["fixed_wjx_template"]["activity_id"] == "264266843"
        assert config["fixed_wjx_template"]["design_url"] == "https://www.wjx.cn/wjx/design/designstart.aspx?activity=264266843"
        assert config["fixed_wjx_template"]["view_url"] == "https://www.wjx.cn/vm/PbkKDaK.aspx"
        assert config["fixed_wjx_template"]["fill_url"] == "https://www.wjx.cn/vm/PbkKDaK.aspx"
        assert get_attendance_service_extra_config(session)["scan_reminder_users"] == ["考勤后台", "文件传输助手"]
        assert get_attendance_service_extra_config(session)["order_lookup_mode"] == "db_only"
        assert get_attendance_service_extra_config(session)["order_operation_password_configured"] is True
        assert get_attendance_service_order_operation_password(session) == "refund-pass"
    finally:
        _clear_user_override()


def test_attendance_run_completes_and_persists_global_selection(client: TestClient, session, monkeypatch, test_device):
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

        account = client.post(
            "/api/attendance/accounts",
            json={
                "login_username": "18850000001",
                "password": "plain-pass",
            },
        ).json()

        config_resp = client.put(
            "/api/attendance/config",
            json={
                "execution_device_entry_id": entry_id,
            },
        )
        assert config_resp.status_code == 200

        monkeypatch.setattr(
            "backend.api.attendance._execute_run_on_entry",
            lambda entry_snapshot, execution_payload: {
                "action": execution_payload["action"],
                "visible_names": ["20260301第44届觉观", "20260309梵呗初阶"],
                "hidden_applied": ["20260301第44届觉观"],
                "added_applied": ["20260401第45届觉观"],
            },
        )
        monkeypatch.setattr("backend.api.attendance.threading.Thread", ImmediateThread)

        run_resp = client.post(
            "/api/attendance/wjx-runs",
            json={
                "action": "apply",
                "hide": ["20260301第44届觉观"],
                "add": ["20260401第45届觉观"],
            },
        )
        assert run_resp.status_code == 200
        run_id = run_resp.json()["id"]

        fetched_run = client.get(f"/api/attendance/wjx-runs/{run_id}")
        assert fetched_run.status_code == 200
        run = fetched_run.json()
        assert run["status"] == "completed"
        assert run["template_id"] == "wjx-course-catalog"
        assert run["result"]["added_applied"] == ["20260401第45届觉观"]

        config_resp = client.get("/api/attendance/config")
        assert config_resp.status_code == 200
        config = config_resp.json()
        assert config["service"]["current_wjx_account_id"] == account["id"]
        assert config["service"]["execution_device_entry_id"] == entry_id
    finally:
        _clear_user_override()


def test_attendance_feedback_form_meta_is_public_and_tracks_course_catalog_updates(client: TestClient, session):
    initial_meta = client.get("/api/attendance/wjx-feedback-form")
    assert initial_meta.status_code == 200
    assert initial_meta.json()["course_names"] == DEFAULT_FEEDBACK_COURSE_NAMES

    admin_user = _create_admin_user(session)
    _override_user(admin_user)

    try:
        update_resp = client.put(
            "/api/attendance/wjx-feedback-form",
            json={
                "course_names": ["20260415第46届觉观", "20260420梵呗初阶", "20260415第46届觉观", "  "],
            },
        )
        assert update_resp.status_code == 200
        update_payload = update_resp.json()
        assert update_payload["course_names"] == ["20260415第46届觉观", "20260420梵呗初阶"]

        extra_config = get_attendance_service_extra_config(session)
        assert extra_config["feedback_course_names"] == ["20260415第46届觉观", "20260420梵呗初阶"]
        assert extra_config["feedback_course_names_updated_at"] is not None
    finally:
        _clear_user_override()

    updated_meta = client.get("/api/attendance/wjx-feedback-form")
    assert updated_meta.status_code == 200
    updated_payload = updated_meta.json()
    assert updated_payload["course_names"] == ["20260415第46届觉观", "20260420梵呗初阶"]
    assert updated_payload["course_names_updated_at"] is not None
    assert updated_payload["template"]["fill_url"] == "https://www.wjx.cn/vm/PbkKDaK.aspx"


def test_attendance_feedback_form_meta_update_bypasses_user_feature_access_policy(client: TestClient, session, auth_user):
    save_feature_access_policy_overrides(
        session,
        subject_type=FEATURE_ACCESS_SUBJECT_USER,
        subject_user_id=auth_user.id,
        overrides={
            "attendance-tools": "deny",
            "attendance.wjx": "deny",
            "attendance.wjx-feedback": "deny",
            "attendance.wjx-templates": "deny",
        },
    )

    response = client.put(
        "/api/attendance/wjx-feedback-form",
        json={"course_names": ["20260415第46届觉观", "20260420梵呗初阶"]},
    )
    assert response.status_code == 200
    assert response.json()["course_names"] == ["20260415第46届觉观", "20260420梵呗初阶"]


def test_attendance_feedback_public_endpoints_bypass_feature_access_policy(client: TestClient, session):
    save_feature_access_policy_overrides(
        session,
        subject_type=FEATURE_ACCESS_SUBJECT_ANONYMOUS,
        overrides={
            "attendance-tools": "deny",
            "attendance.wjx": "deny",
            "attendance.wjx-feedback": "deny",
            "attendance.wjx-templates": "deny",
        },
    )

    meta_response = client.get("/api/attendance/wjx-feedback-form")
    assert meta_response.status_code == 200
    assert meta_response.json()["course_names"] == DEFAULT_FEEDBACK_COURSE_NAMES

    update_response = client.put(
        "/api/attendance/wjx-feedback-form",
        json={"course_names": ["20260415第46届觉观", "20260420梵呗初阶"]},
    )
    assert update_response.status_code == 200
    assert update_response.json()["course_names"] == ["20260415第46届觉观", "20260420梵呗初阶"]

    submit_response = client.post(
        "/api/attendance/wjx-feedback/submissions",
        json={
            "course_name": "20260415第46届觉观",
            "student_id_text": "1",
            "student_name": "游客测试",
            "correction_request": "公开入口仍可提交",
            "extra_note": "",
        },
    )
    assert submit_response.status_code == 200
    assert submit_response.json()["student_name"] == "游客测试"


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


def test_attendance_config_allows_granted_order_user_without_password(client: TestClient, session, auth_user):
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
    config.granted_user_ids = [auth_user.id]
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


def test_attendance_wjx_data_requires_data_feature(client: TestClient, session, auth_user):
    config = get_or_create_attendance_service_config(session)
    config.granted_user_ids = [auth_user.id]
    session.add(config)
    session.commit()

    _grant_feature_access(session, user_id=auth_user.id, feature_key="attendance.wjx-templates")

    public_response = client.get("/api/attendance/wjx-data")
    assert public_response.status_code == 200
    assert public_response.json()["items"] == []

    save_feature_access_policy_overrides(
        session,
        subject_type=FEATURE_ACCESS_SUBJECT_USER,
        subject_user_id=auth_user.id,
        overrides={"attendance.wjx-data": "allow"},
    )

    allowed_response = client.get("/api/attendance/wjx-data")
    assert allowed_response.status_code == 200
    assert allowed_response.json()["items"] == []


def test_attendance_wjx_data_public_view_only_returns_pending_sanitized_rows(client: TestClient, session):
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

    assert payload["total"] == 2
    assert payload["sync_state"] is None
    assert [item["seq"] for item in payload["items"]] == [802, 801]
    assert [item["course_name"] for item in payload["items"]] == ["20260408第39届念住", "20260401第45届觉观"]

    first = payload["items"][0]
    assert first["student_id_text"] == ""
    assert first["student_name"] == ""
    assert first["source"] == ""
    assert first["source_ip"] == ""
    assert first["correction_request"] == ""
    assert first["extra_note"] == ""
    assert first["raw_row"] == {}
    assert first["foreground_colors"]["course"] is not None
    assert first["foreground_colors"]["student"] is None


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


def test_attendance_wjx_data_sync_list_and_update(client: TestClient, session, monkeypatch, test_device):
    admin_user = _create_admin_user(session)
    _override_user(admin_user)

    try:
        add_device_resp = client.post(
            "/api/devices/add",
            json={
                "mode": "local",
                "token": "attendance-local-token",
                "alias": "问卷同步设备",
            },
        )
        assert add_device_resp.status_code == 200
        entry_id = add_device_resp.json()["id"]

        account = client.post(
            "/api/attendance/accounts",
            json={
                "login_username": "18850009999",
                "password": "plain-pass",
            },
        ).json()

        recorded_payloads = []
        sync_results = [
            {
                "activity_id": "264266843",
                "exist_max_id": 0,
                "latest_max_id": 11,
                "recent_count": 2,
                "fetched_count": 2,
                "incremental_count": 2,
                "used_all_pages": False,
                "rows": [
                    {
                        "序号": 10,
                        "提交答卷时间": "2026/4/18 08:00:00",
                        "所用时间": "80秒",
                        "来源": "微信",
                        "来源详情": "福建福州",
                        "来自IP": "1.1.1.1",
                        "1、所属课程": "20260401第45届觉观",
                        "2、学号": "2-17",
                        "3、姓名": "薛伟",
                        "4、修正需求": "第3课没有返款",
                        "5、其他补充说明": "",
                    },
                    {
                        "序号": 11,
                        "提交答卷时间": "2026/4/18 08:05:00",
                        "所用时间": "90秒",
                        "来源": "微信",
                        "来源详情": "上海上海",
                        "来自IP": "2.2.2.2",
                        "1、所属课程": "20260408第39届念住",
                        "2、学号": "39",
                        "3、姓名": "吴菲",
                        "4、修正需求": "",
                        "5、其他补充说明": "备注",
                    },
                ],
            },
            {
                "activity_id": "264266843",
                "exist_max_id": 11,
                "latest_max_id": 12,
                "recent_count": 1,
                "fetched_count": 1,
                "incremental_count": 1,
                "used_all_pages": False,
                "rows": [
                    {
                        "序号": 12,
                        "提交答卷时间": "2026/4/18 08:10:00",
                        "所用时间": "77秒",
                        "来源": "微信",
                        "来源详情": "江苏无锡",
                        "来自IP": "3.3.3.3",
                        "1、所属课程": "20260415梵呗初阶",
                        "2、学号": "13",
                        "3、姓名": "伍琳",
                        "4、修正需求": "补第2课",
                        "5、其他补充说明": "",
                    }
                ],
            },
        ]

        def fake_sync(entry_snapshot, execution_payload):
            recorded_payloads.append(execution_payload)
            return sync_results.pop(0)

        monkeypatch.setattr("backend.api.attendance._execute_wjx_data_sync_on_entry", fake_sync)

        first_sync = client.post(
            "/api/attendance/wjx-data/sync",
            json={
                "account_id": account["id"],
                "execution_device_entry_id": entry_id,
            },
        )
        assert first_sync.status_code == 200
        first_payload = first_sync.json()
        assert first_payload["inserted_count"] == 2
        assert first_payload["updated_count"] == 0
        assert first_payload["latest_max_seq"] == 11
        assert first_payload["sync_state"]["stored_count"] == 2

        second_sync = client.post(
            "/api/attendance/wjx-data/sync",
            json={},
        )
        assert second_sync.status_code == 200
        second_payload = second_sync.json()
        assert second_payload["inserted_count"] == 1
        assert second_payload["latest_max_seq"] == 12
        assert second_payload["sync_state"]["stored_count"] == 3

        assert recorded_payloads[0]["exist_max_id"] == 0
        assert recorded_payloads[1]["exist_max_id"] == 11

        listing = client.get("/api/attendance/wjx-data")
        assert listing.status_code == 200
        page = listing.json()
        assert page["total"] == 3
        assert [item["seq"] for item in page["items"]] == [12, 11, 10]
        assert page["sync_state"]["last_max_seq"] == 12
        assert page["sync_state"]["execution_device_entry_id"] == entry_id

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
        assert filtered_page["items"][0]["seq"] == 12

        config_resp = client.get("/api/attendance/config")
        assert config_resp.status_code == 200
        config_payload = config_resp.json()
        assert config_payload["service"]["current_wjx_account_id"] == account["id"]
        assert config_payload["service"]["execution_device_entry_id"] == entry_id
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
