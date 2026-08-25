from __future__ import annotations

import inspect

from sqlmodel import Session

from backend.api import attendance
from backend.core.attendance.independent_engine_adapter import ensure_attendance_engine_importable


def test_questionnaire_mutation_uses_independent_attendance_database(monkeypatch, tmp_path):
    database = tmp_path / "attendance.sqlite3"
    monkeypatch.setenv("KQ_DATABASE_PATH", str(database))
    ensure_attendance_engine_importable()

    from xlsln.kq5034.engine.db import get_engine, init_db
    from xlsln.kq5034.engine.models import SheetDocument, WorkbookDocument

    init_db(database)
    with Session(get_engine(database)) as session:
        session.add(WorkbookDocument(
            id="attendance-workbook",
            numeric_id=attendance.ATTENDANCE_WJX_DATA_WORKBOOK_ID,
            title="考勤中台",
        ))
        session.add(SheetDocument(
            id="questionnaire-data",
            numeric_id=attendance.ATTENDANCE_WJX_DATA_SHEET_ID,
            title="问卷数据",
            document_json={
                "columns": list(attendance.ATTENDANCE_WJX_DATA_COLUMNS),
                "rows": [["732"] + [""] * (len(attendance.ATTENDANCE_WJX_DATA_COLUMNS) - 1)],
            },
        ))
        session.commit()

    def append_733(document_json):
        next_document, _inserted, changed = attendance._upsert_attendance_wjx_sheet_values(
            document_json,
            {"序号": 733, "姓名": "测试学员"},
            preserve_process_status=False,
        )
        return next_document, changed

    result = attendance._mutate_independent_attendance_wjx_sheet(append_733)
    document = attendance._normalize_attendance_wjx_sheet_document(result.document_json)
    assert result.version == 2
    assert document["rows"][0][0] == "733"
    assert document["rows"][0][6] == "测试学员"


def test_questionnaire_routes_do_not_write_codeyun_sheet_copy():
    submission_source = inspect.getsource(attendance._persist_attendance_feedback_submission)
    assert "_mutate_independent_attendance_wjx_sheet" in submission_source
    assert "session.add(" not in submission_source
    assert "session.commit(" not in submission_source

    for route in (
        attendance._build_attendance_wjx_data_page,
        attendance._collect_attendance_feedback_history_source_items,
        attendance._get_feedback_course_maps_from_summary_sheet,
        attendance._build_attendance_feedback_form_meta,
        attendance.update_attendance_wjx_data,
        attendance._resolve_attendance_wjx_precheck_entry,
        attendance.delete_attendance_wjx_data,
    ):
        source = inspect.getsource(route)
        assert "_ensure_attendance_wjx_sheet_document" not in source


def test_explicit_feedback_course_wins_over_conflicting_page_context():
    resolved = attendance.AttendanceFeedbackResolvedCourse(
        name="修道班9,10期4阶",
        link_url="/workbook/19?sheet=62169",
        strong_context=True,
    )

    assert attendance._select_feedback_course_name("20260809梵呗初阶", resolved) == (
        "20260809梵呗初阶",
        "",
    )
    assert attendance._select_feedback_course_name("考勤表", resolved) == (
        "修道班9,10期4阶",
        "/workbook/19?sheet=62169",
    )
