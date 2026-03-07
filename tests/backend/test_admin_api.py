from backend.app import app
from backend.core.auth import get_current_active_superuser
from backend.models import User


def test_device_control_identity_hides_device_token(client):
    app.dependency_overrides[get_current_active_superuser] = lambda: User(
        id=1,
        username="admin",
        hashed_password="pw",
        is_active=True,
        is_superuser=True,
    )
    try:
        resp = client.get("/api/admin/device-control/identity")
    finally:
        app.dependency_overrides.pop(get_current_active_superuser, None)

    assert resp.status_code == 200
    payload = resp.json()
    assert "device_id" in payload
    assert "device_token_enabled" in payload
    assert "data_dir" in payload
    assert "device_token" not in payload

