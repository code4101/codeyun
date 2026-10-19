"""课程工作簿激活与 CodeYun 注册的事务边界。

CodeYun 侧使用文件 SQLite：调用方会话只 flush 未提交，registry 的全局 engine
指向同一文件的另一连接，与生产中“独立 Session 读不到未提交工作簿”的情形一致。
"""
import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from backend.api import note_sheets as note_sheets_api
from backend.core.attendance import workbook_registry
from backend.core.attendance.independent_engine_adapter import ensure_attendance_engine_importable
from backend.models import SheetDocument, WorkbookDocument, WorkbookSheetLink

ensure_attendance_engine_importable()
from xlsln.kq5034.engine.client import AttendanceStorageError, LocalAttendanceSheetClient

SHEET_SPECS = [
    ("attendance", "考勤表", ["学号", "姓名"], [["1", "甲"]]),
    ("registration", "报名表", ["序号", "姓名"], [["1", "甲"]]),
    ("video_config", "视频配置", ["lesson_id", "lesson_name"], [[1, "第01课"]]),
    ("video_data", "视频数据", ["用户ID", "lesson_id"], [["u1", 1]]),
    ("clockin_config", "打卡配置", ["clockin_id", "name"], [[1, "打卡数"]]),
    ("clockin_data", "打卡数据", ["用户ID", "clockin_id"], [["u1", 1]]),
]


@pytest.fixture(name="codeyun_engines")
def fixture_codeyun_engines(engine, tmp_path, monkeypatch):
    # ``engine`` normalizes shared metadata before creating the file database.
    url = f"sqlite:///{(tmp_path / 'codeyun.sqlite3').as_posix()}"
    caller_engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 1})
    SQLModel.metadata.create_all(caller_engine)
    registry_engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 1})
    monkeypatch.setattr(workbook_registry, "engine", registry_engine)
    yield caller_engine, registry_engine
    caller_engine.dispose()
    registry_engine.dispose()


def _add_course_shell(session: Session, *, workbook_id: int, title: str) -> WorkbookDocument:
    workbook = WorkbookDocument(id=str(workbook_id), numeric_id=workbook_id, title=title)
    session.add(workbook)
    session.flush()
    for order_index, (sheet_key, sheet_title, columns, rows) in enumerate(SHEET_SPECS):
        sheet_id = workbook_id + 1 + order_index
        session.add(SheetDocument(
            id=str(sheet_id),
            numeric_id=sheet_id,
            scope="notes",
            owner_type="course_workbook",
            owner_key=f"activation-tx-{workbook_id}",
            sheet_key=sheet_key,
            title=sheet_title,
            document_json={"columns": columns, "rows": rows, "grid_rows": [columns, *rows],
                           "data_start_row": 1, "field_row_index": 0},
        ))
        session.flush()
        session.add(WorkbookSheetLink(workbook_id=workbook.id, sheet_id=str(sheet_id), order_index=order_index))
    session.flush()
    return workbook


def _correction_sheets(session: Session) -> list[SheetDocument]:
    return session.exec(select(SheetDocument).where(SheetDocument.sheet_key == "attendance_corrections")).all()


def test_activation_registers_inside_uncommitted_caller_transaction(codeyun_engines):
    caller_engine, registry_engine = codeyun_engines
    with Session(caller_engine) as session:
        workbook = _add_course_shell(session, workbook_id=987100, title="事务边界课程A")

        result = note_sheets_api._activate_attendance_course_workbook(session, workbook)

        assert result["created"] is True
        registered = _correction_sheets(session)
        assert len(registered) == 1
        assert session.exec(select(WorkbookSheetLink).where(
            WorkbookSheetLink.sheet_id == str(registered[0].numeric_id),
        )).one().workbook_id == "987100"
        # Nothing was committed behind the caller's back.
        with Session(registry_engine) as other:
            assert other.exec(select(WorkbookDocument)).all() == []
        session.rollback()
        assert _correction_sheets(session) == []


def test_standalone_registration_still_commits_its_own_session(codeyun_engines):
    caller_engine, registry_engine = codeyun_engines
    with Session(caller_engine) as session:
        workbook = _add_course_shell(session, workbook_id=987200, title="事务边界课程B")
        sheets = session.exec(select(SheetDocument)).all()
        session.commit()
        # Activate through the runtime only, then call the registry as an external caller.
        LocalAttendanceSheetClient().activate_course_workbook({
            "workbook": {"numeric_id": 987200, "title": workbook.title},
            "owner_key": "activation-tx-987200",
            "sheets": [
                {"numeric_id": sheet.numeric_id, "sheet_key": sheet.sheet_key, "title": sheet.title,
                 "owner_key": sheet.owner_key, "document_json": dict(sheet.document_json or {})}
                for sheet in sheets
            ],
        })

    added = workbook_registry.register_attendance_workbook_sheets(workbook_id=987200)

    assert len(added) == 1
    with Session(registry_engine) as other:
        assert [sheet.numeric_id for sheet in _correction_sheets(other)] == added
    assert workbook_registry.register_attendance_workbook_sheets(workbook_id=987200) == []


def test_registration_failure_removes_runtime_workbook_created_by_activation(codeyun_engines, monkeypatch):
    caller_engine, _registry_engine = codeyun_engines

    def fail_identity(*_args, **_kwargs):
        raise RuntimeError("identity registry unavailable")

    monkeypatch.setattr("backend.core.resources.identity.ensure_resource_identity", fail_identity)
    with Session(caller_engine) as session:
        workbook = _add_course_shell(session, workbook_id=987300, title="事务边界课程C")

        with pytest.raises(RuntimeError, match="identity registry unavailable"):
            note_sheets_api._activate_attendance_course_workbook(session, workbook)

    with pytest.raises(AttendanceStorageError):
        LocalAttendanceSheetClient().get_workbook_document(987300)
