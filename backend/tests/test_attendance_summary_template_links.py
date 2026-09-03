from copy import deepcopy
from datetime import date
from types import SimpleNamespace

from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from backend.api import note_sheets
from backend.models import SheetDocument, WorkbookDocument, WorkbookSheetLink


def test_new_attendance_template_keeps_resource_counts_but_drops_old_links():
    columns = [
        "课程类型",
        "课程名称",
        "在线考勤表",
        "课次链接",
        "打卡链接",
        "课程开始日期",
        "考勤实际完成结点",
        "报名人数",
    ]
    source_row = [
        "梵呗初阶",
        "梵呗初阶",
        {"value": "20260609梵呗初阶", "link": {"url": "/workbook/12?sheet=55713"}},
        {"value": "11", "link": {"url": "https://example.com/old-lessons"}},
        {"value": "1", "link": {"url": "https://example.com/old-clockin"}},
        "46182",
        "46198",
        "3",
    ]

    result = note_sheets._build_inserted_attendance_template_row(
        source_row,
        columns=columns,
        source_row_index=5,
        target_row_index=0,
        target_date=date(2026, 8, 9),
        course_type="梵呗初阶",
        source_start_date=date(2026, 6, 9),
    )

    assert result[3] == "11"
    assert result[4] == "1"
    assert result[6] == ""
    assert result[7] == ""


def test_current_refund_formula_never_returns_a_negative_amount():
    columns = ["总应返款", "已返款", "订单金额", "当前应返款"]

    result = note_sheets._build_attendance_current_refund_formula(columns, row_number=4)

    assert result == "=IF(C4>0,MAX(A4-B4,0),0)"


def test_nianzhu_and_jueguan_templates_use_zero_registration_fee_from_august_2026():
    columns = [
        "课程类型",
        "课程名称",
        "课程开始日期",
        "报名费",
        "报名人数",
        "总报名费",
    ]

    august_jueguan = note_sheets._build_inserted_attendance_template_row(
        ["觉观", "第49届觉观", "", "499", "22", "=D1*E1"],
        columns=columns,
        source_row_index=0,
        target_row_index=0,
        target_date=date(2026, 8, 1),
        course_type="觉观",
    )
    september_nianzhu = note_sheets._build_inserted_attendance_template_row(
        ["念住", "第43届念住", "", "620", "20", "=D1*E1"],
        columns=columns,
        source_row_index=0,
        target_row_index=0,
        target_date=date(2026, 9, 1),
        course_type="念住",
    )
    july_jueguan = note_sheets._build_inserted_attendance_template_row(
        ["觉观", "第48届觉观", "", "499", "20", "=D1*E1"],
        columns=columns,
        source_row_index=0,
        target_row_index=0,
        target_date=date(2026, 7, 1),
        course_type="觉观",
    )

    assert august_jueguan[3] == "0"
    assert september_nianzhu[3] == "0"
    assert july_jueguan[3] == "499"


def test_course_template_materialization_flushes_numeric_config_before_string_attendance(
    monkeypatch,
):
    events: list[str] = []

    class SessionStub:
        def flush(self):
            events.append("flush")

        def add(self, _value):
            events.append("add_attendance")

    class AttendanceStub:
        numeric_id = 102
        owner_key = "20260901-jueguan-50"
        version = 1
        updated_at = 0.0

        def __init__(self):
            self._document_json = {}

        @property
        def document_json(self):
            return self._document_json

        @document_json.setter
        def document_json(self, value):
            events.append("update_attendance")
            self._document_json = value

    from backend.core.attendance import nianzhu_course_sheets

    monkeypatch.setattr(
        nianzhu_course_sheets,
        "materialize_nianzhu_course_sheets",
        lambda *_args, **_kwargs: events.append("materialize"),
    )
    monkeypatch.setattr(
        nianzhu_course_sheets,
        "ensure_attendance_course_columns_visible",
        lambda document: (document, 0),
    )
    monkeypatch.setattr(
        note_sheets,
        "_prune_no_attendance_video_config_rows_for_course_template",
        lambda *_args, **_kwargs: events.append("prune_config"),
    )

    note_sheets._maybe_materialize_zen_course_data_sheets(
        SessionStub(),
        workbook=SimpleNamespace(numeric_id=101),
        attendance_sheet=AttendanceStub(),
        course_name="第50届觉观",
    )

    assert events.index("prune_config") < events.index("flush")
    assert events.index("flush") < events.index("update_attendance")


def test_monthly_template_job_writes_the_independent_summary_source(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    legacy_document = {
        "schema_version": 1,
        "columns": ["课程类型"],
        "rows": [["旧副本"]],
    }
    with Session(engine) as session:
        workbook = WorkbookDocument(numeric_id=2, title="武陵禅寺网课考勤汇总")
        sheet = SheetDocument(
            numeric_id=4,
            scope="notes",
            title="课程",
            document_json=deepcopy(legacy_document),
            version=3,
        )
        session.add(workbook)
        session.add(sheet)
        session.commit()
        session.refresh(workbook)
        session.refresh(sheet)
        session.add(WorkbookSheetLink(workbook_id=workbook.id, sheet_id=sheet.id, order_index=0))
        session.commit()

    authoritative_document = {
        "schema_version": 1,
        "columns": ["课程类型"],
        "rows": [["权威旧模板"]],
    }
    generated_document = {
        "schema_version": 1,
        "columns": ["课程类型"],
        "rows": [["权威新模板"]],
    }
    source = {
        "id": 4,
        "title": "课程",
        "engine": "handsontable",
        "version": 11,
        "updated_at": 11.0,
        "document_json": deepcopy(authoritative_document),
    }
    replaced: dict[str, object] = {}

    monkeypatch.setattr(note_sheets, "engine", engine)
    monkeypatch.setattr(
        note_sheets,
        "_bind_independent_attendance_document",
        lambda *_args, **_kwargs: deepcopy(source),
    )
    monkeypatch.setattr(
        note_sheets,
        "_repair_attendance_summary_cell_meta",
        lambda document: (document, False),
    )
    monkeypatch.setattr(
        note_sheets,
        "_repair_attendance_summary_online_sheet_links",
        lambda document: (document, False),
    )
    monkeypatch.setattr(note_sheets, "_read_attendance_template_skip_course_types", lambda *_args: set())
    monkeypatch.setattr(note_sheets, "_get_attendance_batch_course_targets", lambda *_args: [])
    monkeypatch.setattr(
        note_sheets,
        "_generate_attendance_next_month_templates",
        lambda *_args, **_kwargs: (deepcopy(generated_document), [SimpleNamespace(course_name="新模板")], []),
    )
    monkeypatch.setattr(
        note_sheets,
        "_materialize_attendance_template_workbooks_for_targets",
        lambda _session, **kwargs: (kwargs["generated_document_json"], 0),
    )

    def replace_summary(_document, source_payload, next_document, **kwargs):
        replaced["source_version"] = source_payload["version"]
        replaced["document_json"] = deepcopy(next_document)
        replaced["sheet_id"] = kwargs["sheet_id"]
        replaced["workbook_id"] = kwargs["workbook_id"]
        return {**source_payload, "version": 12, "document_json": deepcopy(next_document)}

    monkeypatch.setattr(note_sheets, "_replace_independent_attendance_summary_document", replace_summary)

    assert note_sheets.run_attendance_summary_template_job() == (1, 0)
    assert replaced == {
        "source_version": 11,
        "document_json": generated_document,
        "sheet_id": 4,
        "workbook_id": 2,
    }

    with Session(engine) as session:
        shell = session.exec(select(SheetDocument).where(SheetDocument.numeric_id == 4)).one()
        assert shell.version == 3
        assert shell.document_json == legacy_document


def test_independent_summary_replace_preserves_read_metadata(monkeypatch):
    from backend.core.attendance.independent_engine_adapter import ensure_attendance_engine_importable

    ensure_attendance_engine_importable()
    from xlsln.kq5034.engine.client import LocalAttendanceSheetClient

    next_document = {"columns": ["课程名称"], "rows": [["第50届觉观"]]}
    monkeypatch.setattr(
        LocalAttendanceSheetClient,
        "replace_document",
        lambda *_args, **_kwargs: {
            "id": 4,
            "version": 12,
            "updated_at": 12.0,
            "document_json": deepcopy(next_document),
        },
    )
    monkeypatch.setattr(note_sheets, "_broadcast_sheet_resource_update", lambda *_args: None)
    shell = SheetDocument(
        numeric_id=4,
        scope="notes",
        title="旧副本标题",
        engine="handsontable",
        document_json={},
        version=3,
    )
    source = {
        "id": 4,
        "title": "课程",
        "engine": "handsontable",
        "version": 11,
        "updated_at": 11.0,
        "document_json": {"columns": ["课程名称"], "rows": []},
    }

    result = note_sheets._replace_independent_attendance_summary_document(
        shell,
        source,
        next_document,
        sheet_id=4,
        workbook_id=2,
    )

    assert result["title"] == "课程"
    assert result["engine"] == "handsontable"
    assert result["version"] == 12
    assert shell.title == "课程"
    assert shell.version == 12
    assert shell.document_json == next_document
