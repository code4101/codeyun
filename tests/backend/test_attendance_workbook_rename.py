"""PUT /workbooks/{id} 同步独立考勤工作簿标题，避免两个标题事实。"""
from sqlmodel import Session

from backend.app import app
from backend.core.access.auth import get_current_user_from_token, get_optional_current_user_from_token
from backend.core.access.feature_access import FEATURE_ACCESS_SUBJECT_USER, save_feature_access_policy_overrides
from backend.core.attendance.independent_engine_adapter import ensure_attendance_engine_importable
from backend.models import User, WorkbookDocument

ensure_attendance_engine_importable()
from xlsln.kq5034.engine import client as attendance_client_module
from xlsln.kq5034.engine.client import LocalAttendanceSheetClient
from xlsln.kq5034.engine.db import get_engine, init_db
from xlsln.kq5034.engine.models import WorkbookDocument as AttendanceWorkbook


def _owner(session: Session, username: str) -> User:
    user = User(username=username, hashed_password="x", is_active=True)
    session.add(user)
    session.commit()
    session.refresh(user)
    save_feature_access_policy_overrides(
        session, subject_type=FEATURE_ACCESS_SUBJECT_USER, subject_user_id=user.id,
        overrides={"notes.sheets": "allow"},
    )
    app.dependency_overrides[get_current_user_from_token] = lambda: user
    app.dependency_overrides[get_optional_current_user_from_token] = lambda: user
    return user


def _shell(session: Session, user: User, *, workbook_id: int, title: str) -> None:
    session.add(WorkbookDocument(id=str(workbook_id), numeric_id=workbook_id, title=title,
                                 owner_user_id=user.id, created_by_user_id=user.id))
    session.commit()


def _attendance(workbook_id: int, title: str) -> None:
    init_db()
    with Session(get_engine()) as session:
        session.add(AttendanceWorkbook(id=f"rename-{workbook_id}", numeric_id=workbook_id, title=title))
        session.commit()


def _attendance_title(workbook_id: int) -> str:
    return LocalAttendanceSheetClient().get_workbook_document(workbook_id)["title"]


def test_rename_updates_shell_and_attendance_title(client, session):
    user = _owner(session, "rename-attendance-owner")
    _shell(session, user, workbook_id=986100, title="第1届课程")
    _attendance(986100, "第1届课程")

    response = client.put("/api/note-sheets/workbooks/986100", json={"title": "第1届课程（改）"})

    assert response.status_code == 200, response.text
    assert response.json()["title"] == "第1届课程（改）"
    assert _attendance_title(986100) == "第1届课程（改）"


def test_rename_repairs_attendance_drift_when_shell_already_has_target(client, session):
    user = _owner(session, "rename-attendance-drift-owner")
    _shell(session, user, workbook_id=986200, title="新名称")
    _attendance(986200, "旧名称")

    response = client.put("/api/note-sheets/workbooks/986200", json={"title": "新名称"})

    assert response.status_code == 200, response.text
    assert _attendance_title(986200) == "新名称"


def test_rename_conflicting_attendance_title_fails_without_touching_shell(client, session, monkeypatch):
    user = _owner(session, "rename-attendance-conflict-owner")
    _shell(session, user, workbook_id=986300, title="甲")
    _attendance(986300, "甲")
    real_rename = LocalAttendanceSheetClient.rename_workbook

    def concurrent_rename(self, workbook_id, title, *, expected_title=None):
        # Another writer renames between our read and our guarded write.
        real_rename(self, workbook_id, "乙")
        return real_rename(self, workbook_id, title, expected_title=expected_title)

    monkeypatch.setattr(attendance_client_module.LocalAttendanceSheetClient, "rename_workbook", concurrent_rename)

    response = client.put("/api/note-sheets/workbooks/986300", json={"title": "丙"})

    assert response.status_code == 409
    assert "身份不一致" in response.json()["detail"]
    assert _attendance_title(986300) == "乙"
    session.expire_all()
    assert session.get(WorkbookDocument, "986300").title == "甲"


def test_rename_non_attendance_workbook_keeps_existing_behavior(client, session):
    user = _owner(session, "rename-plain-owner")
    _shell(session, user, workbook_id=986400, title="普通工作簿")

    response = client.put("/api/note-sheets/workbooks/986400", json={"title": "普通工作簿2"})

    assert response.status_code == 200, response.text
    assert response.json()["title"] == "普通工作簿2"
