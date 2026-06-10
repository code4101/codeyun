from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, select

import backend.core.fanbei_course_sheets as fanbei_course_sheets
from backend.core.fanbei_course_sheets import (
    CLOCKIN_CONFIG_SHEET_KEY,
    CLOCKIN_DATA_COLUMNS,
    CLOCKIN_DATA_SHEET_KEY,
    VIDEO_CONFIG_COLUMNS,
    VIDEO_CONFIG_SHEET_KEY,
    VIDEO_DATA_COLUMNS,
    VIDEO_DATA_SHEET_KEY,
    materialize_fanbei_course_sheets,
    rebuild_fanbei_attendance_from_course_sheets,
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


def _create_fanbei_workbook(session: Session) -> None:
    workbook = WorkbookDocument(
        id="3",
        numeric_id=3,
        title="20260509梵呗初阶",
        owner_user_id=2,
    )
    columns = ["分组", "学号", "打卡数", "19:30 第01课", "19:30 第02课", "当前应返款"]
    rows = [
        ["1组", "101", "", "", "", "=FORMULA"],
        ["1组", "102", "old", "old", "old", "=KEEP"],
    ]
    attendance = SheetDocument(
        id="6",
        numeric_id=6,
        scope="notes",
        owner_type="course_workbook",
        owner_key="20260509-fanbei-chujie",
        sheet_key="attendance",
        title="考勤表",
        document_json=_sheet_document(columns, rows),
        owner_user_id=2,
    )
    registration = SheetDocument(
        id="7",
        numeric_id=7,
        scope="notes",
        owner_type="course_workbook",
        owner_key="20260509-fanbei-chujie",
        sheet_key="registration",
        title="报名表",
        document_json=_sheet_document(
            ["序号", "姓名", "用户ID", "关联用户ID"],
            [
                ["101", "甲", "u1", ""],
                ["102", "乙", "", ""],
            ],
        ),
        owner_user_id=2,
    )
    session.add(workbook)
    session.add(attendance)
    session.add(registration)
    session.add(WorkbookSheetLink(workbook_id="3", sheet_id="6", order_index=10))
    session.commit()


def _find_sheet(session: Session, sheet_key: str) -> SheetDocument:
    sheet = session.exec(select(SheetDocument).where(SheetDocument.sheet_key == sheet_key)).first()
    assert sheet is not None
    return sheet


def _row_from_dict(columns: list[str], row: dict[str, object]) -> list[object]:
    return [row.get(column, "") for column in columns]


def test_fanbei_step2_column_map_accepts_real_lesson_title_prefix() -> None:
    sheet_columns = ["打卡数", "19:30~20:32 第01课", "19:30~20:03 第02课"]
    data_columns = [
        "user_id2",
        "打卡数",
        "2606堂1-梵呗的概念与历史",
        "2606堂2-梵呗的知识及《炉香赞》教学",
    ]

    assert fanbei_course_sheets._build_step2_column_map(sheet_columns, data_columns) == {
        1: 0,
        2: 1,
        3: 2,
    }


def test_fanbei_lesson_bindings_use_ordered_config_for_attendance_headers() -> None:
    sheet_columns = ["打卡数", "19:30~20:32 第01课", "19:30~20:03 第02课"]
    video_config_rows = [
        {
            "lesson_id": 21,
            "lesson_id2": "l_testLesson01",
            "lesson_name": "2606堂1-梵呗的概念与历史",
        },
        {
            "lesson_id": 22,
            "lesson_id2": "https://example.com/lesson-02",
            "lesson_name": "2606堂2-梵呗的知识及《炉香赞》教学",
        },
    ]
    bindings = fanbei_course_sheets._build_lesson_bindings(video_config_rows)
    data_columns = ["user_id2", "打卡数", *(binding["output_column"] for binding in bindings)]

    assert fanbei_course_sheets._build_step2_column_map(
        sheet_columns,
        data_columns,
        lesson_bindings=bindings,
    ) == {
        1: 0,
        2: 1,
        3: 2,
    }

    document, changed_count = fanbei_course_sheets._apply_fanbei_attendance_header_links(
        {
            "columns": sheet_columns,
            "rows": [],
            "grid_rows": [sheet_columns],
            "data_start_row": 1,
            "field_row_index": 0,
            "cell_meta": {},
        },
        video_config_rows=video_config_rows,
    )

    assert changed_count == 2
    assert (
        document["grid_rows"][0][1]["link"]["url"]
        == "https://admin.xiaoe-tech.com/t/live_management#/userOperation?id=l_testLesson01&tabName=UserManage"
    )
    assert document["grid_rows"][0][2]["link"]["url"] == "https://example.com/lesson-02"


def test_materialize_fanbei_course_sheets_creates_storage_sheets(session: Session, monkeypatch) -> None:
    _create_fanbei_workbook(session)
    monkeypatch.setattr(fanbei_course_sheets, "_query_legacy_lesson_rows", lambda course_name: ([], "skip lesson"))
    monkeypatch.setattr(fanbei_course_sheets, "_query_legacy_lesson_data_rows", lambda lesson_ids: ([], "skip data"))
    monkeypatch.setattr(fanbei_course_sheets, "_query_legacy_clockin_rows", lambda course_name: ([], "skip clockin"))
    monkeypatch.setattr(fanbei_course_sheets, "_query_legacy_clockin_data_rows", lambda clockin_ids: ([], "skip clockin data"))

    summary = materialize_fanbei_course_sheets(session, replace=False)
    session.commit()

    assert summary["course_name"] == "d260509梵呗初阶"
    assert {item["sheet_key"] for item in summary["sheets"]} == {
        VIDEO_CONFIG_SHEET_KEY,
        VIDEO_DATA_SHEET_KEY,
        CLOCKIN_CONFIG_SHEET_KEY,
        CLOCKIN_DATA_SHEET_KEY,
    }
    assert _find_sheet(session, VIDEO_CONFIG_SHEET_KEY).document_json["columns"] == VIDEO_CONFIG_COLUMNS
    assert _find_sheet(session, VIDEO_DATA_SHEET_KEY).document_json["columns"] == VIDEO_DATA_COLUMNS


def test_materialize_fanbei_course_sheets_serializes_legacy_datetimes(session: Session, monkeypatch) -> None:
    _create_fanbei_workbook(session)
    monkeypatch.setattr(
        fanbei_course_sheets,
        "_query_legacy_lesson_rows",
        lambda course_name: (
            [{
                "lesson_id": 1,
                "start_date": datetime(2026, 5, 9, 19, 30),
                "lesson_name": "d260509梵呗初阶-第01课",
                "video_duration": 3600,
            }],
            None,
        ),
    )
    monkeypatch.setattr(
        fanbei_course_sheets,
        "_query_legacy_lesson_data_rows",
        lambda lesson_ids: (
            [{
                "lesson_data_id": 1,
                "user_id2": "u1",
                "update_time": datetime(2026, 5, 9, 20, 30),
                "lesson_id": 1,
            }],
            None,
        ),
    )
    monkeypatch.setattr(fanbei_course_sheets, "_query_legacy_clockin_rows", lambda course_name: ([], "skip clockin"))
    monkeypatch.setattr(fanbei_course_sheets, "_query_legacy_clockin_data_rows", lambda clockin_ids: ([], "skip clockin data"))

    materialize_fanbei_course_sheets(session, replace=False)
    session.commit()

    video_config = _find_sheet(session, VIDEO_CONFIG_SHEET_KEY)
    video_data = _find_sheet(session, VIDEO_DATA_SHEET_KEY)
    assert video_config.document_json["rows"][0][VIDEO_CONFIG_COLUMNS.index("start_date")] == "2026-05-09 19:30:00"
    assert video_data.document_json["rows"][0][VIDEO_DATA_COLUMNS.index("update_time")] == "2026-05-09 20:30:00"


def test_rebuild_fanbei_attendance_uses_sheet_storage_and_compacts_video_rows(session: Session, monkeypatch) -> None:
    _create_fanbei_workbook(session)
    monkeypatch.setattr(fanbei_course_sheets, "_query_legacy_lesson_rows", lambda course_name: ([], "skip lesson"))
    monkeypatch.setattr(fanbei_course_sheets, "_query_legacy_lesson_data_rows", lambda lesson_ids: ([], "skip data"))
    monkeypatch.setattr(fanbei_course_sheets, "_query_legacy_clockin_rows", lambda course_name: ([], "skip clockin"))
    monkeypatch.setattr(fanbei_course_sheets, "_query_legacy_clockin_data_rows", lambda clockin_ids: ([], "skip clockin data"))
    materialize_fanbei_course_sheets(session, replace=False)
    session.commit()

    video_config = _find_sheet(session, VIDEO_CONFIG_SHEET_KEY)
    video_config.document_json = _sheet_document(
        VIDEO_CONFIG_COLUMNS,
        [
            _row_from_dict(
                VIDEO_CONFIG_COLUMNS,
                {
                    "lesson_id": 1,
                    "start_date": "2026-05-09 19:30:00",
                    "lesson_name": "d260509梵呗初阶-第01课",
                    "video_duration": 3600,
                },
            ),
            _row_from_dict(
                VIDEO_CONFIG_COLUMNS,
                {
                    "lesson_id": 2,
                    "start_date": "2026-05-10 19:30:00",
                    "lesson_name": "d260509梵呗初阶-第02课",
                    "video_duration": 3600,
                },
            ),
        ],
    )
    video_data = _find_sheet(session, VIDEO_DATA_SHEET_KEY)
    video_data.document_json = _sheet_document(
        VIDEO_DATA_COLUMNS,
        [
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 1,
                    "user_id2": "u1",
                    "studio_seconds": 1800,
                    "playback_seconds": 0,
                    "cum_seconds": 1800,
                    "update_time": "2026-05-09 21:00:00",
                    "lesson_id": 1,
                    "lesson_name": "d260509梵呗初阶-第01课",
                },
            ),
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 2,
                    "user_id2": "u1",
                    "studio_seconds": 1800,
                    "playback_seconds": 1800,
                    "cum_seconds": 3600,
                    "update_time": "2026-05-10 21:00:00",
                    "lesson_id": 1,
                    "lesson_name": "d260509梵呗初阶-第01课",
                },
            ),
            _row_from_dict(
                VIDEO_DATA_COLUMNS,
                {
                    "lesson_data_id": 3,
                    "user_id2": "u1",
                    "studio_seconds": 0,
                    "playback_seconds": 3600,
                    "cum_seconds": 3600,
                    "update_time": "2026-05-12 08:00:00",
                    "lesson_id": 2,
                    "lesson_name": "d260509梵呗初阶-第02课",
                },
            ),
        ],
    )
    clockin_data = _find_sheet(session, CLOCKIN_DATA_SHEET_KEY)
    clockin_data.document_json = _sheet_document(
        CLOCKIN_DATA_COLUMNS,
        [
            _row_from_dict(CLOCKIN_DATA_COLUMNS, {"clockin_data_id": 1, "user_id2": "u1", "update_title": "学修日志01", "task_date": "2026-05-09", "clockin_id": 1}),
            _row_from_dict(CLOCKIN_DATA_COLUMNS, {"clockin_data_id": 2, "user_id2": "u1", "update_title": "学修日志01", "task_date": "2026-05-09", "clockin_id": 1}),
            _row_from_dict(CLOCKIN_DATA_COLUMNS, {"clockin_data_id": 3, "user_id2": "u1", "update_title": "学修日志02", "task_date": "2026-05-10", "clockin_id": 1}),
            _row_from_dict(CLOCKIN_DATA_COLUMNS, {"clockin_data_id": 4, "user_id2": "u1", "update_title": "测试-学修日志03", "task_date": "2026-05-11", "clockin_id": 1}),
        ],
    )
    session.add(video_config)
    session.add(video_data)
    session.add(clockin_data)
    session.commit()

    summary = rebuild_fanbei_attendance_from_course_sheets(session)
    session.commit()

    assert summary["updated_rows"] == 1
    assert summary["updated_cells"] == 3
    assert summary["video_data_rows"] == 3
    assert summary["video_data_compacted_rows"] == 2

    attendance = _find_sheet(session, "attendance")
    assert attendance.document_json["rows"][0] == ["1组", "101", 2, "当堂完成/100%", "第1天回放/100%", "=FORMULA"]
    assert attendance.document_json["rows"][1] == ["1组", "102", "old", "old", "old", "=KEEP"]

    session.refresh(video_data)
    assert len(video_data.document_json["rows"]) == 2
