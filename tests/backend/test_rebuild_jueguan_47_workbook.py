from scripts.rebuild_jueguan_47_workbook import (
    STANDARD_REGISTRATION_COLUMNS,
    _adapt_attendance_document,
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


def test_jueguan_47_attendance_config_row_keeps_only_refund_period_and_status():
    columns = [
        "分组",
        "学号",
        "姓名",
        "昵称",
        "完成视频数",
        "视频应返款",
        "打卡应返款",
        "总应返款",
        "订单金额",
        "已返款",
        "当前应返款",
        "打卡数",
        *[f"第{index:02d}课" for index in range(1, 25)],
    ]
    document = {
        "schema_version": 1,
        "columns": columns,
        "rows": [],
        "grid_rows": [[""] * len(columns), list(columns), [""] * len(columns)],
        "data_start_row": 3,
        "field_row_index": 1,
        "cell_meta": {},
        "merged_cells": [],
    }

    adapted = _adapt_attendance_document(document)
    adapted_columns = adapted["columns"]
    config_row = adapted["grid_rows"][2]

    assert adapted_columns.index("已返款") < adapted_columns.index("订单金额")
    assert config_row[adapted_columns.index("总应返款")] == ""
    assert config_row[adapted_columns.index("已返款")] == '="第"&返款周期&"天"'
    assert config_row[adapted_columns.index("订单金额")] == 499
    assert config_row[adapted_columns.index("当前应返款")] == "待首次同步"
