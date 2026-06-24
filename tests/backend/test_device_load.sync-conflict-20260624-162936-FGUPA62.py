from backend.core.device import device_manager

def test_device_manager_load(test_device):
    # test_device fixture ensures device_manager is loaded with mock DB
    
    devices = device_manager.get_all_devices()
    print("Devices loaded:", devices)
    
    assert len(devices) > 0
    
    # Check if local exists
    # The test_device fixture creates a device with id="test-device-local"
    local = device_manager.get_device('test-device-local')
    assert local is not None
    assert local.device_id == 'test-device-local'
