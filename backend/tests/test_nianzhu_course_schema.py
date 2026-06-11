from backend.core.nianzhu_course_sheets import (
    _ensure_nianzhu_attendance_schema,
    _requires_attendance_tracking_meta_columns,
    _sync_row_local_managed_formulas,
)


def test_jueguan_course_does_not_require_tracking_meta_columns():
    assert not _requires_attendance_tracking_meta_columns({}, course_name="第47届觉观")


def test_jueguan_schema_removes_tracking_meta_columns():
    document = {
        "columns": ["姓名", "规则版本", "追踪分组", "追踪状态", "冻结时间"],
        "rows": [["张三", "当前规则", "一组", "追踪中", ""]],
        "grid_rows": [["姓名", "规则版本", "追踪分组", "追踪状态", "冻结时间"]],
        "field_row_index": 0,
    }

    next_document, summary = _ensure_nianzhu_attendance_schema(document, course_name="第47届觉观")

    assert next_document["columns"] == ["姓名"]
    assert next_document["rows"] == [["张三"]]
    assert summary["schema_removed_columns"] == ["规则版本", "追踪分组", "追踪状态", "冻结时间"]


def test_plain_nianzhu_course_does_not_require_tracking_meta_columns():
    assert not _requires_attendance_tracking_meta_columns({}, course_name="第41届念住")


def test_chuangguan_course_still_requires_tracking_meta_columns():
    assert _requires_attendance_tracking_meta_columns({}, course_name="d250106念住闯关")


def test_sync_row_local_managed_formulas_replaces_literal_zen_guest_value():
    document = {
        "columns": ["姓名", "禅客", "完成视频数", "打卡数"],
        "rows": [["张三", 0, 11, 7]],
        "grid_rows": [["姓名", "禅客", "完成视频数", "打卡数"], ["张三", 0, 11, 7]],
        "field_row_index": 0,
        "data_start_row": 1,
    }
    row = list(document["rows"][0])

    updated_cells = _sync_row_local_managed_formulas(
        document,
        row,
        columns=document["columns"],
        document_row=1,
        row_number=2,
    )

    assert updated_cells == 1
    assert row[1] == '=IF(AND(C2>=11,D2>=7),"是","")'
