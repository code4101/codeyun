import sys
import pytest
from fastapi.testclient import TestClient

# pytest fixture 会自动处理 client 和 test_device
# 我们直接使用它们即可

def test_node_status_authorized(client: TestClient, test_device):
    """Test getting node status with valid token"""
    token = test_device["token"]
    headers = {"Authorization": f"Bearer {token}"}
    response = client.get("/api/device-control/status", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["hostname"] == "Test Local Device"

def test_node_status_unauthorized(client: TestClient, test_device):
    """Test getting node status without token or invalid token"""
    # No token
    response = client.get("/api/device-control/status")
    assert response.status_code == 401
    
    # Invalid token
    headers = {"Authorization": "Bearer invalid-token"}
    response = client.get("/api/device-control/status", headers=headers)
    assert response.status_code == 401

def test_exec_cmd(client: TestClient, test_device):
    """Test executing a command on the node"""
    token = test_device["token"]
    headers = {"Authorization": f"Bearer {token}"}
    cmd = f"{sys.executable} -c \"print('hello')\""
    response = client.post("/api/device-control/exec_cmd", 
                          json={"command": cmd},
                          headers=headers)
    
    if response.status_code != 200:
        print(f"Exec cmd failed: {response.text}")
        
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "started"
    assert "pid" in data

def test_match_processes(client: TestClient, test_device):
    """Test process matching API"""
    token = test_device["token"]
    headers = {"Authorization": f"Bearer {token}"}
    tasks = [
        {"id": "task-1", "command": "non_existent_process_12345"}
    ]
    response = client.post("/api/device-control/match_processes",
                          json={"tasks": tasks},
                          headers=headers)
    
    assert response.status_code == 200
    data = response.json()
    assert "task-1" in data
    assert data["task-1"]["running"] is False


def test_attendance_user_match_lookup_uses_shared_api(client: TestClient, test_device, monkeypatch):
    """Device-control user lookup should delegate to the shared kq5034 service API."""

    from backend.api import device_control

    calls = []

    def fake_lookup_registration_users_browser(items, **kwargs):
        calls.append({"items": items, **kwargs})
        return [{"key": "row-1", "user_id": "u_live"}]

    monkeypatch.setattr(
        device_control,
        "lookup_registration_users_browser",
        fake_lookup_registration_users_browser,
    )

    token = test_device["token"]
    headers = {"Authorization": f"Bearer {token}"}
    response = client.post(
        "/api/device-control/attendance/user-match/lookup",
        headers=headers,
        json={
            "course_name": "d260509梵呗初阶",
            "course_product_name": "梵呗初阶网课",
            "shop_id": 1,
            "close_browser": True,
            "items": [{"key": "row-1", "names": ["阿丹"], "phones": ["15326693765"]}],
        },
    )

    assert response.status_code == 200
    assert response.json() == {"results": [{"key": "row-1", "user_id": "u_live"}]}
    assert calls == [
        {
            "items": [{"key": "row-1", "names": ["阿丹"], "phones": ["15326693765"]}],
            "course_name": "d260509梵呗初阶",
            "course_product_name": "梵呗初阶网课",
            "shop_id": 1,
            "close_browser": True,
        }
    ]


def test_attendance_fanbei_step1_uses_shared_api(client: TestClient, test_device, monkeypatch):
    """Device-control Fanbei step1 should delegate to the shared kq5034 service API."""

    from backend.api import device_control

    calls = []

    def fake_sync_fanbei_attendance_step1(**kwargs):
        calls.append(kwargs)
        return {
            "course_name": kwargs["course_name"],
            "shop_id": kwargs["shop_id"],
            "lesson_update_count": 3,
            "clockin_update_count": 1,
        }

    monkeypatch.setattr(
        device_control,
        "sync_fanbei_attendance_step1",
        fake_sync_fanbei_attendance_step1,
    )

    token = test_device["token"]
    headers = {"Authorization": f"Bearer {token}"}
    response = client.post(
        "/api/device-control/attendance/fanbei/step1",
        headers=headers,
        json={
            "course_name": "d260509梵呗初阶",
            "shop_id": 1,
            "update_lessons": True,
            "update_clockins": True,
            "clockin_pattern": "d260509梵呗初阶-*",
            "close_browser": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["lesson_update_count"] == 3
    assert calls == [
        {
            "course_name": "d260509梵呗初阶",
            "shop_id": 1,
            "update_lessons": True,
            "update_clockins": True,
            "clockin_pattern": "d260509梵呗初阶-*",
            "close_browser": True,
        }
    ]


def test_attendance_fanbei_step2_data_uses_shared_api(client: TestClient, test_device, monkeypatch):
    """Device-control Fanbei step2 data should delegate to the shared kq5034 service API."""

    from backend.api import device_control

    calls = []

    def fake_build_fanbei_attendance_step2_data(**kwargs):
        calls.append(kwargs)
        return {
            "course_name": kwargs["course_name"],
            "columns": ["user_id2", "打卡数", "-第01课"],
            "rows": [["u1", 3, "当堂完成"]],
        }

    monkeypatch.setattr(
        device_control,
        "build_fanbei_attendance_step2_data",
        fake_build_fanbei_attendance_step2_data,
    )

    token = test_device["token"]
    headers = {"Authorization": f"Bearer {token}"}
    response = client.post(
        "/api/device-control/attendance/fanbei/step2-data",
        headers=headers,
        json={
            "course_name": "d260509梵呗初阶",
            "user_ids": ["u1"],
            "clockin_names": ["打卡数"],
            "clockin_titles": ["学修日志01"],
        },
    )

    assert response.status_code == 200
    assert response.json()["rows"] == [["u1", 3, "当堂完成"]]
    assert calls == [
        {
            "course_name": "d260509梵呗初阶",
            "user_ids": ["u1"],
            "clockin_names": ["打卡数"],
            "clockin_titles": ["学修日志01"],
        }
    ]


def test_attendance_fanbei_step3_uses_local_sheet_api(client: TestClient, test_device, monkeypatch):
    """Device-control Fanbei step3 should refresh the target CodeYun sheet instance."""

    from backend.api import device_control

    calls = []

    def fake_run_fanbei_attendance_step3_for_sheet(**kwargs):
        calls.append(kwargs)
        return {
            "sheet_id": kwargs["sheet_id"],
            "course_name": kwargs["course_name"],
            "lesson_columns": 11,
            "updated_rows": 2,
            "updated_cells": 2,
            "styled_cells": 3,
            "video_refund_total": 80,
            "message": "当前 CodeYun 实例已执行 step3",
        }

    monkeypatch.setattr(
        device_control,
        "run_fanbei_attendance_step3_for_sheet",
        fake_run_fanbei_attendance_step3_for_sheet,
    )

    token = test_device["token"]
    headers = {"Authorization": f"Bearer {token}"}
    response = client.post(
        "/api/device-control/attendance/fanbei/step3",
        headers=headers,
        json={
            "sheet_id": 6,
            "course_name": "d260509梵呗初阶",
        },
    )

    assert response.status_code == 200
    assert response.json()["video_refund_total"] == 80
    assert calls == [{"sheet_id": 6, "course_name": "d260509梵呗初阶"}]


def test_rename_device_is_rejected(client: TestClient, test_device):
    """Local device naming should be managed as user entry alias, not device runtime state."""
    token = test_device["token"]
    headers = {"Authorization": f"Bearer {token}"}
    new_name = "Renamed Device"
    
    response = client.post("/api/device-control/rename",
                          json={"name": new_name},
                          headers=headers)
    
    assert response.status_code == 400
    assert "别名" in response.json()["detail"]
