import requests
import time

BASE_URL = "http://localhost:8000"

def test_local_task():
    print("Testing Local Task...")
    # Create task
    resp = requests.post(f"{BASE_URL}/api/task/create", json={
        "name": "Test Local",
        "command": "python -c \"print('Hello Local')\"",
        "device_id": "local"
    })
    if resp.status_code != 200:
        print("Error creating task:", resp.text)
        return

    print("Create:", resp.status_code, resp.json())
    task_id = resp.json()['id']
    
    # Start task
    resp = requests.post(f"{BASE_URL}/api/task/{task_id}/start")
    print("Start:", resp.status_code, resp.json())
    
    # Wait and check logs
    time.sleep(2)
    resp = requests.get(f"{BASE_URL}/api/task/{task_id}/logs")
    print("Logs:", resp.json())

def test_remote_device_loopback():
    print("\nTesting Remote Device (Loopback)...")
    # Add self as remote device
    resp = requests.post(f"{BASE_URL}/api/task/devices/add", json={
        "id": "loopback",
        "name": "Loopback Device",
        "url": BASE_URL
    })
    print("Add Device:", resp.status_code, resp.json())
    
    # Create task on remote device
    resp = requests.post(f"{BASE_URL}/api/task/create", json={
        "name": "Test Remote",
        "command": "python -c \"print('Hello Remote')\"",
        "device_id": "loopback"
    })
    print("Create Remote Task:", resp.status_code, resp.json())
    task_id = resp.json()['id']
    
    # Start task
    resp = requests.post(f"{BASE_URL}/api/task/{task_id}/start")
    print("Start Remote Task:", resp.status_code, resp.json())

if __name__ == "__main__":
    try:
        test_local_task()
        test_remote_device_loopback()
    except Exception as e:
        print(e)
