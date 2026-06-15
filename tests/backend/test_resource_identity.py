import json
import re

from sqlmodel import Session, select, text

from backend.core.resources import attachments as attachment_resources
from backend.api import note_docs as note_docs_api
from backend.core.access.feature_access import FEATURE_ACCESS_SUBJECT_USER, save_feature_access_policy_overrides
from backend.core.fanxiu.catalog.inventory import get_inventory_storage_path
from backend.core.notes.refs import load_notes_by_refs
from backend.core.resources.sheet_refs import load_sheets_by_refs, load_workbooks_by_refs
from backend.api.notes import _get_accessible_note
from backend.core.resources.identity import (
    RESOURCE_TYPE_DEVICE_FILE,
    RESOURCE_TYPE_NOTE,
    RESOURCE_TYPE_PDF,
    RESOURCE_TYPE_SHEET,
    allocate_resource_id,
    ensure_resource_identity,
)
from backend.migrations.manager import (
    v45_add_global_resource_identity,
    v49_migrate_graph_and_workbook_links_to_public_ids,
    v51_repack_resource_ids_by_priority,
    v52_restore_workbook_route_ids,
    v53_add_device_file_resource_identities,
    v55_migrate_documentasset_primary_key_to_numeric,
    v56_migrate_pdfdocument_primary_key_to_numeric,
    v57_add_legacy_id_shadow_columns,
    v58_migrate_fanxiu_inventory_note_refs,
    v59_migrate_notenode_primary_key_to_numeric,
    v60_migrate_sheet_workbook_primary_keys_to_numeric,
)
from backend.models import (
    CodexDiaryImportRun,
    DeviceFile,
    DocumentAsset,
    DocumentQueryHistory,
    DocumentReductionRun,
    NoteEdge,
    NoteNode,
    PdfDocument,
    PdfPageNote,
    PdfUserState,
    ResourceAccessGrant,
    ResourceIdentity,
    SheetDocument,
    User,
    WorkbookDocument,
    WorkbookSheetLink,
)
from scripts.check_resource_identity import (
    _codex_note_json_ref_report,
    _dangling_grant_report,
    _dangling_ref_count,
    _fanxiu_inventory_note_ref_report,
    _resource_table_report,
)


UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _assert_numeric_resource_id(value):
    assert isinstance(value, int)
    assert value > 0
    assert not UUID_RE.fullmatch(str(value))


def test_global_resource_identity_migration_preserves_priority(session: Session, auth_user: User):
    session.add(SheetDocument(id="sheet-a", numeric_id=1, scope="notes", owner_user_id=auth_user.id, title="Sheet"))
    session.add(WorkbookDocument(id="workbook-a", numeric_id=1, owner_user_id=auth_user.id, title="Workbook"))
    session.add(PdfDocument(id=101, legacy_id="pdf-a", numeric_id=1, owner_user_id=auth_user.id, title="PDF"))
    session.add(DocumentAsset(id=102, legacy_id="asset-a", user_id=auth_user.id, title="Asset", original_filename="a.txt", sha256="abc"))
    session.add(NoteNode(id="note-a", numeric_id=1, user_id=auth_user.id, title="Note"))
    session.commit()

    v45_add_global_resource_identity(session)

    sheet = session.get(SheetDocument, "sheet-a")
    workbook = session.get(WorkbookDocument, "workbook-a")
    pdf = session.get(PdfDocument, 101)
    asset = session.get(DocumentAsset, 102)
    note = session.get(NoteNode, "note-a")

    assert sheet.numeric_id == 1
    assert workbook.numeric_id == 1
    assert pdf.numeric_id == 3
    assert asset.numeric_id == 4
    assert note.numeric_id == 5

    workbook_identity = session.exec(
        select(ResourceIdentity)
        .where(ResourceIdentity.resource_type == "workbook")
        .where(ResourceIdentity.legacy_pk == "workbook-a")
    ).first()
    assert workbook_identity is not None
    assert workbook_identity.id == 2

    ids = [sheet.numeric_id, pdf.numeric_id, asset.numeric_id, note.numeric_id]
    assert len(ids) == len(set(ids))
    assert session.exec(select(ResourceIdentity)).all()


def test_note_api_uses_public_numeric_ids(client, session: Session, auth_user: User):
    first_response = client.post("/api/notes/", json={"title": "A"})
    second_response = client.post("/api/notes/", json={"title": "B"})
    assert first_response.status_code == 200
    assert second_response.status_code == 200
    first_note = first_response.json()
    second_note = second_response.json()

    assert isinstance(first_note["id"], int)
    assert isinstance(second_note["id"], int)
    assert str(first_note["id"]).isdigit()
    assert str(second_note["id"]).isdigit()
    assert "-" not in str(first_note["id"])

    edge_response = client.post(
        "/api/notes/edges/",
        json={"source_id": first_note["id"], "target_id": second_note["id"]},
    )
    assert edge_response.status_code == 200
    edge = edge_response.json()
    assert isinstance(edge["source_id"], int)
    assert isinstance(edge["target_id"], int)
    assert edge["source_id"] == first_note["id"]
    assert edge["target_id"] == second_note["id"]

    program_response = client.post(
        "/api/notes/query-program",
        json={
            "executor": {"kind": "scan"},
            "program": {
                "select": {
                    "default": False,
                    "rules": [{"action": "include", "matcher": {"kind": "id", "ids": [first_note["id"]]}}],
                },
                "expand": {"default": False, "rules": []},
            },
            "result": {"include_edges": False, "limit": 10},
        },
    )
    assert program_response.status_code == 200
    assert [note["id"] for note in program_response.json()["nodes"]] == [first_note["id"]]

    batch_response = client.post(
        "/api/notes/batch-update",
        json={"ids": [first_note["id"]], "patch": {"weight_delta": 1}},
    )
    assert batch_response.status_code == 200
    assert batch_response.json()["updated_count"] == 1

    resolved = _get_accessible_note(first_note["id"], auth_user, session)
    assert resolved is not None
    assert resolved.id == str(first_note["id"])
    assert resolved.numeric_id == first_note["id"]
    assert resolved.legacy_id
    assert resolved.legacy_id != resolved.id

    legacy_ref = resolved.legacy_id

    legacy_read_response = client.get(f"/api/notes/{legacy_ref}")
    assert legacy_read_response.status_code == 404

    legacy_edge_response = client.post(
        "/api/notes/edges/",
        json={"source_id": legacy_ref, "target_id": second_note["id"]},
    )
    assert legacy_edge_response.status_code == 404

    legacy_batch_response = client.post(
        "/api/notes/batch-update",
        json={"ids": [legacy_ref], "patch": {"weight_delta": 1}},
    )
    assert legacy_batch_response.status_code == 400

    legacy_program_response = client.post(
        "/api/notes/query-program",
        json={
            "executor": {"kind": "scan"},
            "program": {
                "select": {
                    "default": False,
                    "rules": [{"action": "include", "matcher": {"kind": "id", "ids": [legacy_ref]}}],
                },
                "expand": {"default": False, "rules": []},
            },
            "result": {"include_edges": False, "limit": 10},
        },
    )
    assert legacy_program_response.status_code == 404


def test_note_doc_access_response_uses_numeric_resource_id(client, session: Session, auth_user: User):
    session.add(
        NoteNode(
            id="note-doc-a",
            numeric_id=10,
            user_id=auth_user.id,
            title="Doc",
            note_form="document",
        )
    )
    session.commit()

    response = client.get("/api/note-docs/10/access")

    assert response.status_code == 200
    payload = response.json()
    assert payload["resource_type"] == "note"
    assert payload["resource_id"] == 10
    assert payload["public_id"] == 10
    assert isinstance(payload["resource_id"], int)

    legacy_response = client.get("/api/note-docs/note-doc-a/access")
    assert legacy_response.status_code == 404


def test_note_doc_update_rejects_stale_base_version(client, session: Session, auth_user: User, monkeypatch):
    session.add(
        NoteNode(
            id="note-doc-version-a",
            numeric_id=11,
            user_id=auth_user.id,
            title="Doc Version",
            content="<p>old</p>",
            note_form="document",
            version=1,
        )
    )
    session.commit()
    broadcasts: list[tuple[str, dict]] = []

    async def fake_broadcast(room: str, message: dict) -> None:
        broadcasts.append((room, message))

    monkeypatch.setattr(note_docs_api.ws_manager, "broadcast", fake_broadcast)

    response = client.put(
        "/api/note-docs/11",
        json={"base_version": 1, "content": "<p>new</p>"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["version"] == 2
    assert payload["content"] == "<p>new</p>"
    assert broadcasts[-1][0] == "resource:note:11"
    assert broadcasts[-1][1]["type"] == "resource-updated"
    assert broadcasts[-1][1]["resource_type"] == "note"
    assert broadcasts[-1][1]["resource_id"] == "11"
    assert broadcasts[-1][1]["version"] == 2

    stale_response = client.put(
        "/api/note-docs/11",
        json={"base_version": 1, "content": "<p>stale</p>"},
    )
    assert stale_response.status_code == 409
    note = session.get(NoteNode, "note-doc-version-a")
    assert note is not None
    assert note.content == "<p>new</p>"
    assert note.version == 2


def test_note_doc_resource_websocket_receives_update_event(client, session: Session, auth_user: User):
    session.add(
        NoteNode(
            id="note-doc-ws-a",
            numeric_id=12,
            user_id=auth_user.id,
            title="Doc WS",
            content="<p>old</p>",
            note_form="document",
            version=1,
        )
    )
    session.commit()

    with client.websocket_connect("/api/note-docs/ws/resources/note/12") as websocket:
        response = client.put(
            "/api/note-docs/12",
            json={"base_version": 1, "content": "<p>new</p>"},
        )
        assert response.status_code == 200, response.text
        message = websocket.receive_json()

    assert message["type"] == "resource-updated"
    assert message["resource_type"] == "note"
    assert message["resource_id"] == "12"
    assert message["version"] == 2


def test_note_doc_resource_websocket_broadcasts_to_multiple_clients(client, session: Session, auth_user: User):
    session.add(
        NoteNode(
            id="note-doc-ws-multi",
            numeric_id=13,
            user_id=auth_user.id,
            title="Doc WS Multi",
            content="<p>old</p>",
            note_form="document",
            version=1,
        )
    )
    session.commit()

    with (
        client.websocket_connect("/api/note-docs/ws/resources/note/13") as first,
        client.websocket_connect("/api/note-docs/ws/resources/note/13") as second,
    ):
        response = client.put(
            "/api/note-docs/13",
            json={"base_version": 1, "content": "<p>new</p>"},
        )
        assert response.status_code == 200, response.text
        first_message = first.receive_json()
        second_message = second.receive_json()

    for message in (first_message, second_message):
        assert message["type"] == "resource-updated"
        assert message["resource_type"] == "note"
        assert message["resource_id"] == "13"
        assert message["version"] == 2


def test_note_sheet_resource_websocket_receives_patch_event(client, session: Session, auth_user: User):
    session.add(
        SheetDocument(
            numeric_id=72,
            scope="notes",
            owner_type="user",
            owner_key=str(auth_user.id),
            sheet_key="ws-patch",
            title="Sheet WS",
            owner_user_id=auth_user.id,
            created_by_user_id=auth_user.id,
            updated_by_user_id=auth_user.id,
            version=1,
            document_json={
                "schema_version": 1,
                "columns": ["状态"],
                "rows": [["待处理"]],
            },
        )
    )
    session.commit()

    with client.websocket_connect("/api/note-sheets/ws/resources/sheet/72") as websocket:
        response = client.post(
            "/api/note-sheets/sheets/72/patch",
            json={
                "base_version": 1,
                "ops": [
                    {"op": "set-cell-value", "row_index": 0, "column_index": 0, "value": "已处理"},
                ],
            },
        )
        assert response.status_code == 200, response.text
        message = websocket.receive_json()

    assert message["type"] == "resource-updated"
    assert message["resource_type"] == "sheet"
    assert message["resource_id"] == "72"
    assert message["version"] == 2


def test_note_sheet_resource_websocket_broadcasts_to_multiple_clients(client, session: Session, auth_user: User):
    session.add(
        SheetDocument(
            numeric_id=73,
            scope="notes",
            owner_type="user",
            owner_key=str(auth_user.id),
            sheet_key="ws-multi-client",
            title="Sheet WS Multi",
            owner_user_id=auth_user.id,
            created_by_user_id=auth_user.id,
            updated_by_user_id=auth_user.id,
            version=1,
            document_json={
                "schema_version": 1,
                "columns": ["状态"],
                "rows": [["待处理"]],
            },
        )
    )
    session.commit()

    with (
        client.websocket_connect("/api/note-sheets/ws/resources/sheet/73") as first,
        client.websocket_connect("/api/note-sheets/ws/resources/sheet/73") as second,
    ):
        response = client.post(
            "/api/note-sheets/sheets/73/patch",
            json={
                "base_version": 1,
                "ops": [
                    {"op": "set-cell-value", "row_index": 0, "column_index": 0, "value": "已处理"},
                ],
            },
        )
        assert response.status_code == 200, response.text
        first_message = first.receive_json()
        second_message = second.receive_json()

    for message in (first_message, second_message):
        assert message["type"] == "resource-updated"
        assert message["resource_type"] == "sheet"
        assert message["resource_id"] == "73"
        assert message["version"] == 2


def test_public_resource_api_responses_use_numeric_ids(client, session: Session, auth_user: User, monkeypatch, tmp_path):
    save_feature_access_policy_overrides(
        session,
        subject_type=FEATURE_ACCESS_SUBJECT_USER,
        subject_user_id=auth_user.id,
        overrides={"notes.sheets": "allow", "tools.ai-reduction": "allow"},
    )

    note_response = client.post("/api/notes/", json={"title": "Public ID contract"})
    assert note_response.status_code == 200
    note_payload = note_response.json()
    _assert_numeric_resource_id(note_payload["id"])

    note_access_response = client.get(f"/api/note-docs/{note_payload['id']}/access")
    assert note_access_response.status_code == 200
    note_access_payload = note_access_response.json()
    _assert_numeric_resource_id(note_access_payload["resource_id"])
    _assert_numeric_resource_id(note_access_payload["public_id"])

    workbook_response = client.post("/api/note-sheets/workbooks", json={"title": "Public ID workbook"})
    assert workbook_response.status_code == 200, workbook_response.text
    workbook_payload = workbook_response.json()
    _assert_numeric_resource_id(workbook_payload["id"])

    sheet_response = client.post(
        "/api/note-sheets/sheets",
        json={
            "title": "Public ID sheet",
            "workbook_id": workbook_payload["id"],
            "document_json": {"schema_version": 1, "columns": ["A"], "rows": [["x"]]},
        },
    )
    assert sheet_response.status_code == 200, sheet_response.text
    sheet_payload = sheet_response.json()
    _assert_numeric_resource_id(sheet_payload["id"])
    _assert_numeric_resource_id(sheet_payload["workbook_items"][0]["id"])

    sheet_access_response = client.get(f"/api/note-sheets/sheets/{sheet_payload['id']}/access")
    assert sheet_access_response.status_code == 200
    _assert_numeric_resource_id(sheet_access_response.json()["resource_id"])

    workbook_access_response = client.get(f"/api/note-sheets/workbooks/{workbook_payload['id']}/access")
    assert workbook_access_response.status_code == 200
    _assert_numeric_resource_id(workbook_access_response.json()["resource_id"])

    pdf_legacy_id = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
    pdf_numeric_id = allocate_resource_id(session, RESOURCE_TYPE_PDF, pdf_legacy_id)
    session.add(
        PdfDocument(
            id=pdf_numeric_id,
            numeric_id=pdf_numeric_id,
            legacy_id=pdf_legacy_id,
            title="contract.pdf",
            owner_user_id=auth_user.id,
            created_by_user_id=auth_user.id,
            updated_by_user_id=auth_user.id,
        )
    )
    session.commit()

    pdf_response = client.get(f"/api/pdf-documents/{pdf_numeric_id}")
    assert pdf_response.status_code == 200, pdf_response.text
    _assert_numeric_resource_id(pdf_response.json()["id"])
    assert pdf_response.json()["id"] == pdf_numeric_id

    pdf_access_response = client.get(f"/api/pdf-documents/{pdf_numeric_id}/access")
    assert pdf_access_response.status_code == 200
    _assert_numeric_resource_id(pdf_access_response.json()["resource_id"])

    reduction_response = client.post(
        "/api/reduction-documents/upload",
        files={"file": ("contract.txt", b"resource id contract", "text/plain")},
    )
    assert reduction_response.status_code == 200, reduction_response.text
    reduction_payload = reduction_response.json()
    _assert_numeric_resource_id(reduction_payload["id"])
    assert reduction_payload["numeric_id"] == reduction_payload["id"]

    reduction_list_response = client.get("/api/reduction-documents")
    assert reduction_list_response.status_code == 200
    _assert_numeric_resource_id(reduction_list_response.json()["items"][0]["id"])

    monkeypatch.setattr("backend.api.upload.get_attachments_dir", lambda: tmp_path)
    monkeypatch.setattr(attachment_resources, "resolve_attachment_device_id", lambda: "test-device")
    upload_response = client.post(
        "/api/upload/file",
        files={"file": ("contract.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert upload_response.status_code == 200, upload_response.text
    _assert_numeric_resource_id(upload_response.json()["data"]["id"])


def test_allocate_resource_id_uses_global_sequence(session: Session, auth_user: User):
    session.add(SheetDocument(id="sheet-a", numeric_id=100, scope="notes", owner_user_id=auth_user.id, title="Sheet"))
    session.commit()

    note_id = allocate_resource_id(session, RESOURCE_TYPE_NOTE, "note-a")

    assert note_id == 101


def test_allocate_resource_id_rejects_preferred_id_already_used_by_resource_table(session: Session, auth_user: User):
    session.add(SheetDocument(id="sheet-a", numeric_id=7, scope="notes", owner_user_id=auth_user.id, title="Sheet"))
    session.commit()

    note_id = allocate_resource_id(session, RESOURCE_TYPE_NOTE, "note-a", preferred_id=7)

    assert note_id == 8


def test_device_file_identity_uses_global_resource_namespace(session: Session, auth_user: User):
    session.add(SheetDocument(id="sheet-a", numeric_id=1, scope="notes", owner_user_id=auth_user.id, title="Sheet"))
    session.add(ResourceIdentity(id=1, resource_type="sheet", legacy_pk="sheet-a"))
    session.add(
        DeviceFile(
            id=1,
            device_id="device-1",
            absolute_path=r"C:\images\a.png",
            last_known_path=r"C:\images\a.png",
            media_kind="image",
        )
    )
    session.add(
        DeviceFile(
            id=50,
            device_id="device-1",
            absolute_path=r"C:\images\b.png",
            last_known_path=r"C:\images\b.png",
            media_kind="image",
        )
    )
    session.commit()

    v53_add_device_file_resource_identities(session)
    session.expire_all()

    conflicting_file = session.get(DeviceFile, 1)
    free_file = session.get(DeviceFile, 50)

    assert conflicting_file.numeric_id == 2
    assert free_file.numeric_id == 50
    assert session.get(ResourceIdentity, 2).resource_type == RESOURCE_TYPE_DEVICE_FILE
    assert session.get(ResourceIdentity, 2).legacy_pk == "1"
    assert session.get(ResourceIdentity, 50).resource_type == RESOURCE_TYPE_DEVICE_FILE
    assert session.get(ResourceIdentity, 50).legacy_pk == "50"


def test_ensure_resource_identity_preserves_existing_resource_numeric_id(session: Session, auth_user: User):
    session.add(SheetDocument(id="sheet-a", numeric_id=7, scope="notes", owner_user_id=auth_user.id, title="Sheet"))
    session.commit()

    sheet_id = ensure_resource_identity(session, RESOURCE_TYPE_SHEET, "sheet-a", 7)

    assert sheet_id == 7
    identity = session.get(ResourceIdentity, 7)
    assert identity is not None
    assert identity.resource_type == "sheet"
    assert identity.legacy_pk == "sheet-a"


def test_high_coupling_link_migration_uses_public_numeric_refs(session: Session, auth_user: User):
    session.add(SheetDocument(id="sheet-a", numeric_id=10, scope="notes", owner_user_id=auth_user.id, title="Sheet"))
    session.add(WorkbookDocument(id="workbook-a", numeric_id=20, owner_user_id=auth_user.id, title="Workbook"))
    session.add(NoteNode(id="note-a", numeric_id=30, user_id=auth_user.id, title="A"))
    session.add(NoteNode(id="note-b", numeric_id=31, user_id=auth_user.id, title="B"))
    session.commit()
    v45_add_global_resource_identity(session)

    session.add(WorkbookSheetLink(id="link-a", workbook_id="workbook-a", sheet_id="sheet-a", order_index=10))
    session.add(NoteEdge(id="edge-a", user_id=auth_user.id, source_id="note-a", target_id="note-b"))
    session.commit()

    sheet = session.get(SheetDocument, "sheet-a")
    workbook = session.get(WorkbookDocument, "workbook-a")
    note_a = session.get(NoteNode, "note-a")
    note_b = session.get(NoteNode, "note-b")

    v49_migrate_graph_and_workbook_links_to_public_ids(session)
    session.expire_all()

    link = session.get(WorkbookSheetLink, "link-a")
    edge = session.get(NoteEdge, "edge-a")
    assert link.workbook_id == str(workbook.numeric_id)
    assert link.sheet_id == str(sheet.numeric_id)
    assert edge.source_id == str(note_a.numeric_id)
    assert edge.target_id == str(note_b.numeric_id)


def test_priority_repack_leaves_workbook_route_ids_local(session: Session, auth_user: User):
    session.add(SheetDocument(id="sheet-a", numeric_id=1, scope="notes", owner_user_id=auth_user.id, title="Sheet"))
    session.add(WorkbookDocument(id="workbook-a", numeric_id=1, owner_user_id=auth_user.id, title="Workbook"))
    session.add(PdfDocument(id=101, legacy_id="pdf-a", numeric_id=101, owner_user_id=auth_user.id, title="PDF"))
    session.add(DocumentAsset(id=102, legacy_id="asset-a", numeric_id=102, user_id=auth_user.id, title="Asset", original_filename="a.txt", sha256="abc"))
    session.add(NoteNode(id="note-a", numeric_id=2, user_id=auth_user.id, title="A"))
    session.add(NoteNode(id="note-b", numeric_id=3, user_id=auth_user.id, title="B"))
    session.add(NoteEdge(id="edge-a", user_id=auth_user.id, source_id="note-a", target_id="note-b"))
    session.add(WorkbookSheetLink(id="link-a", workbook_id="1", sheet_id="1", order_index=10))
    session.commit()

    v45_add_global_resource_identity(session)
    v49_migrate_graph_and_workbook_links_to_public_ids(session)
    v51_repack_resource_ids_by_priority(session)
    session.expire_all()

    workbook = session.get(WorkbookDocument, "workbook-a")
    pdf = session.get(PdfDocument, 101)
    asset = session.get(DocumentAsset, 102)
    note_a = session.get(NoteNode, "note-a")
    note_b = session.get(NoteNode, "note-b")
    link = session.get(WorkbookSheetLink, "link-a")
    edge = session.get(NoteEdge, "edge-a")

    assert workbook.numeric_id == 1
    assert pdf.numeric_id == 3
    assert asset.numeric_id == 4
    assert note_a.numeric_id > 4
    assert note_b.numeric_id > 4
    assert link.workbook_id == "1"
    assert link.sheet_id == "1"
    assert edge.source_id == str(note_a.numeric_id)
    assert edge.target_id == str(note_b.numeric_id)


def test_documentasset_primary_key_migration_preserves_legacy_id_and_refs(session: Session, auth_user: User):
    session.exec(text("DROP TABLE documentasset"))
    session.exec(
        text(
            """
            CREATE TABLE documentasset (
                id VARCHAR NOT NULL PRIMARY KEY,
                numeric_id INTEGER,
                user_id INTEGER NOT NULL,
                title VARCHAR NOT NULL,
                original_filename VARCHAR NOT NULL,
                media_type VARCHAR NOT NULL,
                file_ext VARCHAR NOT NULL,
                size_bytes INTEGER NOT NULL,
                sha256 VARCHAR NOT NULL,
                source_char_count INTEGER NOT NULL,
                status VARCHAR NOT NULL,
                latest_run_id VARCHAR,
                latest_summary VARCHAR NOT NULL,
                latest_query_at FLOAT,
                run_count INTEGER NOT NULL,
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL
            )
            """
        )
    )
    session.execute(
        text(
            """
            INSERT INTO documentasset (
                id, numeric_id, user_id, title, original_filename,
                media_type, file_ext, size_bytes, sha256, source_char_count,
                status, latest_summary, run_count, created_at, updated_at
            ) VALUES (
                :id, :numeric_id, :user_id, :title, :original_filename,
                :media_type, :file_ext, :size_bytes, :sha256, :source_char_count,
                :status, :latest_summary, :run_count, :created_at, :updated_at
            )
            """
        ),
        {
            "id": "asset-a",
            "numeric_id": 102,
            "user_id": auth_user.id,
            "title": "Asset",
            "original_filename": "a.txt",
            "media_type": "text/plain",
            "file_ext": ".txt",
            "size_bytes": 1,
            "sha256": "abc",
            "source_char_count": 1,
            "status": "uploaded",
            "latest_summary": "",
            "run_count": 0,
            "created_at": 1.0,
            "updated_at": 1.0,
        },
    )
    session.add(ResourceIdentity(id=102, resource_type="document_asset", legacy_pk="asset-a"))
    session.add(DocumentReductionRun(id="run-a", document_id="asset-a", user_id=auth_user.id))
    session.add(DocumentQueryHistory(id="query-a", document_id="asset-a", run_id="run-a", user_id=auth_user.id))
    session.add(
        ResourceAccessGrant(
            id="grant-a",
            resource_type="document_asset",
            resource_id="asset-a",
            subject_key="anonymous",
            subject_type="anonymous",
            role="viewer",
        )
    )
    session.commit()

    v55_migrate_documentasset_primary_key_to_numeric(session)
    session.expire_all()

    asset = session.get(DocumentAsset, 102)
    assert asset is not None
    assert asset.id == 102
    assert asset.numeric_id == 102
    assert asset.legacy_id == "asset-a"
    assert session.get(ResourceIdentity, 102).legacy_pk == "asset-a"
    assert session.get(DocumentReductionRun, "run-a").document_id == "102"
    assert session.get(DocumentQueryHistory, "query-a").document_id == "102"
    assert session.get(ResourceAccessGrant, "grant-a").resource_id == "102"

    id_column = next(row for row in session.exec(text("PRAGMA table_info(documentasset)")).all() if row[1] == "id")
    assert "INT" in str(id_column[2]).upper()


def test_pdfdocument_primary_key_migration_preserves_legacy_id_and_refs(session: Session, auth_user: User):
    session.exec(text("DROP TABLE pdfdocument"))
    session.exec(
        text(
            """
            CREATE TABLE pdfdocument (
                id VARCHAR NOT NULL PRIMARY KEY,
                numeric_id INTEGER,
                title VARCHAR NOT NULL,
                source_device_file_id INTEGER,
                source_entry_id VARCHAR NOT NULL,
                source_device_id VARCHAR NOT NULL,
                source_absolute_path VARCHAR NOT NULL,
                mime_type VARCHAR NOT NULL,
                size_bytes INTEGER,
                content_hash VARCHAR,
                hash_algorithm VARCHAR NOT NULL,
                owner_user_id INTEGER NOT NULL,
                created_by_user_id INTEGER,
                updated_by_user_id INTEGER,
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL
            )
            """
        )
    )
    session.execute(
        text(
            """
            INSERT INTO pdfdocument (
                id, numeric_id, title, source_entry_id, source_device_id,
                source_absolute_path, mime_type, hash_algorithm, owner_user_id,
                created_by_user_id, updated_by_user_id, created_at, updated_at
            ) VALUES (
                :id, :numeric_id, :title, :source_entry_id, :source_device_id,
                :source_absolute_path, :mime_type, :hash_algorithm, :owner_user_id,
                :created_by_user_id, :updated_by_user_id, :created_at, :updated_at
            )
            """
        ),
        {
            "id": "pdf-a",
            "numeric_id": 136,
            "title": "PDF",
            "source_entry_id": "entry-a",
            "source_device_id": "device-a",
            "source_absolute_path": "a.pdf",
            "mime_type": "application/pdf",
            "hash_algorithm": "sha256",
            "owner_user_id": auth_user.id,
            "created_by_user_id": auth_user.id,
            "updated_by_user_id": auth_user.id,
            "created_at": 1.0,
            "updated_at": 1.0,
        },
    )
    session.add(ResourceIdentity(id=136, resource_type="pdf", legacy_pk="pdf-a"))
    session.add(PdfUserState(id="state-a", pdf_document_id="pdf-a", user_id=auth_user.id))
    session.add(PdfPageNote(id="note-a", pdf_document_id="pdf-a", user_id=auth_user.id, page_number=1))
    session.add(
        ResourceAccessGrant(
            id="grant-pdf-a",
            resource_type="pdf",
            resource_id="pdf-a",
            subject_key="anonymous",
            subject_type="anonymous",
            role="viewer",
        )
    )
    session.commit()

    v56_migrate_pdfdocument_primary_key_to_numeric(session)
    session.expire_all()

    pdf = session.get(PdfDocument, 136)
    assert pdf is not None
    assert pdf.id == 136
    assert pdf.numeric_id == 136
    assert pdf.legacy_id == "pdf-a"
    assert session.get(ResourceIdentity, 136).legacy_pk == "pdf-a"
    assert session.get(PdfUserState, "state-a").pdf_document_id == "136"
    assert session.get(PdfPageNote, "note-a").pdf_document_id == "136"
    assert session.get(ResourceAccessGrant, "grant-pdf-a").resource_id == "136"

    id_column = next(row for row in session.exec(text("PRAGMA table_info(pdfdocument)")).all() if row[1] == "id")
    assert "INT" in str(id_column[2]).upper()


def test_legacy_id_shadow_columns_are_backfilled_for_high_risk_resources(session: Session, auth_user: User):
    session.add(SheetDocument(id="sheet-a", numeric_id=7, scope="notes", owner_user_id=auth_user.id, title="Sheet"))
    session.add(WorkbookDocument(id="workbook-a", numeric_id=8, owner_user_id=auth_user.id, title="Workbook"))
    session.add(NoteNode(id="note-a", numeric_id=9, user_id=auth_user.id, title="Note"))
    session.commit()

    v57_add_legacy_id_shadow_columns(session)
    session.expire_all()

    sheet = session.get(SheetDocument, "sheet-a")
    workbook = session.get(WorkbookDocument, "workbook-a")
    note = session.get(NoteNode, "note-a")
    assert sheet.legacy_id == "sheet-a"
    assert workbook.legacy_id == "workbook-a"
    assert note.legacy_id == "note-a"
    assert load_sheets_by_refs(session, ["sheet-a"])["sheet-a"].id == "sheet-a"
    assert load_workbooks_by_refs(session, ["workbook-a"])["workbook-a"].id == "workbook-a"
    assert load_notes_by_refs(session, auth_user.id, ["note-a"])["note-a"].id == "note-a"


def test_notenode_primary_key_migration_preserves_legacy_id_and_refs(session: Session, auth_user: User):
    session.add(NoteNode(id="note-a", legacy_id="note-a", numeric_id=10, user_id=auth_user.id, title="A"))
    session.add(NoteNode(id="note-b", legacy_id="note-b", numeric_id=11, user_id=auth_user.id, title="B"))
    session.add(NoteEdge(id="edge-a", user_id=auth_user.id, source_id="10", target_id="11"))
    session.add(ResourceIdentity(id=10, resource_type="note", legacy_pk="note-a"))
    session.add(ResourceIdentity(id=11, resource_type="note", legacy_pk="note-b"))
    session.add(
        ResourceAccessGrant(
            id="grant-note-a",
            resource_type="note",
            resource_id="10",
            subject_key="anonymous",
            subject_type="anonymous",
            role="viewer",
        )
    )
    session.commit()

    v59_migrate_notenode_primary_key_to_numeric(session)

    id_column = next(row for row in session.exec(text("PRAGMA table_info(notenode)")).all() if row[1] == "id")
    assert "INT" in str(id_column[2]).upper()
    note_a = session.exec(text("SELECT id, numeric_id, legacy_id FROM notenode WHERE id = 10")).first()
    note_b = session.exec(text("SELECT id, numeric_id, legacy_id FROM notenode WHERE id = 11")).first()
    assert tuple(note_a) == (10, 10, "note-a")
    assert tuple(note_b) == (11, 11, "note-b")
    assert session.get(NoteEdge, "edge-a").source_id == "10"
    assert session.get(NoteEdge, "edge-a").target_id == "11"
    assert session.get(ResourceAccessGrant, "grant-note-a").resource_id == "10"

    report = _resource_table_report(session, "note", "notenode")
    assert report["id_is_integer_pk"] is True
    assert report["missing_identity"] == 0


def test_restore_workbook_route_ids_uses_original_rowid_ids(session: Session, auth_user: User):
    session.add(SheetDocument(id="sheet-a", numeric_id=1, scope="notes", sheet_key="a", owner_user_id=auth_user.id, title="Sheet A"))
    session.add(SheetDocument(id="sheet-b", numeric_id=2, scope="notes", sheet_key="b", owner_user_id=auth_user.id, title="Sheet B"))
    session.add(SheetDocument(id="sheet-c", numeric_id=3, scope="notes", sheet_key="c", owner_user_id=auth_user.id, title="Sheet C"))
    session.add(WorkbookDocument(id="workbook-a", numeric_id=4, owner_user_id=auth_user.id, title="Workbook A"))
    session.add(WorkbookDocument(id="workbook-b", numeric_id=5, owner_user_id=auth_user.id, title="Workbook B"))
    session.add(WorkbookSheetLink(id="link-a", workbook_id="4", sheet_id="1", order_index=10))
    session.add(
        ResourceAccessGrant(
            id="grant-a",
            resource_type="workbook",
            resource_id="5",
            subject_key="anonymous",
            subject_type="anonymous",
            role="viewer",
        )
    )
    session.commit()

    v52_restore_workbook_route_ids(session)
    session.expire_all()

    workbook_a = session.get(WorkbookDocument, "workbook-a")
    workbook_b = session.get(WorkbookDocument, "workbook-b")
    link = session.get(WorkbookSheetLink, "link-a")
    grant = session.get(ResourceAccessGrant, "grant-a")

    assert workbook_a.numeric_id == 1
    assert workbook_b.numeric_id == 2
    assert link.workbook_id == "1"
    assert grant.resource_id == "2"


def test_sheet_workbook_primary_key_migration_preserves_route_ids(session: Session, auth_user: User):
    session.add(SheetDocument(id="sheet-a", legacy_id="sheet-a", numeric_id=1, scope="notes", sheet_key="a", owner_user_id=auth_user.id, title="Sheet A"))
    session.add(SheetDocument(id="sheet-b", legacy_id="sheet-b", numeric_id=2, scope="notes", sheet_key="b", owner_user_id=auth_user.id, title="Sheet B"))
    session.add(WorkbookDocument(id="workbook-a", legacy_id="workbook-a", numeric_id=1, owner_user_id=auth_user.id, title="Workbook A"))
    session.add(ResourceIdentity(id=1, resource_type="sheet", legacy_pk="sheet-a"))
    session.add(ResourceIdentity(id=2, resource_type="sheet", legacy_pk="sheet-b"))
    session.add(ResourceIdentity(id=99, resource_type="workbook", legacy_pk="workbook-a"))
    session.add(WorkbookSheetLink(id="link-a", workbook_id="1", sheet_id="2", order_index=10))
    session.add(
        ResourceAccessGrant(
            id="grant-workbook-a",
            resource_type="workbook",
            resource_id="1",
            subject_key="anonymous",
            subject_type="anonymous",
            role="viewer",
        )
    )
    session.commit()

    v60_migrate_sheet_workbook_primary_keys_to_numeric(session)

    sheet_id_column = next(row for row in session.exec(text("PRAGMA table_info(sheetdocument)")).all() if row[1] == "id")
    workbook_id_column = next(row for row in session.exec(text("PRAGMA table_info(workbookdocument)")).all() if row[1] == "id")
    assert "INT" in str(sheet_id_column[2]).upper()
    assert "INT" in str(workbook_id_column[2]).upper()

    sheet = session.exec(text("SELECT id, numeric_id, legacy_id FROM sheetdocument WHERE id = 1")).first()
    workbook = session.exec(text("SELECT id, numeric_id, legacy_id FROM workbookdocument WHERE id = 1")).first()
    assert tuple(sheet) == (1, 1, "sheet-a")
    assert tuple(workbook) == (1, 1, "workbook-a")
    assert session.get(WorkbookSheetLink, "link-a").workbook_id == "1"
    assert session.get(WorkbookSheetLink, "link-a").sheet_id == "2"
    assert session.get(ResourceAccessGrant, "grant-workbook-a").resource_id == "1"

    sheet_report = _resource_table_report(session, "sheet", "sheetdocument")
    workbook_report = _resource_table_report(session, "workbook", "workbookdocument")
    assert sheet_report["id_is_integer_pk"] is True
    assert workbook_report["id_is_integer_pk"] is True
    assert sheet_report["missing_identity"] == 0
    assert workbook_report["missing_identity"] == 0


def test_resource_identity_check_documents_workbook_route_id_exception(session: Session, auth_user: User):
    session.add(WorkbookDocument(id="workbook-a", numeric_id=1, owner_user_id=auth_user.id, title="Workbook"))
    session.add(ResourceIdentity(id=99, resource_type="workbook", legacy_pk="workbook-a"))
    session.commit()

    report = _resource_table_report(session, "workbook", "workbookdocument")

    assert report["missing_identity"] == 0
    assert report["public_id_scope"] == "workbook_route_id"
    assert report["identity_id_matches_public_id"] is False


def test_resource_identity_check_reports_dangling_internal_refs(session: Session, auth_user: User):
    session.add(NoteNode(id="note-a", numeric_id=10, user_id=auth_user.id, title="A"))
    session.add(NoteEdge(id="edge-a", user_id=auth_user.id, source_id="10", target_id="999"))
    session.commit()

    valid_count = _dangling_ref_count(
        session,
        table_name="noteedge",
        column_name="source_id",
        target_table="notenode",
        target_column="numeric_id",
    )
    invalid_count = _dangling_ref_count(
        session,
        table_name="noteedge",
        column_name="target_id",
        target_table="notenode",
        target_column="numeric_id",
    )

    assert valid_count == 0
    assert invalid_count == 1


def test_resource_identity_check_reports_dangling_grants_and_codex_refs(session: Session, auth_user: User):
    session.add(NoteNode(id="note-a", numeric_id=10, user_id=auth_user.id, title="A"))
    session.add(
        ResourceAccessGrant(
            id="grant-a",
            resource_type="note",
            resource_id="999",
            subject_key="anonymous",
            subject_type="anonymous",
            role="viewer",
        )
    )
    session.add(
        CodexDiaryImportRun(
            id="run-a",
            user_id=auth_user.id,
            diary_date="2026-05-18",
            created_note_ids=["10", "999"],
            duplicate_note_ids=["not-a-number"],
        )
    )
    session.commit()

    grant_reports = _dangling_grant_report(session)
    created_refs = _codex_note_json_ref_report(session, "created_note_ids")
    duplicate_refs = _codex_note_json_ref_report(session, "duplicate_note_ids")

    assert next(item for item in grant_reports if item["resource_type"] == "note")["dangling"] == 1
    assert created_refs == {"non_numeric": 0, "dangling": 1}
    assert duplicate_refs == {"non_numeric": 1, "dangling": 0}


def test_fanxiu_inventory_note_ref_migration_rewrites_legacy_refs(session: Session, auth_user: User):
    storage_path = get_inventory_storage_path()
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    previous_text = storage_path.read_text(encoding="utf-8") if storage_path.exists() else None
    storage_path.write_text(
        json.dumps(
            {
                "version": 2,
                "warehouses": {
                    "wardrobe_hall": {
                        "shizhuang": [
                            {"id": "item-a", "name": "旧引用", "note_id": "legacy-note-a"},
                        ],
                    },
                },
                "collections": {
                    "activity_list": [
                        {"id": "activity-a", "name": "已有数字引用", "note_id": "11"},
                        {"id": "activity-b", "name": "坏引用", "note_id": "999"},
                    ],
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    try:
        session.add(NoteNode(id="legacy-note-a", legacy_id="legacy-note-a", numeric_id=10, user_id=auth_user.id, title="A"))
        session.add(NoteNode(id="note-b", legacy_id="note-b", numeric_id=11, user_id=auth_user.id, title="B"))
        session.commit()

        before = _fanxiu_inventory_note_ref_report(session)
        assert before["non_numeric"] == 1
        assert before["dangling"] == 1

        v58_migrate_fanxiu_inventory_note_refs(session)

        payload = json.loads(storage_path.read_text(encoding="utf-8"))
        assert payload["warehouses"]["wardrobe_hall"]["shizhuang"][0]["note_id"] == "10"
        assert payload["collections"]["activity_list"][0]["note_id"] == "11"
        assert payload["collections"]["activity_list"][1]["note_id"] == "999"

        after = _fanxiu_inventory_note_ref_report(session)
        assert after["non_numeric"] == 0
        assert after["dangling"] == 1
    finally:
        if previous_text is None:
            storage_path.unlink(missing_ok=True)
        else:
            storage_path.write_text(previous_text, encoding="utf-8")
