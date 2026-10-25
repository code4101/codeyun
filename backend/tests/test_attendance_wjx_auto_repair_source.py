"""问卷自动修复上下文必须来自独立考勤 LocalAttendanceSheetClient（合成匿名数据）。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.api import attendance as attendance_api
from backend.models import AttendanceWjxDataEntry

WORKBOOK_ID, ATTENDANCE_SHEET_ID, REGISTRATION_SHEET_ID = 900, 901, 902


def _wjx_document(seq: int) -> dict:
    document = attendance_api._create_default_attendance_wjx_sheet_document()
    columns = list(document["columns"])
    row = [""] * len(columns)
    row[columns.index("序号")] = str(seq)
    row[columns.index("课程")] = {
        "value": "第99届觉观",
        "link": {"url": f"/workbook/{WORKBOOK_ID}?sheet={ATTENDANCE_SHEET_ID}"},
    }
    return {**document, "rows": [row]}


class FakeProviderClient:
    """模拟独立考勤公共客户端；CodeYun 本地库在这些测试中完全不参与。"""

    writes: list = []

    def __init__(self, *args, **kwargs):
        self.documents = {
            attendance_api.ATTENDANCE_WJX_DATA_SHEET_ID: (_wjx_document(7001), 31),
            REGISTRATION_SHEET_ID: ({
                "columns": ["序号", "姓名", "手机号", "用户ID", "匹配得分"],
                "rows": [["8", "学员甲", "", "u_old", ""]],
            }, 5),
            ATTENDANCE_SHEET_ID: ({
                "columns": ["学号", "姓名", "用户ID", "视频完成数", "打卡次数"],
                "rows": [["8", "学员甲", "u_old", "0", "0"]],
            }, 9),
        }

    def get_document(self, sheet):
        document_json, version = self.documents[int(sheet.sheet_id)]
        return {"id": int(sheet.sheet_id), "title": "", "version": version, "updated_at": 1.0,
                "document_json": document_json, "defined_names_context": {"effective": []}}

    def get_workbook_document(self, workbook_id):
        assert int(workbook_id) == WORKBOOK_ID
        return {"id": WORKBOOK_ID, "title": "第99届觉观", "sheets": [
            {"id": REGISTRATION_SHEET_ID, "title": "报名表", "sheet_key": "registration"},
            {"id": ATTENDANCE_SHEET_ID, "title": "考勤表", "sheet_key": "attendance"},
        ]}

    def replace_documents(self, changes, *, rebuild_attendance_sheet_id=None, course_name=""):
        FakeProviderClient.writes.append((list(changes), rebuild_attendance_sheet_id, course_name))
        return [{"id": c["sheet_id"], "version": c["expected_version"] + 1, "changed": True} for c in changes][:-1] + [
            {"id": changes[-1]["sheet_id"], "version": changes[-1]["expected_version"] + 1, "changed": True,
             "rebuild": {"rebuilt": 1}},
        ]


@pytest.fixture
def provider(monkeypatch):
    from backend.core.attendance.independent_engine_adapter import ensure_attendance_engine_importable

    ensure_attendance_engine_importable()
    import xlsln.kq5034.engine.client as provider_client

    FakeProviderClient.writes = []
    monkeypatch.setattr(provider_client, "LocalAttendanceSheetClient", FakeProviderClient)
    monkeypatch.setattr(
        attendance_api, "_attendance_wjx_lookup_user_candidate",
        lambda *a, **kw: {"user_id": "u_new", "score": 95, "source": "local_db", "warnings": []},
    )
    return FakeProviderClient


def _entry() -> AttendanceWjxDataEntry:
    return AttendanceWjxDataEntry(
        activity_id="synthetic", seq=7001, course_name="第99届觉观",
        student_id_text="8", student_name="学员甲", correction_request="视频数据为空",
    )


def test_course_context_reads_questionnaire_and_course_sheets_from_provider(provider):
    context = attendance_api._attendance_wjx_find_course_context(None, _entry())  # type: ignore[arg-type]

    assert context["warnings"] == []
    assert (context["course_workbook_id"], context["linked_sheet_id"]) == (WORKBOOK_ID, ATTENDANCE_SHEET_ID)
    assert context["registration_document"].numeric_id == REGISTRATION_SHEET_ID
    assert context["attendance_document"].numeric_id == ATTENDANCE_SHEET_ID
    assert context["attendance_document"].version == 9


def test_readonly_repair_without_model_never_calls_model_or_writes(provider, monkeypatch):
    monkeypatch.setattr(attendance_api, "chat_with_provider", lambda **kw: pytest.fail("外部模型不得调用"))

    result = attendance_api._try_auto_repair_attendance_wjx_empty_user_id(
        None, None, _entry(),  # type: ignore[arg-type]
        apply_changes=False, use_remote_browser=False, use_external_model=False,
    )

    assert result["status"] == "needs_review"
    assert result["context"]["workbook"]["numeric_id"] == WORKBOOK_ID
    assert result["codex_spark"] == {**result["codex_spark"], "requested": False, "used": False}
    assert provider.writes == []


def test_applied_repair_writes_through_provider_transaction_with_versions(provider, monkeypatch):
    monkeypatch.setattr(attendance_api, "_attendance_wjx_codex_repair_decision",
                        lambda _ctx: {"decision": "apply", "confidence": 0.9, "reason": "synthetic", "model": "x"})
    monkeypatch.setattr(attendance_api, "_attendance_wjx_analyze_empty_attendance",
                        lambda *a, **kw: {"likely_empty_user_id_issue": True})

    result = attendance_api._try_auto_repair_attendance_wjx_empty_user_id(
        SimpleNamespace(flush=lambda: None), None, _entry(),  # type: ignore[arg-type]
        apply_changes=True, use_remote_browser=False, use_external_model=True,
    )

    assert result["status"] == "applied"
    assert result["rebuild"] == {"attempted": True, "summary": {"rebuilt": 1}}
    (changes, rebuild_sheet_id, course_name), = provider.writes
    assert [(c["sheet_id"], c["expected_version"]) for c in changes] == [
        (REGISTRATION_SHEET_ID, 5), (ATTENDANCE_SHEET_ID, 9),
    ]
    assert (rebuild_sheet_id, course_name) == (ATTENDANCE_SHEET_ID, "第99届觉观")


def test_user_candidate_lookup_uses_provider_master_data(monkeypatch):
    from backend.core.attendance.independent_engine_adapter import ensure_attendance_engine_importable

    ensure_attendance_engine_importable()
    import xlsln.kq5034.engine.client as provider_client

    calls = []

    class Client:
        def lookup_registration_user(self, names, phones, **kwargs):
            calls.append((list(names), list(phones), kwargs.get("shop_id"), "session" in kwargs))
            return "u_new", 96

    monkeypatch.setattr(provider_client, "LocalAttendanceSheetClient", Client)
    note_sheets_api = SimpleNamespace(
        _resolve_registration_shop_id=lambda *_a: 1,
        _load_attendance_user_lookup_provider=lambda: pytest.fail("不得查 CodeYun 旧用户副本"),
    )

    result = attendance_api._attendance_wjx_lookup_user_candidate(
        None, None, note_sheets_api,  # type: ignore[arg-type]
        entry=_entry(), workbook=None, registration_document=None,  # type: ignore[arg-type]
        names=["学员甲"], phones=["10000000000"], current_user_id="u_old", use_remote_browser=False,
    )

    assert (result["user_id"], result["source"]) == ("u_new", "local_db")
    assert calls == [(["学员甲"], ["10000000000"], 1, False)]


def test_precheck_course_summary_reads_provider_sheet_and_link_cells(monkeypatch):
    from backend.core.attendance import ai_precheck

    document = {
        "columns": ["课程类型", "课程名称", "在线考勤表", "考勤实际完成结点"],
        "rows": [["禅宗一阶", "禅宗99期一阶", {"value": "修道班99期1阶", "link": {"url": "/workbook/900?sheet=901"}}, ""]],
    }
    monkeypatch.setattr(ai_precheck, "_load_course_summary_document", lambda: document)

    summary = ai_precheck._collect_course_summary(None, "修道班99期1阶")  # type: ignore[arg-type]

    assert summary == {"课程类型": "禅宗一阶", "课程名称": "禅宗99期一阶", "在线考勤表": "修道班99期1阶", "考勤实际完成结点": ""}
