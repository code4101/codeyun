from scripts.rebuild_jueguan_47_workbook import (
    STANDARD_REGISTRATION_COLUMNS,
    _standard_registration_document,
)


def test_jueguan_47_registration_document_uses_standard_registration_shape():
    document = _standard_registration_document()

    assert document["columns"] == STANDARD_REGISTRATION_COLUMNS
    assert document["rows"] == []
    assert document["data_start_row"] == 2
    assert document["field_row_index"] == 0
    assert document["grid_rows"][0] == STANDARD_REGISTRATION_COLUMNS

    assert "完成视频数" not in document["columns"]
    assert "视频应返款" not in document["columns"]
    assert "05:20~06:18 第01课" not in document["columns"]

    actions = {
        key: value["action"]
        for key, value in document["cell_meta"].items()
        if isinstance(value, dict) and value.get("action")
    }
    assert actions["1:2"] == {"type": "excel_import_reset", "label": "导入excel"}
    assert actions["1:3"] == {"type": "registration_add_student", "label": "新增学员"}
    assert actions["1:8"] == {"type": "registration_order_match", "label": "更新订单匹配"}
    assert actions["1:12"] == {"type": "registration_user_match", "label": "更新用户匹配"}
    assert actions["1:13"] == {"type": "registration_composite_update", "label": "综合更新"}
    assert "关联用户ID" in document["columns"]
    assert document["column_configs"]["用户ID"]["header_background_color"] == "#9DC3E6"
    assert document["column_configs"]["关联用户ID"]["header_background_color"] == "#9DC3E6"
    assert document["column_configs"]["关联用户ID"]["font_family"] == "monospace"
