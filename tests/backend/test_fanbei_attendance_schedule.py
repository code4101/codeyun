from datetime import date

from backend.core.attendance import fanbei_schedule as schedule
from backend.core.attendance.progress_style import (
    PercentageRefundRule,
    highlight_percentage_refund_progress,
    highlight_presence_progress,
    highlight_threshold_refund_progress,
    parse_threshold_refund_rules,
)
from backend.models import SheetDocument


def test_apply_step2_data_updates_only_attendance_data_columns(session):
    document = SheetDocument(
        numeric_id=606,
        title="考勤表",
        owner_user_id=1,
        document_json={
            "columns": ["分组", "用户ID", "打卡数", "19:30 第01课", "当前应返款"],
            "rows": [
                ["1组", "u1", "", "", "=FORMULA"],
                ["1组", "", "old", "old", "=KEEP"],
            ],
            "grid_rows": [
                ["", "", "打卡数据", "5月9日", ""],
                ["分组", "用户ID", "打卡数", "19:30 第01课", "当前应返款"],
                ["1组", "u1", "", "", "=FORMULA"],
                ["1组", "", "old", "old", "=KEEP"],
            ],
            "data_start_row": 2,
        },
    )
    session.add(document)
    session.commit()

    summary = schedule._apply_step2_data_to_attendance_sheet(
        session=session,
        sheet_id=606,
        step2_data={
            "columns": ["user_id2", "´ò¿¨Êý", "-µÚ01¿Î"],
            "rows": [["u1", 4, "µ±ÌÃÍê³É"], ["", 9, "不应写入"]],
        },
    )

    assert summary == {"updated_rows": 1, "updated_cells": 2, "mapped_columns": 2, "remote_rows": 2}
    session.refresh(document)
    rows = document.document_json["rows"]
    assert rows[0] == ["1组", "u1", 4, "当堂完成", "=FORMULA"]
    assert rows[1] == ["1组", "", "old", "old", "=KEEP"]
    assert document.document_json["grid_rows"][2] == rows[0]


def test_parse_fanbei_video_refund_rules_keeps_old_js_semantics():
    rules = schedule._parse_fanbei_video_refund_rules(
        '对应返回"40/32/24/16/8/0"元'
    )

    assert rules == {"当堂": 40, "第1天": 32, "第2天": 24, "第3天": 16, "第4天": 8, "回放": 0}


def test_attendance_refund_progress_style_uses_white_for_no_refund():
    refund_amount, color = schedule._highlight_course_progress(
        {"当堂": 40, "第1天": 32, "回放": 0},
        "第5天回放/100%",
    )

    assert refund_amount == 0
    assert color is None


def test_attendance_threshold_refund_style_parses_clockin_rule():
    rules = parse_threshold_refund_rules('打卡达到"5/10/15/20"次，累计返回"100/150/180/200"元')

    assert [(rule.threshold, rule.refund_amount) for rule in rules] == [
        (5, 100),
        (10, 150),
        (15, 180),
        (20, 200),
    ]
    no_refund, no_color = highlight_threshold_refund_progress(rules, 4)
    partial_refund, partial_color = highlight_threshold_refund_progress(rules, 10)
    full_refund, full_color = highlight_threshold_refund_progress(rules, 20)
    extra_refund, extra_color = highlight_threshold_refund_progress(rules, 30)

    assert no_refund == 0
    assert no_color is None
    assert partial_refund == 150
    assert partial_color == "#FFE08A"
    assert full_refund == 200
    assert full_color == "#80FF80"
    assert extra_refund == 200
    assert extra_color == "#80FF80"


def test_attendance_percentage_refund_style_supports_nianzhu_rule_versions():
    blank_refund, blank_color = highlight_percentage_refund_progress(
        [PercentageRefundRule(90, 20)],
        "--",
    )
    current_refund, current_color = highlight_percentage_refund_progress(
        [PercentageRefundRule(90, 20)],
        "1遍/98%",
    )
    old_partial_refund, old_partial_color = highlight_percentage_refund_progress(
        [
            PercentageRefundRule(90, 10),
            PercentageRefundRule(150, 15),
            PercentageRefundRule(200, 20),
        ],
        "1遍/98%",
    )
    old_full_refund, old_full_color = highlight_percentage_refund_progress(
        [
            PercentageRefundRule(90, 10),
            PercentageRefundRule(150, 15),
            PercentageRefundRule(200, 20),
        ],
        "3遍/242%",
    )

    assert blank_refund == 0
    assert blank_color is None
    assert current_refund == 20
    assert current_color == "#80FF80"
    assert old_partial_refund == 10
    assert old_partial_color == "#FFF3C4"
    assert old_full_refund == 20
    assert old_full_color == "#80FF80"


def test_attendance_presence_progress_style_colors_non_refund_progress():
    assert highlight_presence_progress("") is None
    assert highlight_presence_progress("学习中/0%") is None
    assert highlight_presence_progress("学习中/63%") == "#FFE9A6"
    assert highlight_presence_progress("3遍/228%") == "#80FF80"
    assert highlight_presence_progress("3遍/228%") != highlight_presence_progress("学习中/63%")


def test_fanbei_full_attendance_distinguishes_live_and_replay_completion():
    completed = ["当堂完成/99%", "第1天回放/100%", "当堂完成/98%", "第2天回放/100%", "当堂完成/97%"]

    assert schedule._classify_fanbei_stage_full_attendance(
        clockin_count=5,
        required_count=5,
        lesson_values=completed,
    ) == "回放全勤"
    assert schedule._classify_fanbei_stage_full_attendance(
        clockin_count=5,
        required_count=5,
        lesson_values=["当堂完成/99%"] * 5,
    ) == "直播全勤"
    assert not schedule._classify_fanbei_stage_full_attendance(
        clockin_count=4,
        required_count=5,
        lesson_values=completed,
    )
    assert not schedule._classify_fanbei_stage_full_attendance(
        clockin_count=5,
        required_count=5,
        lesson_values=[*completed[:4], "学习中/38%"],
    )


def test_apply_fanbei_step3_calculates_refunds_and_styles(session):
    columns = [
        "分组",
        "学号",
        "昵称",
        "商户订单号",
        "用户ID",
        "禅客",
        "完成视频数",
        "视频应返款",
        "打卡应返款",
        "总应返款",
        "已返款",
        "订单金额",
        "当前应返款",
        "返款配置",
        "打卡数",
        "19:30 第01课",
        "19:30 第02课",
        "19:30 第03课",
    ]
    rows = [
        [
            "1组",
            1,
            "学员A",
            "ABCDEFGHIJKLMNOPQRS",
            "u1",
            "=FORMULA",
            "=FORMULA",
            "=FORMULA",
            "=FORMULA",
            "=FORMULA",
            10,
            550,
            "=FORMULA",
            "=FORMULA",
            6,
            "当堂完成/100%",
            "第2天回放/50%",
            "学习中/80%",
        ],
        [
            "1组",
            2,
            "学员B",
            "",
            "u2",
            "=FORMULA",
            "=FORMULA",
            "=FORMULA",
            "=FORMULA",
            "=FORMULA",
            0,
            550,
            "=FORMULA",
            "=FORMULA",
            11,
            "",
            "",
            "",
        ],
    ]
    document = SheetDocument(
        numeric_id=607,
        title="考勤表",
        owner_user_id=1,
        document_json={
            "columns": columns,
            "rows": rows,
            "grid_rows": [
                [""] * len(columns),
                columns,
                [
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    '11课*40元=440元\n对应返回"40/32/24/16/8/0"元',
                    "",
                    "",
                    "",
                    "",
                    '=DATEDIF("2026-05-09",TODAY(),"d")',
                    "",
                    '日志打卡达到"3/6/11"次，累计返回"30/60/110"元',
                    "",
                    "",
                    "",
                ],
                *rows,
            ],
            "data_start_row": 3,
            "field_row_index": 1,
            "cell_meta": {
                "3:0": {"style": {"background_color": "#DDEBF7"}},
                "3:15": {"style": {"background_color": "#FFFFFF"}},
            },
        },
    )
    session.add(document)
    session.commit()

    summary = schedule._apply_fanbei_attendance_step3_to_sheet(
        session=session,
        sheet_id=607,
        course_name="d260509梵呗初阶",
        today=date(2026, 5, 10),
    )

    assert summary["lesson_columns"] == 3
    assert summary["updated_rows"] == 2
    assert summary["video_refund_total"] == 64
    assert summary["full_attendance_rows"] == 1
    assert summary["live_full_attendance_rows"] == 1
    assert summary["replay_full_attendance_rows"] == 0
    session.refresh(document)
    assert document.document_json["columns"][6:8] == ["全勤", "完成视频数"]
    next_rows = document.document_json["rows"]
    assert next_rows[0][5:15] == [
        "=FORMULA",
        "直播全勤",
        "=FORMULA",
        64,
        "=FORMULA",
        "=FORMULA",
        10,
        550,
        "=FORMULA",
        "=FORMULA",
    ]
    assert next_rows[1][5:15] == ["=FORMULA", "", "=FORMULA", 0, "=FORMULA", "=FORMULA", 0, 550, "=FORMULA", "=FORMULA"]
    assert document.document_json["grid_rows"][1][6] == "全勤"
    assert document.document_json["grid_rows"][2][13] == '=DATEDIF("2026-05-09",TODAY(),"d")'
    assert document.document_json["grid_rows"][2][14] == ""

    cell_meta = document.document_json["cell_meta"]
    assert cell_meta["3:16"]["style"]["background_color"] != "#FFFFFF"
    assert cell_meta["3:0"]["style"]["text_color"] == schedule.FANBEI_FULL_ATTENDANCE_TEXT_COLOR
    assert cell_meta["3:0"]["style"]["background_color"] == "#DDEBF7"
    assert cell_meta["3:18"]["style"]["text_color"] == schedule.FANBEI_FULL_ATTENDANCE_TEXT_COLOR
    assert "4:0" not in cell_meta
    assert "4:16" not in cell_meta

    schedule._apply_fanbei_attendance_step3_to_sheet(
        session=session,
        sheet_id=607,
        course_name="d260509梵呗初阶",
        today=date(2026, 5, 12),
    )
    session.refresh(document)
    assert document.document_json["columns"].count("全勤") == 1
    assert "text_color" not in document.document_json["cell_meta"]["3:0"]["style"]
    assert document.document_json["cell_meta"]["3:0"]["style"]["background_color"] == "#DDEBF7"


def test_apply_fanbei_step3_uses_source_meta_lesson_count(session):
    columns = [
        "学号",
        "完成视频数",
        "视频应返款",
        "19:30 第01课",
        "19:30 第02课",
        "19:30 第12课",
    ]
    rows = [["1", "=FORMULA", "=FORMULA", "当堂完成/100%", "当堂完成/100%", "当堂完成/100%"]]
    document = SheetDocument(
        numeric_id=608,
        title="考勤表",
        owner_user_id=1,
        document_json={
            "columns": columns,
            "rows": rows,
            "grid_rows": [
                [""] * len(columns),
                columns,
                ["", "", '12课*40元=480元\n对应返回"40/32/24/16/8/0"元', "", "", ""],
                *rows,
            ],
            "data_start_row": 3,
            "field_row_index": 1,
            "cell_meta": {},
            "source_meta": {"official_lesson_count": 12},
        },
    )
    session.add(document)
    session.commit()

    summary = schedule._apply_fanbei_attendance_step3_to_sheet(
        session=session,
        sheet_id=608,
        course_name="d260609梵呗增益",
        today=date(2026, 6, 10),
    )

    assert summary["lesson_columns"] == 3
    assert summary["video_refund_total"] == 120
    session.refresh(document)
    assert document.document_json["rows"][0][3] == 120


def test_apply_fanbei_step3_syncs_styles_to_entity_cells(session):
    columns = ["学号", "全勤", "完成视频数", "视频应返款", "打卡数", "19:30 第01课"]
    rows = [["1", "直播全勤", 1, 40, 1, "当堂完成/98%"]]
    entity_columns = [{"id": f"col-{index}", "kind": "data"} for index in range(len(columns))]
    entity_rows = [
        {"id": "header-1", "kind": "header"},
        {"id": "header-2", "kind": "header"},
        {"id": "header-3", "kind": "header"},
        {"id": "student-1", "kind": "data"},
    ]
    document = SheetDocument(
        numeric_id=609,
        title="考勤表",
        owner_user_id=1,
        document_json={
            "columns": columns,
            "rows": rows,
            "grid_rows": [
                [""] * len(columns),
                columns,
                ["", "", "", '1课*40元=40元\n对应返回"40/32/24/16/8/0"元', "", ""],
                *rows,
            ],
            "data_start_row": 3,
            "field_row_index": 1,
            "cell_meta": {
                **{
                    f"3:{column_index}": {
                        "style": {"text_color": schedule.FANBEI_FULL_ATTENDANCE_TEXT_COLOR}
                    }
                    for column_index in range(len(columns) - 1)
                },
                "3:5": {
                    "style": {
                        "background_color": "#80FF80",
                        "text_color": schedule.FANBEI_FULL_ATTENDANCE_TEXT_COLOR,
                    }
                },
            },
            "entity_columns": entity_columns,
            "entity_rows": entity_rows,
            "entity_cells": {"student-1": {}},
            "source_meta": {"official_lesson_count": 1},
        },
    )
    session.add(document)
    session.commit()
    previous_version = document.version

    schedule._apply_fanbei_attendance_step3_to_sheet(
        session=session,
        sheet_id=609,
        course_name="d260509梵呗初阶",
        today=date(2026, 5, 10),
    )

    session.refresh(document)
    assert document.version == previous_version + 1
    entity_cells = document.document_json["entity_cells"]["student-1"]
    assert entity_cells["col-5"]["style"]["background_color"] == "#80FF80"
    assert entity_cells["col-5"]["style"]["text_color"] == schedule.FANBEI_FULL_ATTENDANCE_TEXT_COLOR
    assert entity_cells["col-0"]["style"]["text_color"] == schedule.FANBEI_FULL_ATTENDANCE_TEXT_COLOR
