import sys
import time
import subprocess
import psutil
import uuid
import pytest
from types import SimpleNamespace
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

class FakeProcess:
    def __init__(self, pid, cmdline):
        self.pid = pid
        self.info = {
            "pid": pid,
            "cmdline": cmdline,
            "create_time": time.time(),
        }

    def is_running(self):
        return True

    def status(self):
        return psutil.STATUS_RUNNING

    def create_time(self):
        return self.info["create_time"]

    def cpu_percent(self, interval=None):
        return 0

    def memory_info(self):
        return SimpleNamespace(rss=0)


def test_reassociation(test_device, running_process, monkeypatch):
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
        
    fake_process = FakeProcess(proc_info["pid"], [sys.executable, "-c", code])
    monkeypatch.setattr(psutil, "process_iter", lambda _attrs: iter([fake_process]))

    # Run scan
    print("Scanning for tasks...")
    device.scan_running_tasks([task], deep_scan=True)
    
    # Verify
    status = device.get_task_status("test-reassoc-1")
    print(f"Task status: {status}")
    
    assert status.running is True, "Task should be running"
    assert status.pid == proc_info["pid"], "PID should match"
    
    print("Re-association successful!")


def test_scan_running_tasks_can_skip_deep_reassociation(test_device, monkeypatch):
    device_id = test_device["id"]
    device = device_manager.get_device(device_id)

    if not isinstance(device, LocalDevice):
        pytest.skip("Not running on LocalDevice")

    task = Task(
        id="test-skip-deep-reassoc",
        name="Test Skip Deep Reassociation",
        command=f'{sys.executable} -c "import time; time.sleep(10)"',
        created_at=time.time(),
        device_id=device_id,
    )
    device.processes.pop(task.id, None)
    device.saved_pids.pop(task.id, None)

    def fail_process_iter(_attrs):
        raise AssertionError("deep process scan should be skipped")

    monkeypatch.setattr(psutil, "process_iter", fail_process_iter)

    device.scan_running_tasks([task], deep_scan=False)

    status = device.get_task_status(task.id)
    assert status.running is False
