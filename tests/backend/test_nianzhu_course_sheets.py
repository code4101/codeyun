from __future__ import annotations

import copy
from datetime import datetime

import pytest
from sqlmodel import Session, select

import backend.core.nianzhu_course_sheets as nianzhu_course_sheets
from backend.core.nianzhu_course_sheets import (
    CLOCKIN_CONFIG_COLUMNS,
    CLOCKIN_CONFIG_SHEET_KEY,
    CLOCKIN_DATA_COLUMNS,
    CLOCKIN_DATA_SHEET_KEY,
    VIDEO_CONFIG_COLUMNS,
    VIDEO_CONFIG_SHEET_KEY,
    VIDEO_DATA_COLUMNS,
    VIDEO_DATA_SHEET_KEY,
    materialize_nianzhu_course_sheets,
    normalize_nianzhu_course_sheet_names,
    rebuild_nianzhu_attendance_from_course_sheets,
)
from backend.models import SheetDocument, WorkbookDocument, WorkbookSheetLink


@pytest.fixture(autouse=True)
def _disable_legacy_queries(monkeypatch) -> None:
    monkeypatch.setattr(
        nianzhu_course_sheets,
        "_query_legacy_lesson_rows",
        lambda course_name: ([], "测试默认不连接 lesson_table"),
    )
    monkeypatch.setattr(
        nianzhu_course_sheets,
        "_query_legacy_lesson_data_rows",
        lambda legacy_lesson_ids: ([], "测试默认不连接 lesson_data_table"),
    )
    monkeypatch.setattr(
        nianzhu_course_sheets,
        "_query_legacy_clockin_rows",
        lambda course_name: ([], "测试默认不连接 clockin_table"),
    )
    monkeypatch.setattr(
        nianzhu_course_sheets,
        "_query_legacy_clockin_data_rows",
        lambda legacy_clockin_ids: ([], "测试默认不连接 clockin_data_table"),
    )


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
        "规则版本",
    ]
    rows = [
        ["101", "甲", "u1", 0, 0, 0, 0, 5, "1遍/100%", "3遍/200%", "参加/100%", "B组", "当前规则"],
        ["102", "乙", "u2", 0, 0, 0, 0, 10, "1遍/100%", "", "", "A组", "当前规则"],
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
    session.add(workbook)
    session.add(attendance)
    session.add(WorkbookSheetLink(workbook_id="7", sheet_id="21", order_index=10))
    session.commit()


def _find_sheet(session: Session, sheet_key: str) -> SheetDocument:
    sheet = session.exec(select(SheetDocument).where(SheetDocument.sheet_key == sheet_key)).first()
    assert sheet is not None
    return sheet


def test_parse_lesson_data_export_rows_converts_duration_text(tmp_path) -> None:
    export_file = tmp_path / "lesson.csv"
    export_file.write_text(
        "用户ID,累计观看时长,播放进度,上次播放时间,完成时间\n"
        "u1,1小时2分钟3秒,104%,--,--\n",
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
            "progress": 104,
            "cum_seconds": 3723,
            "last_play_time": "",
            "finish_time": "",
        }
    ]


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
    assert rows[0][8] == ""
    assert rows[0][3] == 1
    assert rows[0][4] == 1
    assert rows[0][5] == 20
    assert rows[0][6] == 150
    assert rows[0][7] == 10
    assert rows[1][8] == "1遍/100%"


def test_materialize_video_config_preserves_legacy_lesson_table_fields(
    session: Session,
    monkeypatch,
) -> None:
    _create_nianzhu_workbook(session)

    def fake_query_legacy_lesson_rows(course_name: str):
        assert course_name == "d250106念住闯关"
        return [
            {
                "lesson_id": 11037,
                "start_date": "2025-01-01 00:00:00",
                "end_date": None,
                "next_update": "2026-05-21 00:00:00",
                "lesson_id2": "https://admin.xiaoe-tech.com/t/course-1",
                "shop_id": 1,
                "lesson_name": "d250106念住闯关-第01课 测试课程",
                "video_duration": 6271,
            }
        ], None

    monkeypatch.setattr(nianzhu_course_sheets, "_query_legacy_lesson_rows", fake_query_legacy_lesson_rows)

    summary = materialize_nianzhu_course_sheets(session, replace=False)
    session.commit()

    assert summary["legacy_lesson_rows"] == 1
    video_config = _find_sheet(session, VIDEO_CONFIG_SHEET_KEY)
    config_columns = video_config.document_json["columns"]
    assert config_columns == VIDEO_CONFIG_COLUMNS
    config_rows = [dict(zip(config_columns, row)) for row in video_config.document_json["rows"]]
    lesson_config = next(row for row in config_rows if row["lesson_id"] == 1)
    assert lesson_config["shop_id"] == 1
    assert lesson_config["lesson_name"] == "第01课 测试课程"
    assert lesson_config["lesson_id2"] == "https://admin.xiaoe-tech.com/t/course-1"
    assert lesson_config["start_date"] == "2025-01-01 00:00:00"
    assert lesson_config["end_date"] == ""
    assert lesson_config["next_update"] == "2026-05-21 00:00:00"
    assert lesson_config["video_duration"] == 6271

    video_data = _find_sheet(session, VIDEO_DATA_SHEET_KEY)
    data_columns = video_data.document_json["columns"]
    assert data_columns == VIDEO_DATA_COLUMNS
    data_rows = [dict(zip(data_columns, row)) for row in video_data.document_json["rows"]]
    first_lesson_data = next(row for row in data_rows if row["user_id2"] == "u1" and row["lesson_id"] == 1)
    assert first_lesson_data["lesson_id"] == 1


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
