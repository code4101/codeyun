"""考勤汇总表的动作必须落在考勤库，而不是 CodeYun 的历史外壳。

页面读的是考勤库里的汇总表，但动作接口曾经直接读写 ``codeyun.db`` 里的旧副本：
版本号天然和页面错开（必然 409），行序也不一致（版本侥幸相等时会标错课程）。
这里的用例固定住"绑定成功就必须写考勤库"这条边界。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date

from sqlalchemy.orm.attributes import set_committed_value
from sqlmodel import Session, select

from backend.api import note_sheets as note_sheets_api
from backend.app import app
from backend.core.access.auth import get_current_user_from_token, get_optional_current_user_from_token
from backend.models import SheetDocument, User, WorkbookDocument, WorkbookSheetLink

ENGINE_VERSION = 599
SHELL_VERSION = 584


def _override_user(user: User) -> None:
    app.dependency_overrides[get_current_user_from_token] = lambda: user
    app.dependency_overrides[get_optional_current_user_from_token] = lambda: user


def _clear_user_override() -> None:
    app.dependency_overrides.pop(get_current_user_from_token, None)
    app.dependency_overrides.pop(get_optional_current_user_from_token, None)


def _create_user(session: Session, *, username: str) -> User:
    user = User(
        username=username,
        nickname=username,
        email=f"{username}@example.com",
        hashed_password="pw",
        is_active=True,
        is_superuser=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _attendance_document(*, first_course: str, completed_index: int | None) -> dict:
    columns = ["课程类型", "课程名称", "在线考勤表", "考勤负责人", "备注", "返款频次", "课程开始日期", "课程结束日期", "考勤实际完成结点", "报名费", "报名人数"]
    rows = []
    for index, course_name in enumerate([first_course, "第50届觉观", "修道班13期1阶", "第49届觉观"]):
        row = ["禅宗一阶", course_name, f"2026{index:02d}01{course_name}", "", "", "", "46266", "=I1+24", "", "0", "1"]
        if completed_index is not None and index == completed_index:
            row[8] = "46260"
        rows.append(row)
    return {
        "schema_version": 1,
        "columns": columns,
        "data_start_row": 2,
        "rows": rows,
        "cell_meta": {},
    }


def _install_attendance_summary_sheet(session: Session, *, user: User, shell_document: dict) -> SheetDocument:
    workbook = WorkbookDocument(
        numeric_id=2,
        scope="notes",
        owner_type="user",
        owner_key=str(user.id),
        title="武陵禅寺网课考勤汇总",
        owner_user_id=user.id,
        created_by_user_id=user.id,
        updated_by_user_id=user.id,
    )
    sheet = SheetDocument(
        numeric_id=4,
        scope="notes",
        owner_type="user",
        owner_key=str(user.id),
        sheet_key="4",
        title="课程",
        engine="handsontable",
        owner_user_id=user.id,
        created_by_user_id=user.id,
        updated_by_user_id=user.id,
        version=SHELL_VERSION,
        document_json=deepcopy(shell_document),
    )
    session.add(workbook)
    session.add(sheet)
    session.commit()
    session.refresh(workbook)
    session.refresh(sheet)
    session.add(WorkbookSheetLink(workbook_id=workbook.id, sheet_id=sheet.id, order_index=0))
    session.commit()
    session.refresh(sheet)
    return sheet


def _bind_engine_document(monkeypatch, source: dict) -> None:
    """模拟 ``_bind_independent_attendance_document`` 的已提交值回填。"""

    def fake_bind(document, **_kwargs):
        # 真实实现用 set_committed_value 覆盖读取视图，不会把外壳行标脏。
        set_committed_value(document, "title", source["title"])
        set_committed_value(document, "engine", source["engine"])
        set_committed_value(document, "version", source["version"])
        set_committed_value(document, "updated_at", source["updated_at"])
        set_committed_value(document, "document_json", deepcopy(source["document_json"]))
        return deepcopy(source)

    monkeypatch.setattr(note_sheets_api, "_bind_independent_attendance_document", fake_bind)


def test_set_completed_writes_attendance_engine_instead_of_codeyun_shell(session, client, monkeypatch):
    user = _create_user(session, username="att-summary-engine-writer")
    shell_document = _attendance_document(first_course="旧副本课程", completed_index=None)
    shell = _install_attendance_summary_sheet(session, user=user, shell_document=shell_document)
    _override_user(user)

    engine_document = _attendance_document(first_course="20250106念住闯关", completed_index=3)
    source = {
        "id": 4,
        "title": "课程",
        "engine": "handsontable",
        "version": ENGINE_VERSION,
        "updated_at": 1_788_000_000.0,
        "document_json": engine_document,
    }
    _bind_engine_document(monkeypatch, source)
    captured: dict[str, object] = {}

    def fake_replace(document, source_payload, next_document, **kwargs):
        captured["expected_version"] = source_payload["version"]
        captured["document_json"] = deepcopy(next_document)
        captured["sheet_id"] = kwargs["sheet_id"]
        captured["workbook_id"] = kwargs["workbook_id"]
        set_committed_value(document, "version", source_payload["version"] + 1)
        set_committed_value(document, "document_json", deepcopy(next_document))
        return {**source_payload, "version": source_payload["version"] + 1, "document_json": deepcopy(next_document)}

    monkeypatch.setattr(note_sheets_api, "_replace_independent_attendance_summary_document", fake_replace)

    try:
        response = client.post(
            "/api/note-sheets/sheets/4/attendance-summary/set-completed",
            params={"workbook_id": 2},
            json={"base_version": ENGINE_VERSION, "row_index": 1, "completion_date": "2026-04-30"},
        )
        assert response.status_code == 200
        payload = response.json()

        # 动作按考勤库版本落库，且用的是页面看到的行号。
        assert captured["expected_version"] == ENGINE_VERSION
        assert captured["sheet_id"] == 4
        assert captured["workbook_id"] == 2
        assert payload["sheet"]["version"] == ENGINE_VERSION + 1
        moved_row = payload["sheet"]["document_json"]["rows"][payload["row_index"]]
        assert moved_row[1] == "第50届觉观"
        assert moved_row[8] == note_sheets_api._format_attendance_date_serial(date(2026, 4, 30))

        stale_response = client.post(
            "/api/note-sheets/sheets/4/attendance-summary/set-completed",
            params={"workbook_id": 2},
            json={"base_version": SHELL_VERSION, "row_index": 1, "completion_date": "2026-04-30"},
        )
        assert stale_response.status_code == 409
    finally:
        _clear_user_override()

    session.expire_all()
    stored_shell = session.exec(select(SheetDocument).where(SheetDocument.numeric_id == 4)).one()
    assert stored_shell.version == SHELL_VERSION
    assert stored_shell.document_json == shell_document


def test_set_completed_without_attendance_engine_still_updates_codeyun_sheet(session, client, monkeypatch):
    """非考勤库托管的普通汇总表（例如测试/离线部署）保持原行为。"""

    user = _create_user(session, username="att-summary-codeyun-writer")
    shell_document = _attendance_document(first_course="普通部署课程", completed_index=None)
    shell = _install_attendance_summary_sheet(session, user=user, shell_document=shell_document)
    initial_version = shell.version
    _override_user(user)
    monkeypatch.setattr(note_sheets_api, "_bind_independent_attendance_document", lambda *_args, **_kwargs: None)

    try:
        response = client.post(
            "/api/note-sheets/sheets/4/attendance-summary/set-completed",
            params={"workbook_id": 2},
            json={"base_version": initial_version, "row_index": 0, "completion_date": date(2026, 4, 30).isoformat()},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["sheet"]["version"] == initial_version + 1
        moved_row = payload["sheet"]["document_json"]["rows"][payload["row_index"]]
        assert moved_row[1] == "普通部署课程"
        assert moved_row[8] == note_sheets_api._format_attendance_date_serial(date(2026, 4, 30))
    finally:
        _clear_user_override()
