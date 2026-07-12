from backend.api.note_sheets import _adapt_course_template_workbook_defined_names


def test_clone_rebinds_zen_refund_names_to_target_course():
    names = [
        {"name": "开始日期", "formula": '="2026-05-17"'},
        {"name": "返款周期", "formula": "=第几周"},
        {"name": "返款说明", "formula": '="修道班7期5阶第"&返款周期&"周返款"'},
    ]

    result = _adapt_course_template_workbook_defined_names(
        names,
        target_title="修道班11期3阶",
        target_owner_key="20260705-xiudaoban-11-stage3",
    )
    by_name = {item["name"]: item["formula"] for item in result}

    assert by_name["开始日期"] == '="2026-07-05"'
    assert by_name["返款说明"] == '="修道班11期3阶第"&返款周期&"周返款"'
