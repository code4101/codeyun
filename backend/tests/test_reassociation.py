
import unittest
import os
import sys
import time
import subprocess
import psutil
import socket

# Add backend directory to path
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.device import device_manager, LocalDevice
from api.task_manager import TaskConfig

import uuid

class TestReassociation(unittest.TestCase):
    def setUp(self):
        # Ensure we are using LocalDevice
        try:
            local_id = device_manager.get_device(socket.gethostname()).device_id
            self.device = device_manager.get_device(local_id)
            if not isinstance(self.device, LocalDevice):
                self.skipTest("Not running on LocalDevice")
        except:
             self.skipTest("Device manager setup failed")
            
        self.test_pids = []

    def tearDown(self):
        # Kill any processes we started
        for pid in self.test_pids:
            try:
                p = psutil.Process(pid)
                p.terminate()
            except:
                pass

    def test_reassociation(self):
        # 1. Start a process manually with a unique marker
        unique_id = str(uuid.uuid4())
        # Use a comment in python code to make cmdline unique
        code = f"import time; time.sleep(10); # {unique_id}"
        
        cmd = [sys.executable, "-c", code]
        proc = subprocess.Popen(cmd)
        self.test_pids.append(proc.pid)
        
        print(f"Started manual process with PID {proc.pid} and ID {unique_id}")
        
        # 2. Create a task config matches
        # We need to construct the command string exactly as it appears
        # On Windows, subprocess.Popen list args are joined with spaces (and quotes if needed).
        # But here we are passing list to Popen.
        
        # We need to construct a task command string that match_cmdline will accept.
        # Since we stripped quotes in match_cmdline, we can use simple quotes.
        
        # If we use f'{sys.executable} -c "{code}"'
        # The code contains spaces and semicolons, so it needs quotes.
        
        task_cmd = f'{sys.executable} -c "{code}"'
        
        task = TaskConfig(
            id="test-reassoc-1",
            name="Test Reassociation",
            command=task_cmd,
            created_at=time.time(),
            device_id=self.device.device_id
        )
        
        # 3. Ensure the device doesn't know about this task yet
        if "test-reassoc-1" in self.device.processes:
            del self.device.processes["test-reassoc-1"]
        if "test-reassoc-1" in self.device.saved_pids:
            del self.device.saved_pids["test-reassoc-1"]
            
        # 4. Run scan
        print("Scanning for tasks...")
        self.device.scan_running_tasks([task])
        
        # 5. Verify
        status = self.device.get_task_status("test-reassoc-1")
        print(f"Task status: {status}")
        
        self.assertTrue(status.running, "Task should be running")
        self.assertEqual(status.pid, proc.pid, "PID should match")
        
        print("Re-association successful!")

if __name__ == '__main__':
    unittest.main()
