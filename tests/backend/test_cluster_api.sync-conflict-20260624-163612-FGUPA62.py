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
