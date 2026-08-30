from __future__ import annotations

from copy import deepcopy
import inspect
from types import SimpleNamespace

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


def test_questionnaire_normalization_rebuilds_row_entities_from_sequence():
    columns = list(attendance.ATTENDANCE_WJX_DATA_COLUMNS)
    status_index = columns.index("处理状态")
    stale_status = "已处理：属于739的备注"
    document = {
        "columns": columns,
        "rows": [
            ["740"] + [""] * (len(columns) - 1),
            ["739"] + [""] * (len(columns) - 1),
        ],
        "grid_rows": [
            columns,
            ["740"] + [""] * (len(columns) - 1),
            ["739"] + [""] * (status_index - 1) + [stale_status] + [""] * (len(columns) - status_index - 1),
        ],
        "data_start_row": 1,
        "field_row_index": 0,
        "row_ids": ["row_stale_739"],
        "column_ids": [f"column_{index}" for index in range(len(columns))],
        "entity_columns": [
            {"id": f"column_{index}", "header": header}
            for index, header in enumerate(columns)
        ],
        "entity_rows": [
            {"id": "field_stale", "kind": "field"},
            {"id": "row_stale_739", "kind": "data"},
        ],
        "entity_cells": {
            "row_stale_739": {
                f"column_{status_index}": {"value": stale_status},
            },
        },
    }
    document["rows"][1][status_index] = stale_status

    normalized = attendance._normalize_attendance_wjx_sheet_document(document)

    assert normalized["row_ids"] == ["row_wjx_740", "row_wjx_739"]
    assert [row["id"] for row in normalized["entity_rows"][1:]] == normalized["row_ids"]
    status_column_id = normalized["column_ids"][status_index]
    assert status_column_id not in normalized["entity_cells"].get("row_wjx_740", {})
    assert normalized["entity_cells"]["row_wjx_739"][status_column_id]["value"] == stale_status


def test_questionnaire_top_insert_keeps_status_bound_to_sequence():
    columns = list(attendance.ATTENDANCE_WJX_DATA_COLUMNS)
    status_index = columns.index("处理状态")
    existing_status = "已处理：739"
    document = attendance._normalize_attendance_wjx_sheet_document({
        "columns": columns,
        "rows": [
            ["739"] + [""] * (status_index - 1) + [existing_status] + [""] * (len(columns) - status_index - 1),
        ],
    })

    inserted, was_inserted, changed = attendance._upsert_attendance_wjx_sheet_values(
        document,
        {"序号": 740, "姓名": "纪文淅"},
        preserve_process_status=False,
    )
    normalized = attendance._normalize_attendance_wjx_sheet_document(inserted)

    assert was_inserted is True
    assert changed is True
    assert normalized["row_ids"] == ["row_wjx_740", "row_wjx_739"]
    assert attendance._get_attendance_wjx_sheet_cell(normalized["rows"][0], columns, "处理状态") == ""
    assert attendance._get_attendance_wjx_sheet_cell(normalized["rows"][1], columns, "处理状态") == existing_status


def test_questionnaire_course_field_reconciliation_updates_current_courses_only(monkeypatch):
    course_document = {
        "columns": ["课程类型", "课程名称", "在线考勤表", "考勤负责人"],
        "rows": [
            ["修道班", "修道班8期5阶", "修道班8期5阶", "陈坤泽, 敏兮"],
        ],
    }
    questionnaire_document = {
        "columns": list(attendance.ATTENDANCE_WJX_DATA_COLUMNS),
        "rows": [
            ["740", "", "", "修道班8期5阶", "王仁"],
            ["739", "", "", "已下架课程", "历史负责人"],
        ],
    }
    state = {"document": questionnaire_document, "version": 1}

    def load_sheet(sheet_id, *, workbook_id=None):
        assert workbook_id == attendance.ATTENDANCE_WJX_DATA_WORKBOOK_ID
        document = course_document if sheet_id == attendance.FEEDBACK_COURSE_SOURCE_SHEET_ID else state["document"]
        return SimpleNamespace(
            numeric_id=sheet_id,
            document_json=deepcopy(document),
            version=state["version"],
            updated_at=1.0,
        )

    def mutate(mutator):
        next_document, changed = mutator(deepcopy(state["document"]))
        assert changed is True
        state["document"] = next_document
        state["version"] += 1
        return SimpleNamespace(
            numeric_id=attendance.ATTENDANCE_WJX_DATA_SHEET_ID,
            document_json=deepcopy(next_document),
            version=state["version"],
            updated_at=2.0,
        )

    monkeypatch.setattr(attendance, "_load_independent_attendance_sheet", load_sheet)
    monkeypatch.setattr(attendance, "_mutate_independent_attendance_wjx_sheet", mutate)

    result = attendance.reconcile_independent_attendance_wjx_course_fields()
    normalized = attendance._normalize_attendance_wjx_sheet_document(result.document_json)
    columns = list(normalized["columns"])

    assert attendance._get_attendance_wjx_sheet_cell(normalized["rows"][0], columns, "考勤负责人") == "敏兮"
    assert attendance._get_attendance_wjx_sheet_cell(normalized["rows"][1], columns, "考勤负责人") == "历史负责人"
    owner_column_id = normalized["column_ids"][columns.index("考勤负责人")]
    assert normalized["entity_cells"]["row_wjx_740"][owner_column_id]["value"] == "敏兮"


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
