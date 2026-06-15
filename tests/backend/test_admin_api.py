import uuid

from backend.app import app
from backend.core.access.auth import get_current_active_superuser
from backend.core.resources.identity import RESOURCE_TYPE_NOTE, allocate_resource_id
from backend.core.resources.storage import get_attachments_dir
from backend.models import NoteNode, User


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


def test_admin_storage_resource_ids_are_numeric(client, session):
    app.dependency_overrides[get_current_active_superuser] = lambda: User(
        id=1,
        username="admin",
        hashed_password="pw",
        is_active=True,
        is_superuser=True,
    )
    attachments_dir = get_attachments_dir()
    attachments_dir.mkdir(parents=True, exist_ok=True)
    orphan_filename = f"pytest-orphan-{uuid.uuid4().hex}.png"
    orphan_path = attachments_dir / orphan_filename
    orphan_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    note_legacy_id = str(uuid.uuid4())
    note_numeric_id = allocate_resource_id(
        session,
        RESOURCE_TYPE_NOTE,
        note_legacy_id,
        preferred_id=24001,
    )
    session.add(
        NoteNode(
            id=note_legacy_id,
            numeric_id=note_numeric_id,
            user_id=1,
            title="Storage admin note",
            content='<img src="/static/attachments/missing-admin-image.png">',
        )
    )
    session.commit()

    try:
        orphan_resp = client.get("/api/admin/images/orphans")
        analysis_resp = client.get("/api/admin/storage/analysis")
        maintenance_resp = client.get("/api/admin/storage/maintenance")
    finally:
        app.dependency_overrides.pop(get_current_active_superuser, None)
        orphan_path.unlink(missing_ok=True)

    assert orphan_resp.status_code == 200
    orphan_payload = orphan_resp.json()
    orphan_row = next(row for row in orphan_payload["orphans"] if row["filename"] == orphan_filename)
    assert isinstance(orphan_row["device_file_id"], int)

    assert analysis_resp.status_code == 200
    analysis_payload = analysis_resp.json()
    top_file = next(row for row in analysis_payload["top_files"] if row["filename"] == orphan_filename)
    assert isinstance(top_file["device_file_id"], int)
    top_note = next(row for row in analysis_payload["top_nodes"] if row["title"] == "Storage admin note")
    assert top_note["id"] == note_numeric_id

    assert maintenance_resp.status_code == 200
    maintenance_payload = maintenance_resp.json()
    dead_link = next(row for row in maintenance_payload["dead_links"] if row["note_title"] == "Storage admin note")
    assert dead_link["note_id"] == note_numeric_id
