import os
import sys
import time
import subprocess
import psutil
import uuid
import pytest
import backend.core.devices.device as device_core
from types import SimpleNamespace
from backend.core.devices.device import device_manager, LocalDevice
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
            "name": os.path.basename(cmdline[0]),
            "cmdline": cmdline,
            "create_time": time.time(),
        }

    def is_running(self):
        return True

    def status(self):
        return psutil.STATUS_RUNNING

    def create_time(self):
        return self.info["create_time"]

    def cmdline(self):
        return self.info["cmdline"]

    def cpu_percent(self, interval=None):
        return 0

    def memory_info(self):
        return SimpleNamespace(rss=0)


class VolatileInfoProcess:
    def __init__(self, pid, cmdline):
        self.pid = pid
        self._cmdline = cmdline
        self._create_time = time.time()
        self._info_reads = 0

    @property
    def info(self):
        self._info_reads += 1
        if self._info_reads == 1:
            return {
                "pid": self.pid,
                "name": os.path.basename(self._cmdline[0]),
                "cmdline": self._cmdline,
                "create_time": self._create_time,
            }
        return {
            "pid": self.pid,
            "name": os.path.basename(self._cmdline[0]),
            "create_time": self._create_time,
        }

    def is_running(self):
        return True

    def status(self):
        return psutil.STATUS_RUNNING

    def create_time(self):
        return self._create_time

    def cmdline(self):
        return list(self._cmdline)

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
    monkeypatch.setattr(device_core, "process_candidates_by_name", lambda _names: [fake_process])

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

    def fail_process_scan(_names):
        raise AssertionError("deep process scan should be skipped")

    monkeypatch.setattr(device_core, "process_candidates_by_name", fail_process_scan)

    device.scan_running_tasks([task], deep_scan=False)

    status = device.get_task_status(task.id)
    assert status.running is False


def test_scan_running_tasks_snapshots_deep_scan_cmdline(test_device, monkeypatch):
    device_id = test_device["id"]
    device = device_manager.get_device(device_id)

    if not isinstance(device, LocalDevice):
        pytest.skip("Not running on LocalDevice")

    code = "import time; time.sleep(10)"
    task = Task(
        id="test-deep-scan-snapshot",
        name="Test Deep Scan Snapshot",
        command=f'{sys.executable} -c "{code}"',
        created_at=time.time(),
        device_id=device_id,
    )
    device.processes.pop(task.id, None)
    device.saved_pids.pop(task.id, None)

    fake_process = VolatileInfoProcess(12345, [sys.executable, "-c", code])
    monkeypatch.setattr(device_core, "process_candidates_by_name", lambda _names: [fake_process])

    device.scan_running_tasks([task], deep_scan=True)

    status = device.get_task_status(task.id)
    assert status.running is True
    assert status.pid == fake_process.pid
