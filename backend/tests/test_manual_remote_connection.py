import os
import sys
import requests
import json
import pytest

# Add project root to path for imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from sqlmodel import Session, select, create_engine
from backend.models import Device, Task

# This test file is intended for manual execution or integration testing
# against a live environment. It requires the backend server to be running.

# Use absolute path to ensure correct DB location
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../backend/data/codeyun.db'))
ENGINE = create_engine(f'sqlite:///{DB_PATH}')

def get_device_info(device_name="codepc-mi15"):
    with Session(ENGINE) as session:
        dev = session.exec(select(Device).where(Device.name == device_name)).first()
        if not dev:
            # Try finding by partial ID if name fails (fallback)
            dev = session.exec(select(Device).where(Device.id.like("3e13046f%"))).first()
        return dev

def get_local_device():
    with Session(ENGINE) as session:
        return session.exec(select(Device).where(Device.type == "LocalDevice")).first()

def test_remote_connection_mi15():
    """
    Test connection to remote device mi15 using its configured token.
    """
    mi15 = get_device_info("codepc-mi15")
    if not mi15:
        pytest.skip("Device 'codepc-mi15' not found in local DB")

    url = mi15.url.rstrip('/') + "/api/task/"
    token = mi15.api_token
    
    print(f"\n--- Testing Remote Connection to {mi15.name} ---")
    print(f"URL: {url}")
    print(f"Token (from DB): {token[:10]}... if exists else None")
    
    if not token:
        pytest.fail("No API Token found for mi15 in local DB. Please update it.")

    try:
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=5)
        print(f"Status Code: {resp.status_code}")
        
        if resp.status_code == 200:
            tasks = resp.json()
            print(f"Success! Retrieved {len(tasks)} tasks.")
            print(json.dumps(tasks, indent=2, ensure_ascii=False))
            
            # Simple check for task content
            for task in tasks:
                assert 'codepc_mf' not in task.get('command', ''), \
                    f"Task {task.get('name')} seems to belong to codepc_mf!"
        elif resp.status_code == 401:
            # For manual test script, we raise Exception instead of pytest.fail to catch it in main
            raise Exception("Authentication failed (401). The token in local DB is invalid for the remote device.")
        else:
            raise Exception(f"Request failed with status {resp.status_code}: {resp.text}")
            
    except requests.exceptions.ConnectionError:
        raise Exception(f"Could not connect to {url}. Is the remote device online?")

def test_remote_connection_with_local_token():
    """
    Test connection using LOCAL device's token (to check for token confusion/sync issues).
    This test is expected to fail if everything is configured correctly (should be 401 or 403),
    unless the remote device has a copy of local device's data.
    """
    mi15 = get_device_info("codepc-mi15")
    local = get_local_device()
    
    if not mi15 or not local:
        pytest.skip("Devices not found")

    url = mi15.url.rstrip('/') + "/api/task/"
    local_token = local.api_token
    
    print(f"\n--- Testing Connection with LOCAL Token (Confused Identity Check) ---")
    print(f"Using Token of: {local.name}")
    
    try:
        resp = requests.get(url, headers={"Authorization": f"Bearer {local_token}"}, timeout=5)
        print(f"Status Code: {resp.status_code}")
        
        if resp.status_code == 200:
            tasks = resp.json()
            print("WARNING: Connection successful with LOCAL token!")
            print(f"Retrieved {len(tasks)} tasks.")
            # If we get tasks here, it strongly suggests the remote device thinks we are 'codepc_mf'
            # and is returning tasks assigned to 'codepc_mf'.
            
            # Check if these are indeed 'mf' tasks
            mf_tasks = [t for t in tasks if 'codepc_mf' in t.get('command', '')]
            if mf_tasks:
                print(f"CONFIRMED: Remote device returned {len(mf_tasks)} tasks belonging to codepc_mf.")
            else:
                print("Tasks do not explicitly reference codepc_mf, but token was accepted.")
            
        elif resp.status_code in [401, 403]:
            print("Good: Local token was rejected (as expected for independent devices).")
        else:
            print(f"Unexpected status: {resp.status_code}")
            
    except Exception as e:
        print(f"Connection error: {e}")

if __name__ == "__main__":
    # Allow running directly without pytest
    print("Running manual tests...")
    
    # We catch exceptions to ensure all tests run
    try:
        test_remote_connection_mi15()
    except Exception as e:
        print(f"Test mi15 failed: {e}")
        
    try:
        test_remote_connection_with_local_token()
    except Exception as e:
        print(f"Test local_token failed: {e}")
