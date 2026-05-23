from backend.core import nianzhu_attendance_schedule as schedule
from backend.models import SheetDocument


def test_apply_nianzhu_step3_calculates_refunds_scores_and_styles(session):
    columns = [
        "姓名",
        "用户ID",
        "优秀学员评分",
        "视频应返款",
        "打卡数",
        "05:20 第01课",
        "05:20 第02课",
        "05:20 第12课",
        "第2届答疑",
        "追踪分组",
        "追踪状态",
        "冻结时间",
        "规则版本",
    ]
    rows = [
        [
            "学员A",
            "u1",
            "old",
            "old",
            "",
            "1遍/91%",
            "学习中/89%",
            "3遍/250%",
            "学习中/63%",
            "历史组",
            "追踪中",
            "",
            schedule.CURRENT_RULE,
        ],
        [
            "学员B",
            "u2",
            0,
            0,
            "",
            "1遍/91%",
            "2遍/151%",
            "3遍/250%",
            "",
            "当前组",
            "已冻结",
            "2026-05-16 14:07:07",
            schedule.LEGACY_BEFORE_20250522_RULE,
        ],
    ]
    document = SheetDocument(
        numeric_id=721,
        title="考勤表",
        owner_user_id=1,
        document_json={
            "columns": columns,
            "rows": rows,
            "grid_rows": [
                [""] * len(columns),
                columns,
                [""] * len(columns),
                *rows,
            ],
            "data_start_row": 3,
            "field_row_index": 1,
            "cell_meta": {"3:5": {"style": {"background_color": "#FFFFFF"}}},
        },
    )
    session.add(document)
    session.commit()

    summary = schedule._apply_nianzhu_attendance_step3_to_sheet(
        session=session,
        sheet_id=721,
        course_name=schedule.NIANZHU_CHUANGGUAN_COURSE_NAME,
    )

    assert summary["lesson_columns"] == 3
    assert summary["non_refund_progress_columns"] == 1
    assert summary["video_refund_total"] == 40
    assert summary["score_total"] == 2
    assert summary["skipped_rows"] == 1
    session.refresh(document)
    next_rows = document.document_json["rows"]
    assert next_rows[0][2:4] == [2, 40]
    assert next_rows[1][2:4] == [0, 0]

    cell_meta = document.document_json["cell_meta"]
    assert cell_meta["3:5"]["style"]["background_color"] != "#FFFFFF"
    assert "3:6" not in cell_meta
    assert "3:8" in cell_meta
    assert "4:5" not in cell_meta
    assert "4:8" not in cell_meta


def test_run_nianzhu_step3_endpoint_rebuilds_from_course_sheets(client, test_device, monkeypatch):
    import backend.api.device_control as device_control

    calls = []

    def fake_rebuild_nianzhu_attendance_from_course_sheets(session, **kwargs):
        calls.append(kwargs)
        return {
            "attendance_sheet_id": kwargs["attendance_sheet_id"],
            "active_only": kwargs["active_only"],
            "rows": 3,
            "updated_rows": 2,
            "updated_cells": 5,
            "styled_cells": 7,
        }

    monkeypatch.setattr(
        device_control,
        "rebuild_nianzhu_attendance_from_course_sheets",
        fake_rebuild_nianzhu_attendance_from_course_sheets,
    )

    response = client.post(
        "/api/device-control/attendance/nianzhu/step3",
        json={"sheet_id": 721, "course_name": "d250106念住闯关"},
        headers={"X-Device-Token": test_device["token"]},
    )

    assert response.status_code == 200
    assert response.json()["sheet_id"] == 721
    assert response.json()["attendance_sheet_id"] == 721
    assert "从课程存储 sheet 重建 3 行" in response.json()["message"]
    assert calls == [{"attendance_sheet_id": 721, "active_only": True}]


def test_run_nianzhu_step3_endpoint_include_frozen_rebuilds_all_rows(client, test_device, monkeypatch):
    import backend.api.device_control as device_control

    calls = []

    def fake_rebuild_nianzhu_attendance_from_course_sheets(session, **kwargs):
        calls.append(kwargs)
        return {
            "attendance_sheet_id": kwargs["attendance_sheet_id"],
            "active_only": kwargs["active_only"],
            "rows": 3,
            "updated_rows": 1,
            "updated_cells": 2,
            "styled_cells": 4,
        }

    monkeypatch.setattr(
        device_control,
        "rebuild_nianzhu_attendance_from_course_sheets",
        fake_rebuild_nianzhu_attendance_from_course_sheets,
    )

    response = client.post(
        "/api/device-control/attendance/nianzhu/step3",
        json={"sheet_id": 21, "course_name": "d250106念住闯关", "include_frozen": True},
        headers={"X-Device-Token": test_device["token"]},
    )

    assert response.status_code == 200
    assert response.json()["active_only"] is False
    assert calls == [{"attendance_sheet_id": 21, "active_only": False}]


def test_run_nianzhu_step1_endpoint_updates_course_sheets(client, test_device, monkeypatch):
    calls = []

    def fake_run_nianzhu_course_sheet_step1(session, **kwargs):
        calls.append(kwargs)
        return {
            "workbook_id": kwargs["workbook_id"],
            "course_name": kwargs["course_name"],
            "lesson_data_insert_count": 2,
            "clockin_data_insert_count": 3,
        }

    monkeypatch.setattr(
        "backend.api.device_control.run_nianzhu_course_sheet_step1",
        fake_run_nianzhu_course_sheet_step1,
    )

    response = client.post(
        "/api/device-control/attendance/nianzhu/step1",
        json={
            "workbook_id": 7,
            "attendance_sheet_id": 21,
            "course_name": "d250106念住闯关",
            "shop_id": 1,
            "update_lessons": True,
            "update_clockins": True,
            "clockin_pattern": "d250106念住闯关-*",
            "close_browser": False,
        },
        headers={"X-Device-Token": test_device["token"]},
    )

    assert response.status_code == 200
    assert response.json()["lesson_data_insert_count"] == 2
    assert calls == [
        {
            "workbook_id": 7,
            "attendance_sheet_id": 21,
            "course_name": "d250106念住闯关",
            "shop_id": 1,
            "update_lessons": True,
            "update_clockins": True,
            "clockin_pattern": "d250106念住闯关-*",
            "close_browser": False,
        }
    ]


def test_run_nianzhu_step2_endpoint_compacts_course_sheets(client, test_device, monkeypatch):
    import backend.api.device_control as device_control

    calls = []

    def fake_compact_nianzhu_course_sheet_step2(session, **kwargs):
        calls.append(("step2", kwargs))
        return {
            "attendance_sheet_id": kwargs["attendance_sheet_id"],
            "video_data_rows_before": 4,
            "video_data_rows_after": 2,
            "changed": True,
        }

    def fake_rebuild_nianzhu_attendance_from_course_sheets(session, **kwargs):
        calls.append(("rebuild", kwargs))
        return {
            "attendance_sheet_id": kwargs["attendance_sheet_id"],
            "updated_rows": 1,
        }

    monkeypatch.setattr(device_control, "compact_nianzhu_course_sheet_step2", fake_compact_nianzhu_course_sheet_step2)
    monkeypatch.setattr(
        device_control,
        "rebuild_nianzhu_attendance_from_course_sheets",
        fake_rebuild_nianzhu_attendance_from_course_sheets,
    )

    response = client.post(
        "/api/device-control/attendance/nianzhu/step2",
        json={"attendance_sheet_id": 21, "rebuild": True, "include_frozen": True},
        headers={"X-Device-Token": test_device["token"]},
    )

    assert response.status_code == 200
    assert response.json()["step2"]["video_data_rows_after"] == 2
    assert response.json()["rebuild"]["updated_rows"] == 1
    assert calls == [
        ("step2", {"attendance_sheet_id": 21}),
        ("rebuild", {"attendance_sheet_id": 21, "active_only": False}),
    ]
