from __future__ import annotations

import re

from sqlmodel import Session, select

from backend.app import app
from backend.core.auth import get_current_user_from_token, get_optional_current_user_from_token
from backend.models import SheetDocument, User


def _override_user(user: User) -> None:
    app.dependency_overrides[get_current_user_from_token] = lambda: user
    app.dependency_overrides[get_optional_current_user_from_token] = lambda: user


def _clear_user_override() -> None:
    app.dependency_overrides.pop(get_current_user_from_token, None)
    app.dependency_overrides.pop(get_optional_current_user_from_token, None)


def _create_admin_user(session: Session) -> User:
    user = User(
        username="attendance-sheet-admin",
        email="attendance-sheet-admin@example.com",
        hashed_password="pw",
        is_active=True,
        is_superuser=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def test_attendance_sheet_document_upsert_persists_and_reads_by_owner_and_id(client, session):
    admin_user = _create_admin_user(session)
    _override_user(admin_user)

    try:
        payload = {
            "owner_type": "course_session",
            "owner_key": "20260412-chanzong-12qi-1jie",
            "sheet_key": "registration",
            "title": "报名表",
            "document_json": {
                "schema_version": 1,
                "columns": ["组号", "序号", "备注"],
                "rows": [["1", "1", "首条"]],
            },
        }

        response = client.put("/api/attendance/sheets", json=payload)

        assert response.status_code == 200
        saved = response.json()
        assert re.fullmatch(r"[0-9a-z]{12}", saved["id"])
        assert saved["scope"] == "attendance"
        assert saved["owner_type"] == "course_session"
        assert saved["owner_key"] == "20260412-chanzong-12qi-1jie"
        assert saved["sheet_key"] == "registration"
        assert saved["title"] == "报名表"
        assert saved["engine"] == "handsontable"
        assert saved["version"] == 1
        assert saved["document_json"]["rows"] == [["1", "1", "首条"]]

        by_owner = client.get(
            "/api/attendance/sheets/by-owner",
            params={
                "owner_type": "course_session",
                "owner_key": "20260412-chanzong-12qi-1jie",
                "sheet_key": "registration",
            },
        )
        assert by_owner.status_code == 200
        assert by_owner.json()["id"] == saved["id"]

        by_id = client.get(f"/api/attendance/sheets/{saved['id']}")
        assert by_id.status_code == 200
        assert by_id.json()["id"] == saved["id"]

        stored = session.exec(select(SheetDocument)).one()
        assert stored.id == saved["id"]
        assert stored.scope == "attendance"
        assert stored.document_json["rows"] == [["1", "1", "首条"]]
        assert stored.created_by_user_id == admin_user.id
        assert stored.updated_by_user_id == admin_user.id
    finally:
        _clear_user_override()


def test_attendance_sheet_document_upsert_reuses_existing_locator_and_increments_version(client, session):
    admin_user = _create_admin_user(session)
    _override_user(admin_user)

    try:
        first_response = client.put(
            "/api/attendance/sheets",
            json={
                "owner_type": "course_session",
                "owner_key": "20260412-chanzong-12qi-1jie",
                "sheet_key": "registration",
                "title": "报名表",
                "document_json": {
                    "schema_version": 1,
                    "columns": ["组号", "序号"],
                    "rows": [["1", "1"]],
                },
            },
        )
        assert first_response.status_code == 200
        first_saved = first_response.json()

        second_response = client.put(
            "/api/attendance/sheets",
            json={
                "owner_type": "course_session",
                "owner_key": "20260412-chanzong-12qi-1jie",
                "sheet_key": "registration",
                "title": "报名表",
                "document_json": {
                    "schema_version": 1,
                    "columns": ["组号", "序号", "自定义字段1"],
                    "rows": [["1", "1", "扩展列"]],
                },
            },
        )
        assert second_response.status_code == 200
        second_saved = second_response.json()

        assert second_saved["id"] == first_saved["id"]
        assert second_saved["version"] == 2
        assert second_saved["document_json"]["columns"] == ["组号", "序号", "自定义字段1"]

        stored_items = session.exec(select(SheetDocument)).all()
        assert len(stored_items) == 1
        assert stored_items[0].id == first_saved["id"]
        assert stored_items[0].version == 2
        assert stored_items[0].document_json["rows"] == [["1", "1", "扩展列"]]
    finally:
        _clear_user_override()
