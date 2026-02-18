import sys
import os
import json

# Add backend directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.device import DeviceManager

def test_device_manager():
    dm = DeviceManager()
    print("Devices loaded:", dm.get_all_devices())
    
    # Check if local exists
    local = dm.get_device('local')
    if local:
        print("Local device found")
    else:
        print("Local device NOT found")

if __name__ == "__main__":
    test_device_manager()
