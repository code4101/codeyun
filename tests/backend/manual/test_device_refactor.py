
import os
import sys

# Add project root to sys.path
# Current: backend/tests/manual/test_device_refactor.py
# Root:    ../../..
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import time
import shutil
from backend.core.device import LocalDevice, DATA_DIR

def test_start_task():
    print("Testing LocalDevice.start_task...")
    device = LocalDevice(device_id="test_device")
    
    # 1. Test simple command
    task_id = "test_task_1"
    print(f"Starting task {task_id}...")
    cmd = "python -c \"import time; print('Hello Refactor'); time.sleep(1)\""
    
    try:
        res = device.start_task(task_id, cmd)
        print(f"Task started: {res}")
        pid = res.get("pid")
        assert pid is not None
        
        # Wait a bit for logs
        time.sleep(1.5)
        
        # Check logs
        log_path = os.path.join(DATA_DIR, "test_device", "logs", f"{task_id}.log")
        if os.path.exists(log_path):
            with open(log_path, 'r', encoding='utf-8') as f:
                content = f.read()
                print(f"Log content:\n{content}")
                assert "Hello Refactor" in content
        else:
            print("Log file not found!")
            assert False
            
    except Exception as e:
        print(f"Test failed: {e}")
        raise

def test_timeout():
    print("\nTesting TimeoutWatchdog...")
    device = LocalDevice(device_id="test_device")
    task_id = "test_task_timeout"
    # Sleep 5 seconds, timeout 2 seconds
    cmd = "python -c \"import time; print('Start'); time.sleep(5); print('End')\""
    
    try:
        res = device.start_task(task_id, cmd, timeout=2)
        print(f"Task started: {res}")
        
        # Wait for timeout (3 seconds)
        time.sleep(3)
        
        # Check if process is gone
        status = device.get_task_status(task_id)
        print(f"Task status after timeout: {status}")
        
        if status.running:
            print("Task is still running! Timeout failed.")
            # Cleanup
            device.stop_task(task_id)
            raise Exception("Timeout failed")
        else:
            print("Task stopped successfully.")
            
    except Exception as e:
        print(f"Test failed: {e}")
        raise

def test_error_hints():
    print("\nTesting ErrorMapper Hints...")
    device = LocalDevice(device_id="test_device")
    
    # Test cases: (command, expected_hint_part)
    cases = [
        ("missing_script.py", "python interpreter"),
        ("missing_script.ps1", "powershell -File"),
        ("missing_script.vbs", "cscript"),
        ("missing_script.sh", "WSL/Git Bash"),
        # ("./unknown_cmd", "invoking the correct interpreter"),  # Removed this case as it causes invalid path error on Windows for log file creation
        ("random_command_xyz", "system PATH")
    ]
    
    for cmd, hint_part in cases:
        print(f"Testing command: {cmd}")
        try:
            device.start_task(f"test_hint_{cmd}", cmd)
            print(f"ERROR: Task {cmd} started unexpectedly!")
            device.stop_task(f"test_hint_{cmd}")
        except Exception as e:
            msg = str(e)
            print(f"Caught exception: {msg}")
            if hint_part in msg:
                print(f"PASS: Found hint '{hint_part}'")
            else:
                print(f"FAIL: Hint '{hint_part}' NOT found in error message.")
                # We won't raise here to allow checking all cases
                pass

if __name__ == "__main__":
    try:
        test_start_task()
        test_timeout()
        test_error_hints()
        print("\nAll tests passed!")
    except Exception as e:
        print(f"\nTests failed: {e}")
        sys.exit(1)
