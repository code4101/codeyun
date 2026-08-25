from datetime import date

from backend.api import note_sheets


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
