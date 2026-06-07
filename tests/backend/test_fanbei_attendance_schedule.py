from datetime import date

from backend.core import fanbei_attendance_schedule as schedule
from backend.core.attendance_progress_style import (
    PercentageRefundRule,
    highlight_percentage_refund_progress,
    highlight_presence_progress,
    highlight_threshold_refund_progress,
    parse_threshold_refund_rules,
)
from backend.models import SheetDocument


class _FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, payload=None):
        self.payload = payload or {
            "lesson_update_count": 2,
            "clockin_update_count": 1,
        }

    def json(self):
        return self.payload


def test_run_step1_on_remote_entry_posts_to_device_control(monkeypatch):
    calls = []
    sessions = []

    class FakeSession:
        def __init__(self):
            self.trust_env = True
            sessions.append(self)

        def post(self, url, **kwargs):
            calls.append({"url": url, **kwargs})
            return _FakeResponse()

    monkeypatch.setattr(schedule.requests, "Session", FakeSession)

    result = schedule._run_step1_on_entry(
        {
            "mode": "remote",
            "server_url": "http://mi15:8000/",
            "token": "token-1",
        },
        {
            "course_name": "d260509梵呗初阶",
            "shop_id": 1,
        },
    )

    assert result["lesson_update_count"] == 2
    assert sessions[0].trust_env is False
    assert calls == [
        {
            "url": "http://mi15:8000/api/device-control/attendance/fanbei/step1",
            "json": {
                "course_name": "d260509梵呗初阶",
                "shop_id": 1,
            },
            "headers": {
                "Authorization": "Bearer token-1",
                "X-Device-Token": "token-1",
            },
            "timeout": schedule.FANBEI_ATTENDANCE_REMOTE_TIMEOUT_SECONDS,
        }
    ]


def test_run_step2_data_on_remote_entry_posts_to_device_control(monkeypatch):
    calls = []

    class FakeSession:
        trust_env = True

        def post(self, url, **kwargs):
            calls.append({"url": url, **kwargs, "trust_env": self.trust_env})
            return _FakeResponse({"columns": ["user_id2", "打卡数"], "rows": [["u1", 3]]})

    monkeypatch.setattr(schedule.requests, "Session", FakeSession)

    result = schedule._run_step2_data_on_entry(
        {"mode": "remote", "server_url": "http://mi15:8000/", "token": "token-1"},
        {"course_name": "d260509梵呗初阶", "user_ids": ["u1"]},
    )

    assert result["rows"] == [["u1", 3]]
    assert calls == [
        {
            "url": "http://mi15:8000/api/device-control/attendance/fanbei/step2-data",
            "json": {"course_name": "d260509梵呗初阶", "user_ids": ["u1"]},
            "headers": {"Authorization": "Bearer token-1", "X-Device-Token": "token-1"},
            "timeout": schedule.FANBEI_ATTENDANCE_REMOTE_TIMEOUT_SECONDS,
            "trust_env": False,
        }
    ]


def test_run_step2_on_remote_data_host_posts_whole_step(monkeypatch):
    calls = []

    class FakeSession:
        trust_env = True

        def post(self, url, **kwargs):
            calls.append({"url": url, **kwargs, "trust_env": self.trust_env})
            return _FakeResponse({"message": "mf 已执行 step2"})

    monkeypatch.setattr(schedule.requests, "Session", FakeSession)

    execution_snapshot = {
        "entry_id": "exec-entry",
        "mode": "remote",
        "server_url": "http://mi15:8000/",
        "token": "mi15-token",
    }
    result = schedule._run_step2_on_data_host(
        {
            "mode": "remote",
            "server_url": "http://mf:8000/",
            "token": "mf-token",
        },
        execution_entry_snapshot=execution_snapshot,
    )

    assert result == "mf 已执行 step2"
    assert calls == [
        {
            "url": "http://mf:8000/api/device-control/attendance/fanbei/step2",
            "json": {"execution_device": execution_snapshot},
            "headers": {"Authorization": "Bearer mf-token", "X-Device-Token": "mf-token"},
            "timeout": schedule.FANBEI_ATTENDANCE_REMOTE_TIMEOUT_SECONDS,
            "trust_env": False,
        }
    ]


def test_run_step3_on_remote_data_host_posts_to_device_control(monkeypatch):
    calls = []

    class FakeSession:
        trust_env = True

        def post(self, url, **kwargs):
            calls.append({"url": url, **kwargs, "trust_env": self.trust_env})
            return _FakeResponse({"message": "mf 已执行 step3"})

    monkeypatch.setattr(schedule.requests, "Session", FakeSession)

    result = schedule._run_step3_on_data_host(
        {
            "mode": "remote",
            "server_url": "http://mf:8000/",
            "token": "mf-token",
        },
        {"sheet_id": 6, "course_name": "d260509梵呗初阶"},
    )

    assert result == "mf 已执行 step3"
    assert calls == [
        {
            "url": "http://mf:8000/api/device-control/attendance/fanbei/step3",
            "json": {"sheet_id": 6, "course_name": "d260509梵呗初阶"},
            "headers": {"Authorization": "Bearer mf-token", "X-Device-Token": "mf-token"},
            "timeout": schedule.FANBEI_ATTENDANCE_REMOTE_TIMEOUT_SECONDS,
            "trust_env": False,
        }
    ]


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
            "第5天回放/100%",
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
            "cell_meta": {"3:15": {"style": {"background_color": "#FFFFFF"}}},
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
    session.refresh(document)
    next_rows = document.document_json["rows"]
    assert next_rows[0][5:14] == [
        "=FORMULA",
        "=FORMULA",
        64,
        "=FORMULA",
        "=FORMULA",
        10,
        550,
        "=FORMULA",
        "=FORMULA",
    ]
    assert next_rows[1][5:14] == ["=FORMULA", "=FORMULA", 0, "=FORMULA", "=FORMULA", 0, 550, "=FORMULA", "=FORMULA"]
    assert document.document_json["grid_rows"][2][12] == '=DATEDIF("2026-05-09",TODAY(),"d")'
    assert document.document_json["grid_rows"][2][13] == ""

    cell_meta = document.document_json["cell_meta"]
    assert cell_meta["3:15"]["style"]["background_color"] != "#FFFFFF"
    assert "3:17" not in cell_meta
    assert "4:15" not in cell_meta
