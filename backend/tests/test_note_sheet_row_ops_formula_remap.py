from types import SimpleNamespace

from backend.api import note_sheets
from backend.api.note_sheets import NoteSheetPatchOperation, _apply_note_sheet_patch_ops


def _summary_document():
    columns = ["课程名称", "报名费", "报名人数", "总报名费"]
    rows = [
        ["甲", 10, 2, "=B3*C3"],
        ["乙", 20, 3, "=B4*C4"],
        ["丙", 0, 5, "=B5*C5"],
    ]
    return {
        "columns": columns,
        "column_ids": ["c1", "c2", "c3", "c4"],
        "rows": rows,
        "row_ids": ["r1", "r2", "r3"],
        "grid_rows": [columns, ["", "", "", ""], *rows],
        "data_start_row": 2,
        "field_row_index": 0,
        "formula_reference_origin": "sheet_v2",
    }


def _formulas(document):
    return {row[0]: row[3] for row in document["rows"]}


def test_move_row_keeps_row_local_formulas_on_their_own_row():
    document, _ = _apply_note_sheet_patch_ops(
        _summary_document(),
        [NoteSheetPatchOperation(op="move-row", row_id="r3", after_row_id=None)],
    )

    assert [row[0] for row in document["rows"]] == ["丙", "甲", "乙"]
    assert _formulas(document) == {"丙": "=B3*C3", "甲": "=B4*C4", "乙": "=B5*C5"}


def test_insert_and_delete_row_shift_formulas_like_a_spreadsheet():
    inserted, _ = _apply_note_sheet_patch_ops(
        _summary_document(),
        [NoteSheetPatchOperation(op="insert-row", after_row_id="r1", row_id="r9", row=["新", 0, 1, "=B4*C4"])],
    )
    assert _formulas(inserted) == {"甲": "=B3*C3", "新": "=B4*C4", "乙": "=B5*C5", "丙": "=B6*C6"}

    deleted, _ = _apply_note_sheet_patch_ops(
        inserted, [NoteSheetPatchOperation(op="delete-row", row_id="r1")],
    )
    assert _formulas(deleted) == {"新": "=B3*C3", "乙": "=B4*C4", "丙": "=B5*C5"}


def test_workbook_detail_projects_attendance_runtime_names(monkeypatch):
    runtime_names = [{"name": "渐进课程预计课数", "formula": "=25", "comment": "", "scope": "workbook"}]
    monkeypatch.setattr(note_sheets, "_independent_workbook_defined_names", lambda workbook_id: runtime_names)
    monkeypatch.setattr(note_sheets, "_get_workbook_defined_names", lambda session, workbook: [{"name": "旧"}])
    sheet = SimpleNamespace(id="s1", owner_type="course_workbook")

    class _Session:
        def exec(self, _query):
            return SimpleNamespace(all=lambda: [])

    monkeypatch.setattr(note_sheets, "_load_sheet_summaries_by_refs", lambda session, ids: {"s1": sheet})
    monkeypatch.setattr(note_sheets, "_list_workbook_refs_for_sheet_ids", lambda *a, **kw: {})
    monkeypatch.setattr(note_sheets, "_require_workbook_numeric_id", lambda workbook: 7)
    monkeypatch.setattr(note_sheets, "_serialize_workbook_summary", lambda workbook, **kw: {"id": 7})

    monkeypatch.setattr(note_sheets, "workbook_ref_aliases", lambda workbook: {"7"})
    detail = note_sheets._serialize_workbook_detail(_Session(), SimpleNamespace())

    assert detail["defined_names"] == runtime_names


def test_table_upsert_appends_rows_with_their_key():
    from backend.api.note_sheets import NoteSheetTablePatchOperation as TableOperation
    from backend.api.note_sheets import _apply_table_write_fields_operation

    columns = ["clockin_id", "name"]
    rows = [[1, ""]]
    operation = TableOperation(
        type="write_fields", key_field="clockin_id", fields=["name"], append_missing=True,
        rows=[{"clockin_id": 1, "name": "共学"}, {"clockin_id": 2, "name": "随念"}],
    )

    _apply_table_write_fields_operation(rows, columns=columns, data_start_row=1, operation=operation)
    _apply_table_write_fields_operation(rows, columns=columns, data_start_row=1, operation=operation)

    assert rows == [[1, "共学"], [2, "随念"]]
