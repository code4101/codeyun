import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Add backend to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.device import DeviceManager, RemoteDevice, LocalDevice

class TestDeviceSync(unittest.TestCase):
    def setUp(self):
        self.dm = DeviceManager()
        # Mock the devices dict to avoid side effects
        self.dm.devices = {}
        
    def test_rename_remote_device_flow(self):
        """Test that rename_device calls remote API"""
        dev_id = "test-remote-1"
        dev = RemoteDevice(dev_id, "OldName", "http://example.com")
        self.dm.devices[dev_id] = dev
        
        # Mock requests
        with patch('requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            
            # Action
            result = self.dm.rename_device(dev_id, "NewName")
            
            # Verify
            self.assertTrue(result)
            self.assertEqual(dev.name, "NewName")
            mock_post.assert_called_with(
                "http://example.com/api/agent/rename",
                json={"name": "NewName"},
                headers={},
                timeout=5
            )

    def test_push_config_flow(self):
        """Test that update_device triggers push_config"""
        dev_id = "test-remote-2"
        dev = RemoteDevice(dev_id, "Remote2", "http://example.com")
        # Mock push_config to verify it's called
        dev.push_config = MagicMock()
        
        self.dm.devices[dev_id] = dev
        
        # Patch save to db to avoid DB errors
        with patch.object(self.dm, '_save_device_to_db'):
            # Action
            self.dm.update_device(dev_id, python_exec="/usr/bin/python3")
            
            # Verify local update
            self.assertEqual(dev.python_exec, "/usr/bin/python3")
            
            # Verify push_config called (might need sleep if threaded)
            import time
            time.sleep(0.1) 
            dev.push_config.assert_called_once()

    def test_push_config_implementation(self):
        """Test that push_config sends correct payload"""
        dev = RemoteDevice("test-remote-3", "Remote3", "http://example.com", python_exec="/bin/python")
        
        with patch('requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            
            dev.push_config()
            
            mock_post.assert_called_with(
                "http://example.com/api/agent/config",
                json={"python_exec": "/bin/python"},
                headers={},
                timeout=5
            )

if __name__ == '__main__':
    unittest.main()
