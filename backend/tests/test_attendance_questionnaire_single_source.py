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


def test_public_status_update_preserves_neighbor_and_rebinds_entities(monkeypatch):
    columns = list(attendance.ATTENDANCE_WJX_DATA_COLUMNS)
    document = attendance._normalize_attendance_wjx_sheet_document({
        "columns": columns, "rows": [["751"], ["750"]],
        "grid_rows": [columns], "data_start_row": 1,
    })

    def mutate(mutator):
        result, changed = mutator(document)
        assert changed
        return SimpleNamespace(document_json=result)

    monkeypatch.setattr(attendance, "_mutate_independent_attendance_wjx_sheet", mutate)
    result = attendance.update_independent_attendance_wjx_status(
        seq=750, process_status="已处理：账号已关联",
    ).document_json
    index = columns.index("处理状态")
    assert result["rows"][0][index] == ""
    assert result["rows"][1][index] == "已处理：账号已关联"
    assert result["grid_rows"][2][index] == "已处理：账号已关联"
    assert result["entity_cells"]["row_wjx_750"][result["column_ids"][index]]["value"] == "已处理：账号已关联"


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


def test_consecutive_insertions_preserve_courses_and_all_row_projections():
    columns = list(attendance.ATTENDANCE_WJX_DATA_COLUMNS)
    document = attendance._normalize_attendance_wjx_sheet_document({
        "columns": columns, "rows": [], "grid_rows": [columns],
        "data_start_row": 1,
    })
    courses = {750: "修道班8期5阶", 751: "修道班11期3阶", 752: "第50届觉观"}
    owners = {courses[750]: "敏兮", courses[751]: "王仁", courses[752]: "一步一步"}
    links = {course: f"/workbook/{seq}" for seq, course in courses.items()}
    for seq, course in courses.items():
        document, inserted, changed = attendance._upsert_attendance_wjx_sheet_values(
            document,
            {"序号": seq, "课程": course, "补充说明": f"备注{seq}", "处理状态": f"状态{seq}"},
            course_link_map=links, course_owner_map=owners,
        )
        assert inserted and changed
        # Inspect the returned document directly; normalizing it again would
        # hide the stale entity/grid state that production readers receive.
        for index, row in enumerate(document["rows"]):
            row_seq = int(row[0])
            assert document["grid_rows"][index + 1] == row
            assert document["row_ids"][index] == f"row_wjx_{row_seq}"
            for header, expected in {
                "课程": courses[row_seq], "考勤负责人": owners[courses[row_seq]],
                "补充说明": f"备注{row_seq}", "处理状态": f"状态{row_seq}",
            }.items():
                assert attendance._get_attendance_wjx_sheet_cell(row, columns, header) == expected
                column_id = document["column_ids"][columns.index(header)]
                assert document["entity_cells"][f"row_wjx_{row_seq}"][column_id]["value"] == expected
            assert attendance._extract_inline_cell_link_url(row[3]) == links[courses[row_seq]]


def test_removal_rebinds_grid_and_entities_before_returning():
    columns = list(attendance.ATTENDANCE_WJX_DATA_COLUMNS)
    document = attendance._normalize_attendance_wjx_sheet_document({
        "columns": columns, "rows": [["752"], ["751"], ["750"]],
        "grid_rows": [columns], "data_start_row": 1,
    })
    result, changed = attendance._remove_attendance_wjx_sheet_row(document, seq=751)
    assert changed
    assert result["row_ids"] == ["row_wjx_752", "row_wjx_750"]
    assert result["grid_rows"][1:] == result["rows"]
    assert "row_wjx_751" not in result["entity_cells"]


def test_questionnaire_course_field_reconciliation_updates_current_courses_only(monkeypatch):
    course_document = {
        "columns": ["课程类型", "课程名称", "在线考勤表", "考勤负责人"],
        "rows": [["修道班", "修道班8期5阶", "修道班8期5阶", "陈坤泽, 敏兮"]],
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


def test_unique_enrollment_identity_overrides_stale_explicit_course(monkeypatch):
    ensure_attendance_engine_importable()
    from xlsln.kq5034.engine.client import LocalAttendanceSheetClient

    workbooks = {
        15: {"sheets": [{"id": 60340, "title": "报名表"}]},
        22: {"sheets": [{"id": 62624, "title": "报名表"}]},
    }
    registration_rows = {
        15: [{"序号": "2_05", "姓名": "纪文淅"}],
        22: [{"序号": "2", "姓名": "孟鑫"}],
    }
    monkeypatch.setattr(
        LocalAttendanceSheetClient,
        "get_workbook_document",
        lambda _self, workbook_id: deepcopy(workbooks[int(workbook_id)]),
    )
    monkeypatch.setattr(
        LocalAttendanceSheetClient,
        "get_table",
        lambda _self, ref: {"rows": deepcopy(registration_rows[int(ref.workbook_id)])},
    )

    resolved = attendance.resolve_feedback_course_from_current_enrollment(
        [
            attendance.AttendanceFeedbackCourseOption(
                name="修道班8期5阶",
                attendance_sheet_url="/workbook/22?sheet=62623",
            ),
            attendance.AttendanceFeedbackCourseOption(
                name="修道班11期3阶",
                attendance_sheet_url="/workbook/15?sheet=60339",
            ),
        ],
        student_id_text="2-05",
        student_name="纪文淅",
    )

    assert resolved.identity_confirmed is True
    assert attendance._select_feedback_course_name("修道班8期5阶", resolved) == (
        "修道班11期3阶",
        "/workbook/15?sheet=60339",
    )


def test_course_correction_rebuilds_owner_and_link(monkeypatch):
    columns = list(attendance.ATTENDANCE_WJX_DATA_COLUMNS)
    state = {
        "document": {
            "columns": columns,
            "rows": [["740", "", "", "修道班8期5阶", "敏兮"]],
        },
        "version": 1,
    }
    monkeypatch.setattr(
        attendance,
        "_get_feedback_course_maps_from_summary_sheet",
        lambda _session: (
            {"修道班11期3阶": "/workbook/15?sheet=60339"},
            {"修道班11期3阶": "王仁"},
        ),
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

    monkeypatch.setattr(attendance, "_mutate_independent_attendance_wjx_sheet", mutate)

    result = attendance.correct_independent_attendance_wjx_course(
        seq=740,
        course_name="修道班11期3阶",
    )
    document = attendance._normalize_attendance_wjx_sheet_document(result.document_json)
    row = document["rows"][0]

    assert attendance._get_attendance_wjx_sheet_cell(row, columns, "课程") == "修道班11期3阶"
    assert attendance._get_attendance_wjx_sheet_cell(row, columns, "考勤负责人") == "王仁"
    assert attendance._extract_inline_cell_link_url(row[columns.index("课程")]) == "/workbook/15?sheet=60339"
