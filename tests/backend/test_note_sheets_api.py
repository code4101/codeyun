from __future__ import annotations

from sqlmodel import Session, select

from backend.app import app
from backend.core.auth import get_current_user_from_token, get_optional_current_user_from_token
from backend.core.feature_access import FEATURE_ACCESS_SUBJECT_USER, save_feature_access_policy_overrides
from backend.migrations.manager import (
    v29_migrate_attendance_course_sheets_to_notes_workbook,
    v30_add_numeric_sheet_and_workbook_ids,
)
from backend.models import SheetDocument, User, WorkbookDocument, WorkbookSheetLink


def _override_user(user: User) -> None:
    app.dependency_overrides[get_current_user_from_token] = lambda: user
    app.dependency_overrides[get_optional_current_user_from_token] = lambda: user


def _clear_user_override() -> None:
    app.dependency_overrides.pop(get_current_user_from_token, None)
    app.dependency_overrides.pop(get_optional_current_user_from_token, None)


def _create_user(session: Session, *, username: str, is_superuser: bool = False) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password="pw",
        is_active=True,
        is_superuser=is_superuser,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _grant_feature_access(session: Session, *, user_id: int, feature_key: str) -> None:
    save_feature_access_policy_overrides(
        session,
        subject_type=FEATURE_ACCESS_SUBJECT_USER,
        subject_user_id=user_id,
        overrides={feature_key: "allow"},
    )


def test_note_sheet_workbook_mvp_flow(client, session):
    user = _create_user(session, username="note-sheet-user")
    _grant_feature_access(session, user_id=user.id, feature_key="notes.sheets")
    _override_user(user)

    try:
        create_workbook_response = client.post(
            "/api/note-sheets/workbooks",
            json={"title": "禅宗工作簿"},
        )
        assert create_workbook_response.status_code == 200
        workbook = create_workbook_response.json()
        assert isinstance(workbook["id"], int) and workbook["id"] > 0
        assert workbook["title"] == "禅宗工作簿"
        assert workbook["sheet_count"] == 0

        create_sheet_response = client.post(
            "/api/note-sheets/sheets",
            json={
                "title": "报名表",
                "workbook_id": workbook["id"],
                "document_json": {
                    "schema_version": 1,
                    "columns": ["姓名", "手机号"],
                    "rows": [["时秋菊", "15641363033"]],
                },
            },
        )
        assert create_sheet_response.status_code == 200
        sheet = create_sheet_response.json()
        assert isinstance(sheet["id"], int) and sheet["id"] > 0
        assert sheet["scope"] == "notes"
        assert sheet["title"] == "报名表"
        assert sheet["owner_user_id"] == user.id
        assert sheet["document_json"]["rows"] == [["时秋菊", "15641363033"]]
        assert sheet["workbook_items"] == [{"id": workbook["id"], "title": "禅宗工作簿"}]

        list_workbooks_response = client.get("/api/note-sheets/workbooks")
        assert list_workbooks_response.status_code == 200
        assert list_workbooks_response.json()[0]["sheet_count"] == 1

        list_sheets_response = client.get("/api/note-sheets/sheets")
        assert list_sheets_response.status_code == 200
        assert list_sheets_response.json()[0]["workbook_items"] == [{"id": workbook["id"], "title": "禅宗工作簿"}]

        detail_workbook_response = client.get(f"/api/note-sheets/workbooks/{workbook['id']}")
        assert detail_workbook_response.status_code == 200
        assert detail_workbook_response.json()["sheets"][0]["id"] == sheet["id"]

        update_sheet_response = client.put(
            f"/api/note-sheets/sheets/{sheet['id']}",
            json={
                "title": "报名表-更新",
                "document_json": {
                    "schema_version": 1,
                    "columns": ["姓名", "手机号", "组号"],
                    "rows": [["时秋菊", "15641363033", "1"]],
                },
            },
        )
        assert update_sheet_response.status_code == 200
        updated_sheet = update_sheet_response.json()
        assert updated_sheet["title"] == "报名表-更新"
        assert updated_sheet["version"] == 2
        assert updated_sheet["document_json"]["columns"] == ["姓名", "手机号", "组号"]

        delete_workbook_response = client.delete(f"/api/note-sheets/workbooks/{workbook['id']}")
        assert delete_workbook_response.status_code == 200

        workbook_count = len(session.exec(select(WorkbookDocument)).all())
        sheet_count = len(session.exec(select(SheetDocument).where(SheetDocument.scope == "notes")).all())
        link_count = len(session.exec(select(WorkbookSheetLink)).all())
        assert workbook_count == 0
        assert sheet_count == 1
        assert link_count == 0

        delete_sheet_response = client.delete(f"/api/note-sheets/sheets/{sheet['id']}")
        assert delete_sheet_response.status_code == 200
        assert session.exec(select(SheetDocument).where(SheetDocument.scope == "notes")).all() == []
    finally:
        _clear_user_override()


def test_note_sheet_attach_existing_sheet_to_workbook(client, session):
    user = _create_user(session, username="note-sheet-link-user")
    _grant_feature_access(session, user_id=user.id, feature_key="notes.sheets")
    _override_user(user)

    try:
        workbook_response = client.post("/api/note-sheets/workbooks", json={"title": "工作簿A"})
        assert workbook_response.status_code == 200
        workbook_id = workbook_response.json()["id"]

        first_sheet_response = client.post("/api/note-sheets/sheets", json={"title": "表格A"})
        assert first_sheet_response.status_code == 200
        sheet_id = first_sheet_response.json()["id"]

        attach_response = client.post(
            f"/api/note-sheets/workbooks/{workbook_id}/sheets",
            json={"sheet_id": sheet_id},
        )
        assert attach_response.status_code == 200
        detail = attach_response.json()
        assert [item["id"] for item in detail["sheets"]] == [sheet_id]
    finally:
        _clear_user_override()


def test_workbook_save_as_template_and_duplicate(client, session):
    user = _create_user(session, username="note-sheet-save-as-user")
    _grant_feature_access(session, user_id=user.id, feature_key="notes.sheets")
    _override_user(user)

    try:
        workbook_response = client.post("/api/note-sheets/workbooks", json={"title": "原工作簿"})
        assert workbook_response.status_code == 200
        workbook_id = workbook_response.json()["id"]

        create_sheet_response = client.post(
            "/api/note-sheets/sheets",
            json={
                "title": "报名表",
                "workbook_id": workbook_id,
                "document_json": {
                    "schema_version": 1,
                    "columns": ["姓名", "手机号"],
                    "rows": [["时秋菊", "15641363033"]],
                    "column_configs": {"手机号": {"note": "保留原始手机号", "hidden": True}},
                },
            },
        )
        assert create_sheet_response.status_code == 200
        source_sheet = create_sheet_response.json()

        template_response = client.post(
            f"/api/note-sheets/workbooks/{workbook_id}/save-as",
            json={"mode": "template", "title": "原工作簿 模版"},
        )
        assert template_response.status_code == 200
        template_workbook = template_response.json()
        assert template_workbook["id"] != workbook_id
        assert template_workbook["title"] == "原工作簿 模版"
        assert len(template_workbook["sheets"]) == 1
        assert template_workbook["sheets"][0]["id"] != source_sheet["id"]

        template_sheet_detail = client.get(
            f"/api/note-sheets/sheets/{template_workbook['sheets'][0]['id']}",
            params={"paginate": False},
        )
        assert template_sheet_detail.status_code == 200
        assert template_sheet_detail.json()["document_json"]["rows"] == []
        assert template_sheet_detail.json()["document_json"]["column_configs"]["手机号"] == {
            "note": "保留原始手机号",
            "hidden": True,
        }

        duplicate_response = client.post(
            f"/api/note-sheets/workbooks/{workbook_id}/save-as",
            json={"mode": "duplicate", "title": "原工作簿 副本"},
        )
        assert duplicate_response.status_code == 200
        duplicate_workbook = duplicate_response.json()
        assert duplicate_workbook["id"] != workbook_id
        assert duplicate_workbook["title"] == "原工作簿 副本"
        assert len(duplicate_workbook["sheets"]) == 1
        assert duplicate_workbook["sheets"][0]["id"] not in {workbook_id, source_sheet["id"]}

        duplicate_sheet_detail = client.get(
            f"/api/note-sheets/sheets/{duplicate_workbook['sheets'][0]['id']}",
            params={"paginate": False},
        )
        assert duplicate_sheet_detail.status_code == 200
        assert duplicate_sheet_detail.json()["document_json"]["rows"] == [["时秋菊", "15641363033"]]
        assert duplicate_sheet_detail.json()["document_json"]["column_configs"]["手机号"] == {
            "note": "保留原始手机号",
            "hidden": True,
        }
    finally:
        _clear_user_override()


def test_note_sheet_pagination_load_and_page_patch_save(client, session):
    user = _create_user(session, username="note-sheet-pagination-user")
    _grant_feature_access(session, user_id=user.id, feature_key="notes.sheets")
    _override_user(user)

    try:
        create_sheet_response = client.post(
            "/api/note-sheets/sheets",
            json={
                "title": "大表格",
                "document_json": {
                    "schema_version": 1,
                    "columns": ["序号", "内容"],
                    "rows": [[str(index), f"row-{index}"] for index in range(1, 251)],
                    "view_settings": {
                        "pagination": {
                            "enabled": True,
                            "page_size": 100,
                        },
                    },
                },
            },
        )
        assert create_sheet_response.status_code == 200
        sheet_id = create_sheet_response.json()["id"]

        page2_response = client.get(
            f"/api/note-sheets/sheets/{sheet_id}",
            params={"page": 2, "page_size": 100},
        )
        assert page2_response.status_code == 200
        page2_detail = page2_response.json()
        assert page2_detail["pagination"] == {
            "page": 2,
            "page_size": 100,
            "total_rows": 250,
            "page_count": 3,
            "row_offset": 100,
            "loaded_row_count": 100,
        }
        assert page2_detail["document_json"]["rows"][0] == ["101", "row-101"]
        assert page2_detail["document_json"]["rows"][-1] == ["200", "row-200"]

        edited_rows = page2_detail["document_json"]["rows"][2:]
        save_response = client.put(
            f"/api/note-sheets/sheets/{sheet_id}",
            json={
                "document_json": {
                    "schema_version": 1,
                    "columns": ["序号", "内容"],
                    "rows": edited_rows,
                },
                "page_patch": {
                    "page": 2,
                    "page_size": 100,
                    "row_offset": 100,
                    "loaded_row_count": 100,
                },
            },
        )
        assert save_response.status_code == 200
        save_detail = save_response.json()
        assert save_detail["pagination"] == {
            "page": 2,
            "page_size": 100,
            "total_rows": 248,
            "page_count": 3,
            "row_offset": 100,
            "loaded_row_count": 98,
        }
        assert len(save_detail["document_json"]["rows"]) == 98
        assert save_detail["document_json"]["rows"][0] == ["103", "row-103"]

        normalized_page2_response = client.get(
            f"/api/note-sheets/sheets/{sheet_id}",
            params={"page": 2, "page_size": 100},
        )
        assert normalized_page2_response.status_code == 200
        normalized_page2 = normalized_page2_response.json()
        assert normalized_page2["pagination"]["loaded_row_count"] == 100
        assert normalized_page2["document_json"]["rows"][0] == ["103", "row-103"]
        assert normalized_page2["document_json"]["rows"][-1] == ["202", "row-202"]

        page3_response = client.get(
            f"/api/note-sheets/sheets/{sheet_id}",
            params={"page": 3, "page_size": 100},
        )
        assert page3_response.status_code == 200
        page3_detail = page3_response.json()
        assert page3_detail["pagination"]["loaded_row_count"] == 48
        assert page3_detail["document_json"]["rows"][0] == ["203", "row-203"]
    finally:
        _clear_user_override()


def test_note_sheet_respects_document_pagination_settings(client, session):
    user = _create_user(session, username="note-sheet-pagination-settings-user")
    _grant_feature_access(session, user_id=user.id, feature_key="notes.sheets")
    _override_user(user)

    try:
        create_sheet_response = client.post(
            "/api/note-sheets/sheets",
            json={
                "title": "可配置分页表格",
                "document_json": {
                    "schema_version": 1,
                    "columns": ["序号", "内容"],
                    "rows": [[str(index), f"row-{index}"] for index in range(1, 251)],
                    "view_settings": {
                        "pagination": {
                            "enabled": True,
                            "page_size": 50,
                        },
                    },
                },
            },
        )
        assert create_sheet_response.status_code == 200
        sheet_id = create_sheet_response.json()["id"]

        auto_page_response = client.get(f"/api/note-sheets/sheets/{sheet_id}")
        assert auto_page_response.status_code == 200
        auto_page_detail = auto_page_response.json()
        assert auto_page_detail["pagination"] == {
            "page": 1,
            "page_size": 50,
            "total_rows": 250,
            "page_count": 5,
            "row_offset": 0,
            "loaded_row_count": 50,
        }
        assert len(auto_page_detail["document_json"]["rows"]) == 50

        disable_pagination_response = client.put(
            f"/api/note-sheets/sheets/{sheet_id}",
            json={
                "document_json": {
                    **auto_page_detail["document_json"],
                    "view_settings": {
                        "pagination": {
                            "enabled": False,
                            "page_size": 50,
                        },
                    },
                },
                "page_patch": {
                    "page": 1,
                    "page_size": 50,
                    "row_offset": 0,
                    "loaded_row_count": 50,
                },
            },
        )
        assert disable_pagination_response.status_code == 200
        disable_detail = disable_pagination_response.json()
        assert disable_detail["pagination"] is None
        assert disable_detail["document_json"]["view_settings"]["pagination"]["enabled"] is False

        full_sheet_response = client.get(f"/api/note-sheets/sheets/{sheet_id}")
        assert full_sheet_response.status_code == 200
        full_sheet_detail = full_sheet_response.json()
        assert full_sheet_detail["pagination"] is None
        assert len(full_sheet_detail["document_json"]["rows"]) == 250
        assert full_sheet_detail["document_json"]["rows"][0] == ["1", "row-1"]
        assert full_sheet_detail["document_json"]["rows"][-1] == ["250", "row-250"]
    finally:
        _clear_user_override()


def test_note_sheet_sort_action_reorders_full_sheet_and_returns_first_page(client, session):
    user = _create_user(session, username="note-sheet-sort-user")
    _grant_feature_access(session, user_id=user.id, feature_key="notes.sheets")
    _override_user(user)

    try:
        create_sheet_response = client.post(
            "/api/note-sheets/sheets",
            json={
                "title": "排序表格",
                "document_json": {
                    "schema_version": 1,
                    "columns": ["序号", "内容"],
                    "rows": [
                        ["10", "row-10"],
                        ["2", "row-2"],
                        ["1", "row-1"],
                        ["", "row-empty"],
                        ["11", "row-11"],
                    ],
                    "view_settings": {
                        "pagination": {
                            "enabled": True,
                            "page_size": 2,
                        },
                    },
                },
            },
        )
        assert create_sheet_response.status_code == 200
        sheet_id = create_sheet_response.json()["id"]

        asc_response = client.post(
            f"/api/note-sheets/sheets/{sheet_id}/sort",
            json={
                "column_index": 0,
                "direction": "asc",
            },
        )
        assert asc_response.status_code == 200
        asc_detail = asc_response.json()
        assert asc_detail["pagination"] == {
            "page": 1,
            "page_size": 2,
            "total_rows": 5,
            "page_count": 3,
            "row_offset": 0,
            "loaded_row_count": 2,
        }
        assert asc_detail["document_json"]["rows"] == [
            ["1", "row-1"],
            ["2", "row-2"],
        ]

        page2_response = client.get(
            f"/api/note-sheets/sheets/{sheet_id}",
            params={"page": 2, "page_size": 2},
        )
        assert page2_response.status_code == 200
        assert page2_response.json()["document_json"]["rows"] == [
            ["10", "row-10"],
            ["11", "row-11"],
        ]

        desc_response = client.post(
            f"/api/note-sheets/sheets/{sheet_id}/sort",
            json={
                "column_index": 0,
                "direction": "desc",
            },
        )
        assert desc_response.status_code == 200
        desc_detail = desc_response.json()
        assert desc_detail["document_json"]["rows"] == [
            ["11", "row-11"],
            ["10", "row-10"],
        ]

        last_page_response = client.get(
            f"/api/note-sheets/sheets/{sheet_id}",
            params={"page": 3, "page_size": 2},
        )
        assert last_page_response.status_code == 200
        assert last_page_response.json()["document_json"]["rows"] == [
            ["", "row-empty"],
        ]
    finally:
        _clear_user_override()


def test_migrate_attendance_course_sheets_to_notes_workbook_is_idempotent(session):
    owner_user = _create_user(session, username="attendance-workbook-owner")
    source_document = SheetDocument(
        scope="attendance",
        owner_type="course_session",
        owner_key="20260412-chanzong-12qi-1jie",
        sheet_key="registration",
        title="报名表",
        engine="handsontable",
        document_json={
            "schema_version": 1,
            "columns": ["姓名", "手机号"],
            "rows": [["时秋菊", "15641363033"]],
        },
        version=3,
        owner_user_id=owner_user.id,
        created_by_user_id=owner_user.id,
        updated_by_user_id=owner_user.id,
    )
    session.add(source_document)
    session.commit()

    v29_migrate_attendance_course_sheets_to_notes_workbook(session)
    v29_migrate_attendance_course_sheets_to_notes_workbook(session)

    workbooks = session.exec(select(WorkbookDocument)).all()
    note_sheets = session.exec(
        select(SheetDocument)
        .where(SheetDocument.scope == "notes")
        .where(SheetDocument.owner_type == "course_workbook")
        .where(SheetDocument.owner_key == "20260412-chanzong-12qi-1jie")
    ).all()
    links = session.exec(select(WorkbookSheetLink)).all()

    assert len(workbooks) == 1
    assert workbooks[0].title == "20260412禅宗12期一阶"
    assert workbooks[0].owner_user_id == owner_user.id

    note_sheet_map = {item.sheet_key: item for item in note_sheets}
    assert set(note_sheet_map) == {"registration", "attendance"}
    assert note_sheet_map["registration"].document_json["rows"] == [["时秋菊", "15641363033"]]
    assert note_sheet_map["attendance"].title == "考勤表"
    assert len(links) == 2


def test_v30_backfills_numeric_sheet_and_workbook_ids(session):
    owner_user = _create_user(session, username="numeric-id-owner")
    workbook = WorkbookDocument(
        title="工作簿A",
        owner_user_id=owner_user.id,
        created_by_user_id=owner_user.id,
        updated_by_user_id=owner_user.id,
    )
    sheet = SheetDocument(
        scope="notes",
        owner_type="user",
        owner_key=str(owner_user.id),
        sheet_key="sheet-a",
        title="表格A",
        engine="handsontable",
        document_json={"schema_version": 1, "columns": [], "rows": []},
        owner_user_id=owner_user.id,
        created_by_user_id=owner_user.id,
        updated_by_user_id=owner_user.id,
    )
    session.add(workbook)
    session.add(sheet)
    session.commit()

    assert workbook.numeric_id is None
    assert sheet.numeric_id is None

    v30_add_numeric_sheet_and_workbook_ids(session)
    session.refresh(workbook)
    session.refresh(sheet)

    assert workbook.numeric_id == 1
    assert sheet.numeric_id == 1
