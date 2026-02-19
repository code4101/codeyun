import sys
import pytest
from fastapi.testclient import TestClient

# pytest fixture 会自动处理 client 和 test_device
# 我们直接使用它们即可

def test_agent_status_authorized(client: TestClient, test_device):
    """Test getting agent status with valid token"""
    token = test_device["token"]
    headers = {"Authorization": f"Bearer {token}"}
    response = client.get("/api/agent/status", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["hostname"] == "Test Local Device"

def test_agent_status_unauthorized(client: TestClient):
    """Test getting agent status without token or invalid token"""
    # No token
    response = client.get("/api/agent/status")
    assert response.status_code == 401
    
    # Invalid token
    headers = {"Authorization": "Bearer invalid-token"}
    response = client.get("/api/agent/status", headers=headers)
    assert response.status_code == 401

def test_exec_cmd(client: TestClient, test_device):
    """Test executing a command on the agent"""
    token = test_device["token"]
    headers = {"Authorization": f"Bearer {token}"}
    cmd = f"{sys.executable} -c \"print('hello')\""
    response = client.post("/api/agent/exec_cmd", 
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
    response = client.post("/api/agent/match_processes",
                          json={"tasks": tasks},
                          headers=headers)
    
    assert response.status_code == 200
    data = response.json()
    assert "task-1" in data
    assert data["task-1"]["running"] is False

def test_rename_agent(client: TestClient, test_device):
    """Test renaming the agent"""
    token = test_device["token"]
    headers = {"Authorization": f"Bearer {token}"}
    new_name = "Renamed Device"
    
    response = client.post("/api/agent/rename",
                          json={"name": new_name},
                          headers=headers)
    
    assert response.status_code == 200
    assert response.json()["name"] == new_name
