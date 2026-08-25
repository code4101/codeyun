from __future__ import annotations

import copy
from datetime import datetime

import pytest
from sqlmodel import Session, select

import backend.core.attendance.nianzhu_course_sheets as nianzhu_course_sheets
from backend.core.attendance.nianzhu_course_sheets import (
    CLOCKIN_CONFIG_COLUMNS,
    CLOCKIN_CONFIG_SHEET_KEY,
    CLOCKIN_DATA_COLUMNS,
    CLOCKIN_DATA_SHEET_KEY,
    VIDEO_CONFIG_COLUMNS,
    VIDEO_CONFIG_SHEET_KEY,
    VIDEO_DATA_COLUMNS,
    VIDEO_DATA_SHEET_KEY,
    compact_nianzhu_course_sheet_step2,
    materialize_nianzhu_course_sheets,
    normalize_nianzhu_course_sheet_names,
    rebuild_nianzhu_attendance_from_course_sheets,
    repair_nianzhu_clockin_refunds_from_course_sheets,
)
from backend.models import SheetDocument, WorkbookDocument, WorkbookSheetLink


def _sheet_document(columns: list[str], rows: list[list[object]]) -> dict:
    return {
        "schema_version": 1,
        "columns": columns,
        "rows": rows,
        "grid_rows": [columns, *rows],
        "data_start_row": 1,
        "field_row_index": 0,
        "formula_reference_origin": "sheet_v2",
    }


def test_video_rule_system_boundaries_are_explicit() -> None:
    assert nianzhu_course_sheets._resolve_video_rule_system(
        course_name="修道班7期5阶",
        lesson_name="第1周=佛教史1",
    ) == nianzhu_course_sheets.VIDEO_RULE_SYSTEM_ZEN_STAGE
    assert nianzhu_course_sheets._resolve_video_rule_system(
        course_name="d250106念住闯关",
        lesson_name="第01课",
    ) == nianzhu_course_sheets.VIDEO_RULE_SYSTEM_CHALLENGE
    assert nianzhu_course_sheets._resolve_video_rule_system(
        course_name="d260601第47届觉观",
        lesson_name="第01课",
    ) == nianzhu_course_sheets.VIDEO_RULE_SYSTEM_REGULAR


def test_clockin_output_fields_strip_course_prefix_but_keep_storage_key() -> None:
    course_name = "d260712禅宗13期一阶"
    config = _sheet_document(
        ["clockin_id", "name"],
        [[1, f"{course_name}-共学打卡"], [2, f"{course_name}-共修打卡-忏悔门"]],
    )

    assert nianzhu_course_sheets._clockin_output_fields(
        config,
        ["共学打卡", "共修打卡-忏悔门"],
        course_name=course_name,
    ) == [
        (f"{course_name}-共学打卡", "共学打卡", 0),
        (f"{course_name}-共修打卡-忏悔门", "共修打卡-忏悔门", 1),
    ]
    assert nianzhu_course_sheets._resolve_video_rule_system(
        course_name="修道班7期5阶",
        lesson_name="第2届答疑",
        is_qa_item=True,
    ) == nianzhu_course_sheets.VIDEO_RULE_SYSTEM_REGULAR


def test_course_sheet_documents_inherit_video_refund_rules_from_attendance_template_note() -> None:
    columns = ["姓名", "用户ID", "完成视频数", "视频应返款", "打卡数", "第01课"]
    note_row = [""] * len(columns)
    note_row[columns.index("视频应返款")] = (
        '21课*19元=399元。\n视频在"当堂(直播)/第1天(当天)/第2天/第3天/第4~5天"看完，'
        '对应返回"19/14/9/4/0"元'
    )
    attendance_document = {
        "schema_version": 1,
        "columns": columns,
        "rows": [["甲", "u1", "", "", "", "当堂完成/100%"]],
        "grid_rows": [[""] * len(columns), columns, note_row, ["甲", "u1", "", "", "", "当堂完成/100%"]],
        "data_start_row": 3,
        "field_row_index": 1,
        "formula_reference_origin": "sheet_v2",
    }

    documents = nianzhu_course_sheets._course_sheet_documents_from_attendance(
        attendance_document,
        course_name="第48届觉观",
    )

    expected_rules = {"当堂": 19, "第1天": 14, "第2天": 9, "第3天": 4, "回放": 0}
    video_meta = documents[VIDEO_CONFIG_SHEET_KEY]["source_meta"]
    video_data_meta = documents[VIDEO_DATA_SHEET_KEY]["source_meta"]
    assert video_meta["video_refund_rule_mode"] == "timed_text"
    assert video_meta["timed_video_rules"] == expected_rules
    assert video_data_meta["video_refund_rule_mode"] == "timed_text"
    assert video_data_meta["timed_video_rules"] == expected_rules


def test_attendance_video_refund_rules_read_merged_note_anchor() -> None:
    columns = ["姓名", "完成视频数", "视频应返款", "第01课"]
    note_row = [
        "",
        '21课*20元=420元。\n视频在"当堂(直播)/第1天(当天)/第2天/第3天/第4~7天"看完，'
        '对应返回"20/15/10/5/0"元',
        "",
        "",
    ]
    document = {
        "columns": columns,
        "grid_rows": [[""] * len(columns), columns, note_row],
        "data_start_row": 3,
        "merged_cells": [{"row": 2, "col": 1, "rowspan": 1, "colspan": 2}],
    }

    assert nianzhu_course_sheets._attendance_video_refund_rules_override(document, columns) == {
        "当堂": 20,
        "第1天": 15,
        "第2天": 10,
        "第3天": 5,
        "回放": 0,
    }


def test_zen_stage_attendance_schema_adds_50_video_columns_and_caps_refund_formula() -> None:
    document = {
        "schema_version": 1,
        "columns": ["姓名", "规则版本"],
        "rows": [["甲", "当前规则"]],
        "grid_rows": [["", ""], ["姓名", "规则版本"], ["", ""], ["甲", "当前规则"]],
        "data_start_row": 3,
        "field_row_index": 1,
        "formula_reference_origin": "sheet_v2",
    }
    video_config_document = _sheet_document(
        VIDEO_CONFIG_COLUMNS,
        [
            _row_from_dict(
                VIDEO_CONFIG_COLUMNS,
                {
                    "lesson_id": index,
                    "lesson_id2": f"https://example.test/lesson/{index}",
                    "lesson_name": f"第{((index - 1) // 3) + 1}周=第{index:02d}课",
                },
            )
            for index in range(1, 51)
        ],
    )
    video_config_document["source_meta"] = {"course_name": "修道班7期5阶"}
    video_config = nianzhu_course_sheets._load_video_config(video_config_document)

    next_document, inserted = nianzhu_course_sheets._ensure_video_progress_columns(document, video_config)
    columns = next_document["columns"]
    video_column_indexes = {
        item.lesson_id: columns.index(nianzhu_course_sheets._lesson_header_from_video_item(item))
        for item in video_config
    }
    next_document, repaired = nianzhu_course_sheets._sync_zen_stage_video_header_layout(
        next_document,
        video_config,
        video_column_indexes,
    )
    formula = nianzhu_course_sheets._build_legacy_zen_video_refund_formula(
        video_config,
        video_column_indexes,
        row_number=2,
        refund_amount=15,
        max_refund_count=49,
    )

    assert len(inserted) == 50
    assert columns[1:51] == [f"第{index:02d}课" for index in range(1, 51)]
    assert columns[-1] == "规则版本"
    assert formula == '=MIN(COUNTIF(B2:AY2,"准时完成"),49)*15'
    assert repaired > 0
    assert next_document["grid_rows"][0][1] == "第1周"
    assert next_document["grid_rows"][0][49] == "第17周"
    assert next_document["grid_rows"][0][50] == ""
    assert next_document["merged_cells"][-1] == {"row": 0, "col": 49, "rowspan": 1, "colspan": 2}
    assert next_document["column_configs"]["第50课"]["header_background_color"] == "#E2F0D9"
    assert next_document["cell_meta"]["0:1"]["style"] != next_document["cell_meta"]["0:4"]["style"]
    assert next_document["cell_meta"]["1:1"]["style"] != next_document["cell_meta"]["1:4"]["style"]
    assert next_document["cell_meta"]["0:49"]["style"] == next_document["cell_meta"]["0:50"]["style"]
    assert next_document["cell_meta"]["1:49"]["style"] == next_document["cell_meta"]["1:50"]["style"]
    assert next_document["cell_meta"]["2:50"]["style"]["background_color"] == "#D8D8D8"
    assert next_document["grid_rows"][1][50]["link"]["url"] == "https://example.test/lesson/50"

    stable_document, stable_repaired = nianzhu_course_sheets._sync_zen_stage_video_header_layout(
        next_document,
        video_config,
        video_column_indexes,
    )
    assert stable_repaired == 0
    assert stable_document == next_document


def test_zen_stage_header_layout_repairs_fixed_groups_and_prefixed_course_names() -> None:
    columns = [
        "分组", "学号", "姓名", "昵称",
        "考试资格", "视频应返款", "打卡应返款", "总应返款", "已返款", "订单金额", "当前应返款",
        "共学打卡", "共修打卡-随念门", "共修打卡-忏悔门", "共修打卡-崇拜祈祝",
        "佛教概观4", "崇拜祈祝门", "礼仪文化1",
    ]
    document = {
        "schema_version": 1,
        "columns": columns,
        "rows": [],
        "grid_rows": [
            ["用户信息", "", "", "", "返款总计（视频729元 + 打卡210元）", "", "", "", "", "", "", "打卡数据（满210元）", "", "", "", "第3周", "", ""],
            columns,
            [
                "",
                {"value": "考勤返款常见问题解答", "link": {"url": "https://kdocs.cn/l/cmMznDf1i3ye"}},
                "", "", "", "", "",
                {"value": "反馈问题", "link": {"url": "/attendance-feedback"}},
                *([""] * (len(columns) - 8)),
            ],
        ],
        "data_start_row": 3,
        "field_row_index": 1,
        "header_groups": [[{"label": "返款总计（视频702元 + 打卡210元）", "colspan": 32}]],
        "merged_cells": [
            {"row": 0, "col": 0, "rowspan": 1, "colspan": 4},
            {"row": 0, "col": 4, "rowspan": 1, "colspan": 7},
        ],
        "cell_meta": {},
    }
    video_config_document = _sheet_document(
        VIDEO_CONFIG_COLUMNS,
        [
            _row_from_dict(VIDEO_CONFIG_COLUMNS, {
                "lesson_id": 1,
                "lesson_id2": "https://example.test/overview",
                "lesson_name": "d260712禅宗13期一阶-第3周=佛教概观4",
            }),
            _row_from_dict(VIDEO_CONFIG_COLUMNS, {
                "lesson_id": 2,
                "lesson_id2": "https://example.test/practice",
                "lesson_name": "d260712禅宗13期一阶-第3周=法门通论-崇拜祈祝门",
            }),
            _row_from_dict(VIDEO_CONFIG_COLUMNS, {
                "lesson_id": 3,
                "lesson_id2": "https://example.test/ritual",
                "lesson_name": "d260712禅宗13期一阶-第3周=礼仪文化1",
            }),
        ],
    )
    video_config_document["source_meta"] = {"course_name": "修道班13期1阶"}
    video_config = nianzhu_course_sheets._load_video_config(video_config_document)
    video_column_indexes = nianzhu_course_sheets._video_column_indexes_from_columns(video_config, columns)

    next_document, repaired = nianzhu_course_sheets._sync_zen_stage_video_header_layout(
        document,
        video_config,
        video_column_indexes,
    )

    assert repaired > 0
    assert video_column_indexes["2"] == 16
    assert {"row": 0, "col": 11, "rowspan": 1, "colspan": 4} in next_document["merged_cells"]
    assert {"row": 0, "col": 15, "rowspan": 1, "colspan": 3} in next_document["merged_cells"]
    assert next_document["cell_meta"]["0:11"]["style"]["background_color"] == "#C55A9B"
    assert next_document["cell_meta"]["1:14"]["style"]["background_color"] == "#E4B7D9"
    assert next_document["cell_meta"]["2:14"]["style"]["background_color"] == "#D8D8D8"
    assert next_document["cell_meta"]["2:1"]["style"] == nianzhu_course_sheets.ZEN_STAGE_FAQ_LINK_STYLE
    assert next_document["cell_meta"]["2:7"]["style"] == nianzhu_course_sheets.ZEN_STAGE_FEEDBACK_LINK_STYLE
    assert next_document["cell_meta"]["1:16"]["style"]["background_color"] == "#CCC0DA"
    assert next_document["cell_meta"]["0:11"]["style"] != next_document["cell_meta"]["0:15"]["style"]
    assert next_document["cell_meta"]["1:14"]["style"] != next_document["cell_meta"]["1:15"]["style"]
    assert next_document["grid_rows"][1][16]["link"]["url"] == "https://example.test/practice"
    assert next_document["header_groups"][0][4]["label"] == "返款总计（视频729元 + 打卡210元）"

    stable_document, stable_repaired = nianzhu_course_sheets._sync_zen_stage_video_header_layout(
        next_document,
        video_config,
        video_column_indexes,
    )
    assert stable_repaired == 0
    assert stable_document == next_document


def test_attendance_fixed_header_extends_user_group_through_hidden_identity_columns() -> None:
    columns = [
        "分组", "学号", "姓名", "昵称", "用户ID", "禅客",
        "完成视频数", "视频应返款",
    ]
    document = {
        "schema_version": 1,
        "columns": columns,
        "rows": [],
        "grid_rows": [
            ["用户信息", "", "", "", "", "", "返款总计", ""],
            columns,
            ["", "", "", "", "", "", "", ""],
        ],
        "data_start_row": 3,
        "field_row_index": 1,
        "header_groups": [[
            {"label": value, "colspan": 1}
            for value in ["用户信息", "", "", "", "", "", "返款总计", ""]
        ]],
        "merged_cells": [
            {"row": 0, "col": 0, "rowspan": 1, "colspan": 4},
        ],
        "cell_meta": {},
    }

    next_document, repaired = nianzhu_course_sheets._sync_zen_stage_video_header_layout(
        document,
        [],
        {},
    )

    assert repaired > 0
    assert {"row": 0, "col": 0, "rowspan": 1, "colspan": 6} in next_document["merged_cells"]
    assert {"row": 0, "col": 0, "rowspan": 1, "colspan": 4} not in next_document["merged_cells"]
    assert next_document["cell_meta"]["0:4"]["style"]["background_color"] == "#5B8FC9"
    assert next_document["cell_meta"]["0:5"]["style"]["background_color"] == "#5B8FC9"
    assert next_document["cell_meta"]["1:4"]["style"]["background_color"] == "#D9EAF7"
    assert next_document["cell_meta"]["1:5"]["style"]["background_color"] == "#D9EAF7"

    stable_document, stable_repaired = nianzhu_course_sheets._sync_zen_stage_video_header_layout(
        next_document,
        [],
        {},
    )
    assert stable_repaired == 0
    assert stable_document == next_document


def test_attendance_fixed_header_groups_plain_shared_practice_clockin_column() -> None:
    columns = ["分组", "学号", "姓名", "昵称", "共学打卡", "共修打卡", "佛教史1"]
    document = {
        "schema_version": 1,
        "columns": columns,
        "rows": [],
        "grid_rows": [
            ["用户信息", "", "", "", "打卡数据", "", "第1周"],
            columns,
            ["", "", "", "", "累计共学打卡次数", "累计共修打卡周数", ""],
        ],
        "data_start_row": 3,
        "field_row_index": 1,
        "header_groups": [],
        "merged_cells": [
            {"row": 0, "col": 0, "rowspan": 1, "colspan": 4},
            {"row": 0, "col": 4, "rowspan": 1, "colspan": 1},
        ],
        "cell_meta": {},
    }

    next_document, repaired = nianzhu_course_sheets._sync_zen_stage_fixed_header_layout(document)

    assert repaired > 0
    assert {"row": 0, "col": 4, "rowspan": 1, "colspan": 2} in next_document["merged_cells"]
    assert {"row": 0, "col": 4, "rowspan": 1, "colspan": 1} not in next_document["merged_cells"]
    assert next_document["grid_rows"][0][4] == "打卡数据"
    assert next_document["grid_rows"][0][5] == ""
    assert next_document["cell_meta"]["0:4"]["style"] == nianzhu_course_sheets.ZEN_STAGE_CLOCKIN_GROUP_STYLE
    assert next_document["cell_meta"]["0:5"]["style"] == nianzhu_course_sheets.ZEN_STAGE_CLOCKIN_GROUP_STYLE
    assert next_document["cell_meta"]["1:4"]["style"] == nianzhu_course_sheets.ZEN_STAGE_CLOCKIN_FIELD_STYLE
    assert next_document["cell_meta"]["1:5"]["style"] == nianzhu_course_sheets.ZEN_STAGE_CLOCKIN_FIELD_STYLE

    stable_document, stable_repaired = nianzhu_course_sheets._sync_zen_stage_fixed_header_layout(next_document)
    assert stable_repaired == 0
    assert stable_document == next_document


def _create_nianzhu_workbook(session: Session) -> None:
    workbook = WorkbookDocument(
        id="7",
        numeric_id=7,
        title="20250106念住闯关",
        owner_user_id=2,
    )
    columns = [
        "学号",
        "姓名",
        "用户ID",
        "优秀学员评分",
        "完成视频数",
        "视频应返款",
        "打卡应返款",
        "打卡数",
        "第01课",
        "第12课",
        "第2届答疑",
        "追踪分组",
        "追踪状态",
        "冻结时间",
        "规则版本",
    ]
    rows = [
        ["101", "甲", "u1", 0, 0, 0, 0, 5, "1遍/100%", "3遍/200%", "参加/100%", "历史组", "追踪中", "", "当前规则"],
        ["102", "乙", "u2", 0, 0, 0, 0, 10, "1遍/100%", "", "", "当前组", "已冻结", "2026-05-16 14:07:07", "当前规则"],
    ]
    attendance = SheetDocument(
        id="21",
        numeric_id=21,
        scope="notes",
        owner_type="course_workbook",
        owner_key="20250106-nianzhu-chuangguan",
        sheet_key="attendance",
        title="考勤表",
        document_json=_sheet_document(columns, rows),
        owner_user_id=2,
    )
    registration = SheetDocument(
        id="20",
        numeric_id=20,
        scope="notes",
        owner_type="course_workbook",
        owner_key="20250106-nianzhu-chuangguan",
        sheet_key="registration",
        title="报名表",
        document_json=_sheet_document(
            ["序号", "姓名", "用户ID", "关联用户ID"],
            [
                ["101", "甲", "u1", ""],
                ["102", "乙", "u2", ""],
            ],
        ),
        owner_user_id=2,
    )
    session.add(workbook)
    session.add(attendance)
    session.add(registration)
    session.add(WorkbookSheetLink(workbook_id="7", sheet_id="21", order_index=10))
    session.commit()


def _find_sheet(session: Session, sheet_key: str) -> SheetDocument:
    sheet = session.exec(select(SheetDocument).where(SheetDocument.sheet_key == sheet_key)).first()
    assert sheet is not None
    return sheet


def _row_from_dict(columns: list[str], row: dict[str, object]) -> list[object]:
    return [row.get(column, "") for column in columns]


def test_parse_lesson_data_export_rows_converts_duration_text(tmp_path) -> None:
    export_file = tmp_path / "lesson.csv"
    export_file.write_text(
        "用户ID,用户昵称,备注名,状态,累计观看时长,播放进度,上次播放时间,完成时间\n"
        "u1,昵称甲,备注乙,已参与,1小时2分钟3秒,104%,--,--\n",
        encoding="utf-8",
    )

    rows = nianzhu_course_sheets._parse_lesson_data_export_rows(
        export_file,
        lesson_id=3,
        update_time=datetime(2026, 5, 20, 8, 0, 0),
    )

    assert rows == [
        {
            "lesson_id": 3,
            "update_time": "2026-05-20 08:00:00",
            "user_id2": "u1",
            "nickname": "昵称甲",
            "remark_nm": "备注乙",
            "state": "已参与",
            "progress": 104,
            "cum_seconds": 3723,
            "last_play_time": "",
            "finish_time": "",
        }
    ]


def test_nianzhu_video_aggregation_matches_legacy_challenge_semantics() -> None:
    video_config_document = _sheet_document(
        VIDEO_CONFIG_COLUMNS,
        [
            _row_from_dict(
                VIDEO_CONFIG_COLUMNS,
                {"lesson_id": 1, "lesson_name": "第01课 测试课程", "video_duration": 100},
            ),
        ],
    )
    video_config_document["source_meta"] = {"course_name": "d250106念住闯关"}
    video_config = nianzhu_course_sheets._load_video_config(video_config_document)
    video_data_document = _sheet_document(
        VIDEO_DATA_COLUMNS,
        [
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 1,
                    "user_id2": "u1",
                    "cum_seconds": 180,
                    "progress": 1,
                    "update_time": "2026-05-20 08:00:00",
                    "lesson_id": 1,
                },
            ),
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 2,
                    "user_id2": "u1",
                    "cum_seconds": 1,
                    "progress": 999,
                    "update_time": "2026-05-21 08:00:00",
                    "lesson_id": 1,
                },
            ),
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {"lesson_data_id": 3, "user_id2": "u2", "cum_seconds": 89, "lesson_id": 1},
            ),
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {"lesson_data_id": 4, "user_id2": "u3", "cum_seconds": 250, "lesson_id": 1},
            ),
        ],
    )

    video_data = nianzhu_course_sheets._load_video_data(video_data_document, video_config)

    assert video_data[("user:u1", "1")] == "2遍/180%"
    assert video_data[("user:u2", "1")] == "学习中/89%"
    assert video_data[("user:u3", "1")] == "3遍/250%"


def test_video_aggregation_matches_legacy_regular_course_semantics() -> None:
    video_config_document = _sheet_document(
        VIDEO_CONFIG_COLUMNS,
        [
            _row_from_dict(
                VIDEO_CONFIG_COLUMNS,
                {
                    "lesson_id": 1,
                    "start_date": "2026-05-20 08:00:00",
                    "lesson_name": "第01课 普通课程",
                    "video_duration": 100,
                },
            ),
        ],
    )
    video_config = nianzhu_course_sheets._load_video_config(video_config_document)
    video_data_document = _sheet_document(
        VIDEO_DATA_COLUMNS,
        [
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 1,
                    "user_id2": "u1",
                    "studio_seconds": 10,
                    "playback_seconds": 40,
                    "update_time": "2026-05-20 08:10:00",
                    "lesson_id": 1,
                },
            ),
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 2,
                    "user_id2": "u1",
                    "studio_seconds": 60,
                    "playback_seconds": 40,
                    "update_time": "2026-05-20 08:20:00",
                    "lesson_id": 1,
                },
            ),
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 3,
                    "user_id2": "u2",
                    "studio_seconds": 10,
                    "playback_seconds": 20,
                    "update_time": "2026-05-20 08:10:00",
                    "lesson_id": 1,
                },
            ),
        ],
    )

    video_data = nianzhu_course_sheets._load_video_data(video_data_document, video_config)
    compacted_document, summary = nianzhu_course_sheets._compact_nianzhu_video_data_document(
        video_data_document,
        video_config,
    )

    assert video_data[("user:u1", "1")] == "当堂完成/100%"
    assert video_data[("user:u2", "1")] == "学习中/30%"
    assert summary["video_data_rows_before"] == 3
    assert summary["video_data_rows_after"] == 2
    rows = [dict(zip(VIDEO_DATA_COLUMNS, row)) for row in compacted_document["rows"]]
    assert [row["lesson_data_id"] for row in rows] == [1, 3]
    assert rows[0]["cum_seconds"] == 100
    assert rows[0]["studio_seconds"] == 60
    assert rows[0]["playback_seconds"] == 40
    assert rows[0]["progress"] == 100


def test_video_aggregation_matches_legacy_zen_course_semantics() -> None:
    video_config_document = _sheet_document(
        VIDEO_CONFIG_COLUMNS,
        [
            _row_from_dict(
                VIDEO_CONFIG_COLUMNS,
                {
                    "lesson_id": 1,
                    "start_date": "2026-05-01 00:00:00",
                    "lesson_name": "禅宗第01课",
                    "video_duration": 3600,
                },
            ),
        ],
    )
    video_config = nianzhu_course_sheets._load_video_config(video_config_document)
    video_data_document = _sheet_document(
        VIDEO_DATA_COLUMNS,
        [
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 1,
                    "user_id2": "u1",
                    "cum_seconds": 600,
                    "progress": 50,
                    "update_time": "2026-05-02 08:00:00",
                    "lesson_id": 1,
                },
            ),
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 2,
                    "user_id2": "u1",
                    "cum_seconds": 1800,
                    "progress": 20,
                    "update_time": "2026-05-09 13:00:00",
                    "lesson_id": 1,
                },
            ),
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 3,
                    "user_id2": "u2",
                    "cum_seconds": 600,
                    "progress": 18.94,
                    "update_time": "2026-05-02 08:00:00",
                    "lesson_id": 1,
                },
            ),
        ],
    )

    video_data = nianzhu_course_sheets._load_video_data(video_data_document, video_config)
    compacted_document, summary = nianzhu_course_sheets._compact_nianzhu_video_data_document(
        video_data_document,
        video_config,
    )

    assert video_data[("user:u1", "1")] == "进度50%"
    assert video_data[("user:u2", "1")] == "进度19%"
    assert summary["video_data_rows_before"] == 3
    assert summary["video_data_rows_after"] == 2
    rows = [dict(zip(VIDEO_DATA_COLUMNS, row)) for row in compacted_document["rows"]]
    assert [row["lesson_data_id"] for row in rows] == [1, 3]
    assert rows[0]["progress"] == 50
    assert rows[0]["cum_seconds"] == 600

    # 观看满 30 分钟仍然只是局部进度，压缩后不能被误判为完成。
    compacted_video_data = nianzhu_course_sheets._load_video_data(compacted_document, video_config)
    assert compacted_video_data[("user:u1", "1")] == "进度50%"


def test_zen_video_completion_uses_optimistic_download_interval_lower_bound() -> None:
    video_config_document = _sheet_document(
        VIDEO_CONFIG_COLUMNS,
        [
            _row_from_dict(
                VIDEO_CONFIG_COLUMNS,
                {
                    "lesson_id": 1,
                    "start_date": "2026-05-01 00:00:00",
                    "lesson_name": "禅宗第01课",
                    "video_duration": 3600,
                },
            ),
        ],
    )
    video_config = nianzhu_course_sheets._load_video_config(video_config_document)
    video_data_document = _sheet_document(
        VIDEO_DATA_COLUMNS,
        [
            # 第一次下载时已经完成：即使第 4 天才下载，也按开课时完成。
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 1,
                    "user_id2": "first_complete",
                    "cum_seconds": 1800,
                    "progress": 100,
                    "update_time": "2026-05-04 12:00:00",
                    "lesson_id": 1,
                },
            ),
            # 上次下载已超过截止线且尚未完成：之后完成仍应判延期。
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 2,
                    "user_id2": "late_transition",
                    "cum_seconds": 600,
                    "progress": 50,
                    "update_time": "2026-05-10 12:00:00",
                    "lesson_id": 1,
                },
            ),
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 3,
                    "user_id2": "late_transition",
                    "cum_seconds": 1800,
                    "progress": 100,
                    "update_time": "2026-05-18 12:00:00",
                    "lesson_id": 1,
                },
            ),
            # 平台有明确完成时间时，明确证据优先于下载区间推定。
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 4,
                    "user_id2": "exact_finish",
                    "cum_seconds": 1800,
                    "progress": 100,
                    "update_time": "2026-05-20 12:00:00",
                    "finish_time": "2026-05-03 09:00:00",
                    "lesson_id": 1,
                },
            ),
        ],
    )

    video_data = nianzhu_course_sheets._load_video_data(video_data_document, video_config)
    compacted_document, _summary = nianzhu_course_sheets._compact_nianzhu_video_data_document(
        video_data_document,
        video_config,
    )

    assert video_data[("user:first_complete", "1")] == "准时完成"
    assert video_data[("user:late_transition", "1")] == "延1周完成"
    assert video_data[("user:exact_finish", "1")] == "准时完成"
    rows = [dict(zip(VIDEO_DATA_COLUMNS, row)) for row in compacted_document["rows"]]
    rows_by_user = {row["user_id2"]: row for row in rows}
    assert rows_by_user["first_complete"]["finish_time"] == "2026-05-01 00:00:00"
    assert rows_by_user["late_transition"]["finish_time"] == "2026-05-10 12:00:00"
    assert rows_by_user["exact_finish"]["finish_time"] == "2026-05-03 09:00:00"


def test_nianzhu_step2_compacts_video_data_source_rows(session: Session) -> None:
    _create_nianzhu_workbook(session)
    materialize_nianzhu_course_sheets(session, replace=False)

    video_config = _find_sheet(session, VIDEO_CONFIG_SHEET_KEY)
    video_config.document_json = _sheet_document(
        VIDEO_CONFIG_COLUMNS,
        [
            _row_from_dict(
                VIDEO_CONFIG_COLUMNS,
                {"lesson_id": 1, "lesson_name": "第01课 测试课程", "video_duration": 100},
            ),
        ],
    )
    video_config.document_json["source_meta"] = {"course_name": "d250106念住闯关"}
    video_data = _find_sheet(session, VIDEO_DATA_SHEET_KEY)
    video_data.document_json = _sheet_document(
        VIDEO_DATA_COLUMNS,
        [
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 1,
                    "user_id2": "u1",
                    "cum_seconds": 180,
                    "progress": 1,
                    "update_time": "2026-05-20 08:00:00",
                    "lesson_id": 1,
                },
            ),
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 2,
                    "user_id2": "u1",
                    "cum_seconds": 1,
                    "progress": 999,
                    "update_time": "2026-05-21 08:00:00",
                    "lesson_id": 1,
                },
            ),
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {"lesson_data_id": 3, "user_id2": "u2", "cum_seconds": 89, "lesson_id": 1},
            ),
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {"lesson_data_id": 4, "cum_seconds": 300, "lesson_id": 1},
            ),
        ],
    )
    session.add(video_config)
    session.add(video_data)
    session.commit()

    summary = compact_nianzhu_course_sheet_step2(session)
    session.commit()

    assert summary["changed"] is True
    assert summary["video_data_rows_before"] == 4
    assert summary["video_data_rows_after"] == 3
    assert summary["video_data_removed_rows"] == 1
    assert summary["video_data_preserved_unusable_rows"] == 1
    assert summary["video_data_user_lesson_pairs"] == 2

    session.refresh(video_data)
    rows = [dict(zip(VIDEO_DATA_COLUMNS, row)) for row in video_data.document_json["rows"]]
    assert [row["lesson_data_id"] for row in rows] == [1, 3, 4]
    assert rows[0]["user_id2"] == "u1"
    assert rows[0]["cum_seconds"] == 180
    assert rows[0]["progress"] == 1
    assert rows[1]["user_id2"] == "u2"
    assert rows[1]["progress"] == ""
    assert rows[2]["user_id2"] == ""
    assert rows[2]["cum_seconds"] == 300


def test_nianzhu_clockin_aggregation_matches_legacy_dedup_semantics() -> None:
    rows = [
        {"clockin_data_id": 1, "user_id2": "u1", "clockin_name": "打卡数", "task_date": "2026-05-20", "update_title": "第1天"},
        {"clockin_data_id": 2, "user_id2": "u1", "clockin_name": "打卡数", "task_date": "2026-05-20", "update_title": "第1天"},
        {"clockin_data_id": 3, "user_id2": "u1", "clockin_name": "打卡数", "publish_time": "2026-05-21 08:00:00", "update_title": "第2天"},
        {"clockin_data_id": 4, "user_id2": "u1", "clockin_name": "打卡数", "publish_time": "2026-05-21 12:00:00", "update_title": "第2天"},
        {"clockin_data_id": 5, "user_id2": "u1", "clockin_name": "打卡数", "task_date": "2026-05-22", "update_title": "测试-忽略"},
        {"clockin_data_id": 6, "user_id2": "u1", "clockin_name": "打卡数", "update_title": "自由"},
        {"clockin_data_id": 7, "user_id2": "u1", "clockin_name": "打卡数", "update_title": "自由"},
        {"clockin_data_id": 8, "user_id2": "u2", "clockin_name": "打卡数", "task_date": "2026-05-20", "update_title": "第1天"},
    ]
    document = _sheet_document(CLOCKIN_DATA_COLUMNS, [_row_from_dict(CLOCKIN_DATA_COLUMNS, row) for row in rows])

    clockin_data = nianzhu_course_sheets._load_clockin_data(document)

    assert clockin_data[("user:u1", "打卡数")] == 4
    assert clockin_data[("user:u2", "打卡数")] == 1


def test_dynamic_clockin_plugin_is_explicit_and_course_scoped() -> None:
    plugin = nianzhu_course_sheets._resolve_dynamic_clockin_supplement_plugin(
        nianzhu_course_sheets.DYNAMIC_CLOCKIN_PLUGIN_ZEN46_STAGE5_20260621,
        course_name="20260301禅宗46期五阶",
    )

    assert plugin is not None
    assert plugin.community_id == "c_69a3a337724fc_TJoXKy8E2351"
    assert nianzhu_course_sheets._resolve_dynamic_clockin_supplement_plugin("", course_name="20260301禅宗46期五阶") is None
    with pytest.raises(ValueError, match="只能用于"):
        nianzhu_course_sheets._resolve_dynamic_clockin_supplement_plugin(
            nianzhu_course_sheets.DYNAMIC_CLOCKIN_PLUGIN_ZEN46_STAGE5_20260621,
            course_name="20260503禅宗9期4.5阶",
        )


def test_dynamic_clockin_plugin_builds_rows_after_start_and_deduplicates() -> None:
    plugin = nianzhu_course_sheets._resolve_dynamic_clockin_supplement_plugin(
        nianzhu_course_sheets.DYNAMIC_CLOCKIN_PLUGIN_ZEN46_STAGE5_20260621,
        course_name="d260301禅宗46期五阶",
    )
    assert plugin is not None
    clockin_config_rows = [
        {"clockin_id": 1, "name": "共学打卡"},
        {"clockin_id": 2, "name": "共修打卡"},
    ]
    existing_rows = [
        {"user_id2": "u1", "clockin_name": "共学打卡", "publish_time": "2026-06-21 08:00:00"},
        {"user_id2": "u2", "clockin_name": "共修打卡", "task_date": "2026-06-21"},
    ]
    feed_rows = [
        {
            "id": "before",
            "apply_type": "calendar_clock",
            "user_id": "u0",
            "nick_name": "旧数据",
            "created_at": "2026-06-20 23:59:59",
            "content": {"text": "共修补打卡"},
        },
        {
            "id": "formal-duplicate-gongxue",
            "apply_type": "calendar_clock",
            "user_id": "u1",
            "nick_name": "重复共学",
            "created_at": "2026-06-21 09:00:00",
            "content": {"text": "打卡"},
        },
        {
            "id": "formal-duplicate-gongxiu",
            "apply_type": "calendar_clock",
            "user_id": "u2",
            "nick_name": "重复共修",
            "created_at": "2026-06-21 09:00:00",
            "content": {"text": "共修补打卡"},
        },
        {
            "id": "new-gongxiu",
            "apply_type": "calendar_clock",
            "user_id": "u3",
            "nick_name": "新增共修",
            "group_name": "46期",
            "created_at": "2026-06-21 10:00:00",
            "content": {"text": "今天共修补打卡"},
            "record_num": 3,
            "zan_num": 1,
            "comment_count": 0,
            "extend_field1": "ac_69a45d419ee00_PGQ5wYTC",
        },
        {
            "id": "new-gongxue",
            "apply_type": "text",
            "user_id": "u4",
            "nick_name": "新增共学",
            "created_at": "2026-06-21 11:00:00",
            "content": {"text": "补打卡"},
            "extend_field1": "ac_69a45d0234f97_rHFfClYC",
        },
        {
            "id": "dynamic-duplicate-gongxue",
            "apply_type": "calendar_clock",
            "user_id": "u4",
            "nick_name": "新增共学",
            "created_at": "2026-06-21 12:00:00",
            "content": {"text": "第二条也按共学算，但同日重复"},
        },
    ]

    rows, summary = nianzhu_course_sheets._build_dynamic_clockin_supplement_rows(
        feed_rows,
        plugin=plugin,
        clockin_config_rows=clockin_config_rows,
        existing_clockin_rows=existing_rows,
        course_name="d260301禅宗46期五阶",
    )

    assert summary["candidate_rows"] == 5
    assert summary["inserted_rows"] == 2
    assert summary["skipped_before_start_rows"] == 1
    assert summary["skipped_duplicate_rows"] == 3
    assert summary["inserted_by_clockin_name"] == {"共修打卡": 1, "共学打卡": 1}
    assert [(row["user_id2"], row["clockin_name"], row["task_date"]) for row in rows] == [
        ("u3", "共修打卡", "2026-06-21"),
        ("u4", "共学打卡", "2026-06-21"),
    ]
    assert rows[0]["update_content"] == "今天共修补打卡"
    assert rows[0]["extra"]["source"] == "dynamic_feed_supplement"


def test_zen_stage_clockin_title_allowlist_matches_legacy_positive_titles() -> None:
    titles = nianzhu_course_sheets._clockin_title_allowlist_for_course("d260601第47届觉观")

    assert titles is not None
    assert "【打卡】中心教室-1" in titles
    assert "【打卡】第47届中心教室-1" in titles
    assert "【第47届中心教室】—第1课打卡" in titles
    assert "【打卡】中心教室-测试1" not in titles


def test_zen_stage_clockin_aggregation_keeps_only_positive_course_titles() -> None:
    rows = [
        {
            "clockin_data_id": 1,
            "user_id2": "u1",
            "clockin_name": "打卡数",
            "task_date": "2026-06-01",
            "update_title": "【打卡】中心教室-1",
        },
        {
            "clockin_data_id": 2,
            "user_id2": "u1",
            "clockin_name": "打卡数",
            "task_date": "2026-06-02",
            "update_title": "【打卡】中心教室-1",
        },
        {
            "clockin_data_id": 3,
            "user_id2": "u1",
            "clockin_name": "打卡数",
            "task_date": "2026-06-01",
            "update_title": "【打卡】中心教室-测试1",
        },
        {
            "clockin_data_id": 4,
            "user_id2": "u1",
            "clockin_name": "打卡数",
            "task_date": "2026-06-01",
            "update_title": "【打卡】中心教室-测试2",
        },
        {
            "clockin_data_id": 5,
            "user_id2": "u1",
            "clockin_name": "打卡数",
            "task_date": "2026-06-01",
            "update_title": "",
        },
        {
            "clockin_data_id": 6,
            "user_id2": "u2",
            "clockin_name": "打卡数",
            "task_date": "2026-06-01",
            "update_title": "【打卡】第47届中心教室-1",
        },
    ]
    document = _sheet_document(CLOCKIN_DATA_COLUMNS, [_row_from_dict(CLOCKIN_DATA_COLUMNS, row) for row in rows])
    allowed_titles = nianzhu_course_sheets._clockin_title_allowlist_for_course("d260601第47届觉观")

    grouped_keys, numeric_counts = nianzhu_course_sheets._collect_clockin_data(
        document,
        allowed_titles=allowed_titles,
    )
    clockin_data = {
        key: numeric_counts.get(key, 0.0) + len(clockin_keys)
        for key, clockin_keys in grouped_keys.items()
    }

    assert clockin_data[("user:u1", "打卡数")] == 1
    assert clockin_data[("user:u2", "打卡数")] == 1


def test_nianzhu_stage_clockin_title_allowlist_matches_legacy_positive_titles() -> None:
    titles = nianzhu_course_sheets._clockin_title_allowlist_for_course("d260601第41届念住")

    assert titles is not None
    assert "念住学修日志-01" in titles
    assert "念住学修日志-21" in titles
    assert "第41届念住学修日志-01" in titles
    assert "第01天" in titles
    assert "第24天" in titles
    assert "学写学修日志" not in titles
    assert "立下学修目标" not in titles
    assert nianzhu_course_sheets._clockin_title_allowlist_for_course("d250106念住闯关") is None


def test_nianzhu_stage_clockin_aggregation_keeps_only_positive_course_titles() -> None:
    rows = [
        {
            "clockin_data_id": 1,
            "user_id2": "u1",
            "clockin_name": "打卡数",
            "task_date": "2026-06-01",
            "update_title": "念住学修日志-01",
        },
        {
            "clockin_data_id": 2,
            "user_id2": "u1",
            "clockin_name": "打卡数",
            "task_date": "2026-06-02",
            "update_title": "念住学修日志-01",
        },
        {
            "clockin_data_id": 3,
            "user_id2": "u1",
            "clockin_name": "打卡数",
            "task_date": "2026-06-03",
            "update_title": "第01天：身念处-分部位循身观",
        },
        {
            "clockin_data_id": 4,
            "user_id2": "u1",
            "clockin_name": "打卡数",
            "task_date": "2026-06-04",
            "update_title": "第24天:总结",
        },
        {
            "clockin_data_id": 5,
            "user_id2": "u1",
            "clockin_name": "打卡数",
            "task_date": "2026-06-01",
            "update_title": "学写学修日志",
        },
        {
            "clockin_data_id": 6,
            "user_id2": "u1",
            "clockin_name": "打卡数",
            "task_date": "2026-06-01",
            "update_title": "立下学修目标",
        },
        {
            "clockin_data_id": 7,
            "user_id2": "u1",
            "clockin_name": "打卡数",
            "task_date": "2026-06-01",
            "update_title": "第25天：超出课程范围",
        },
        {
            "clockin_data_id": 8,
            "user_id2": "u2",
            "clockin_name": "打卡数",
            "task_date": "2026-06-01",
            "update_title": "第41届念住学修日志-01",
        },
    ]
    document = _sheet_document(CLOCKIN_DATA_COLUMNS, [_row_from_dict(CLOCKIN_DATA_COLUMNS, row) for row in rows])
    allowed_titles = nianzhu_course_sheets._clockin_title_allowlist_for_course("d260601第41届念住")

    grouped_keys, numeric_counts = nianzhu_course_sheets._collect_clockin_data(
        document,
        allowed_titles=allowed_titles,
    )
    clockin_data = {
        key: numeric_counts.get(key, 0.0) + len(clockin_keys)
        for key, clockin_keys in grouped_keys.items()
    }

    assert clockin_data[("user:u1", "打卡数")] == 3
    assert clockin_data[("user:u2", "打卡数")] == 1


def test_materialize_nianzhu_course_sheets_splits_attendance_storage(session: Session) -> None:
    _create_nianzhu_workbook(session)

    summary = materialize_nianzhu_course_sheets(session, replace=False)
    session.commit()

    assert summary["changed"] is True
    assert {item["sheet_key"] for item in summary["sheets"]} == {
        "video_config",
        "video_data",
        "clockin_config",
        "clockin_data",
    }
    assert summary["sheets"][0]["rows"] == 3

    video_config = _find_sheet(session, VIDEO_CONFIG_SHEET_KEY)
    video_data = _find_sheet(session, VIDEO_DATA_SHEET_KEY)
    clockin_config = _find_sheet(session, CLOCKIN_CONFIG_SHEET_KEY)
    clockin_data = _find_sheet(session, CLOCKIN_DATA_SHEET_KEY)
    assert video_config.document_json["columns"] == VIDEO_CONFIG_COLUMNS
    assert video_data.document_json["columns"] == VIDEO_DATA_COLUMNS
    assert clockin_config.document_json["columns"] == CLOCKIN_CONFIG_COLUMNS
    assert clockin_data.document_json["columns"] == CLOCKIN_DATA_COLUMNS
    assert len(video_config.document_json["rows"]) == 3
    assert len(video_data.document_json["rows"]) == 4
    assert len(clockin_config.document_json["rows"]) == 1
    assert len(clockin_data.document_json["rows"]) == 15

    links = session.exec(select(WorkbookSheetLink).order_by(WorkbookSheetLink.order_index)).all()
    assert [link.order_index for link in links] == [10, 30, 40, 50, 60]


def test_rebuild_nianzhu_attendance_uses_course_sheets_as_source(session: Session) -> None:
    _create_nianzhu_workbook(session)
    materialize_nianzhu_course_sheets(session, replace=False)
    session.commit()

    video_data = _find_sheet(session, VIDEO_DATA_SHEET_KEY)
    video_document = copy.deepcopy(video_data.document_json)
    for row in video_document["rows"]:
        if row[1] == "u1" and row[15] == 1:
            row[10] = "学习中"
            row[11] = 0
    video_data.document_json = video_document
    clockin_data = _find_sheet(session, CLOCKIN_DATA_SHEET_KEY)
    clockin_document = copy.deepcopy(clockin_data.document_json)
    next_clockin_data_id = max(row[0] for row in clockin_document["rows"])
    u1_rows = [row for row in clockin_document["rows"] if row[1] == "u1"]
    for source_row in u1_rows[:5]:
        next_clockin_data_id += 1
        new_row = list(source_row)
        new_row[0] = next_clockin_data_id
        clockin_document["rows"].append(new_row)
    clockin_data.document_json = clockin_document
    session.add(video_data)
    session.add(clockin_data)
    session.commit()

    summary = rebuild_nianzhu_attendance_from_course_sheets(session, active_only=True)
    session.commit()

    assert summary["updated_rows"] == 1
    assert summary["skipped_rows"] == 1

    attendance = _find_sheet(session, "attendance")
    rows = attendance.document_json["rows"]
    assert rows[0][8] == "1遍/100%"
    assert rows[0][3] == 2
    assert rows[0][4] == '=COUNTIF(I2:J2,"*遍*")'
    assert rows[0][5] == '=COUNTIF(I2:J2,"*遍*")*20'
    assert rows[0][6] == 150
    assert rows[0][7] == 10
    assert rows[1][8] == "1遍/100%"


def test_rebuild_nianzhu_attendance_can_use_40th_timed_text_refund_rules(session: Session) -> None:
    _create_nianzhu_workbook(session)
    materialize_nianzhu_course_sheets(session, replace=False)

    attendance = _find_sheet(session, "attendance")
    attendance.document_json = _sheet_document(
        ["姓名", "用户ID", "完成视频数", "视频应返款", "打卡应返款", "打卡数", "第01课", "第02课"],
        [["甲", "u1", "", "", "", "", "", ""]],
    )

    video_config = _find_sheet(session, VIDEO_CONFIG_SHEET_KEY)
    video_config.document_json = _sheet_document(
        VIDEO_CONFIG_COLUMNS,
        [
            _row_from_dict(VIDEO_CONFIG_COLUMNS, {"lesson_id": 1, "lesson_name": "第01课", "video_duration": 3600}),
            _row_from_dict(VIDEO_CONFIG_COLUMNS, {"lesson_id": 2, "lesson_name": "第02课", "video_duration": 3600}),
        ],
    )
    video_config.document_json["source_meta"] = {
        "course_name": "d260601第41届念住",
        "video_refund_rule_mode": "timed_text",
    }

    video_data = _find_sheet(session, VIDEO_DATA_SHEET_KEY)
    video_data.document_json = _sheet_document(
        VIDEO_DATA_COLUMNS,
        [
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 1,
                    "user_id2": "u1",
                    "studio_seconds": 3132,
                    "cum_seconds": 3132,
                    "study_state": "已完成",
                    "progress": 87,
                    "lesson_id": 1,
                },
            ),
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 2,
                    "user_id2": "u1",
                    "studio_seconds": 3528,
                    "cum_seconds": 3528,
                    "study_state": "已完成",
                    "progress": 98,
                    "lesson_id": 2,
                },
            ),
        ],
    )

    clockin_data = _find_sheet(session, CLOCKIN_DATA_SHEET_KEY)
    clockin_data.document_json = _sheet_document(CLOCKIN_DATA_COLUMNS, [])
    session.add(attendance)
    session.add(video_config)
    session.add(video_data)
    session.add(clockin_data)
    session.commit()

    summary = rebuild_nianzhu_attendance_from_course_sheets(session, attendance_sheet_id=21)
    session.commit()

    row = attendance.document_json["rows"][0]
    assert summary["video_refund_total"] == 40
    assert row[2] == '=COUNTIF(G2:H2,"*完成*")+COUNTIF(G2:H2,"*回放*")'
    assert row[3] == 40
    assert row[6] == "当堂完成/87%"
    assert row[7] == "当堂完成/98%"


def test_rebuild_nianzhu_attendance_can_use_custom_timed_text_refund_rules(session: Session) -> None:
    _create_nianzhu_workbook(session)
    materialize_nianzhu_course_sheets(session, replace=False)

    attendance = _find_sheet(session, "attendance")
    attendance.document_json = _sheet_document(
        ["姓名", "用户ID", "完成视频数", "视频应返款", "打卡应返款", "打卡数", "第01课", "第02课"],
        [["甲", "u1", "", "", "", "", "", ""]],
    )

    video_config = _find_sheet(session, VIDEO_CONFIG_SHEET_KEY)
    video_config.document_json = _sheet_document(
        VIDEO_CONFIG_COLUMNS,
        [
            _row_from_dict(VIDEO_CONFIG_COLUMNS, {"lesson_id": 1, "lesson_name": "第01课", "video_duration": 3600}),
            _row_from_dict(VIDEO_CONFIG_COLUMNS, {"lesson_id": 2, "lesson_name": "第02课", "video_duration": 3600}),
        ],
    )
    video_config.document_json["source_meta"] = {
        "course_name": "d260601第47届觉观",
        "video_refund_rule_mode": "timed_text",
        "timed_video_rules": {"当堂": 19, "第1天": 14, "第2天": 9, "第3天": 4, "回放": 0},
    }

    video_data = _find_sheet(session, VIDEO_DATA_SHEET_KEY)
    video_data.document_json = _sheet_document(
        VIDEO_DATA_COLUMNS,
        [
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 1,
                    "user_id2": "u1",
                    "studio_seconds": 3132,
                    "cum_seconds": 3132,
                    "study_state": "已完成",
                    "progress": 87,
                    "lesson_id": 1,
                },
            ),
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 2,
                    "user_id2": "u1",
                    "studio_seconds": 3528,
                    "cum_seconds": 3528,
                    "study_state": "已完成",
                    "progress": 98,
                    "lesson_id": 2,
                },
            ),
        ],
    )

    clockin_data = _find_sheet(session, CLOCKIN_DATA_SHEET_KEY)
    clockin_data.document_json = _sheet_document(CLOCKIN_DATA_COLUMNS, [])
    session.add(attendance)
    session.add(video_config)
    session.add(video_data)
    session.add(clockin_data)
    session.commit()

    summary = rebuild_nianzhu_attendance_from_course_sheets(session, attendance_sheet_id=21)
    session.commit()

    row = attendance.document_json["rows"][0]
    assert summary["video_refund_total"] == 38
    assert row[2] == '=COUNTIF(G2:H2,"*完成*")+COUNTIF(G2:H2,"*回放*")'
    assert row[3] == 38


def test_rebuild_nianzhu_attendance_shifts_international_student_replay_days(session: Session) -> None:
    _create_nianzhu_workbook(session)
    materialize_nianzhu_course_sheets(session, replace=False)

    attendance = _find_sheet(session, "attendance")
    attendance.document_json = _sheet_document(
        ["姓名", "用户ID", "完成视频数", "视频应返款", "打卡应返款", "打卡数", "第01课"],
        [["甲", "u1", "", "", "", "", ""]],
    )

    video_config = _find_sheet(session, VIDEO_CONFIG_SHEET_KEY)
    video_config.document_json = _sheet_document(
        VIDEO_CONFIG_COLUMNS,
        [
            _row_from_dict(
                VIDEO_CONFIG_COLUMNS,
                {
                    "lesson_id": 1,
                    "lesson_name": "第01课",
                    "start_date": "2026-07-01 05:20:00",
                    "video_duration": 3600,
                },
            ),
        ],
    )
    video_config.document_json["source_meta"] = {
        "course_name": "d260501第46届觉观",
        "video_refund_rule_mode": "timed_text",
        "timed_video_rules": {"当堂": 19, "第1天": 14, "第2天": 9, "第3天": 4, "回放": 0},
        "international_student_user_ids": ["u1"],
    }

    video_data = _find_sheet(session, VIDEO_DATA_SHEET_KEY)
    video_data.document_json = _sheet_document(
        VIDEO_DATA_COLUMNS,
        [
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 1,
                    "user_id2": "u1",
                    "playback_seconds": 3600,
                    "cum_seconds": 3600,
                    "progress": 100,
                    "lesson_id": 1,
                    "update_time": "2026-07-02 06:30:00",
                },
            ),
        ],
    )

    clockin_data = _find_sheet(session, CLOCKIN_DATA_SHEET_KEY)
    clockin_data.document_json = _sheet_document(CLOCKIN_DATA_COLUMNS, [])
    session.add(attendance)
    session.add(video_config)
    session.add(video_data)
    session.add(clockin_data)
    session.commit()

    summary = rebuild_nianzhu_attendance_from_course_sheets(session, attendance_sheet_id=21)
    session.commit()

    row = attendance.document_json["rows"][0]
    assert summary["video_refund_total"] == 19
    assert row[3] == 19
    assert row[6] == "当堂完成/100%"


def test_rebuild_nianzhu_attendance_uses_registration_overseas_flag_for_replay_shift(session: Session) -> None:
    _create_nianzhu_workbook(session)
    materialize_nianzhu_course_sheets(session, replace=False)

    attendance = _find_sheet(session, "attendance")
    attendance.document_json = _sheet_document(
        ["学号", "姓名", "用户ID", "完成视频数", "视频应返款", "打卡应返款", "打卡数", "第01课"],
        [["101", "甲", "", "", "", "", "", ""]],
    )

    registration = _find_sheet(session, "registration")
    registration.document_json = _sheet_document(
        ["序号", "姓名", "海外", "用户ID", "关联用户ID"],
        [["101", "甲", "是", "u1", ""]],
    )

    video_config = _find_sheet(session, VIDEO_CONFIG_SHEET_KEY)
    video_config.document_json = _sheet_document(
        VIDEO_CONFIG_COLUMNS,
        [
            _row_from_dict(
                VIDEO_CONFIG_COLUMNS,
                {
                    "lesson_id": 1,
                    "lesson_name": "第01课",
                    "start_date": "2026-07-01 05:20:00",
                    "video_duration": 3600,
                },
            ),
        ],
    )
    video_config.document_json["source_meta"] = {
        "course_name": "d260701第48届觉观",
        "video_refund_rule_mode": "timed_text",
        "timed_video_rules": {"当堂": 19, "第1天": 14, "第2天": 9, "第3天": 4, "回放": 0},
    }

    video_data = _find_sheet(session, VIDEO_DATA_SHEET_KEY)
    video_data.document_json = _sheet_document(
        VIDEO_DATA_COLUMNS,
        [
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 1,
                    "user_id2": "u1",
                    "playback_seconds": 3600,
                    "cum_seconds": 3600,
                    "progress": 100,
                    "lesson_id": 1,
                    "update_time": "2026-07-02 06:30:00",
                },
            ),
        ],
    )

    clockin_data = _find_sheet(session, CLOCKIN_DATA_SHEET_KEY)
    clockin_data.document_json = _sheet_document(CLOCKIN_DATA_COLUMNS, [])
    session.add(attendance)
    session.add(registration)
    session.add(video_config)
    session.add(video_data)
    session.add(clockin_data)
    session.commit()

    summary = rebuild_nianzhu_attendance_from_course_sheets(session, attendance_sheet_id=21)
    session.commit()

    row = attendance.document_json["rows"][0]
    assert summary["video_refund_total"] == 19
    assert row[4] == 19
    assert row[7] == "当堂完成/100%"


def test_rebuild_nianzhu_attendance_uses_challenge_refund_formulas(session: Session) -> None:
    _create_nianzhu_workbook(session)
    materialize_nianzhu_course_sheets(session, replace=False)

    attendance = _find_sheet(session, "attendance")
    columns = ["姓名", "用户ID", "完成视频数", "视频应返款", "打卡应返款", "打卡数", "第01课", "第02课"]
    rows = [["甲", "u1", "", "", "", "", "", ""]]
    note_row = [""] * len(columns)
    note_row[columns.index("视频应返款")] = (
        '21课*20元=420元。\n进度达到90%即视为完成，退还全部促学金。'
        '视频在"当堂/第1天/第2天/第3天"看完，对应返回"20/15/10/5"元'
    )
    attendance.document_json = {
        "schema_version": 1,
        "columns": columns,
        "rows": rows,
        "grid_rows": [[""] * len(columns), columns, note_row, *rows],
        "data_start_row": 3,
        "field_row_index": 1,
        "formula_reference_origin": "sheet_v2",
    }

    video_config = _find_sheet(session, VIDEO_CONFIG_SHEET_KEY)
    video_config.document_json = _sheet_document(
        VIDEO_CONFIG_COLUMNS,
        [
            _row_from_dict(VIDEO_CONFIG_COLUMNS, {"lesson_id": 1, "lesson_name": "第01课", "video_duration": 3600}),
            _row_from_dict(VIDEO_CONFIG_COLUMNS, {"lesson_id": 2, "lesson_name": "第02课", "video_duration": 3600}),
        ],
    )
    video_config.document_json["source_meta"] = {
        "course_name": "d250106念住闯关",
        "video_refund_rule_mode": "timed_text",
    }

    video_data = _find_sheet(session, VIDEO_DATA_SHEET_KEY)
    video_data.document_json = _sheet_document(
        VIDEO_DATA_COLUMNS,
        [
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 1,
                    "user_id2": "u1",
                    "cum_seconds": 3528,
                    "progress": 98,
                    "lesson_id": 1,
                },
            ),
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 2,
                    "user_id2": "u1",
                    "cum_seconds": 5400,
                    "progress": 150,
                    "lesson_id": 2,
                },
            ),
        ],
    )

    clockin_data = _find_sheet(session, CLOCKIN_DATA_SHEET_KEY)
    clockin_data.document_json = _sheet_document(CLOCKIN_DATA_COLUMNS, [])
    session.add(attendance)
    session.add(video_config)
    session.add(video_data)
    session.add(clockin_data)
    session.commit()

    summary = rebuild_nianzhu_attendance_from_course_sheets(session, attendance_sheet_id=21)
    session.commit()

    row = attendance.document_json["rows"][0]
    assert summary["video_refund_total"] == 40
    assert row[2] == '=COUNTIF(G4:H4,"*遍*")'
    assert row[3] == 40
    assert row[6] == "1遍/98%"
    assert row[7] == "2遍/150%"


def test_rebuild_nianzhu_attendance_prefers_attendance_video_refund_note_rules(session: Session) -> None:
    _create_nianzhu_workbook(session)
    materialize_nianzhu_course_sheets(session, replace=False)

    attendance = _find_sheet(session, "attendance")
    columns = ["姓名", "用户ID", "完成视频数", "视频应返款", "打卡应返款", "打卡数", "第01课", "第02课"]
    rows = [["甲", "u1", '=COUNTIF(G4:H4,"*完成*")', '=COUNTIF(G4:H4,"*当堂*")*20', "", "", "", ""]]
    note_row = [""] * len(columns)
    note_row[columns.index("视频应返款")] = (
        '21课*19元=399元。\n视频在"当堂(直播)/第1天(当天)/第2天/第3天/第4~5天"看完，'
        '对应返回"19/14/9/4/0"元'
    )
    attendance.document_json = {
        "schema_version": 1,
        "columns": columns,
        "rows": rows,
        "grid_rows": [[""] * len(columns), columns, note_row, *rows],
        "data_start_row": 3,
        "field_row_index": 1,
        "formula_reference_origin": "sheet_v2",
    }

    video_config = _find_sheet(session, VIDEO_CONFIG_SHEET_KEY)
    video_config.document_json = _sheet_document(
        VIDEO_CONFIG_COLUMNS,
        [
            _row_from_dict(VIDEO_CONFIG_COLUMNS, {"lesson_id": 1, "lesson_name": "第01课", "video_duration": 3600}),
            _row_from_dict(VIDEO_CONFIG_COLUMNS, {"lesson_id": 2, "lesson_name": "第02课", "video_duration": 3600}),
        ],
    )
    video_config.document_json["source_meta"] = {"course_name": "d260601第47届觉观"}

    video_data = _find_sheet(session, VIDEO_DATA_SHEET_KEY)
    video_data.document_json = _sheet_document(
        VIDEO_DATA_COLUMNS,
        [
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 1,
                    "user_id2": "u1",
                    "studio_seconds": 3132,
                    "cum_seconds": 3132,
                    "study_state": "已完成",
                    "progress": 87,
                    "lesson_id": 1,
                },
            ),
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 2,
                    "user_id2": "u1",
                    "studio_seconds": 3528,
                    "cum_seconds": 3528,
                    "study_state": "已完成",
                    "progress": 98,
                    "lesson_id": 2,
                },
            ),
        ],
    )
    clockin_data = _find_sheet(session, CLOCKIN_DATA_SHEET_KEY)
    clockin_data.document_json = _sheet_document(CLOCKIN_DATA_COLUMNS, [])
    session.add(attendance)
    session.add(video_config)
    session.add(video_data)
    session.add(clockin_data)
    session.commit()

    summary = rebuild_nianzhu_attendance_from_course_sheets(session, attendance_sheet_id=21)
    session.commit()

    row = attendance.document_json["rows"][0]
    assert summary["video_refund_total"] == 38
    assert row[columns.index("完成视频数")] == '=COUNTIF(G4:H4,"*完成*")+COUNTIF(G4:H4,"*回放*")'
    assert row[columns.index("视频应返款")] == 38


def test_rebuild_jueguan_attendance_uses_inherited_timed_refund_rules(session: Session) -> None:
    _create_nianzhu_workbook(session)
    materialize_nianzhu_course_sheets(session, replace=False)

    attendance = _find_sheet(session, "attendance")
    columns = ["姓名", "用户ID", "完成视频数", "视频应返款", "打卡应返款", "打卡数", "第01课", "第02课"]
    rows = [["甲", "u1", "", "", "", "", "", ""]]
    attendance.document_json = {
        "schema_version": 1,
        "columns": columns,
        "rows": rows,
        "grid_rows": [[""] * len(columns), columns, [""] * len(columns), *rows],
        "data_start_row": 3,
        "field_row_index": 1,
        "formula_reference_origin": "sheet_v2",
    }

    video_config = _find_sheet(session, VIDEO_CONFIG_SHEET_KEY)
    video_config.document_json = _sheet_document(
        VIDEO_CONFIG_COLUMNS,
        [
            _row_from_dict(VIDEO_CONFIG_COLUMNS, {"lesson_id": 1, "lesson_name": "第01课", "video_duration": 3600}),
            _row_from_dict(VIDEO_CONFIG_COLUMNS, {"lesson_id": 2, "lesson_name": "第02课", "video_duration": 3600}),
        ],
    )
    video_config.document_json["source_meta"] = {
        "course_name": "第48届觉观",
        "video_refund_rule_mode": "timed_text",
        "timed_video_rules": {"当堂": 19, "第1天": 14, "第2天": 9, "第3天": 4, "回放": 0},
    }

    video_data = _find_sheet(session, VIDEO_DATA_SHEET_KEY)
    video_data.document_json = _sheet_document(
        VIDEO_DATA_COLUMNS,
        [
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 1,
                    "user_id2": "u1",
                    "studio_seconds": 3132,
                    "cum_seconds": 3132,
                    "study_state": "已完成",
                    "progress": 87,
                    "lesson_id": 1,
                },
            ),
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 2,
                    "user_id2": "u1",
                    "studio_seconds": 3528,
                    "cum_seconds": 3528,
                    "study_state": "已完成",
                    "progress": 98,
                    "lesson_id": 2,
                },
            ),
        ],
    )
    clockin_data = _find_sheet(session, CLOCKIN_DATA_SHEET_KEY)
    clockin_data.document_json = _sheet_document(CLOCKIN_DATA_COLUMNS, [])
    session.add(attendance)
    session.add(video_config)
    session.add(video_data)
    session.add(clockin_data)
    session.commit()

    summary = rebuild_nianzhu_attendance_from_course_sheets(session, attendance_sheet_id=21)
    session.commit()

    row = attendance.document_json["rows"][0]
    assert summary["video_refund_total"] == 38
    assert row[columns.index("视频应返款")] == 38
    assert row[columns.index("第01课")] == "当堂完成/87%"
    assert row[columns.index("第02课")] == "当堂完成/98%"


def test_rebuild_attendance_preserves_existing_refunded_amount(session: Session) -> None:
    _create_nianzhu_workbook(session)
    materialize_nianzhu_course_sheets(session, replace=False)

    attendance = _find_sheet(session, "attendance")
    columns = [
        "姓名",
        "用户ID",
        "完成视频数",
        "视频应返款",
        "打卡应返款",
        "总应返款",
        "已返款",
        "订单金额",
        "当前应返款",
        "打卡数",
        "第01课",
    ]
    rows = [["甲", "u1", "", "", "", "", "7", "499", "", "", ""]]
    attendance.document_json = {
        "schema_version": 1,
        "columns": columns,
        "rows": rows,
        "grid_rows": [[""] * len(columns), columns, [""] * len(columns), *rows],
        "data_start_row": 3,
        "field_row_index": 1,
        "formula_reference_origin": "sheet_v2",
    }

    video_config = _find_sheet(session, VIDEO_CONFIG_SHEET_KEY)
    video_config.document_json = _sheet_document(
        VIDEO_CONFIG_COLUMNS,
        [_row_from_dict(VIDEO_CONFIG_COLUMNS, {"lesson_id": 1, "lesson_name": "第01课", "video_duration": 3600})],
    )
    video_config.document_json["source_meta"] = {
        "course_name": "第48届觉观",
        "video_refund_rule_mode": "timed_text",
        "timed_video_rules": {"当堂": 19, "第1天": 14, "第2天": 9, "第3天": 4, "回放": 0},
    }

    video_data = _find_sheet(session, VIDEO_DATA_SHEET_KEY)
    video_data.document_json = _sheet_document(
        VIDEO_DATA_COLUMNS,
        [
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 1,
                    "user_id2": "u1",
                    "studio_seconds": 3600,
                    "cum_seconds": 3600,
                    "study_state": "已完成",
                    "progress": 100,
                    "lesson_id": 1,
                },
            ),
        ],
    )
    clockin_data = _find_sheet(session, CLOCKIN_DATA_SHEET_KEY)
    clockin_data.document_json = _sheet_document(CLOCKIN_DATA_COLUMNS, [])
    session.add(attendance)
    session.add(video_config)
    session.add(video_data)
    session.add(clockin_data)
    session.commit()

    rebuild_nianzhu_attendance_from_course_sheets(session, attendance_sheet_id=21)
    session.commit()

    row = attendance.document_json["rows"][0]
    assert row[columns.index("视频应返款")] == 19
    assert row[columns.index("总应返款")] == "=IF(H4>0,MIN(MAX(IFERROR(D4+E4+H4-IF($H$3>0,$H$3,H4),0),0),H4),0)"
    assert row[columns.index("已返款")] == "7"
    assert row[columns.index("当前应返款")] == "=IF(H4>0,F4-G4,0)"


def test_rebuild_nianzhu_attendance_updates_refund_tracking_totals(session: Session) -> None:
    _create_nianzhu_workbook(session)
    materialize_nianzhu_course_sheets(session, replace=False)

    attendance = _find_sheet(session, "attendance")
    columns = [
        "姓名",
        "用户ID",
        "完成视频数",
        "视频应返款",
        "打卡应返款",
        "总应返款",
        "订单金额",
        "已返款",
        "当前应返款",
        "打卡数",
        "第01课",
        "第12课",
    ]
    rows = [
        ["甲", "u1", 0, 0, 0, 0, 620, 10, 0, "", "", ""],
        ["乙", "u_missing", 0, 0, 0, "=I5+J5", 620, 100, "=K5-M5", "", "", ""],
    ]
    note_row = [""] * len(columns)
    note_row[columns.index("打卡应返款")] = '打卡达到"5/10/15"次，累计返回"30/60/100"元'
    note_row[columns.index("已返款")] = 620
    attendance.document_json = {
        "schema_version": 1,
        "columns": columns,
        "rows": rows,
        "grid_rows": [[""] * len(columns), columns, note_row, *rows],
        "data_start_row": 3,
        "field_row_index": 1,
        "formula_reference_origin": "sheet_v2",
    }
    clockin_data = _find_sheet(session, CLOCKIN_DATA_SHEET_KEY)
    clockin_data.document_json = _sheet_document(CLOCKIN_DATA_COLUMNS, [])
    session.add(attendance)
    session.add(clockin_data)
    session.commit()

    summary = rebuild_nianzhu_attendance_from_course_sheets(session, active_only=True)
    session.commit()

    assert summary["video_refund_total"] == 0
    session.refresh(attendance)
    rows = attendance.document_json["rows"]
    rebuilt_columns = attendance.document_json["columns"]
    config_row = attendance.document_json["grid_rows"][2]
    assert rebuilt_columns.index("已返款") < rebuilt_columns.index("订单金额")
    assert config_row[rebuilt_columns.index("已返款")] == '="第"&返款周期&"天"'
    assert config_row[rebuilt_columns.index("订单金额")] == "620"
    for header in ["视频应返款", "打卡应返款", "总应返款", "已返款", "订单金额", "当前应返款"]:
        assert attendance.document_json["column_configs"][header]["value_type"] == "number"
    first_row = rows[0]
    assert first_row[rebuilt_columns.index("视频应返款")] == 0
    assert first_row[rebuilt_columns.index("打卡应返款")] == "=SWITCH(TRUE,J4>=15,100,J4>=10,60,J4>=5,30,0)"
    assert first_row[rebuilt_columns.index("总应返款")] == "=IF(H4>0,MIN(MAX(IFERROR(D4+E4+H4-IF($H$3>0,$H$3,H4),0),0),H4),0)"
    assert first_row[rebuilt_columns.index("当前应返款")] == "=IF(H4>0,F4-G4,0)"
    second_row = rows[1]
    assert second_row[rebuilt_columns.index("总应返款")] == "=IF(H5>0,MIN(MAX(IFERROR(D5+E5+H5-IF($H$3>0,$H$3,H5),0),0),H5),0)"
    assert second_row[rebuilt_columns.index("当前应返款")] == "=IF(H5>0,F5-G5,0)"


def test_rebuild_nianzhu_attendance_uses_clockin_note_rules_for_color(session: Session) -> None:
    _create_nianzhu_workbook(session)
    materialize_nianzhu_course_sheets(session, replace=False)

    attendance = _find_sheet(session, "attendance")
    columns = ["姓名", "用户ID", "打卡应返款", "打卡数"]
    rows = [["甲", "u1", "", ""]]
    note_row = [""] * len(columns)
    note_row[columns.index("打卡应返款")] = '打卡达到"5/10/15/20"次，累计返回"100/150/180/200"元'
    attendance.document_json = {
        "schema_version": 1,
        "columns": columns,
        "rows": rows,
        "grid_rows": [[""] * len(columns), columns, note_row, *rows],
        "data_start_row": 3,
        "field_row_index": 1,
        "formula_reference_origin": "sheet_v2",
    }

    clockin_data = _find_sheet(session, CLOCKIN_DATA_SHEET_KEY)
    clockin_data.document_json = _sheet_document(
        CLOCKIN_DATA_COLUMNS,
        [
            _row_from_dict(
                CLOCKIN_DATA_COLUMNS,
                {
                    "clockin_data_id": index,
                    "user_id2": "u1",
                    "clockin_name": "打卡数",
                    "task_date": f"2026-05-{index:02}",
                    "update_title": f"第{index}天",
                },
            )
            for index in range(1, 18)
        ],
    )
    session.add(attendance)
    session.add(clockin_data)
    session.commit()

    rebuild_nianzhu_attendance_from_course_sheets(session, active_only=True)
    session.commit()

    session.refresh(attendance)
    document = attendance.document_json
    rebuilt_columns = document["columns"]
    row = document["rows"][0]
    clockin_index = rebuilt_columns.index("打卡数")
    assert row[clockin_index] == 17
    assert row[rebuilt_columns.index("打卡应返款")] == 180
    color = document["cell_meta"][f"3:{clockin_index}"]["style"]["background_color"]
    assert color == "#FFC43D"
    assert color != "#80FF80"


def test_rebuild_nianzhu_attendance_removes_merchant_order_display_column(session: Session) -> None:
    _create_nianzhu_workbook(session)
    materialize_nianzhu_course_sheets(session, replace=False)

    attendance = _find_sheet(session, "attendance")
    columns = [
        "分组",
        "学号",
        "姓名",
        "昵称",
        "商户订单号",
        "用户ID",
        "禅客",
        "完成视频数",
        "视频应返款",
        "打卡应返款",
        "总应返款",
        "订单金额",
        "已返款",
        "当前应返款",
        "打卡数",
        "第01课",
    ]
    rows = [
        [
            "一组",
            "1_01",
            "甲",
            "甲昵称",
            "MA202606010001",
            "u1",
            0,
            '=COUNTIF(P4:P4,"*完成*")+COUNTIF(P4:P4,"*回放*")',
            '=COUNTIF(P4:P4,"*当堂*")*19',
            '=SWITCH(TRUE,O4>=5,30,0)',
            "=I4+J4+L4-M4",
            499,
            0,
            "=IF(L4>0,MAX(K4-M4,0),0)",
            5,
            "当堂/100%",
        ],
    ]
    note_row = [""] * len(columns)
    note_row[columns.index("已返款")] = 499
    attendance.document_json = {
        "schema_version": 1,
        "columns": columns,
        "rows": rows,
        "grid_rows": [[""] * len(columns), columns, note_row, *rows],
        "data_start_row": 3,
        "field_row_index": 1,
        "formula_reference_origin": "sheet_v2",
    }
    session.add(attendance)
    session.commit()

    summary = rebuild_nianzhu_attendance_from_course_sheets(session, active_only=True)
    session.commit()

    assert summary["schema_removed_columns"] == ["商户订单号"]
    session.refresh(attendance)
    rebuilt_columns = attendance.document_json["columns"]
    row = attendance.document_json["rows"][0]
    assert "商户订单号" not in rebuilt_columns
    assert row[rebuilt_columns.index("用户ID")] == "u1"
    assert row[rebuilt_columns.index("禅客")] == '=IF(AND(G4>=11,N4>=7),"是","")'
    assert row[rebuilt_columns.index("完成视频数")] == '=COUNTIF(O4:O4,"*完成*")+COUNTIF(O4:O4,"*回放*")'
    assert row[rebuilt_columns.index("视频应返款")] == 0
    assert row[rebuilt_columns.index("打卡应返款")] == '=SWITCH(TRUE,N4>=15,200,N4>=10,150,N4>=5,100,0)'
    assert rebuilt_columns.index("已返款") < rebuilt_columns.index("订单金额")
    assert row[rebuilt_columns.index("总应返款")] == "=IF(L4>0,MIN(MAX(IFERROR(H4+I4+L4-IF($L$3>0,$L$3,L4),0),0),L4),0)"
    assert row[rebuilt_columns.index("当前应返款")] == "=IF(L4>0,J4-K4,0)"


def test_rebuild_nianzhu_attendance_keeps_partial_zen_progress_unstyled_and_unpaid(session: Session) -> None:
    _create_nianzhu_workbook(session)
    materialize_nianzhu_course_sheets(session, replace=False)

    attendance = _find_sheet(session, "attendance")
    attendance_document = copy.deepcopy(attendance.document_json)
    attendance_document["columns"].append("共学打卡")
    for row in attendance_document["rows"]:
        row.append("")
    for row in attendance_document["grid_rows"]:
        row.append("")
    refund_column = attendance_document["columns"].index("视频应返款")
    clockin_column = attendance_document["columns"].index("共学打卡")
    config_row = attendance_document["data_start_row"] - 1
    attendance_document["grid_rows"][config_row][refund_column] = "1课×20元=20元"
    attendance_document["grid_rows"][config_row][clockin_column] = "1次×每次10元=10元"
    attendance.document_json = attendance_document

    video_config = _find_sheet(session, VIDEO_CONFIG_SHEET_KEY)
    video_config.document_json = _sheet_document(
        VIDEO_CONFIG_COLUMNS,
        [
            _row_from_dict(
                VIDEO_CONFIG_COLUMNS,
                {
                    "lesson_id": 1,
                    "start_date": "2026-05-01 00:00:00",
                    "lesson_name": "禅宗第01课",
                    "video_duration": 3600,
                },
            ),
        ],
    )
    video_config.document_json["source_meta"] = {"course_name": "修道班7期5阶"}
    video_data = _find_sheet(session, VIDEO_DATA_SHEET_KEY)
    video_data.document_json = _sheet_document(
        VIDEO_DATA_COLUMNS,
        [
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 1,
                    "user_id2": "u1",
                    "cum_seconds": 1800,
                    "progress": 18.94,
                    "update_time": "2026-05-03 08:00:00",
                    "lesson_id": 1,
                },
            ),
        ],
    )
    session.add(video_config)
    session.add(video_data)
    session.add(attendance)
    session.commit()

    summary = rebuild_nianzhu_attendance_from_course_sheets(session, active_only=True)
    session.commit()

    assert summary["updated_rows"] >= 1
    attendance = _find_sheet(session, "attendance")
    document = attendance.document_json
    columns = document["columns"]
    row = document["rows"][0]
    lesson_column = columns.index("第01课")
    assert row[lesson_column] == "进度19%"
    assert row[columns.index("完成视频数")] == '=COUNTIF(I2,"*完成*")+COUNTIF(I2,"*回放*")'
    assert row[columns.index("视频应返款")] == 0
    document_row = document["data_start_row"]
    style = document.get("cell_meta", {}).get(f"{document_row}:{lesson_column}", {}).get("style", {})
    assert not style.get("background_color")


def test_rebuild_nianzhu_attendance_counts_zen_stage_title_videos_as_refundable(session: Session) -> None:
    _create_nianzhu_workbook(session)
    materialize_nianzhu_course_sheets(session, replace=False)

    attendance = _find_sheet(session, "attendance")
    attendance_document = copy.deepcopy(attendance.document_json)
    columns = attendance_document["columns"]
    columns.append("共学打卡")
    for row in attendance_document["rows"]:
        row.append("")
    for row in attendance_document["grid_rows"]:
        row.append("")
    columns[8] = "佛教史1"
    attendance_document["rows"][0][8] = ""
    refund_column = columns.index("视频应返款")
    clockin_column = columns.index("共学打卡")
    config_row = attendance_document["data_start_row"] - 1
    attendance_document["grid_rows"][config_row][refund_column] = "1课×20元=20元"
    attendance_document["grid_rows"][config_row][clockin_column] = "1次×每次10元=10元"
    attendance.document_json = attendance_document

    video_config = _find_sheet(session, VIDEO_CONFIG_SHEET_KEY)
    video_config.document_json = _sheet_document(
        VIDEO_CONFIG_COLUMNS,
        [
            _row_from_dict(
                VIDEO_CONFIG_COLUMNS,
                {
                    "lesson_id": 1,
                    "start_date": "2026-05-01 00:00:00",
                    "lesson_name": "第1周=佛教史1",
                    "video_duration": 3600,
                },
            ),
        ],
    )
    video_config.document_json["source_meta"] = {"course_name": "修道班7期5阶"}
    video_data = _find_sheet(session, VIDEO_DATA_SHEET_KEY)
    video_data.document_json = _sheet_document(
        VIDEO_DATA_COLUMNS,
        [
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 1,
                    "user_id2": "u1",
                    "cum_seconds": 1800,
                    "progress": 100,
                    "update_time": "2026-05-03 08:00:00",
                    "lesson_id": 1,
                },
            ),
        ],
    )
    session.add(attendance)
    session.add(video_config)
    session.add(video_data)
    session.commit()

    summary = rebuild_nianzhu_attendance_from_course_sheets(session, active_only=True)
    session.commit()

    assert summary["video_refund_total"] == 20
    document = _find_sheet(session, "attendance").document_json
    row = document["rows"][0]
    assert row[columns.index("佛教史1")] == "准时完成"
    assert row[columns.index("视频应返款")] == 20


def test_rebuild_nianzhu_attendance_clears_blank_progress_background_on_skipped_rows(session: Session) -> None:
    _create_nianzhu_workbook(session)
    materialize_nianzhu_course_sheets(session, replace=False)

    attendance = _find_sheet(session, "attendance")
    attendance_document = copy.deepcopy(attendance.document_json)
    columns = attendance_document["columns"]
    document_row = attendance_document["data_start_row"] + 1
    filled_column = columns.index("第01课")
    blank_column = columns.index("第12课")
    entity_row_id = f"row_{document_row}"
    filled_column_id = f"col_{filled_column}"
    blank_column_id = f"col_{blank_column}"
    attendance_document["entity_rows"] = [
        {"id": f"row_{index}"}
        for index in range(attendance_document["data_start_row"] + len(attendance_document["rows"]))
    ]
    attendance_document["entity_columns"] = [
        {"id": f"col_{index}", "header": column}
        for index, column in enumerate(columns)
    ]
    attendance_document["entity_cells"] = {
        entity_row_id: {
            filled_column_id: {"value": "1遍/100%", "style": {"background_color": "#80FF80"}},
            blank_column_id: {"style": {"background_color": "#80FF80"}},
        },
    }
    attendance_document["cell_meta"] = {
        f"{document_row}:{filled_column}": {"style": {"background_color": "#80FF80"}},
        f"{document_row}:{blank_column}": {"style": {"background_color": "#80FF80"}},
    }
    attendance.document_json = attendance_document
    session.add(attendance)
    session.commit()

    summary = rebuild_nianzhu_attendance_from_course_sheets(session, active_only=True)
    session.commit()

    assert summary["skipped_rows"] == 1
    assert summary["styled_cells"] >= 1
    session.refresh(attendance)
    cell_meta = attendance.document_json["cell_meta"]
    assert cell_meta[f"{document_row}:{filled_column}"]["style"]["background_color"] == "#80FF80"
    assert f"{document_row}:{blank_column}" not in cell_meta
    entity_cells = attendance.document_json["entity_cells"][entity_row_id]
    assert entity_cells[filled_column_id]["style"]["background_color"] == "#80FF80"
    assert blank_column_id not in entity_cells


def test_rebuild_nianzhu_attendance_keeps_existing_progress_when_source_has_no_progress(session: Session) -> None:
    _create_nianzhu_workbook(session)
    materialize_nianzhu_course_sheets(session, replace=False)

    video_data = _find_sheet(session, VIDEO_DATA_SHEET_KEY)
    video_document = copy.deepcopy(video_data.document_json)
    for row in video_document["rows"]:
        if row[1] == "u1" and row[15] == 1:
            row[5] = 0
            row[10] = "学习中"
            row[11] = 0
    video_data.document_json = video_document
    session.add(video_data)
    session.commit()

    summary = rebuild_nianzhu_attendance_from_course_sheets(session, active_only=True)
    session.commit()

    assert summary["updated_rows"] == 1
    attendance = _find_sheet(session, "attendance")
    columns = attendance.document_json["columns"]
    row = attendance.document_json["rows"][0]
    assert row[columns.index("第01课")] == "1遍/100%"


def test_rebuild_nianzhu_attendance_keeps_existing_clockin_when_source_has_no_count(session: Session) -> None:
    _create_nianzhu_workbook(session)
    materialize_nianzhu_course_sheets(session, replace=False)

    clockin_data = _find_sheet(session, CLOCKIN_DATA_SHEET_KEY)
    clockin_data.document_json = _sheet_document(CLOCKIN_DATA_COLUMNS, [])
    session.add(clockin_data)
    session.commit()

    summary = rebuild_nianzhu_attendance_from_course_sheets(session, active_only=True)
    session.commit()

    assert summary["updated_rows"] == 1
    attendance = _find_sheet(session, "attendance")
    columns = attendance.document_json["columns"]
    row = attendance.document_json["rows"][0]
    assert row[columns.index("打卡数")] == 5
    assert row[columns.index("打卡应返款")] == 100


def test_rebuild_zen_stage_attendance_clears_existing_test_clockin_counts(session: Session) -> None:
    _create_nianzhu_workbook(session)
    materialize_nianzhu_course_sheets(session, replace=False)

    clockin_data = _find_sheet(session, CLOCKIN_DATA_SHEET_KEY)
    clockin_data.document_json = _sheet_document(
        CLOCKIN_DATA_COLUMNS,
        [
            _row_from_dict(
                CLOCKIN_DATA_COLUMNS,
                {
                    "clockin_data_id": 1,
                    "user_id2": "u1",
                    "clockin_name": "打卡数",
                    "task_date": "2026-06-01",
                    "update_title": "【打卡】中心教室-测试1",
                },
            ),
            _row_from_dict(
                CLOCKIN_DATA_COLUMNS,
                {
                    "clockin_data_id": 2,
                    "user_id2": "u1",
                    "clockin_name": "打卡数",
                    "task_date": "2026-06-01",
                    "update_title": "【打卡】中心教室-测试2",
                },
            ),
            _row_from_dict(
                CLOCKIN_DATA_COLUMNS,
                {
                    "clockin_data_id": 3,
                    "user_id2": "u2",
                    "clockin_name": "打卡数",
                    "task_date": "2026-06-01",
                    "update_title": "【打卡】中心教室-1",
                },
            ),
        ],
    )
    session.add(clockin_data)
    session.commit()

    rebuild_nianzhu_attendance_from_course_sheets(
        session,
        active_only=False,
        course_name="d260601第47届觉观",
    )
    session.commit()

    attendance = _find_sheet(session, "attendance")
    columns = attendance.document_json["columns"]
    rows = attendance.document_json["rows"]
    assert rows[0][columns.index("打卡数")] == ""
    assert rows[1][columns.index("打卡数")] == 1


def test_rebuild_nianzhu_attendance_merges_linked_user_ids(session: Session) -> None:
    _create_nianzhu_workbook(session)
    attendance = _find_sheet(session, "attendance")
    attendance_document = copy.deepcopy(attendance.document_json)
    user_id_index = attendance_document["columns"].index("用户ID")
    attendance_document["columns"].pop(user_id_index)
    for row in attendance_document["rows"]:
        row.pop(user_id_index)
    attendance_document["grid_rows"] = [attendance_document["columns"], *attendance_document["rows"]]
    attendance.document_json = attendance_document
    session.add(attendance)

    registration = _find_sheet(session, "registration")
    registration_document = copy.deepcopy(registration.document_json)
    linked_index = registration_document["columns"].index("关联用户ID")
    registration_document["rows"][0][linked_index] = "u_alias"
    registration_document["grid_rows"] = [registration_document["columns"], *registration_document["rows"]]
    registration.document_json = registration_document
    session.add(registration)

    materialize_nianzhu_course_sheets(session, replace=False)

    video_config = _find_sheet(session, VIDEO_CONFIG_SHEET_KEY)
    video_config.document_json = _sheet_document(
        VIDEO_CONFIG_COLUMNS,
        [
            _row_from_dict(
                VIDEO_CONFIG_COLUMNS,
                {"lesson_id": 1, "lesson_name": "第01课 测试课程", "video_duration": 100},
            ),
        ],
    )
    video_config.document_json["source_meta"] = {"course_name": "d250106念住闯关"}
    video_data = _find_sheet(session, VIDEO_DATA_SHEET_KEY)
    video_data.document_json = _sheet_document(
        VIDEO_DATA_COLUMNS,
        [
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {"lesson_data_id": 1, "user_id2": "u_alias", "cum_seconds": 180, "lesson_id": 1},
            ),
        ],
    )
    clockin_data = _find_sheet(session, CLOCKIN_DATA_SHEET_KEY)
    clockin_data.document_json = _sheet_document(
        CLOCKIN_DATA_COLUMNS,
        [
            _row_from_dict(
                CLOCKIN_DATA_COLUMNS,
                {"clockin_data_id": 1, "user_id2": "u1", "task_date": "2026-05-20", "update_title": "第1天"},
            ),
            _row_from_dict(
                CLOCKIN_DATA_COLUMNS,
                {"clockin_data_id": 2, "user_id2": "u_alias", "task_date": "2026-05-20", "update_title": "第1天"},
            ),
            _row_from_dict(
                CLOCKIN_DATA_COLUMNS,
                {"clockin_data_id": 3, "user_id2": "u_alias", "task_date": "2026-05-21", "update_title": "第2天"},
            ),
        ],
    )
    session.add(video_config)
    session.add(video_data)
    session.add(clockin_data)
    session.commit()

    summary = rebuild_nianzhu_attendance_from_course_sheets(session, active_only=True)
    session.commit()

    assert summary["updated_rows"] == 1
    session.refresh(attendance)
    columns = attendance.document_json["columns"]
    row = attendance.document_json["rows"][0]
    assert row[columns.index("第01课")] == "2遍/180%"
    assert row[columns.index("完成视频数")] == '=COUNTIF(H2,"*遍*")'
    assert row[columns.index("视频应返款")] == 20
    assert row[columns.index("打卡数")] == 2


def test_rebuild_nianzhu_attendance_repairs_missing_refund_tracking_columns(session: Session) -> None:
    _create_nianzhu_workbook(session)
    attendance = _find_sheet(session, "attendance")
    columns = [
        "分组",
        "学号",
        "姓名",
        "用户ID",
        "完成视频数",
        "视频应返款",
        "打卡应返款",
        "总应返款",
        "订单金额",
        "当前应返款",
        "打卡数",
        "第01课",
    ]
    rows = [
        ["A组", "101", "甲", "u1", 0, 0, 0, 0, 620, 20, 1, ""],
        ["", "102", "乙", "u2", 0, 0, 0, 0, 620, 0, 0, ""],
    ]
    attendance.document_json = {
        "schema_version": 1,
        "columns": columns,
        "rows": rows,
        "grid_rows": [[""] * len(columns), columns, [""] * len(columns), *rows],
        "data_start_row": 3,
        "field_row_index": 1,
        "formula_reference_origin": "sheet_v2",
    }
    session.add(attendance)
    session.commit()

    materialize_nianzhu_course_sheets(session, replace=False)
    summary = rebuild_nianzhu_attendance_from_course_sheets(session, active_only=True)
    session.commit()

    session.refresh(attendance)
    repaired = attendance.document_json
    repaired_columns = repaired["columns"]
    assert summary["schema_inserted_columns"] == ["已返款", "规则版本", "追踪分组", "追踪状态", "冻结时间"]
    assert repaired_columns[repaired_columns.index("订单金额") - 1] == "已返款"
    for column in ["已返款", "当前应返款", "规则版本", "追踪分组", "追踪状态", "冻结时间"]:
        assert column in repaired_columns
        assert repaired["grid_rows"][1][repaired_columns.index(column)] == column

    first_row = repaired["rows"][0]
    assert first_row[repaired_columns.index("已返款")] == 0
    assert first_row[repaired_columns.index("追踪分组")] == "A组"
    assert first_row[repaired_columns.index("追踪状态")] == "追踪中"
    assert first_row[repaired_columns.index("规则版本")] == "当前规则"


def test_nianzhu_attendance_schema_removes_existing_tracking_columns_for_jueguan() -> None:
    columns = ["分组", "姓名", "规则版本", "追踪分组", "追踪状态", "冻结时间", "第01课"]
    rows = [["一组", "甲", "当前规则", "一组", "追踪中", "", "当堂完成/98%"]]
    document = {
        "schema_version": 1,
        "columns": columns,
        "rows": rows,
        "grid_rows": [[""] * len(columns), columns, [""] * len(columns), *rows],
        "data_start_row": 3,
        "field_row_index": 1,
    }

    repaired, summary = nianzhu_course_sheets._ensure_nianzhu_attendance_schema(
        document,
        course_name="d260601第47届觉观",
    )

    assert summary["schema_removed_columns"] == ["规则版本", "追踪分组", "追踪状态", "冻结时间"]
    for column in ["规则版本", "追踪分组", "追踪状态", "冻结时间"]:
        assert column not in repaired["columns"]
        assert column not in repaired["grid_rows"][1]
    assert repaired["rows"][0] == ["一组", "甲", "当堂完成/98%"]


def test_nianzhu_attendance_schema_does_not_insert_tracking_columns_for_plain_nianzhu() -> None:
    columns = ["分组", "姓名", "第01课"]
    rows = [["一组", "甲", "当堂完成/98%"]]
    document = {
        "schema_version": 1,
        "columns": columns,
        "rows": rows,
        "grid_rows": [[""] * len(columns), columns, [""] * len(columns), *rows],
        "data_start_row": 3,
        "field_row_index": 1,
    }

    repaired, summary = nianzhu_course_sheets._ensure_nianzhu_attendance_schema(
        document,
        course_name="d260601第41届念住",
    )

    assert summary["schema_inserted_columns"] == []
    for column in ["规则版本", "追踪分组", "追踪状态", "冻结时间"]:
        assert column not in repaired["columns"]
        assert column not in repaired["grid_rows"][1]
    assert repaired["rows"][0] == rows[0]


def test_nianzhu_attendance_schema_keeps_tracking_columns_for_chuangguan() -> None:
    columns = ["分组", "姓名", "第01课"]
    rows = [["一组", "甲", "1遍/98%"]]
    document = {
        "schema_version": 1,
        "columns": columns,
        "rows": rows,
        "grid_rows": [[""] * len(columns), columns, [""] * len(columns), *rows],
        "data_start_row": 3,
        "field_row_index": 1,
    }

    repaired, summary = nianzhu_course_sheets._ensure_nianzhu_attendance_schema(
        document,
        course_name="d250106念住闯关",
    )

    assert summary["schema_inserted_columns"] == ["规则版本", "追踪分组", "追踪状态", "冻结时间"]
    for column in ["规则版本", "追踪分组", "追踪状态", "冻结时间"]:
        assert column in repaired["columns"]


def test_repair_nianzhu_clockin_refunds_updates_frozen_static_refunds(session: Session) -> None:
    _create_nianzhu_workbook(session)
    materialize_nianzhu_course_sheets(session, replace=False)

    attendance = _find_sheet(session, "attendance")
    attendance_document = copy.deepcopy(attendance.document_json)
    columns = attendance_document["columns"]
    for column in ["总应返款", "已返款", "订单金额", "当前应返款"]:
        columns.append(column)
    for row_index, row in enumerate(attendance_document["rows"]):
        if row_index == 1:
            row[columns.index("打卡数")] = 1
            row[columns.index("打卡应返款")] = 0
            row[columns.index("视频应返款")] = 420
            row.extend(["=MIN(IFERROR(I5+J5+L5-IF($K$3>0,$K$3,L5),0),L5)", 620, 620, -200])
        else:
            row.extend([0, 0, 620, 0])
    attendance_document["grid_rows"] = [columns, ["" for _ in columns], ["" for _ in columns], *attendance_document["rows"]]
    attendance_document["data_start_row"] = 3
    attendance_document["grid_rows"][2][columns.index("已返款")] = 620
    attendance.document_json = attendance_document
    session.add(attendance)
    session.commit()

    summary = repair_nianzhu_clockin_refunds_from_course_sheets(session)
    session.commit()

    assert summary["updated_rows"] == 2
    session.refresh(attendance)
    columns = attendance.document_json["columns"]
    row = attendance.document_json["rows"][1]
    assert row[columns.index("打卡数")] == 10
    assert row[columns.index("打卡应返款")] == 150
    assert row[columns.index("总应返款")] == "=IF(R5>0,MIN(MAX(IFERROR(F5+G5+R5-IF($R$3>0,$R$3,R5),0),0),R5),0)"
    assert row[columns.index("当前应返款")] == -50


def test_ensure_video_progress_columns_matches_prefixed_lesson_name_by_course_key() -> None:
    document = _sheet_document(
        ["姓名", "05:20~06:18 第01课"],
        [["甲", ""]],
    )
    item = nianzhu_course_sheets.VideoConfigItem(
        order_index=1,
        lesson_id="1",
        course_key="lesson:1",
        lesson_name="d260701第48届觉观-第01课",
        item_type="课次",
        lesson_number=1,
        rule_system=nianzhu_course_sheets.VIDEO_RULE_SYSTEM_REGULAR,
        participates_refund=True,
        participates_score=False,
        rules_by_version={},
        text_rules_by_version={},
        video_duration=3600,
        lesson_url="",
    )

    next_document, inserted_headers = nianzhu_course_sheets._ensure_video_progress_columns(document, [item])

    assert next_document["columns"] == ["姓名", "05:20~06:18 第01课"]
    assert inserted_headers == []
    assert nianzhu_course_sheets._lesson_header_from_video_item(item) == "第01课"


def test_compute_zen_stage_next_update_does_not_collect_at_week_start() -> None:
    next_update = nianzhu_course_sheets._compute_next_lesson_update(
        {
            "lesson_name": "第6周=测试课程",
            "start_date": "2026-06-21 00:00:00",
            "next_update": "2026-06-21 00:00:00",
            "end_date": "2026-10-18 00:00:00",
            "video_duration": 3600,
        },
        course_name="修道班7期5阶",
        now=datetime(2026, 6, 21, 11, 52, 50),
    )

    assert next_update == "2026-06-28 00:00:00"


def test_refund_period_config_cell_uses_week_for_zen_stage_course() -> None:
    columns = ["学号", "已返款"]
    document = _sheet_document(columns, [["1", "0"]])
    document["data_start_row"] = 2
    document["grid_rows"] = [
        columns,
        ["", ""],
        ["1", "0"],
    ]

    next_document, changed = nianzhu_course_sheets._ensure_refund_period_config_cell(
        document,
        course_name="修道班7期5阶",
    )

    assert changed == 1
    assert next_document["grid_rows"][1][1] == '="第"&返款周期&"周"'


@pytest.mark.skip(reason="旧PG课程数据导入已从当前CodeYun课程创建链路退役")
def test_materialize_imports_legacy_video_and_clockin_pg_table_shapes(
    session: Session,
    monkeypatch,
) -> None:
    _create_nianzhu_workbook(session)

    monkeypatch.setattr(
        nianzhu_course_sheets,
        "_query_legacy_lesson_rows",
        lambda course_name: (
            [
                {
                    "lesson_id": 11037,
                    "start_date": "2025-01-01 00:00:00",
                    "end_date": None,
                    "next_update": "2026-05-21 00:00:00",
                    "lesson_id2": "https://admin.xiaoe-tech.com/t/course-1",
                    "shop_id": 1,
                    "lesson_name": "d250106念住闯关-第01课 测试课程",
                    "video_duration": 6271,
                },
            ],
            None,
        ),
    )
    monkeypatch.setattr(
        nianzhu_course_sheets,
        "_query_legacy_lesson_data_rows",
        lambda legacy_lesson_ids: (
            [
                {
                    "lesson_data_id": 90001,
                    "user_id2": "u1",
                    "remark_nm": "甲",
                    "state": "ok",
                    "stay_seconds": 100,
                    "cum_seconds": 200,
                    "studio_seconds": 0,
                    "playback_seconds": 200,
                    "num_of_comments": 0,
                    "studio_amount": 0,
                    "study_state": "已完成",
                    "progress": 104,
                    "last_play_time": "2026-05-20 08:00:00",
                    "shop_id": 1,
                    "update_time": "2026-05-20 08:01:00",
                    "lesson_id": 11037,
                    "finish_time": "2026-05-20 08:02:00",
                    "comment_times": 0,
                    "money": 0,
                    "lesson_name": "d250106念住闯关-第01课 测试课程",
                },
            ],
            None,
        ),
    )
    monkeypatch.setattr(
        nianzhu_course_sheets,
        "_query_legacy_clockin_rows",
        lambda course_name: (
            [
                {
                    "clockin_id": 701,
                    "name": "d250106念住闯关打卡",
                    "url": "https://admin.xiaoe-tech.com/t/clockin-1",
                    "start_date": "2025-01-01 00:00:00",
                    "end_date": None,
                    "days": 21,
                    "clockin_user_num": 88,
                    "total_user_num": 120,
                },
            ],
            None,
        ),
    )
    monkeypatch.setattr(
        nianzhu_course_sheets,
        "_query_legacy_clockin_data_rows",
        lambda legacy_clockin_ids: (
            [
                {
                    "clockin_data_id": 80001,
                    "user_id2": "u1",
                    "nickname": "甲",
                    "groupname": "B组",
                    "publish_time": "2026-05-20 07:00:00",
                    "update_content": "打卡内容",
                    "update_title": "第1天",
                    "update_type": "text",
                    "tags": "念住",
                    "read_num": 1,
                    "like_num": 2,
                    "comment_num": 3,
                    "is_essence": False,
                    "share_num": 4,
                    "update_url": "https://admin.xiaoe-tech.com/t/update-1",
                    "clockin_name": "d250106念住闯关打卡",
                    "is_repair": False,
                    "task_date": "2026-05-20",
                    "extra": {"source": "pg"},
                    "clockin_id": 701,
                },
            ],
            None,
        ),
    )

    summary = materialize_nianzhu_course_sheets(session, replace=False)
    session.commit()

    assert summary["legacy_lesson_rows"] == 1
    assert summary["legacy_lesson_data_rows"] == 1
    assert summary["legacy_clockin_rows"] == 1
    assert summary["legacy_clockin_data_rows"] == 1

    video_data = _find_sheet(session, VIDEO_DATA_SHEET_KEY)
    assert video_data.document_json["columns"] == VIDEO_DATA_COLUMNS
    video_rows = [dict(zip(VIDEO_DATA_COLUMNS, row)) for row in video_data.document_json["rows"]]
    assert video_rows == [
        {
            "lesson_data_id": 1,
            "user_id2": "u1",
            "nickname": "",
            "remark_nm": "甲",
            "state": "ok",
            "stay_seconds": 100,
            "cum_seconds": 200,
            "studio_seconds": 0,
            "playback_seconds": 200,
            "num_of_comments": 0,
            "studio_amount": 0,
            "study_state": "已完成",
            "progress": 104,
            "last_play_time": "2026-05-20 08:00:00",
            "shop_id": 1,
            "update_time": "2026-05-20 08:01:00",
            "lesson_id": 1,
            "finish_time": "2026-05-20 08:02:00",
            "comment_times": 0,
            "money": 0,
            "lesson_name": "第01课 测试课程",
        },
    ]

    clockin_config = _find_sheet(session, CLOCKIN_CONFIG_SHEET_KEY)
    assert clockin_config.document_json["columns"] == CLOCKIN_CONFIG_COLUMNS
    clockin_config_row = dict(zip(CLOCKIN_CONFIG_COLUMNS, clockin_config.document_json["rows"][0]))
    assert clockin_config_row["clockin_id"] == 1
    assert clockin_config_row["name"] == "打卡"

    clockin_data = _find_sheet(session, CLOCKIN_DATA_SHEET_KEY)
    assert clockin_data.document_json["columns"] == [*CLOCKIN_DATA_COLUMNS, "extra_source"]
    clockin_data_row = dict(zip(clockin_data.document_json["columns"], clockin_data.document_json["rows"][0]))
    assert clockin_data_row["clockin_data_id"] == 1
    assert clockin_data_row["clockin_id"] == 1
    assert clockin_data_row["user_id2"] == "u1"
    assert clockin_data_row["clockin_name"] == "打卡"
    assert clockin_data_row["extra"] == '{"source":"pg"}'
    assert clockin_data_row["extra_source"] == "pg"


def test_nianzhu_step1_matches_workbook_local_clockin_names(
    session: Session,
    monkeypatch,
    tmp_path,
) -> None:
    import sys
    import types

    _create_nianzhu_workbook(session)
    materialize_nianzhu_course_sheets(session, replace=False)
    clockin_config = _find_sheet(session, CLOCKIN_CONFIG_SHEET_KEY)
    document = copy.deepcopy(clockin_config.document_json)
    document["rows"][0][2] = "https://admin.xiaoe-tech.com/t/clockin-1"
    clockin_config.document_json = document
    session.add(clockin_config)
    session.commit()

    export_file = tmp_path / "clockin.csv"
    export_file.write_text(
        "用户ID,打卡时间,文字内容\n"
        "u1,2026-05-20 08:00:00,今日打卡\n",
        encoding="utf-8",
    )
    exported_urls: list[str] = []

    class FakeXe2:
        def switch_shop(self, shop_name: str) -> None:
            assert shop_name == "shop1"

        def export_clockin_data(self, url: str, *, start_date: str | None, end_date: str | None):
            exported_urls.append(url)
            return export_file

    class FakeKqTools:
        def __init__(self) -> None:
            self.xe2 = FakeXe2()

    kq5034_module = types.ModuleType("kq5034")
    kq5034_module.__path__ = []
    attendance_api_module = types.ModuleType("kq5034.attendance_api")
    attendance_api_module.ensure_attendance_runtime = lambda: None
    attendance_api_module._normalize_shop = lambda shop_id: (int(shop_id), f"shop{int(shop_id)}")
    attendance_api_module._close_kqtools_browser = lambda tools: None
    tools_module = types.ModuleType("kq5034.tools")
    tools_module.KqTools = FakeKqTools
    monkeypatch.setitem(sys.modules, "kq5034", kq5034_module)
    monkeypatch.setitem(sys.modules, "kq5034.attendance_api", attendance_api_module)
    monkeypatch.setitem(sys.modules, "kq5034.tools", tools_module)

    summary = nianzhu_course_sheets.run_nianzhu_course_sheet_step1(
        session,
        update_lessons=False,
        update_clockins=True,
        clockin_pattern="",
        close_browser=False,
    )
    session.commit()

    assert exported_urls == ["https://admin.xiaoe-tech.com/t/clockin-1"]
    assert summary["clockin_pattern"] == ""
    assert summary["effective_clockin_pattern"] == ""
    assert summary["clockin_names"] == ["打卡数"]
    assert summary["clockin_data_insert_count"] == 1

    clockin_data = _find_sheet(session, CLOCKIN_DATA_SHEET_KEY)
    rows = [dict(zip(clockin_data.document_json["columns"], row)) for row in clockin_data.document_json["rows"]]
    assert rows == [
        {
            "clockin_data_id": 1,
            "user_id2": "u1",
            "nickname": "",
            "groupname": "",
            "publish_time": "2026-05-20 08:00:00",
            "update_content": "今日打卡",
            "update_title": "",
            "update_type": "",
            "tags": "",
            "read_num": "",
            "like_num": "",
            "comment_num": "",
            "is_essence": "",
            "share_num": "",
            "update_url": "",
            "clockin_name": "打卡数",
            "is_repair": "",
            "task_date": "",
            "extra": "{}",
            "clockin_id": 1,
        }
    ]


def test_normalize_nianzhu_course_sheet_names_strips_existing_prefixes(session: Session) -> None:
    _create_nianzhu_workbook(session)
    materialize_nianzhu_course_sheets(session, replace=False)

    video_config = _find_sheet(session, VIDEO_CONFIG_SHEET_KEY)
    video_document = copy.deepcopy(video_config.document_json)
    video_document["rows"][0][6] = "d250106念住闯关-第01课 测试课程"
    video_config.document_json = video_document

    clockin_config = _find_sheet(session, CLOCKIN_CONFIG_SHEET_KEY)
    clockin_config_document = copy.deepcopy(clockin_config.document_json)
    clockin_config_document["rows"][0][1] = "d250106念住闯关-打卡数"
    clockin_config.document_json = clockin_config_document

    clockin_data = _find_sheet(session, CLOCKIN_DATA_SHEET_KEY)
    clockin_data_document = copy.deepcopy(clockin_data.document_json)
    clockin_data_document["rows"][0][15] = "d250106念住闯关打卡"
    clockin_data.document_json = clockin_data_document

    session.add(video_config)
    session.add(clockin_config)
    session.add(clockin_data)
    session.commit()

    summary = normalize_nianzhu_course_sheet_names(session)
    session.commit()

    assert summary["changed_cells"] == 3
    session.refresh(video_config)
    session.refresh(clockin_config)
    session.refresh(clockin_data)
    assert video_config.document_json["rows"][0][6] == "第01课 测试课程"
    assert video_config.document_json["grid_rows"][1][6] == "第01课 测试课程"
    assert clockin_config.document_json["rows"][0][1] == "打卡数"
    assert clockin_data.document_json["rows"][0][15] == "打卡"


def test_nianzhu_course_sheet_names_use_workbook_local_scope() -> None:
    assert nianzhu_course_sheets._strip_course_name_prefix(
        "d250106念住闯关-第01课 测试课程",
        "d250106念住闯关",
    ) == "第01课 测试课程"
    assert nianzhu_course_sheets._strip_course_name_prefix(
        "d250106念住闯关打卡",
        "d250106念住闯关",
    ) == "打卡"
    assert nianzhu_course_sheets._local_clockin_pattern(
        "d250106念住闯关-*",
        "d250106念住闯关",
    ) == "*"
