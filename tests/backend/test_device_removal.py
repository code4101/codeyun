from backend.models import UserDevice

def test_remove_device(client, session, auth_user):
    device_id = "remote-dev-1"
    
    link = UserDevice(
        user_id=auth_user.id,
        device_id=device_id,
        mode="remote",
        name="My Remote",
        server_url="http://1.2.3.4:8000",
        token="abc",
    )
    session.add(link)
    session.commit()
    
    # 1. Verify it exists via API
    resp = client.get("/api/devices/")
    assert resp.status_code == 200
    devices = resp.json()
    assert len(devices) == 1
    assert devices[0]["device_id"] == device_id
    entry_id = devices[0]["id"]
    
    # 2. Remove device
    resp = client.delete(f"/api/devices/{entry_id}")
    assert resp.status_code == 200
    
    # 3. Verify it is gone via API
    resp = client.get("/api/devices/")
    assert resp.status_code == 200
    devices = resp.json()
    assert len(devices) == 0
    
    link = session.get(UserDevice, entry_id)
    assert link is None
