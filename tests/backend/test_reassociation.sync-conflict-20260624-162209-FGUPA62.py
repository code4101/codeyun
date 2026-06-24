import sys
import time
import subprocess
import psutil
import uuid
import pytest
from backend.core.device import device_manager, LocalDevice
from backend.models import Task

@pytest.fixture
def running_process():
    # Start a process manually with a unique marker
    unique_id = str(uuid.uuid4())
    # Use a comment in python code to make cmdline unique
    code = f"import time; time.sleep(10); # {unique_id}"
    
    cmd = [sys.executable, "-c", code]
    proc = subprocess.Popen(cmd)
    
    print(f"Started manual process with PID {proc.pid} and ID {unique_id}")
    
    yield {"pid": proc.pid, "unique_id": unique_id, "code": code}
    
    # Teardown
    try:
        proc.terminate()
        proc.wait(timeout=2)
    except:
        try:
            proc.kill()
        except:
            pass

def test_reassociation(test_device, running_process):
    # Ensure we are using LocalDevice
    device_id = test_device["id"]
    device = device_manager.get_device(device_id)
    
    if not isinstance(device, LocalDevice):
        pytest.skip("Not running on LocalDevice")
        
    proc_info = running_process
    code = proc_info["code"]
    
    # Construct task command string matching the process
    task_cmd = f'{sys.executable} -c "{code}"'
    
    task = Task(
        id="test-reassoc-1",
        name="Test Reassociation",
        command=task_cmd,
        created_at=time.time(),
        device_id=device_id
    )
    
    # Ensure the device doesn't know about this task yet
    if "test-reassoc-1" in device.processes:
        del device.processes["test-reassoc-1"]
    if "test-reassoc-1" in device.saved_pids:
        del device.saved_pids["test-reassoc-1"]
        
    # Run scan
    print("Scanning for tasks...")
    device.scan_running_tasks([task])
    
    # Verify
    status = device.get_task_status("test-reassoc-1")
    print(f"Task status: {status}")
    
    assert status.running is True, "Task should be running"
    assert status.pid == proc_info["pid"], "PID should match"
    
    print("Re-association successful!")
