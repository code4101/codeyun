from pathlib import Path

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlmodel import Session, create_engine, select, text

from backend.app import app
from backend.core.access.auth import get_current_active_user, get_current_user_from_token, get_optional_current_user_from_token
from backend.models import PdfBookshelfPlacement, PdfDocument, PdfLibraryBookshelf, PdfPageNote, PdfUserState, User, UserDevice
from backend.migrations.manager import (
    v87_add_pdf_document_metadata,
    v88_add_pdf_bookshelf_placements,
    v89_add_pdf_bookshelf_orientation,
    v90_add_pdf_library_bookshelves,
)
from backend.api import pdf_documents as pdf_documents_api


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


def _write_valid_pdf(path: Path) -> Path:
    writer = PdfWriter()
    writer.add_metadata({"/Author": "测试作者"})
    writer.add_blank_page(width=612, height=792)
    writer.add_blank_page(width=612, height=792)
    writer.add_blank_page(width=595, height=842)
    with path.open("wb") as output:
        writer.write(output)
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


def test_pdf_metadata_migration_adds_cache_column():
    engine = create_engine("sqlite://")
    with Session(engine) as migration_session:
        migration_session.exec(text("CREATE TABLE pdfdocument (id INTEGER PRIMARY KEY)"))
        migration_session.commit()
        v87_add_pdf_document_metadata(migration_session)
        columns = {
            str(row[1])
            for row in migration_session.exec(text("PRAGMA table_info(pdfdocument)")).all()
        }
    assert "metadata_json" in columns


def test_pdf_bookshelf_placement_migration_creates_layout_table():
    engine = create_engine("sqlite://")
    with Session(engine) as migration_session:
        migration_session.exec(text("CREATE TABLE user (id INTEGER PRIMARY KEY)"))
        migration_session.commit()
        v88_add_pdf_bookshelf_placements(migration_session)
        table = migration_session.exec(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pdfbookshelfplacement'"
        )).first()
    assert table is not None


def test_pdf_bookshelf_orientation_migration_adds_default_pose():
    engine = create_engine("sqlite://")
    with Session(engine) as migration_session:
        migration_session.exec(text("CREATE TABLE user (id INTEGER PRIMARY KEY)"))
        migration_session.commit()
        v88_add_pdf_bookshelf_placements(migration_session)
        v89_add_pdf_bookshelf_orientation(migration_session)
        columns = {
            str(row[1]): str(row[4])
            for row in migration_session.exec(text("PRAGMA table_info(pdfbookshelfplacement)")).all()
        }
    assert columns["orientation"] == "'spine_vertical'"


def test_pdf_library_bookshelf_migration_adds_group_and_membership_column():
    engine = create_engine("sqlite://")
    with Session(engine) as migration_session:
        migration_session.exec(text("CREATE TABLE user (id INTEGER PRIMARY KEY)"))
        migration_session.commit()
        v88_add_pdf_bookshelf_placements(migration_session)
        v89_add_pdf_bookshelf_orientation(migration_session)
        v90_add_pdf_library_bookshelves(migration_session)
        table = migration_session.exec(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pdflibrarybookshelf'"
        )).first()
        columns = {
            str(row[1])
            for row in migration_session.exec(text("PRAGMA table_info(pdfbookshelfplacement)")).all()
        }
    assert table is not None
    assert "bookshelf_id" in columns


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


def test_pdf_document_upload_is_hosted_and_idempotent(
    client: TestClient,
    session: Session,
    tmp_path: Path,
    monkeypatch,
):
    owner = _create_user(session, "pdf-upload-owner")
    pdf_bytes = _write_valid_pdf(tmp_path / "dropped.pdf").read_bytes()
    naming_calls: list[list[str]] = []

    def fake_generate(documents):
        naming_calls.append([document.title for document in documents])
        return ({
            int(documents[0].numeric_id): {
                "title": "标准拖入书名",
                "author": "测试作者",
            },
        }, "gpt-5.3-codex-spark")

    monkeypatch.setattr(pdf_documents_api, "_generate_pdf_display_titles", fake_generate)

    _override_user(owner)
    try:
        first_response = client.post(
            "/api/pdf-documents/upload",
            files={"file": ("dropped.pdf", pdf_bytes, "application/pdf")},
        )
        second_response = client.post(
            "/api/pdf-documents/upload",
            files={"file": ("dropped.pdf", pdf_bytes, "application/pdf")},
        )
    finally:
        _clear_user_override()

    assert first_response.status_code == 200, first_response.text
    assert second_response.status_code == 200, second_response.text
    first = first_response.json()
    assert second_response.json()["id"] == first["id"]
    assert first["source_device_id"] == pdf_documents_api.PDF_HOSTED_DEVICE_ID
    assert first["metadata"]["page_count"] == 3
    assert first["display_title_status"] == "pending"
    assert naming_calls == [["dropped.pdf"]]
    document = session.exec(select(PdfDocument).where(PdfDocument.numeric_id == first["id"])).one()
    assert document.metadata_json["title_naming"]["display_title"] == "标准拖入书名"
    assert document.metadata_json["title_naming"]["status"] == "ready"
    assert Path(document.source_absolute_path).is_file()
    assert len(session.exec(select(PdfDocument)).all()) == 1


def test_pdf_document_upload_rejects_fake_pdf(client: TestClient, session: Session):
    owner = _create_user(session, "pdf-upload-invalid-owner")
    _override_user(owner)
    try:
        response = client.post(
            "/api/pdf-documents/upload",
            files={"file": ("fake.pdf", b"not-a-pdf", "application/pdf")},
        )
    finally:
        _clear_user_override()

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "文件内容不是有效的 PDF"
    assert session.exec(select(PdfDocument)).all() == []


def test_pdf_document_metadata_is_scanned_once_and_cached(client: TestClient, session: Session, tmp_path: Path):
    owner = _create_user(session, "pdf-metadata-owner")
    entry = _create_local_entry(session, owner)
    pdf_path = _write_valid_pdf(tmp_path / "geometry.pdf")

    _override_user(owner)
    try:
        created = _create_pdf_document(client, entry, pdf_path)
        document = session.exec(select(PdfDocument).where(PdfDocument.numeric_id == created["id"])).one()
        hosted_path = Path(document.source_absolute_path)
        hosted_path.unlink()
        cached_response = client.get(f"/api/pdf-documents/{created['id']}")
    finally:
        _clear_user_override()

    assert created["metadata"] == {
        "status": "ready",
        "page_count": 3,
        "page_width_points": 612.0,
        "page_height_points": 792.0,
        "cover_average_color": "#ffffff",
        "unit": "pt",
        "scanned_at": created["metadata"]["scanned_at"],
    }
    assert created["display_author"] == "测试作者"
    assert cached_response.status_code == 200, cached_response.text
    assert cached_response.json()["metadata"] == created["metadata"]
    session.refresh(document)
    assert document.metadata_json["source_fingerprint"].endswith(str(document.content_hash))


def test_pdf_page_preview_renders_only_requested_page_and_reuses_cache(
    client: TestClient,
    session: Session,
    tmp_path: Path,
    monkeypatch,
):
    owner = _create_user(session, "pdf-preview-owner")
    entry = _create_local_entry(session, owner)
    pdf_path = _write_valid_pdf(tmp_path / "preview.pdf")
    preview_path = tmp_path / "preview-cache.webp"
    render_calls: list[tuple[int, int]] = []

    def fake_cache_path(_document, page_number, max_width=pdf_documents_api.PDF_PAGE_PREVIEW_MAX_WIDTH):
        assert page_number == 2
        return preview_path

    def fake_render(_pdf_path, page_number, target_path, max_width=pdf_documents_api.PDF_PAGE_PREVIEW_MAX_WIDTH):
        render_calls.append((page_number, max_width))
        target_path.write_bytes(b"single-page-preview")

    monkeypatch.setattr(pdf_documents_api, "_pdf_page_preview_cache_path", fake_cache_path)
    monkeypatch.setattr(pdf_documents_api, "_render_pdf_page_preview", fake_render)

    _override_user(owner)
    try:
        created = _create_pdf_document(client, entry, pdf_path)
        first = client.get(f"/api/pdf-documents/{created['id']}/pages/2/preview")
        second = client.get(f"/api/pdf-documents/{created['id']}/pages/2/preview")
        missing = client.get(f"/api/pdf-documents/{created['id']}/pages/4/preview")
    finally:
        _clear_user_override()

    assert first.status_code == 200, first.text
    assert first.headers["content-type"] == "image/webp"
    assert first.content == b"single-page-preview"
    assert second.status_code == 200, second.text
    assert render_calls == [(2, pdf_documents_api.PDF_PAGE_PREVIEW_MAX_WIDTH)]
    assert missing.status_code == 404


def test_pdf_display_title_is_ai_normalized_and_cached(client: TestClient, session: Session, tmp_path: Path, monkeypatch):
    owner = _create_user(session, "pdf-title-owner")
    entry = _create_local_entry(session, owner)
    pdf_path = _write_valid_pdf(
        tmp_path / "200602惟海 五蕴心理学（下册） (z-library.sk, 1lib.sk) .pdf"
    )
    calls = []

    def fake_generate(documents):
        calls.append([document.title for document in documents])
        return ({
            int(documents[0].numeric_id): {
                "title": "五蕴心理学（下册）",
                "author": "惟海",
            },
        }, "gpt-5.3-codex-spark")

    monkeypatch.setattr(pdf_documents_api, "_generate_pdf_display_titles", fake_generate)
    _override_user(owner)
    try:
        created = _create_pdf_document(client, entry, pdf_path)
        first_list = client.get("/api/pdf-documents")
        second_list = client.get("/api/pdf-documents")
    finally:
        _clear_user_override()

    assert created["title"].endswith(".pdf")
    assert first_list.status_code == 200, first_list.text
    assert first_list.json()[0]["display_title"] == "惟海 五蕴心理学（下册）"
    assert first_list.json()[0]["display_title_status"] == "pending"
    assert second_list.json()[0]["display_title"] == "五蕴心理学（下册）"
    assert second_list.json()[0]["display_author"] == "惟海"
    assert second_list.json()[0]["display_title_status"] == "ready"
    assert len(calls) == 1
    document = session.exec(select(PdfDocument).where(PdfDocument.numeric_id == created["id"])).one()
    assert document.title.endswith(".pdf")
    assert document.metadata_json["title_naming"]["source"] == "ai"


def test_pdf_display_title_falls_back_to_clean_filename_when_ai_fails(
    client: TestClient,
    session: Session,
    tmp_path: Path,
    monkeypatch,
):
    owner = _create_user(session, "pdf-title-fallback-owner")
    entry = _create_local_entry(session, owner)
    pdf_path = _write_valid_pdf(tmp_path / "资本论（全三卷） (z-library.sk) .pdf")

    def fail_generate(_documents):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(pdf_documents_api, "_generate_pdf_display_titles", fail_generate)
    _override_user(owner)
    try:
        created = _create_pdf_document(client, entry, pdf_path)
        response = client.get("/api/pdf-documents")
    finally:
        _clear_user_override()

    assert response.status_code == 200, response.text
    assert response.json()[0]["display_title"] == "资本论（全三卷）"
    document = session.exec(select(PdfDocument).where(PdfDocument.numeric_id == created["id"])).one()
    assert document.metadata_json["title_naming"]["source"] == "fallback"


def test_pdf_display_title_recleans_legacy_cached_batch_number():
    document = PdfDocument(
        title="200602惟海 五蕴心理学（下册）.pdf",
        mime_type="application/pdf",
    )
    document.metadata_json = {
        "title_naming": {
            "schema_version": pdf_documents_api.PDF_TITLE_NAMING_SCHEMA_VERSION,
            "source_fingerprint": pdf_documents_api._pdf_title_source_fingerprint(document),
            "display_title": "200602惟海 五蕴心理学（下册）",
            "status": "ready",
            "source": "ai",
        },
    }

    assert pdf_documents_api._pdf_display_title(document) == "惟海 五蕴心理学（下册）"


def test_pdf_bookshelf_layout_is_saved_per_user(client: TestClient, session: Session, tmp_path: Path):
    owner = _create_user(session, "pdf-layout-owner")
    viewer = _create_user(session, "pdf-layout-viewer")
    entry = _create_local_entry(session, owner)
    first_path = _write_valid_pdf(tmp_path / "layout-first.pdf")
    second_path = _write_valid_pdf(tmp_path / "layout-second.pdf")
    second_path.write_bytes(second_path.read_bytes() + b"\n% distinct layout test file")

    _override_user(owner)
    try:
        first = _create_pdf_document(client, entry, first_path)
        second = _create_pdf_document(client, entry, second_path)
        grant_response = client.put(
            f"/api/pdf-documents/{first['id']}/access",
            json={"grants": [{"subject_type": "user", "subject_user_id": viewer.id, "role": "viewer"}]},
        )
        owner_layout = client.put(
            "/api/pdf-documents/bookshelf-layout",
            json={"placements": [
                {
                    "pdf_id": first["id"],
                    "shelf_index": 2,
                    "position_index": 1,
                    "orientation": "cover_front",
                },
                {"pdf_id": second["id"], "shelf_index": 0, "position_index": 0},
            ]},
        )
        owner_list = client.get("/api/pdf-documents")
    finally:
        _clear_user_override()

    assert grant_response.status_code == 200, grant_response.text
    assert owner_layout.status_code == 200, owner_layout.text
    owner_positions = {item["id"]: item["bookshelf_placement"] for item in owner_list.json()}
    assert owner_positions[first["id"]] == {
        "pdf_id": first["id"],
        "shelf_index": 2,
        "position_index": 1,
        "orientation": "cover_front",
    }
    assert owner_positions[second["id"]] == {
        "pdf_id": second["id"],
        "shelf_index": 0,
        "position_index": 0,
        "orientation": "spine_vertical",
    }

    _override_user(viewer)
    try:
        viewer_list = client.get("/api/pdf-documents")
        viewer_layout = client.put(
            "/api/pdf-documents/bookshelf-layout",
            json={"placements": [{"pdf_id": first["id"], "shelf_index": 1, "position_index": 0}]},
        )
    finally:
        _clear_user_override()

    assert viewer_list.status_code == 200, viewer_list.text
    assert viewer_list.json()[0]["bookshelf_placement"] == {
        "pdf_id": first["id"],
        "shelf_index": 0,
        "position_index": 0,
        "orientation": "spine_vertical",
    }
    assert viewer_layout.status_code == 200, viewer_layout.text
    placements = session.exec(select(PdfBookshelfPlacement)).all()
    assert {(placement.user_id, placement.shelf_index) for placement in placements} == {
        (owner.id, 0),
        (owner.id, 2),
        (viewer.id, 1),
    }


def test_pdf_library_bookshelves_group_and_lazy_load_documents(
    client: TestClient,
    session: Session,
    tmp_path: Path,
):
    owner = _create_user(session, "pdf-library-bookshelf-owner")
    entry = _create_local_entry(session, owner)
    first_path = _write_valid_pdf(tmp_path / "bookshelf-first.pdf")
    second_path = _write_valid_pdf(tmp_path / "bookshelf-second.pdf")
    second_path.write_bytes(second_path.read_bytes() + b"\n% distinct bookshelf file")

    _override_user(owner)
    try:
        first = _create_pdf_document(client, entry, first_path)
        second = _create_pdf_document(client, entry, second_path)
        initial_shelves = client.get("/api/pdf-documents/bookshelves")
        assert initial_shelves.status_code == 200, initial_shelves.text
        initial_payload = initial_shelves.json()
        assert [item["name"] for item in initial_payload] == ["1", "2", "4", "5"]
        assert initial_payload[0]["book_count"] == 2

        created_shelf = client.post(
            "/api/pdf-documents/bookshelves",
            json={"name": "哲学"},
        )
        assert created_shelf.status_code == 200, created_shelf.text
        shelf_id = created_shelf.json()["id"]
        renamed_shelf = client.put(
            f"/api/pdf-documents/bookshelves/{shelf_id}",
            json={"name": "理论"},
        )
        moved = client.put(
            f"/api/pdf-documents/{first['id']}/bookshelf",
            json={"bookshelf_id": shelf_id},
        )
        default_documents = client.get(
            "/api/pdf-documents",
            params={"bookshelf_id": initial_payload[0]["id"]},
        )
        theory_documents = client.get(
            "/api/pdf-documents",
            params={"bookshelf_id": shelf_id},
        )
        final_shelves = client.get("/api/pdf-documents/bookshelves")
    finally:
        _clear_user_override()

    assert renamed_shelf.status_code == 200, renamed_shelf.text
    assert renamed_shelf.json()["name"] == "理论"
    assert moved.status_code == 200, moved.text
    assert [item["id"] for item in default_documents.json()] == [second["id"]]
    assert [item["id"] for item in theory_documents.json()] == [first["id"]]
    counts = {item["name"]: item["book_count"] for item in final_shelves.json()}
    assert counts["1"] == 1
    assert counts["理论"] == 1
    assert len(session.exec(select(PdfLibraryBookshelf)).all()) == 5


def test_pdf_library_bookshelf_delete_requires_empty(
    client: TestClient,
    session: Session,
    tmp_path: Path,
):
    owner = _create_user(session, "pdf-library-bookshelf-delete-owner")
    entry = _create_local_entry(session, owner)
    pdf_path = _write_valid_pdf(tmp_path / "occupied-bookshelf.pdf")

    _override_user(owner)
    try:
        _create_pdf_document(client, entry, pdf_path)
        initial_shelves = client.get("/api/pdf-documents/bookshelves")
        occupied_shelf_id = initial_shelves.json()[0]["id"]
        empty_shelf = client.post(
            "/api/pdf-documents/bookshelves",
            json={"name": "待删除空柜"},
        )
        occupied_delete = client.delete(
            f"/api/pdf-documents/bookshelves/{occupied_shelf_id}"
        )
        empty_delete = client.delete(
            f"/api/pdf-documents/bookshelves/{empty_shelf.json()['id']}"
        )
        final_shelves = client.get("/api/pdf-documents/bookshelves")
    finally:
        _clear_user_override()

    assert occupied_delete.status_code == 409, occupied_delete.text
    assert occupied_delete.json()["detail"] == "只能删除空书柜"
    assert empty_delete.status_code == 204, empty_delete.text
    assert "待删除空柜" not in {item["name"] for item in final_shelves.json()}


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
