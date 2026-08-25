from __future__ import annotations

import json
from datetime import date

from backend.api import note_sheets
from backend.core.attendance.course_data_sheet_storage import make_table_document_from_dicts
from backend.core.attendance.fanbei_course_sheets import (
    _build_step2_column_map,
    _fanbei_identity_repair_eligible,
    _plan_fanbei_registration_user_id_repairs,
)


def _document(columns: list[str], rows: list[dict[str, object]]) -> dict[str, object]:
    return make_table_document_from_dicts(columns=columns, rows=rows)


def test_course_progress_reads_forwarded_clockin_identity_and_group(monkeypatch):
    monkeypatch.setattr(note_sheets, "_query_registration_detection_user_ids_by_phone", lambda _phone: [])
    monkeypatch.setattr(note_sheets, "_query_registration_detection_user_ids_by_names", lambda _names: [])
    clockin = _document(
        ["user_id2", "nickname", "extra"],
        [{
            "user_id2": "correct-id",
            "nickname": "店铺昵称",
            "extra": json.dumps({"打卡昵称": "和风细雨", "打卡分组": "第一组"}, ensure_ascii=False),
        }],
    )

    progress = note_sheets._collect_registration_course_user_progress({}, clockin)
    candidates = note_sheets._build_registration_user_id_detection_candidates(
        {"姓名": "吉春蓉", "微信昵称": "和风细雨", "分组": "第一组"},
        progress,
    )

    assert len(candidates) == 1
    assert candidates[0].user_id == "correct-id"
    assert candidates[0].confidence == "high"
    assert candidates[0].clockin_count == 1


def test_plan_repairs_only_empty_wrong_primary_id(monkeypatch):
    monkeypatch.setattr(note_sheets, "_query_registration_detection_user_ids_by_phone", lambda _phone: [])
    monkeypatch.setattr(note_sheets, "_query_registration_detection_user_ids_by_names", lambda _names: [])
    progress = {
        "correct-id": {
            "video_count": 4,
            "clockin_count": 3,
            "labels": {"静"},
            "groups": {"旁听教室"},
        },
    }
    rows = [{
        "序号": "4_13",
        "分组": "旁听教室",
        "姓名": "谭宏丽",
        "微信昵称": "静",
        "用户ID": "wrong-id",
        "关联用户ID": "",
    }]

    repairs = _plan_fanbei_registration_user_id_repairs(rows, progress)

    assert repairs == [{
        "row_index": 0,
        "student_id": "4_13",
        "name": "谭宏丽",
        "old_user_id": "wrong-id",
        "new_user_id": "correct-id",
        "video_count": 4,
        "clockin_count": 3,
        "evidence": ["课程数据姓名/昵称命中", "课程数据分组命中"],
    }]

    progress["wrong-id"] = {"video_count": 1, "clockin_count": 0, "labels": set()}
    assert _plan_fanbei_registration_user_id_repairs(rows, progress) == []


def test_plan_does_not_guess_between_multiple_high_confidence_ids(monkeypatch):
    monkeypatch.setattr(note_sheets, "_query_registration_detection_user_ids_by_phone", lambda _phone: [])
    monkeypatch.setattr(note_sheets, "_query_registration_detection_user_ids_by_names", lambda _names: [])
    progress = {
        "candidate-a": {"video_count": 1, "clockin_count": 1, "labels": {"同名"}, "groups": {"第一组"}},
        "candidate-b": {"video_count": 2, "clockin_count": 1, "labels": {"同名"}, "groups": {"第一组"}},
    }
    rows = [{"序号": "1_01", "分组": "第一组", "姓名": "同名", "用户ID": "wrong-id"}]

    assert _plan_fanbei_registration_user_id_repairs(rows, progress) == []


def test_identity_repair_starts_only_after_three_days():
    start = date(2026, 8, 9)

    assert not _fanbei_identity_repair_eligible(start, date(2026, 8, 11))
    assert _fanbei_identity_repair_eligible(start, date(2026, 8, 12))


def test_step2_maps_resolved_identity_back_to_attendance_user_id():
    mapping = _build_step2_column_map(
        ["学号", "姓名", "用户ID", "打卡数"],
        ["user_id2", "打卡数"],
    )

    assert mapping == {0: 2, 1: 3}
