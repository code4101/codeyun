from backend.models import Device, UserDevice
from sqlmodel import Session

def test_remove_device(client, session, auth_user):
    # 1. Create a device
    device_id = "remote-dev-1"
    
    dev = Device(id=device_id, name="Remote 1", type="RemoteDevice", url="http://1.2.3.4:8000")
    session.add(dev)
    
    # 2. Link to user
    link = UserDevice(user_id=auth_user.id, device_id=device_id, alias="My Remote", token="abc")
    session.add(link)
    session.commit()
    
    # 3. Verify it exists via API
    resp = client.get("/api/devices/")
    assert resp.status_code == 200
    devices = resp.json()
    assert len(devices) == 1
    assert devices[0]["device_id"] == device_id
    
    # 4. Remove device
    resp = client.delete(f"/api/devices/{device_id}")
    assert resp.status_code == 200
    
    # 5. Verify it is gone via API
    resp = client.get("/api/devices/")
    assert resp.status_code == 200
    devices = resp.json()
    assert len(devices) == 0
    
    # 6. Verify Device record still exists (it should not be deleted, only the link)
    dev = session.get(Device, device_id)
    assert dev is not None
    
    link = session.get(UserDevice, (auth_user.id, device_id))
    assert link is None
