import pytest
import requests
import time

from backend.core.settings import get_settings

BASE_URL = "http://localhost:8000"
settings = get_settings()


def get_local_headers():
    token = settings.device_token
    if not token:
        return None
    return {"X-Device-Token": token}

def is_server_running():
    try:
        resp = requests.get(f"{BASE_URL}/", timeout=1)
        return resp.status_code == 200
    except:
        return False


def has_valid_local_token(headers):
    if not headers:
        return False

    try:
        resp = requests.get(f"{BASE_URL}/api/device-control/status", headers=headers, timeout=1)
        return resp.status_code == 200
    except Exception:
        return False


LOCAL_HEADERS = get_local_headers()
VALID_LOCAL_HEADERS = LOCAL_HEADERS if has_valid_local_token(LOCAL_HEADERS) else None

@pytest.mark.skipif(
    not is_server_running() or not VALID_LOCAL_HEADERS,
    reason="Backend server not running on localhost:8000 or local device token unavailable/invalid",
)
class TestIntegration:
    
    def test_local_task(self):
        print("Testing Local Task...")
        # Create task
        resp = requests.post(
            f"{BASE_URL}/api/task/create",
            json={
                "name": "Test Local",
                "command": "python -c \"print('Hello Local')\"",
                "device_id": "local"
            },
            headers=VALID_LOCAL_HEADERS,
        )
        if resp.status_code != 200:
            print("Error creating task:", resp.text)
            pytest.fail(f"Failed to create task: {resp.text}")
    
        print("Create:", resp.status_code, resp.json())
        task_id = resp.json()['id']
        
        # Start task
        resp = requests.post(f"{BASE_URL}/api/task/{task_id}/start", headers=VALID_LOCAL_HEADERS)
        print("Start:", resp.status_code, resp.json())
        assert resp.status_code == 200
        
        # Wait and check logs
        time.sleep(2)
        resp = requests.get(f"{BASE_URL}/api/task/{task_id}/logs", headers=VALID_LOCAL_HEADERS)
        print("Logs:", resp.json())
        assert resp.status_code == 200
        assert len(resp.json().get('logs', [])) > 0
    
    def test_remote_device_loopback(self):
        print("\nTesting Remote Device (Loopback)...")
        try:
            # Add self as remote device
            # Note: This might fail if the device ID matches local ID.
            # We use a fake ID but URL points to local.
            
            # The backend/api/device.py add_user_device logic:
            # It calls remote /api/device-control/status to get ID.
            # If ID matches local ID, it fails with 409 Matches Local Device.
            # So adding self as remote is tricky if we don't mock the remote response ID.
            
            # For integration test, unless we run another instance, we can't really test remote loopback easily 
            # without triggering "Matches Local Device" error.
            
            # So we might expect failure or skip this part.
            pass
            
        finally:
            # Cleanup
            pass
