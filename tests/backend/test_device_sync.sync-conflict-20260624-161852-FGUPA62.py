import unittest
from unittest.mock import patch

from backend.core.device import DeviceManager, LocalDevice


class TestDeviceManagerLocal(unittest.TestCase):
    def setUp(self):
        self.dm = DeviceManager()
        self.local_id = "test-local-1"
        self.local = LocalDevice(
            device_id=self.local_id,
            name="Old Name",
            python_exec="python",
            api_token="test-token",
        )
        self.dm.devices = {self.local_id: self.local}
        
    def test_rename_local_device_is_rejected(self):
        with patch.object(self.dm, "_save_device_to_db") as mock_save:
            result = self.dm.rename_device(self.local_id, "New Name")

        self.assertFalse(result)
        self.assertEqual(self.local.name, "Old Name")
        mock_save.assert_not_called()

    def test_update_local_device_is_rejected(self):
        with patch.object(self.dm, "_save_device_to_db") as mock_save:
            result = self.dm.update_device(self.local_id, python_exec="/usr/bin/python3")

        self.assertFalse(result)
        self.assertEqual(self.local.python_exec, "python")
        mock_save.assert_not_called()

    def test_unknown_device_update_is_rejected(self):
        self.assertFalse(self.dm.rename_device("missing-device", "Nope"))
        self.assertFalse(self.dm.update_device("missing-device", python_exec="python3"))

    def test_load_uses_env_device_token(self):
        with (
            patch("backend.core.device.get_device_id", return_value=self.local_id),
            patch("socket.gethostname", return_value="Host Name"),
            patch("backend.core.device.get_device_token", return_value="env-token"),
        ):
            self.dm.load()

        self.assertEqual(self.dm.devices[self.local_id].api_token, "env-token")
        self.assertEqual(self.dm.devices[self.local_id].name, "Host Name")

if __name__ == '__main__':
    unittest.main()
