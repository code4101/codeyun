import datetime as dt
from pathlib import Path

from backend.core.attendance.course_completion import run_attendance_course_completion_job
from backend.models import SheetDocument


def _cell_text(value):
    return str(value.get("value") if isinstance(value, dict) else value)


def test_attendance_course_completion_archives_due_rows_and_updates_kqmain(session, tmp_path: Path, monkeypatch):
    def serial(year: int, month: int, day: int) -> int:
        return (dt.date(year, month, day) - dt.date(1970, 1, 1)).days + 25569

    columns = [
        "课程类型",
        "在线考勤表",
        "考勤负责人",
        "课次链接",
        "打卡链接",
        "备注",
        "课程开始日期",
        "课程结束日期",
        "考勤实际完成结点",
        "报名费",
        "报名人数",
        "退课人数",
        "实际总报名费",
        "已返款",
        "剩余促学金",
        "返款率",
    ]
    rows = [
        ["念住", "第42届念住", "", "21", "1", "", serial(2026, 7, 1), "=G1+26", "", "620", "", "", "=J1*K1", "", "0", "#DIV/0!"],
        ["念住", {"value": "第41届念住", "link": {"url": "/workbook/10?sheet=9001"}}, "", "21", "1", "", serial(2026, 6, 1), "=G2+26", "", "620", "", "", "=J2*K2", "", "=M2-O2", "=N2/M2"],
        ["觉观", {"value": "第47届觉观", "link": {"url": "/workbook/11?sheet=9002"}}, "", "21", "1", "", serial(2026, 6, 1), "=G3+24", "", "499", "", "", "=J3*K3", "", "=M3-O3", "=N3/M3"],
        ["梵呗初阶", "20260609梵呗初阶", "", "11", "1", "", serial(2026, 6, 9), serial(2026, 6, 24), "", "550", "59", "", "=J4*K4", "", "32450", "0%"],
        ["念住", "20250106念住闯关", "", "", "", "", serial(2025, 1, 6), serial(2026, 6, 1), "", "620", "", "", "=J5*K5", "", "0", "#DIV/0!"],
        ["念住", "20260501第40届念住", "", "21", "1", "", serial(2026, 5, 1), "=G6+26", serial(2026, 5, 28), "620", "10", "1", "=J6*K6", "100", "10", "10%"],
    ]
    sheet = SheetDocument(
        numeric_id=404,
        scope="test",
        sheet_key="404",
        title="课程",
        document_json={
            "schema_version": 1,
            "columns": columns,
            "rows": rows,
            "grid_rows": [columns, *rows, ["备注行"]],
            "data_start_row": 1,
            "cell_meta": {
                "2:1": {"style": {"background_color": "#ABCDEF"}},
                "4:1": {"style": {"background_color": "#EEEEEE"}},
            },
        },
    )
    session.add(sheet)
    session.add(
        SheetDocument(
            numeric_id=9001,
            scope="test",
            sheet_key="9001",
            title="第41届念住考勤表",
            document_json={
                "columns": ["姓名", "已返款", "订单金额"],
                "rows": [["甲", "100", "620"], ["乙", "200", "620"]],
            },
        )
    )
    session.add(
        SheetDocument(
            numeric_id=9002,
            scope="test",
            sheet_key="9002",
            title="第47届觉观考勤表",
            document_json={
                "columns": ["姓名", "已返款", "订单金额"],
                "rows": [["丙", "30", "499"], ["丁", "40", "0"], ["戊", "50", "499"]],
            },
        )
    )
    session.commit()
    monkeypatch.setattr(
        "backend.core.attendance.nianzhu_course_sheets.compact_nianzhu_course_sheet_step2",
        lambda *args, **kwargs: {"changed": False},
    )
    monkeypatch.setattr(
        "backend.core.attendance.nianzhu_course_sheets.rebuild_nianzhu_attendance_from_course_sheets",
        lambda *args, **kwargs: {"rows": 0, "updated_rows": 0, "updated_cells": 0, "styled_cells": 0},
    )

    kqmain_path = tmp_path / "kqmain.py"
    kqmain_path.write_text(
        "\n".join(
            [
                "觉观念住类型 = [",
                '    "d260601第41届念住",',
                '    "d260601第47届觉观",',
                '    "d260701第42届念住",',
                "]",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = run_attendance_course_completion_job(
        session,
        today=dt.date(2026, 6, 28),
        sheet_id=404,
        kqmain_path=kqmain_path,
    )
    session.commit()

    assert result["archived_count"] == 2
    assert result["skipped_count"] == 1
    assert result["skipped_courses"][0]["course_type"] == "梵呗初阶"
    assert result["skipped_courses"][0]["reason"] == "在线考勤表不是本地工作簿链接"
    assert result["sheet_changed"] is True
    assert result["kqmain"]["removed"] == ["d260601第41届念住", "d260601第47届觉观"]
    assert "d260701第42届念住" in kqmain_path.read_text(encoding="utf-8")

    session.refresh(sheet)
    next_rows = sheet.document_json["rows"]
    assert [_cell_text(row[1]) for row in next_rows] == [
        "第42届念住",
        "20260609梵呗初阶",
        "20250106念住闯关",
        "第41届念住",
        "第47届觉观",
        "20260501第40届念住",
    ]
    assert next_rows[2][8] == ""
    assert next_rows[3][8] == str(serial(2026, 6, 28))
    assert next_rows[3][10] == "2"
    assert next_rows[3][13] == "300"
    assert next_rows[4][8] == str(serial(2026, 6, 26))
    assert next_rows[4][10] == "2"
    assert next_rows[4][11] == ""
    assert next_rows[4][13] == "80"
    assert next_rows[3][12] == "=J4*K4"
    assert not any("text_color" in (meta.get("style") or {}) for meta in sheet.document_json["cell_meta"].values())

    second_result = run_attendance_course_completion_job(
        session,
        today=dt.date(2026, 6, 28),
        sheet_id=404,
        kqmain_path=kqmain_path,
    )
    assert second_result["archived_count"] == 0
    assert second_result["kqmain"]["changed"] is False


def test_attendance_course_completion_archives_due_fanbei_course(session, tmp_path: Path, monkeypatch):
    def serial(year: int, month: int, day: int) -> int:
        return (dt.date(year, month, day) - dt.date(1970, 1, 1)).days + 25569

    columns = [
        "课程类型",
        "在线考勤表",
        "考勤负责人",
        "课次链接",
        "打卡链接",
        "备注",
        "课程开始日期",
        "课程结束日期",
        "考勤实际完成结点",
        "报名费",
        "报名人数",
        "退课人数",
        "实际总报名费",
        "已返款",
        "剩余促学金",
        "返款率",
    ]
    rows = [
        ["念住", "第42届念住", "", "21", "1", "", serial(2026, 7, 1), serial(2026, 7, 27), "", "620", "", "", "=J1*K1", "", "0", "#DIV/0!"],
        [
            "梵呗初阶",
            {"value": "20260609梵呗初阶", "link": {"url": "/workbook/12?sheet=9003"}},
            "王秀芹, 卓尔不凡, 陈坤泽",
            "11",
            "1",
            "",
            serial(2026, 6, 9),
            serial(2026, 6, 24),
            "",
            "550",
            "59",
            "",
            "=J2*K2",
            "",
            "32450",
            "0%",
        ],
    ]
    summary_sheet = SheetDocument(
        numeric_id=405,
        scope="test",
        sheet_key="405",
        title="课程",
        document_json={
            "schema_version": 1,
            "columns": columns,
            "rows": rows,
            "grid_rows": [columns, *rows],
            "data_start_row": 1,
        },
    )
    attendance_sheet = SheetDocument(
        numeric_id=9003,
        scope="test",
        sheet_key="9003",
        title="20260609梵呗初阶考勤表",
        document_json={
            "columns": ["姓名", "视频应返款", "已返款", "订单金额", "19:30 第01课"],
            "rows": [["甲", 40, "40", "550", "当堂完成/100%"], ["乙", 0, "0", "0", ""]],
        },
    )
    session.add(summary_sheet)
    session.add(attendance_sheet)
    session.commit()

    fanbei_calls = []

    def fake_rebuild_fanbei(*args, **kwargs):
        fanbei_calls.append(("step2", kwargs))
        return {"updated_rows": 0, "updated_cells": 0}

    def fake_step3(*args, **kwargs):
        fanbei_calls.append(("step3", kwargs))
        return {"updated_rows": 0, "updated_cells": 0, "video_refund_total": 40}

    monkeypatch.setattr(
        "backend.core.attendance.fanbei_course_sheets.rebuild_fanbei_attendance_from_course_sheets",
        fake_rebuild_fanbei,
    )
    monkeypatch.setattr(
        "backend.core.attendance.fanbei_schedule._apply_fanbei_attendance_step3_to_sheet",
        fake_step3,
    )

    result = run_attendance_course_completion_job(
        session,
        today=dt.date(2026, 6, 30),
        sheet_id=405,
        kqmain_path=tmp_path / "missing_kqmain.py",
    )
    session.commit()

    assert result["archived_count"] == 1
    assert result["archived_courses"][0]["course_type"] == "梵呗初阶"
    assert result["archived_courses"][0]["completed_date"] == "2026-06-25"
    assert [call[0] for call in fanbei_calls] == ["step2", "step3"]
    assert fanbei_calls[0][1]["attendance_sheet_id"] == 9003
    assert fanbei_calls[1][1]["sheet_id"] == 9003
    assert fanbei_calls[1][1]["course_name"] == "20260609梵呗初阶"

    session.refresh(summary_sheet)
    next_rows = summary_sheet.document_json["rows"]
    assert _cell_text(next_rows[1][1]) == "20260609梵呗初阶"
    assert next_rows[1][8] == str(serial(2026, 6, 25))
    assert next_rows[1][10] == "1"
    assert next_rows[1][11] == ""
    assert next_rows[1][13] == "40"
