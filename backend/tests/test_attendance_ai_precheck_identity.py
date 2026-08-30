from __future__ import annotations

from backend.core.attendance import ai_precheck
from backend.models import AttendanceWjxDataEntry


def _entry(*, process_status: str = "") -> AttendanceWjxDataEntry:
    return AttendanceWjxDataEntry(
        activity_id="test",
        seq=741,
        course_name="修道班8期5阶",
        student_id_text="16",
        student_name="董佳玮",
        correction_request="姓名是童佳玮，昵称是杺云流水",
        process_status=process_status,
    )


def test_identity_precheck_compares_registration_and_attendance(monkeypatch) -> None:
    monkeypatch.setattr(ai_precheck, "_collect_course_summary", lambda *_args: None)
    monkeypatch.setattr(
        ai_precheck,
        "_collect_course_identity_rows",
        lambda *_args: {
            "registration": {"序号": "16", "姓名": "董佳玮", "微信昵称": "松云流水"},
            "attendance": {"学号": "16", "姓名": "董佳玮", "昵称": "松云流水"},
        },
    )

    result = ai_precheck.build_attendance_wjx_ai_precheck(
        _entry(),
        session=None,  # type: ignore[arg-type]
        use_codex_cli=False,
    )

    assert result["skill"] == ai_precheck.IDENTITY_CORRECTION_SKILL
    assert result["status"] == "identity_change_confirmed"
    assert result["facts"]["requested_identity"] == {"姓名": "童佳玮", "昵称": "杺云流水"}
    assert result["facts"]["identity_comparison"]["姓名"] == {
        "before": "董佳玮",
        "after": "童佳玮",
        "changed": True,
    }
    assert "报名表：姓名“董佳玮”，微信昵称“松云流水”" in result["report"]
    assert "因此，这条反馈应理解为修改姓名/昵称" in result["report"]
    assert "AI 初判只记录证据和需求判断" in result["report"]


def test_processed_identity_precheck_preserves_before_values(monkeypatch) -> None:
    monkeypatch.setattr(ai_precheck, "_collect_course_summary", lambda *_args: None)
    monkeypatch.setattr(
        ai_precheck,
        "_collect_course_identity_rows",
        lambda *_args: {
            "registration": {"序号": "16", "姓名": "童佳玮", "微信昵称": "杺云流水"},
            "attendance": {"学号": "16", "姓名": "童佳玮", "昵称": "杺云流水"},
        },
    )
    process_status = "已处理：姓名由“董佳玮”更正为“童佳玮”，昵称由“松云流水”更正为“杺云流水”。"

    result = ai_precheck.build_attendance_wjx_ai_precheck(
        _entry(process_status=process_status),
        session=None,  # type: ignore[arg-type]
        use_codex_cli=False,
    )

    assert result["facts"]["identity_comparison"]["昵称"]["before"] == "松云流水"
    assert "处理前原始数据（依据处理状态留痕）" in result["report"]
    assert "报名表和考勤表当前值均已与问卷目标值一致" in result["report"]
    assert process_status in result["report"]


def test_identity_precheck_understands_surname_correction_from_questionnaire_name(monkeypatch) -> None:
    entry = AttendanceWjxDataEntry(
        activity_id="test",
        seq=742,
        course_name="修道班8期5阶",
        student_id_text="10",
        student_name="苑小杰",
        correction_request="考勤表名字写错了，我姓苑，不是范。",
    )
    monkeypatch.setattr(ai_precheck, "_collect_course_summary", lambda *_args: None)
    monkeypatch.setattr(
        ai_precheck,
        "_collect_course_identity_rows",
        lambda *_args: {
            "registration": {"序号": "10", "姓名": "范小杰", "微信昵称": "山月小安"},
            "attendance": {"学号": "10", "姓名": "范小杰", "昵称": "山月小安"},
        },
    )

    result = ai_precheck.build_attendance_wjx_ai_precheck(
        entry,
        session=None,  # type: ignore[arg-type]
        use_codex_cli=False,
    )

    assert result["facts"]["requested_identity"] == {"姓名": "苑小杰"}
    assert result["facts"]["identity_comparison"]["姓名"] == {
        "before": "范小杰",
        "after": "苑小杰",
        "changed": True,
    }
    assert "因此，这条反馈应理解为修改姓名/昵称" in result["report"]
