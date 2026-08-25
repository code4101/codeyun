from copy import deepcopy
from types import SimpleNamespace

from backend.api import attendance, note_sheets
from backend.core.attendance.independent_engine_adapter import ensure_attendance_engine_importable
from fastapi import HTTPException


def test_codeyun_sheet_shell_binds_attendance_owned_document(monkeypatch):
    ensure_attendance_engine_importable()
    from xlsln.kq5034.engine.client import LocalAttendanceSheetClient

    monkeypatch.setattr(
        LocalAttendanceSheetClient,
        "get_document",
        lambda self, sheet: {
            "id": sheet.sheet_id,
            "title": "独立考勤表",
            "engine": "handsontable",
            "version": 27,
            "updated_at": 123.0,
            "document_json": {"columns": ["第16课"], "rows": [["当堂完成"]]},
            "workbook_id": 20,
            "workbook_title": "第49届觉观",
            "defined_names_context": None,
        },
    )
    document = note_sheets.SheetDocument(
        numeric_id=62210,
        scope="notes",
        title="旧副本",
        engine="handsontable",
        version=9,
        document_json={"columns": ["第16课"], "rows": [[""]]},
    )

    result = note_sheets._bind_independent_attendance_document(
        document,
        sheet_id=62210,
        workbook_id=20,
    )

    assert result is not None
    assert document.title == "独立考勤表"
    assert document.version == 27
    assert document.document_json["rows"] == [["当堂完成"]]


def test_codeyun_sheet_shell_does_not_fallback_on_attendance_engine_failure(monkeypatch):
    ensure_attendance_engine_importable()
    from xlsln.kq5034.engine.client import LocalAttendanceSheetClient

    def fail(_self, _sheet):
        raise RuntimeError("attendance database unavailable")

    monkeypatch.setattr(LocalAttendanceSheetClient, "get_document", fail)
    document = note_sheets.SheetDocument(
        numeric_id=62210,
        scope="notes",
        title="旧副本",
        engine="handsontable",
        version=9,
        document_json={"columns": ["第16课"], "rows": [[""]]},
    )

    try:
        note_sheets._bind_independent_attendance_document(document, sheet_id=62210)
    except RuntimeError as exc:
        assert "attendance database unavailable" in str(exc)
    else:
        raise AssertionError("独立考勤库故障时禁止回退到 CodeYun 旧副本")


def test_codeyun_rejects_legacy_mutation_for_attendance_owned_sheet(monkeypatch):
    ensure_attendance_engine_importable()
    from xlsln.kq5034.engine.client import LocalAttendanceSheetClient

    monkeypatch.setattr(
        LocalAttendanceSheetClient,
        "get_document",
        lambda self, sheet: {
            "id": sheet.sheet_id,
            "title": "独立考勤表",
            "engine": "handsontable",
            "version": 27,
            "updated_at": 123.0,
            "document_json": {"columns": ["第16课"], "rows": [["当堂完成"]]},
            "workbook_id": 20,
            "workbook_title": "第49届觉观",
            "defined_names_context": None,
        },
    )
    document = note_sheets.SheetDocument(
        numeric_id=62210,
        scope="notes",
        title="旧副本",
        engine="handsontable",
        version=9,
        document_json={"columns": ["第16课"], "rows": [[""]]},
    )

    try:
        note_sheets._reject_independent_attendance_legacy_mutation(
            document,
            sheet_id=62210,
            workbook_id=20,
        )
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "旧写入入口已关闭" in str(exc.detail)
    else:
        raise AssertionError("独立考勤表不得继续写入 CodeYun 旧副本")


def test_independent_attendance_cell_patch_rebases_an_unrelated_runtime_update(monkeypatch):
    ensure_attendance_engine_importable()
    from xlsln.kq5034.engine.client import AttendanceVersionConflict, LocalAttendanceSheetClient

    original = {
        "schema_version": 1,
        "columns": ["课程名称", "考勤负责人"],
        "column_ids": ["col-course", "col-owner"],
        "rows": [["修道班8期5阶", ""]],
        "row_ids": ["row-xiudaoban-8-5"],
        "grid_rows": [["课程名称", "考勤负责人"], ["修道班8期5阶", ""]],
        "data_start_row": 1,
        "field_row_index": 0,
    }
    state = {"version": 1, "document_json": deepcopy(original), "replace_calls": 0}

    def get_document(_self, ref):
        return {
            "id": ref.sheet_id,
            "title": "课程",
            "engine": "handsontable",
            "version": state["version"],
            "updated_at": float(state["version"]),
            "document_json": deepcopy(state["document_json"]),
        }

    def replace_document(_self, ref, document_json, *, expected_version=None):
        state["replace_calls"] += 1
        if state["replace_calls"] == 1:
            state["document_json"]["rows"][0][0] = "系统更新后的课程名"
            state["document_json"]["grid_rows"][1][0] = "系统更新后的课程名"
            state["version"] = 2
            raise AttendanceVersionConflict("simulated concurrent runtime update")
        assert expected_version == state["version"]
        state["document_json"] = deepcopy(document_json)
        state["version"] += 1
        return {
            "id": ref.sheet_id,
            "version": state["version"],
            "updated_at": float(state["version"]),
            "document_json": deepcopy(state["document_json"]),
        }

    monkeypatch.setattr(LocalAttendanceSheetClient, "get_document", get_document)
    monkeypatch.setattr(LocalAttendanceSheetClient, "replace_document", replace_document)
    shell = note_sheets.SheetDocument(
        numeric_id=4,
        title="课程",
        version=1,
        document_json=deepcopy(original),
    )
    payload = note_sheets.NoteSheetPatchRequest.model_validate({
        "base_version": 1,
        "ops": [{
            "op": "set-cell-value",
            "row_index": 0,
            "column_index": 1,
            "row_id": "row-xiudaoban-8-5",
            "column_id": "col-owner",
            "expected_value": "",
            "value": "陈坤泽, 敏兮",
        }],
    })
    access = SimpleNamespace(capabilities=SimpleNamespace(
        can_edit_data=True,
        editable_data_columns=[],
    ))

    result = note_sheets._patch_independent_attendance_document(
        shell,
        get_document(None, SimpleNamespace(sheet_id=4)),
        payload,
        sheet_id=4,
        workbook_id=2,
        access=access,
        current_user=SimpleNamespace(id=1),
    )

    assert result.version == 3
    assert state["document_json"]["rows"][0] == ["系统更新后的课程名", "陈坤泽, 敏兮"]


def test_independent_attendance_cell_patch_rejects_a_real_same_cell_conflict(monkeypatch):
    ensure_attendance_engine_importable()
    from xlsln.kq5034.engine.client import LocalAttendanceSheetClient

    original = {
        "columns": ["课程名称", "考勤负责人"],
        "column_ids": ["col-course", "col-owner"],
        "rows": [["修道班8期5阶", ""]],
        "row_ids": ["row-xiudaoban-8-5"],
    }
    changed = deepcopy(original)
    changed["rows"][0][1] = "其他负责人"
    monkeypatch.setattr(
        LocalAttendanceSheetClient,
        "get_document",
        lambda _self, ref: {
            "id": ref.sheet_id,
            "title": "课程",
            "engine": "handsontable",
            "version": 2,
            "updated_at": 2.0,
            "document_json": deepcopy(changed),
        },
    )
    payload = note_sheets.NoteSheetPatchRequest.model_validate({
        "base_version": 1,
        "ops": [{
            "op": "set-cell-value",
            "row_index": 0,
            "column_index": 1,
            "row_id": "row-xiudaoban-8-5",
            "column_id": "col-owner",
            "expected_value": "",
            "value": "陈坤泽, 敏兮",
        }],
    })
    access = SimpleNamespace(capabilities=SimpleNamespace(
        can_edit_data=True,
        editable_data_columns=[],
    ))

    try:
        note_sheets._patch_independent_attendance_document(
            note_sheets.SheetDocument(numeric_id=4, title="课程", version=1),
            {
                "version": 2,
                "document_json": changed,
            },
            payload,
            sheet_id=4,
            workbook_id=2,
            access=access,
            current_user=SimpleNamespace(id=1),
        )
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "当前单元格" in str(exc.detail)
    else:  # pragma: no cover
        raise AssertionError("same-cell changes must remain a real conflict")


def test_public_shell_can_read_new_attendance_sheet_without_legacy_copy(monkeypatch):
    ensure_attendance_engine_importable()
    from xlsln.kq5034.engine.client import LocalAttendanceSheetClient

    monkeypatch.setattr(
        LocalAttendanceSheetClient,
        "get_document",
        lambda _self, ref: {
            "id": ref.sheet_id,
            "workbook_id": ref.workbook_id,
            "title": "考勤表",
            "document_json": {"columns": ["姓名"], "rows": [["测试学员"]]},
        },
    )

    result = attendance.get_independent_attendance_sheet_document_by_id(
        62623,
        workbook_id=22,
    )

    assert result["id"] == 62623
    assert result["workbook_id"] == 22
    assert result["document_json"]["rows"] == [["测试学员"]]


def test_public_shell_is_not_hidden_behind_attendance_tools_router():
    path = "/independent/sheets/{sheet_id}"
    assert path in {route.path for route in attendance.public_router.routes}
    assert path not in {route.path for route in attendance.router.routes}


def test_public_shell_does_not_expose_registration_sheet(monkeypatch):
    ensure_attendance_engine_importable()
    from xlsln.kq5034.engine.client import LocalAttendanceSheetClient

    monkeypatch.setattr(
        LocalAttendanceSheetClient,
        "get_document",
        lambda _self, ref: {
            "id": ref.sheet_id,
            "title": "报名表",
            "document_json": {"columns": ["手机号"], "rows": [["13800000000"]]},
        },
    )

    try:
        attendance.get_independent_attendance_sheet_document_by_id(
            62624,
            workbook_id=22,
        )
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("独立报名表不得通过只读考勤表壳暴露")


def test_authorised_operator_can_read_independent_workbook_shell(monkeypatch):
    ensure_attendance_engine_importable()
    from xlsln.kq5034.engine.client import LocalAttendanceSheetClient

    monkeypatch.setattr(attendance, "ensure_can_use_attendance_service", lambda *_args: None)
    monkeypatch.setattr(
        LocalAttendanceSheetClient,
        "get_workbook_document",
        lambda _self, workbook_id: {
            "id": workbook_id,
            "title": "修道班8期5阶",
            "sheet_count": 6,
            "sheets": [{"id": 62623, "title": "考勤表"}],
        },
    )

    result = attendance.get_independent_attendance_workbook_document_by_id(
        22,
        session=object(),
        current_user=object(),
        _=None,
    )

    assert result["id"] == 22
    assert result["title"] == "修道班8期5阶"


def test_private_workbook_sheet_requires_membership(monkeypatch):
    ensure_attendance_engine_importable()
    from xlsln.kq5034.engine.client import LocalAttendanceSheetClient

    monkeypatch.setattr(attendance, "ensure_can_use_attendance_service", lambda *_args: None)
    monkeypatch.setattr(
        LocalAttendanceSheetClient,
        "get_workbook_document",
        lambda _self, workbook_id: {
            "id": workbook_id,
            "sheets": [{"id": 62623, "title": "考勤表"}],
        },
    )

    try:
        attendance.get_independent_attendance_workbook_sheet_document_by_id(
            22,
            62624,
            session=object(),
            current_user=object(),
            _=None,
        )
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("不得跨工作簿编号读取独立考勤表")
