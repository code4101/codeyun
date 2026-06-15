from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from backend.app import app
from backend.core.access.auth import get_current_active_user, get_current_user_from_token, get_optional_current_user_from_token
from backend.models import PdfDocument, PdfPageNote, PdfUserState, User, UserDevice


def _create_user(session: Session, username: str, *, superuser: bool = False) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password="pw",
        is_active=True,
        is_superuser=superuser,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _create_local_entry(session: Session, user: User, *, entry_id: str = "pdf-entry") -> UserDevice:
    entry = UserDevice(
        entry_id=entry_id,
        user_id=user.id,
        device_id="pdf-device",
        name="PDF 设备",
        mode="local",
        token="local-token",
        is_active=True,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _write_pdf(path: Path) -> Path:
    path.write_bytes(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n")
    return path


def _override_user(user: User | None) -> None:
    if user is None:
        app.dependency_overrides.pop(get_current_active_user, None)
        app.dependency_overrides.pop(get_current_user_from_token, None)
        app.dependency_overrides[get_optional_current_user_from_token] = lambda: None
        return

    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_current_user_from_token] = lambda: user
    app.dependency_overrides[get_optional_current_user_from_token] = lambda: user


def _clear_user_override() -> None:
    app.dependency_overrides.pop(get_current_active_user, None)
    app.dependency_overrides.pop(get_current_user_from_token, None)
    app.dependency_overrides.pop(get_optional_current_user_from_token, None)


def _create_pdf_document(client: TestClient, entry: UserDevice, path: Path) -> dict:
    response = client.post(
        "/api/pdf-documents/from-device-file",
        json={"entry_id": entry.entry_id, "absolute_path": str(path)},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_pdf_document_from_device_file_is_idempotent(client: TestClient, session: Session, tmp_path: Path):
    owner = _create_user(session, "pdf-owner")
    entry = _create_local_entry(session, owner)
    pdf_path = _write_pdf(tmp_path / "manual.pdf")

    _override_user(owner)
    try:
        first = _create_pdf_document(client, entry, pdf_path)
        second = _create_pdf_document(client, entry, pdf_path)
    finally:
        _clear_user_override()

    assert first["id"] == second["id"]
    documents = session.exec(select(PdfDocument)).all()
    assert len(documents) == 1
    assert documents[0].title == "manual.pdf"


def test_pdf_document_is_private_until_shared(client: TestClient, session: Session, tmp_path: Path):
    owner = _create_user(session, "pdf-private-owner")
    other = _create_user(session, "pdf-private-other")
    entry = _create_local_entry(session, owner)
    pdf_path = _write_pdf(tmp_path / "private.pdf")

    _override_user(owner)
    try:
        document = _create_pdf_document(client, entry, pdf_path)
    finally:
        _clear_user_override()

    _override_user(other)
    try:
        other_response = client.get(f"/api/pdf-documents/{document['id']}")
    finally:
        _clear_user_override()
    assert other_response.status_code == 403

    _override_user(None)
    try:
        anonymous_response = client.get(f"/api/pdf-documents/{document['id']}")
    finally:
        _clear_user_override()
    assert anonymous_response.status_code == 403


def test_pdf_document_public_share_allows_anonymous_content(client: TestClient, session: Session, tmp_path: Path):
    owner = _create_user(session, "pdf-share-owner")
    entry = _create_local_entry(session, owner)
    pdf_path = _write_pdf(tmp_path / "shared.pdf")

    _override_user(owner)
    try:
        document = _create_pdf_document(client, entry, pdf_path)
        access_response = client.put(
            f"/api/pdf-documents/{document['id']}/access",
            json={"grants": [{"subject_type": "anonymous", "role": "viewer"}]},
        )
        assert access_response.status_code == 200, access_response.text
    finally:
        _clear_user_override()

    _override_user(None)
    try:
        detail_response = client.get(f"/api/pdf-documents/{document['id']}")
        content_url_response = client.post(f"/api/pdf-documents/{document['id']}/content-url")
        assert detail_response.status_code == 200, detail_response.text
        assert content_url_response.status_code == 200, content_url_response.text
        content_response = client.get(content_url_response.json()["url"])
    finally:
        _clear_user_override()

    assert detail_response.json()["access"]["role"] == "viewer"
    assert detail_response.json()["access"]["capabilities"]["can_update_state"] is False
    assert content_response.status_code == 200
    assert content_response.content.startswith(b"%PDF")


def test_pdf_user_state_is_per_user(client: TestClient, session: Session, tmp_path: Path):
    owner = _create_user(session, "pdf-state-owner")
    viewer = _create_user(session, "pdf-state-viewer")
    entry = _create_local_entry(session, owner)
    pdf_path = _write_pdf(tmp_path / "state.pdf")

    _override_user(owner)
    try:
        document = _create_pdf_document(client, entry, pdf_path)
        owner_state = client.put(
            f"/api/pdf-documents/{document['id']}/my-state",
            json={"current_page": 7, "zoom": "page-width", "sidebar_open": False, "state_json": {}},
        )
        assert owner_state.status_code == 200, owner_state.text
        grant_response = client.put(
            f"/api/pdf-documents/{document['id']}/access",
            json={"grants": [{"subject_type": "user", "subject_user_id": viewer.id, "role": "viewer"}]},
        )
        assert grant_response.status_code == 200, grant_response.text
    finally:
        _clear_user_override()

    _override_user(viewer)
    try:
        viewer_detail = client.get(f"/api/pdf-documents/{document['id']}")
        viewer_state = client.put(
            f"/api/pdf-documents/{document['id']}/my-state",
            json={"current_page": 3, "zoom": "100", "sidebar_open": True, "state_json": {}},
        )
        assert viewer_detail.status_code == 200, viewer_detail.text
        assert viewer_state.status_code == 200, viewer_state.text
    finally:
        _clear_user_override()

    _override_user(owner)
    try:
        owner_detail = client.get(f"/api/pdf-documents/{document['id']}")
    finally:
        _clear_user_override()

    assert viewer_detail.json()["my_state"] is None
    assert owner_detail.json()["my_state"]["current_page"] == 7
    states = session.exec(select(PdfUserState)).all()
    assert sorted(state.current_page for state in states) == [3, 7]
    assert {state.pdf_document_id for state in states} == {str(document["id"])}


def test_pdf_page_note_is_virtual_until_content_is_saved(client: TestClient, session: Session, tmp_path: Path):
    owner = _create_user(session, "pdf-page-note-owner")
    entry = _create_local_entry(session, owner)
    pdf_path = _write_pdf(tmp_path / "page-note.pdf")

    _override_user(owner)
    try:
        document = _create_pdf_document(client, entry, pdf_path)
        blank_response = client.get(f"/api/pdf-documents/{document['id']}/page-notes/2")
        empty_save_response = client.put(
            f"/api/pdf-documents/{document['id']}/page-notes/2",
            json={"content_html": "<p><br></p>"},
        )
        content_response = client.put(
            f"/api/pdf-documents/{document['id']}/page-notes/2",
            json={"content_html": "<p>第二页笔记</p>"},
        )
        saved_response = client.get(f"/api/pdf-documents/{document['id']}/page-notes/2")
        clear_response = client.put(
            f"/api/pdf-documents/{document['id']}/page-notes/2",
            json={"content_html": ""},
        )
    finally:
        _clear_user_override()

    assert blank_response.status_code == 200, blank_response.text
    assert blank_response.json()["exists"] is False
    assert empty_save_response.status_code == 200, empty_save_response.text
    assert empty_save_response.json()["exists"] is False
    assert content_response.status_code == 200, content_response.text
    assert content_response.json()["exists"] is True
    assert content_response.json()["page_number"] == 2
    assert saved_response.json()["content_html"] == "<p>第二页笔记</p>"
    assert clear_response.status_code == 200, clear_response.text
    assert clear_response.json()["exists"] is False
    assert session.exec(select(PdfPageNote)).all() == []


def test_pdf_page_note_is_per_user_private_overlay(client: TestClient, session: Session, tmp_path: Path):
    owner = _create_user(session, "pdf-page-note-private-owner")
    viewer = _create_user(session, "pdf-page-note-private-viewer")
    entry = _create_local_entry(session, owner)
    pdf_path = _write_pdf(tmp_path / "page-note-private.pdf")

    _override_user(owner)
    try:
        document = _create_pdf_document(client, entry, pdf_path)
        owner_note = client.put(
            f"/api/pdf-documents/{document['id']}/page-notes/5",
            json={"content_html": "<p>owner note</p>"},
        )
        grant_response = client.put(
            f"/api/pdf-documents/{document['id']}/access",
            json={"grants": [{"subject_type": "user", "subject_user_id": viewer.id, "role": "viewer"}]},
        )
        assert owner_note.status_code == 200, owner_note.text
        assert grant_response.status_code == 200, grant_response.text
    finally:
        _clear_user_override()

    _override_user(viewer)
    try:
        viewer_blank = client.get(f"/api/pdf-documents/{document['id']}/page-notes/5")
        viewer_note = client.put(
            f"/api/pdf-documents/{document['id']}/page-notes/5",
            json={"content_html": "<p>viewer note</p>"},
        )
    finally:
        _clear_user_override()

    _override_user(owner)
    try:
        owner_saved = client.get(f"/api/pdf-documents/{document['id']}/page-notes/5")
    finally:
        _clear_user_override()

    assert viewer_blank.status_code == 200, viewer_blank.text
    assert viewer_blank.json()["exists"] is False
    assert viewer_blank.json()["can_edit"] is True
    assert viewer_note.status_code == 200, viewer_note.text
    assert owner_saved.json()["content_html"] == "<p>owner note</p>"
    notes = session.exec(select(PdfPageNote)).all()
    assert sorted(note.content_html for note in notes) == ["<p>owner note</p>", "<p>viewer note</p>"]
    assert {note.pdf_document_id for note in notes} == {str(document["id"])}


def test_pdf_page_note_requires_login(client: TestClient, session: Session, tmp_path: Path):
    owner = _create_user(session, "pdf-page-note-login-owner")
    entry = _create_local_entry(session, owner)
    pdf_path = _write_pdf(tmp_path / "page-note-login.pdf")

    _override_user(owner)
    try:
        document = _create_pdf_document(client, entry, pdf_path)
        access_response = client.put(
            f"/api/pdf-documents/{document['id']}/access",
            json={"grants": [{"subject_type": "anonymous", "role": "viewer"}]},
        )
        assert access_response.status_code == 200, access_response.text
    finally:
        _clear_user_override()

    _override_user(None)
    try:
        response = client.get(f"/api/pdf-documents/{document['id']}/page-notes/1")
    finally:
        _clear_user_override()

    assert response.status_code in {401, 403}


def test_pdf_document_rejects_non_pdf_file(client: TestClient, session: Session, tmp_path: Path):
    owner = _create_user(session, "pdf-reject-owner")
    entry = _create_local_entry(session, owner)
    text_path = tmp_path / "notes.txt"
    text_path.write_text("not a pdf", encoding="utf-8")

    _override_user(owner)
    try:
        response = client.post(
            "/api/pdf-documents/from-device-file",
            json={"entry_id": entry.entry_id, "absolute_path": str(text_path)},
        )
    finally:
        _clear_user_override()

    assert response.status_code == 400
