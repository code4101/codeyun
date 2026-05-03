import re
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from backend.core.note_semantics import build_note_category_palette_setting_key
from backend.api.notes import (
    _build_codex_diary_body_html,
    _build_codex_diary_blocks,
    _fetch_remote_codex_json,
    _normalize_codex_diary_ai_summary_items,
    _normalize_codex_diary_ai_title,
    _repair_codex_diary_body_number_prefixes,
)
from backend.models import AppSetting, NoteNode, UserDevice


def _plain_text(html_text: str) -> str:
    return re.sub(r"<[^>]+>", "", html_text)


def _ts(year: int, month: int, day: int, hour: int, minute: int = 0) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()


def _create_device_entries(session: Session, user_id: int) -> list[UserDevice]:
    entries = [
        UserDevice(
            user_id=user_id,
            device_id="local-device",
            mode="local",
            name="codepc_mf",
            token="local-token",
            order_index=0,
        ),
        UserDevice(
            user_id=user_id,
            device_id="remote-device",
            mode="remote",
            name="codepc_mi15",
            server_url="http://remote-device",
            token="remote-token",
            order_index=1,
        ),
    ]
    session.add_all(entries)
    session.commit()
    for entry in entries:
        session.refresh(entry)
    return entries


def test_fetch_remote_codex_json_bypasses_environment_proxy(monkeypatch):
    captured: list[dict] = []

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = ""

        def json(self):
            return {"ok": True}

    def fake_request(method, url, headers=None, proxies=None, timeout=None):
        captured.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "proxies": proxies,
                "timeout": timeout,
            }
        )
        return FakeResponse()

    monkeypatch.setattr("backend.api.notes.requests.request", fake_request)

    payload = _fetch_remote_codex_json(
        UserDevice(
            user_id=1,
            device_id="remote-device",
            mode="remote",
            name="remote",
            server_url="http://192.168.31.15:8000",
            token="remote-token",
        ),
        "GET",
        "/codex/workload",
        timeout=20,
    )

    assert payload == {"ok": True}
    assert captured[0]["url"] == "http://192.168.31.15:8000/api/codex/workload"
    assert captured[0]["headers"]["Authorization"] == "Bearer remote-token"
    assert captured[0]["proxies"] == {"http": "", "https": "", "all": "", "no_proxy": "*"}


def _wait_for_import_run(client, run_id: str) -> dict:
    payload = {}
    for _ in range(60):
        response = client.get(f"/api/notes/codex-diary/import-runs/{run_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] != "running":
            return payload
    raise AssertionError(f"Codex diary import run did not finish: {payload}")


def _fake_codex_diary_ai_draft(source, blocks):
    for block in blocks:
        titles = []
        summary_items = []
        for record in block["records"]:
            title = str(record.get("thread_title") or "").strip()
            if title and title not in titles:
                titles.append(title)
            result = str(record.get("assistant_result") or "").strip()
            if result:
                summary_items.append(result)
        block["title"] = "、".join(titles[:2]) or "Codex 日记"
        block["summary_items"] = summary_items or ["该事项已完成处理。"]
        block["lifecycle_stage"] = "done"
        block["completion_progress"] = "1"
    return blocks


def test_codex_diary_import_creates_notes_from_all_active_devices(
    client,
    session: Session,
    auth_user,
    monkeypatch,
):
    entries = _create_device_entries(session, auth_user.id)
    session.add(
        AppSetting(
            key=build_note_category_palette_setting_key(auth_user.id),
            value={
                "items": [
                    {"key": "general", "label": "综合", "color": "#909399", "order": 0},
                    {"key": "custom_codeyun", "label": "codeyun", "color": "#409eff", "order": 10},
                ]
            },
        )
    )
    session.commit()

    captured_entry_specs: list[list[dict]] = []

    def fake_collect_source(entry_specs, root_identity, target_date_text, *, user_id, session):
        captured_entry_specs.append(entry_specs)
        start_a = _ts(2026, 4, 30, 9, 0)
        start_b = _ts(2026, 4, 30, 10, 10)
        return {
            "date": target_date_text,
            "timezone": ZoneInfo("Asia/Shanghai"),
            "type_items": [],
            "turn_records": [
                {
                    "thread_id": f"{entries[0].entry_id}:thread-a",
                    "thread_title": "统一 CodeYun 工作目录",
                    "project_label": "codeyun",
                    "time_range": "2026-04-30 09:00 ~ 2026-04-30 09:40",
                    "user_request": "统一 CodeYun 工作目录，并修复缓存接口。",
                    "assistant_result": "完成工作目录统一和接口修复。",
                    "assistant_process": "",
                    "start_at": start_a,
                    "end_at": start_a + 40 * 60,
                    "source_entry_id": entries[0].entry_id,
                    "source_device_name": "codepc_mf",
                    "source_root_dir": r"C:\Users\kzche\.codex",
                },
                {
                    "thread_id": f"{entries[1].entry_id}:thread-b",
                    "thread_title": "补充远端 Codex 数据",
                    "project_label": "codeyun",
                    "time_range": "2026-04-30 10:10 ~ 2026-04-30 10:45",
                    "user_request": "把 codepc_mi15 的 Codex 数据合并进日记来源。",
                    "assistant_result": "远端数据已经合并。",
                    "assistant_process": "工具输出里有大量 JSON，不应写入星图笔记正文。",
                    "start_at": start_b,
                    "end_at": start_b + 35 * 60,
                    "source_entry_id": entries[1].entry_id,
                    "source_device_name": "codepc_mi15",
                    "source_root_dir": r"C:\Users\chen\.codex",
                },
            ],
            "threads": [],
            "thread_count": 2,
            "turn_count": 2,
            "user_message_count": 2,
            "assistant_message_count": 2,
        }

    monkeypatch.setattr("backend.api.notes._collect_codex_diary_source", fake_collect_source)
    monkeypatch.setattr("backend.api.notes._draft_codex_diary_blocks_with_ai", _fake_codex_diary_ai_draft)

    response = client.post(
        "/api/notes/codex-diary/import-runs",
        json={"date": "2026-04-30"},
    )
    assert response.status_code == 200
    started = response.json()
    assert started["status"] == "running"
    assert started["entry_ids"] == [entry.entry_id for entry in entries]

    completed = _wait_for_import_run(client, started["id"])
    assert completed["status"] == "completed"
    assert completed["source_turn_count"] == 2
    assert completed["created_note_count"] == 1
    assert len(captured_entry_specs[0]) == 2

    note = session.exec(select(NoteNode).where(NoteNode.id == completed["created_note_ids"][0])).one()
    assert note.title == "统一 CodeYun 工作目录、补充远端 Codex 数据"
    assert not note.title.startswith("codeyun：")
    assert not note.title.endswith(("...", "…"))
    assert note.primary_category == "custom_codeyun"
    assert note.note_form == "note"
    assert note.lifecycle_stage == "done"
    assert note.weight == 0
    assert note.weight_mode is None
    assert note.start_at == _ts(2026, 4, 30, 9, 0)
    assert "<h3>" not in note.content
    plain_content = _plain_text(note.content)
    assert "统一 CodeYun 工作目录，并修复缓存接口。" not in plain_content
    assert "把 codepc_mi15 的 Codex 数据合并进日记来源。" not in plain_content
    assert "工具输出里有大量 JSON" not in plain_content
    assert "完成工作目录统一和接口修复" in plain_content
    assert "远端数据已经合并" in plain_content
    assert "codepc_mf" in plain_content
    assert "codepc_mi15" in plain_content
    assert "接口" in plain_content
    custom_fields = note.custom_fields
    assert any(item[0] == "__codex_diary_run_id" and item[2] == completed["id"] for item in custom_fields)
    assert any(item[0] == "__codex_diary_date" and item[2] == "2026-04-30" for item in custom_fields)
    assert any(item[0] == "__codex_source_thread_ids" and len(item[2]) == 2 for item in custom_fields)


def test_codex_diary_import_empty_source_creates_no_notes(
    client,
    session: Session,
    auth_user,
    monkeypatch,
):
    _create_device_entries(session, auth_user.id)

    def fake_collect_source(entry_specs, root_identity, target_date_text, *, user_id, session):
        return {
            "date": target_date_text,
            "timezone": ZoneInfo("Asia/Shanghai"),
            "type_items": [],
            "turn_records": [],
            "threads": [],
            "thread_count": 0,
            "turn_count": 0,
            "user_message_count": 0,
            "assistant_message_count": 0,
        }

    monkeypatch.setattr("backend.api.notes._collect_codex_diary_source", fake_collect_source)

    response = client.post(
        "/api/notes/codex-diary/import-runs",
        json={"date": "2026-05-01"},
    )
    assert response.status_code == 200
    completed = _wait_for_import_run(client, response.json()["id"])
    assert completed["status"] == "completed"
    assert completed["stage"] == "empty"
    assert completed["created_note_count"] == 0
    assert completed["created_note_ids"] == []


def test_codex_diary_import_duplicate_requires_confirmation(
    client,
    session: Session,
    auth_user,
    monkeypatch,
):
    _create_device_entries(session, auth_user.id)

    def fake_collect_source(entry_specs, root_identity, target_date_text, *, user_id, session):
        start_at = _ts(2026, 5, 1, 13, 0)
        return {
            "date": target_date_text,
            "timezone": ZoneInfo("Asia/Shanghai"),
            "type_items": [],
            "turn_records": [
                {
                    "thread_id": "local:refund",
                    "thread_title": "退款流程修复",
                    "project_label": "codeyun",
                    "time_range": "2026-05-01 13:00 ~ 2026-05-01 13:20",
                    "user_request": "修复退款流程。",
                    "assistant_result": "退款流程已修复。",
                    "assistant_process": "",
                    "start_at": start_at,
                    "end_at": start_at + 20 * 60,
                    "source_entry_id": "local",
                    "source_device_name": "codepc_mf",
                }
            ],
            "threads": [],
            "thread_count": 1,
            "turn_count": 1,
            "user_message_count": 1,
            "assistant_message_count": 1,
        }

    monkeypatch.setattr("backend.api.notes._collect_codex_diary_source", fake_collect_source)
    monkeypatch.setattr("backend.api.notes._draft_codex_diary_blocks_with_ai", _fake_codex_diary_ai_draft)

    first_response = client.post(
        "/api/notes/codex-diary/import-runs",
        json={"date": "2026-05-01"},
    )
    assert first_response.status_code == 200
    first_completed = _wait_for_import_run(client, first_response.json()["id"])
    assert first_completed["created_note_count"] == 1

    duplicate_response = client.post(
        "/api/notes/codex-diary/import-runs",
        json={"date": "2026-05-01"},
    )
    assert duplicate_response.status_code == 409
    assert duplicate_response.json()["detail"]["duplicate_note_ids"] == first_completed["created_note_ids"]

    confirmed_response = client.post(
        "/api/notes/codex-diary/import-runs",
        json={"date": "2026-05-01", "confirm_duplicate": True},
    )
    assert confirmed_response.status_code == 200
    confirmed_completed = _wait_for_import_run(client, confirmed_response.json()["id"])
    assert confirmed_completed["created_note_count"] == 1
    assert confirmed_completed["created_note_ids"] != first_completed["created_note_ids"]
    assert confirmed_completed["duplicate_note_ids"] == first_completed["created_note_ids"]


def test_codex_diary_import_merges_fragmented_same_category_records(
    client,
    session: Session,
    auth_user,
    monkeypatch,
):
    entries = _create_device_entries(session, auth_user.id)
    session.add(
        AppSetting(
            key=build_note_category_palette_setting_key(auth_user.id),
            value={
                "items": [
                    {"key": "general", "label": "综合", "color": "#909399", "order": 0},
                    {"key": "custom_codeyun", "label": "codeyun", "color": "#409eff", "order": 10},
                ]
            },
        )
    )
    session.commit()
    captured_block_counts: list[int] = []

    def fake_collect_source(entry_specs, root_identity, target_date_text, *, user_id, session):
        morning = _ts(2026, 5, 2, 9, 0)
        afternoon = _ts(2026, 5, 2, 15, 0)
        evening = _ts(2026, 5, 2, 20, 0)
        return {
            "date": target_date_text,
            "timezone": ZoneInfo("Asia/Shanghai"),
            "type_items": [],
            "turn_records": [
                {
                    "thread_id": "codeyun:morning",
                    "thread_title": "星云表格课程清单读取",
                    "project_label": "codeyun",
                    "time_range": "2026-05-02 09:00 ~ 2026-05-02 09:25",
                    "user_request": "改成从表格读取课程清单。",
                    "assistant_result": "已改成从星云表格读取课程清单。",
                    "assistant_process": "",
                    "start_at": morning,
                    "end_at": morning + 25 * 60,
                    "source_entry_id": entries[0].entry_id,
                    "source_device_name": "codepc_mf",
                },
                {
                    "thread_id": "codeyun:afternoon",
                    "thread_title": "问卷采集菜单合并",
                    "project_label": "codeyun",
                    "time_range": "2026-05-02 15:00 ~ 2026-05-02 15:20",
                    "user_request": "统一问卷采集菜单。",
                    "assistant_result": "已合并问卷采集菜单入口。",
                    "assistant_process": "",
                    "start_at": afternoon,
                    "end_at": afternoon + 20 * 60,
                    "source_entry_id": entries[0].entry_id,
                    "source_device_name": "codepc_mf",
                },
                {
                    "thread_id": "codeyun:evening",
                    "thread_title": "Codex 会话全量分页",
                    "project_label": "codeyun",
                    "time_range": "2026-05-02 20:00 ~ 2026-05-02 20:18",
                    "user_request": "会话列表改成全量分页。",
                    "assistant_result": "会话列表已改成全量时间线分页加载。",
                    "assistant_process": "",
                    "start_at": evening,
                    "end_at": evening + 18 * 60,
                    "source_entry_id": entries[0].entry_id,
                    "source_device_name": "codepc_mf",
                },
            ],
            "threads": [],
            "thread_count": 3,
            "turn_count": 3,
            "user_message_count": 3,
            "assistant_message_count": 3,
        }

    def fake_draft(source, blocks):
        captured_block_counts.append(len(blocks))
        assert len(blocks) == 1
        assert len(blocks[0]["records"]) == 3
        blocks[0]["title"] = "星云表格课程清单与会话分页"
        blocks[0]["summary_items"] = [
            "星云表格课程清单读取、问卷采集菜单合并、Codex 会话全量分页加载已合并到同一个约 1 小时工作块。"
        ]
        blocks[0]["lifecycle_stage"] = "done"
        blocks[0]["completion_progress"] = "1"
        return blocks

    monkeypatch.setattr("backend.api.notes._collect_codex_diary_source", fake_collect_source)
    monkeypatch.setattr("backend.api.notes._draft_codex_diary_blocks_with_ai", fake_draft)

    response = client.post(
        "/api/notes/codex-diary/import-runs",
        json={"date": "2026-05-02"},
    )
    assert response.status_code == 200
    completed = _wait_for_import_run(client, response.json()["id"])
    assert completed["status"] == "completed"
    assert completed["created_note_count"] == 1
    assert captured_block_counts == [1]

    note = session.exec(select(NoteNode).where(NoteNode.id == completed["created_note_ids"][0])).one()
    assert note.title == "星云表格课程清单与会话分页"
    assert note.start_at == _ts(2026, 5, 2, 9, 0)
    assert "约 63 分钟" in note.content


def test_codex_diary_blocks_prefer_content_category_over_thread_context(
    session: Session,
    auth_user,
):
    session.add(
        AppSetting(
            key=build_note_category_palette_setting_key(auth_user.id),
            value={
                "items": [
                    {"key": "general", "label": "综合", "color": "#909399", "order": 0},
                    {"key": "custom_attendance", "label": "考勤", "color": "#67c23a", "order": 10},
                    {"key": "custom_fanxiu", "label": "凡修", "color": "#409eff", "order": 20},
                ]
            },
        )
    )
    session.add_all(
        [
            NoteNode(
                id="hint-attendance",
                user_id=auth_user.id,
                title="念住考勤打卡课程脚本",
                primary_category="custom_attendance",
                note_categories=[{"key": "custom_attendance", "weight": 100}],
            ),
            NoteNode(
                id="hint-fanxiu",
                user_id=auth_user.id,
                title="凡修祈愿轮换炼丹妖王活动",
                primary_category="custom_fanxiu",
                note_categories=[{"key": "custom_fanxiu", "weight": 100}],
            ),
        ]
    )
    session.commit()
    start_at = _ts(2026, 5, 3, 14, 0)
    source = {
        "date": "2026-05-03",
        "timezone": ZoneInfo("Asia/Shanghai"),
        "turn_records": [
            {
                "thread_id": "mixed:thread",
                "thread_title": "祈愿轮换与退款流程",
                "project_label": "凡修",
                "time_range": "2026-05-03 14:00 ~ 2026-05-03 14:30",
                "user_request": "修复课程脚本导入缺口，并把念住打卡链接写入 clockin_table。",
                "assistant_result": "已修复课程脚本、微信零钱退款流程和念住打卡链接导入。",
                "start_at": start_at,
                "end_at": start_at + 30 * 60,
            },
            {
                "thread_id": "mixed:thread",
                "thread_title": "祈愿轮换与退款流程",
                "project_label": "凡修",
                "time_range": "2026-05-03 14:30 ~ 2026-05-03 15:00",
                "user_request": "新增 prayer_cycle 纯时间轮换规则。",
                "assistant_result": "已按炼丹、淬体、灵兽、洗灵、仙花推导凡修活动。",
                "start_at": start_at + 30 * 60,
                "end_at": start_at + 60 * 60,
            },
        ],
    }

    blocks = _build_codex_diary_blocks(source, user_id=auth_user.id, session=session)

    assert [block["category_key"] for block in blocks] == ["custom_attendance", "custom_fanxiu"]
    assert "课程脚本" in blocks[0]["records"][0]["user_request"]
    assert "prayer_cycle" in blocks[1]["records"][0]["user_request"]


def test_codex_diary_general_fallback_does_not_merge_unrelated_threads(
    session: Session,
    auth_user,
):
    session.add(
        AppSetting(
            key=build_note_category_palette_setting_key(auth_user.id),
            value={
                "items": [
                    {"key": "general", "label": "综合", "color": "#909399", "order": 0},
                ]
            },
        )
    )
    session.commit()
    start_at = _ts(2026, 5, 3, 16, 0)
    source = {
        "date": "2026-05-03",
        "timezone": ZoneInfo("Asia/Shanghai"),
        "turn_records": [
            {
                "thread_id": "unknown:a",
                "thread_title": "临时资料整理",
                "project_label": "未命名",
                "time_range": "2026-05-03 16:00 ~ 2026-05-03 16:05",
                "user_request": "整理一份临时资料。",
                "assistant_result": "临时资料已整理。",
                "start_at": start_at,
                "end_at": start_at + 5 * 60,
            },
            {
                "thread_id": "unknown:b",
                "thread_title": "命名讨论",
                "project_label": "未命名",
                "time_range": "2026-05-03 16:10 ~ 2026-05-03 16:15",
                "user_request": "讨论一个变量命名。",
                "assistant_result": "变量命名建议已给出。",
                "start_at": start_at + 10 * 60,
                "end_at": start_at + 15 * 60,
            },
        ],
    }

    blocks = _build_codex_diary_blocks(source, user_id=auth_user.id, session=session)

    assert len(blocks) == 2
    assert [block["category_key"] for block in blocks] == ["general", "general"]
    assert [len(block["records"]) for block in blocks] == [1, 1]


def test_codex_diary_ai_title_rejects_low_information_prefixes():
    assert _normalize_codex_diary_ai_title("可以") == ""
    assert _normalize_codex_diary_ai_title("已改完") == ""
    assert _normalize_codex_diary_ai_title("是的，会话列表全量时间线分页加载") == "会话列表全量时间线分页加载"
    assert _normalize_codex_diary_ai_title("综合修复事项") == "修复事项"


def test_codex_diary_ai_summary_items_strip_number_prefixes():
    assert _normalize_codex_diary_ai_summary_items(
        [
            "1. 定位 xiaotong.py 下载轮询异常",
            "2、核查 kqmain2.codepc_mi15.state.json",
            "（3）量化更新阻塞点",
        ]
    ) == [
        "定位 xiaotong.py 下载轮询异常",
        "核查 kqmain2.codepc_mi15.state.json",
        "量化更新阻塞点",
    ]


def test_codex_diary_body_html_does_not_duplicate_ordered_list_numbering():
    block = {
        "records": [
            {
                "source_device_name": "codepc_mf",
                "start_at": _ts(2026, 5, 1, 10, 0),
                "end_at": _ts(2026, 5, 1, 10, 30),
            }
        ],
        "start_at": _ts(2026, 5, 1, 10, 0),
        "end_at": _ts(2026, 5, 1, 10, 30),
        "duration_seconds": 30 * 60,
        "summary_items": ["1. 定位下载轮询异常", "2. 核查状态文件"],
    }

    html_text = _build_codex_diary_body_html(block)

    assert "<li><span>定位下载轮询异常</span></li>" in html_text
    assert "<li><span>核查状态文件</span></li>" in html_text
    assert "<li><span>1. " not in html_text
    assert "<li><span>2. " not in html_text

    repaired_html = _repair_codex_diary_body_number_prefixes("<ol><li><code>1. 1. 更新 codeyun/.gitignore</code></li></ol>")
    assert repaired_html == "<ol><li><code>更新 codeyun/.gitignore</code></li></ol>"
