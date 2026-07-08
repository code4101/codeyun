from sqlmodel import Session, SQLModel, create_engine

from backend.api import attendance as attendance_api
from backend.models import AttendanceAccountAsset, User, UserDevice


def test_attendance_configs_bootstrap_payload_includes_page_core_data_and_devices():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        user = User(id=1, username="tester", nickname="Tester", hashed_password="x", is_superuser=True)
        primary_device = UserDevice(
            entry_id="device-1",
            user_id=user.id,
            device_id="device-local",
            mode="local",
            name="本机设备",
            token="local-token",
            is_active=True,
            order_index=0,
        )
        browser_device = UserDevice(
            entry_id="device-2",
            user_id=user.id,
            device_id="device-remote",
            mode="remote",
            name="远程浏览器",
            token="remote-token",
            server_url="http://127.0.0.1:9001",
            is_active=True,
            order_index=1,
        )
        account = AttendanceAccountAsset(
            id="account-1",
            login_username="wjx-admin",
            name="问卷星主账号",
            password_encrypted=attendance_api.encrypt_attendance_secret("secret-pass"),
            is_active=True,
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        )
        session.add(user)
        session.add(primary_device)
        session.add(browser_device)
        session.add(account)
        session.commit()

        config = attendance_api.get_or_create_attendance_service_config(session, actor=user)
        config.current_wjx_account_id = account.id
        config.execution_device_entry_id = primary_device.entry_id
        session.add(config)
        session.commit()

        attendance_api.update_attendance_service_extra_config(
            session,
            scan_reminder_users=["考勤后台", "文件传输助手"],
            order_lookup_mode="hybrid",
        )
        attendance_api.update_attendance_course_data_flow_config(
            session,
            browser_device_entry_id=browser_device.entry_id,
            data_device_entry_id=primary_device.entry_id,
            step_device_entry_ids={"1": browser_device.entry_id, "2": primary_device.entry_id},
        )

        payload = attendance_api._build_attendance_configs_bootstrap_payload(session, user)

    assert payload.config["service"]["execution_device_entry_id"] == "device-1"
    assert payload.config["service"]["scan_reminder_users"] == ["考勤后台", "文件传输助手"]
    assert payload.config["service"]["order_lookup_mode"] == "hybrid"
    assert payload.course_data_flow_config["course_data_flow"]["effective_browser_device_entry_id"] == "device-2"
    assert payload.course_data_flow_config["course_data_flow"]["data_device_entry_id"] == "device-1"
    assert [item["id"] for item in payload.devices] == ["device-1", "device-2"]
    assert payload.accounts[0]["login_username"] == "wjx-admin"
    assert payload.accounts[0]["password"] == "secret-pass"
