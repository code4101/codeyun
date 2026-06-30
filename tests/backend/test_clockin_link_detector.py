from backend.core.attendance.clockin_link_detector import (
    build_xiaoe_diary_list_url,
    choose_attendance_clockin_activities,
)


def _activity(activity_id: str, title: str, *, actor_count: int = 0, clock_count: int = 0) -> dict:
    return {
        "id": activity_id,
        "app_id": "apporrfwkpb5562",
        "title": title,
        "activity_start_at": "2026-06-28",
        "activity_stop_at": "2026-07-27",
        "task_count": 24,
        "actor_user_count": actor_count,
        "clock_count": clock_count,
        "mini_middle_url": (
            "https://apporrfwkpb5562.h5.xet.pomoho.com/xiaoe_clock/mini_middle"
            f"?activity_id={activity_id}&app_id=apporrfwkpb5562"
        ),
    }


def test_build_xiaoe_diary_list_url_uses_activity_id_and_encoded_middle_url() -> None:
    url = build_xiaoe_diary_list_url(
        activity_id="ac_center",
        app_id="apporrfwkpb5562",
        mini_middle_url="https://example.test/middle?activity_id=ac_center&app_id=apporrfwkpb5562",
    )

    assert "punchDetail/diaryList?activity_id=ac_center" in url
    assert "miniMiddleUrl=https%3A%2F%2Fexample.test%2Fmiddle%3Factivity_id%3Dac_center%26app_id%3Dapporrfwkpb5562" in url


def test_choose_attendance_clockin_activities_prefers_center_and_excludes_audit_only() -> None:
    result = choose_attendance_clockin_activities(
        [
            _activity("ac_side", "202607念住初阶网课日志【旁听教室】（42）", actor_count=24, clock_count=30),
            _activity("ac_center", "202607念住初阶网课日志【中心教室】（42）", actor_count=7, clock_count=9),
        ],
        target_keywords=["202607念住初阶网课日志", "42"],
    )

    assert [item["activity_id"] for item in result["selected"]] == ["ac_center"]
    assert [item["activity_id"] for item in result["excluded_audit_only"]] == ["ac_side"]
    assert result["selected"][0]["clockin_user_num"] == 7
    assert result["selected"][0]["total_user_num"] == 9
    assert result["selection_reason"] == "matched_center_classroom"


def test_choose_attendance_clockin_activities_does_not_use_audit_only_without_center() -> None:
    result = choose_attendance_clockin_activities(
        [_activity("ac_side", "202607念住初阶网课日志【旁听教室】（42）")],
        target_keywords=["202607念住初阶网课日志", "42"],
    )

    assert result["selected"] == []
    assert [item["activity_id"] for item in result["excluded_audit_only"]] == ["ac_side"]
    assert result["selection_reason"] == "audit_only_without_center"


def test_choose_attendance_clockin_activities_allows_single_unmarked_match() -> None:
    result = choose_attendance_clockin_activities(
        [_activity("ac_plain", "202607觉观网课日志（48）")],
        target_keywords=["202607觉观网课日志", "48"],
    )

    assert [item["activity_id"] for item in result["selected"]] == ["ac_plain"]
    assert result["selection_reason"] == "matched_unmarked_activity"
