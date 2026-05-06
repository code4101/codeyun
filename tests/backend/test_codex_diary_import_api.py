import re
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlmodel import Session, select

from backend.api import device_entries as device_entries_api
from backend.core import codex_device_summary
from backend.core.note_semantics import build_note_category_palette_setting_key
from backend.api.notes import (
    _build_codex_diary_body_html,
    _build_codex_diary_blocks,
    _build_codex_diary_completion_progress_expr,
    _create_codex_diary_note,
    _normalize_codex_diary_ai_summary_items,
    _normalize_codex_diary_ai_title,
    _repair_codex_diary_body_number_prefixes,
    run_codex_diary_auto_import_job,
)
from backend.models import AppSetting, CodexDiaryImportRun, NoteNode, UserDevice


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


def test_device_entry_remote_json_bypasses_environment_proxy(monkeypatch):
    captured: list[dict] = []

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = ""

        def json(self):
            return {"ok": True}

    def fake_request(method, url, headers=None, params=None, json=None, proxies=None, timeout=None):
        captured.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "params": params,
                "json": json,
                "proxies": proxies,
                "timeout": timeout,
            }
        )
        return FakeResponse()

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)

    payload, error_response = device_entries_api._fetch_remote_json(
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

    assert error_response is None
    assert payload == {"ok": True}
    assert captured[0]["url"] == "http://192.168.31.15:8000/api/codex/workload"
    assert captured[0]["headers"]["Authorization"] == "Bearer remote-token"
    assert captured[0]["proxies"] == {"http": "", "https": "", "all": "", "no_proxy": "*"}

    core_payload = codex_device_summary.fetch_remote_codex_json(
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

    assert core_payload == {"ok": True}
    assert captured[1]["url"] == "http://192.168.31.15:8000/api/codex/workload"
    assert captured[1]["proxies"] == {"http": "", "https": "", "all": "", "no_proxy": "*"}


def test_collect_remote_entry_daily_summary_source_uses_long_codex_timeouts(session: Session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-device",
        mode="remote",
        name="codepc_mi15",
        server_url="http://remote-device",
        token="remote-token",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    start_at = _ts(2026, 5, 3, 9, 0)
    remote_root = r"C:\Users\chen\.codex"
    workload_payload = {
        "root_dir": remote_root,
        "default_root_dir": remote_root,
        "state_db_path": rf"{remote_root}\state_5.sqlite",
        "session_index_path": rf"{remote_root}\session_index.jsonl",
        "global_state_path": rf"{remote_root}\.codex-global-state.json",
        "total_threads": 1,
        "total_turns": 1,
        "skipped_threads": 0,
        "max_concurrency": 1,
        "time_range_start": start_at,
        "time_range_end": start_at + 600,
        "turns": [
            {
                "id": "remote-thread-1:1",
                "thread_id": "remote-thread-1",
                "turn_index": 1,
                "thread_title": "周日 Codex 日记",
                "project_label": "codeyun",
                "project_secondary_label": None,
                "workspace_root": r"D:\home\chenkunze\slns\codeyun",
                "group_key": r"D:\home\chenkunze\slns\codeyun",
                "group_label": "codeyun",
                "user_seq": 1,
                "assistant_seq": 2,
                "start_at": start_at,
                "end_at": start_at + 600,
                "duration_seconds": 600,
                "completed": True,
                "preview": "生成周日 Codex 日记。",
            }
        ],
        "segments": [],
    }
    detail_payload = {
        "root_dir": remote_root,
        "thread": {
            "id": "remote-thread-1",
            "title": "周日 Codex 日记",
            "preview": "生成周日 Codex 日记。",
            "cwd": r"D:\home\chenkunze\slns\codeyun",
            "created_at": start_at,
            "updated_at": start_at + 600,
            "archived": False,
            "project_label": "codeyun",
            "project_secondary_label": None,
            "workspace_root": r"D:\home\chenkunze\slns\codeyun",
        },
        "message_count": 2,
        "user_message_count": 1,
        "assistant_message_count": 1,
        "messages": [
            {
                "seq": 1,
                "timestamp": "2026-05-03T01:00:00+00:00",
                "role": "user",
                "phase": None,
                "text": "生成周日 Codex 日记。",
            },
            {
                "seq": 2,
                "timestamp": "2026-05-03T01:10:00+00:00",
                "role": "assistant",
                "phase": "final_answer",
                "text": "周日 Codex 日记来源已整理。",
            },
        ],
    }
    calls: list[tuple[str, int | None]] = []

    def fake_fetch_remote_json(remote_entry, method, path, *, timeout=20):
        assert method == "GET"
        calls.append((path, timeout))
        if path == "/codex/workload":
            return workload_payload
        if path == "/codex/threads/remote-thread-1":
            return detail_payload
        raise AssertionError(f"Unexpected remote path: {path}")

    monkeypatch.setattr(codex_device_summary, "fetch_remote_codex_json", fake_fetch_remote_json)

    source = codex_device_summary.collect_remote_codex_entry_daily_summary_source(
        {
            "entry_id": entry.entry_id,
            "device_id": entry.device_id,
            "mode": entry.mode,
            "name": entry.name,
            "server_url": entry.server_url,
            "token": entry.token,
        },
        "2026-05-03",
        user_id=auth_user.id,
        session=session,
    )

    assert calls == [
        ("/codex/workload", 180),
        ("/codex/threads/remote-thread-1", 120),
    ]
    assert source["turn_count"] == 1
    assert source["turn_records"][0]["thread_title"] == "周日 Codex 日记"


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
    entry_ids = [entry.entry_id for entry in entries]

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
                        "thread_id": f"{entry_ids[0]}:thread-a",
                    "thread_title": "统一 CodeYun 工作目录",
                    "project_label": "codeyun",
                    "time_range": "2026-04-30 09:00 ~ 2026-04-30 09:40",
                    "user_request": "统一 CodeYun 工作目录，并修复缓存接口。",
                    "assistant_result": "完成工作目录统一和接口修复。",
                    "assistant_process": "",
                    "start_at": start_a,
                    "end_at": start_a + 40 * 60,
                        "source_entry_id": entry_ids[0],
                    "source_device_name": "codepc_mf",
                    "source_root_dir": r"C:\Users\kzche\.codex",
                    },
                    {
                        "thread_id": f"{entry_ids[1]}:thread-b",
                    "thread_title": "补充远端 Codex 数据",
                    "project_label": "codeyun",
                    "time_range": "2026-04-30 10:10 ~ 2026-04-30 10:45",
                    "user_request": "把 codepc_mi15 的 Codex 数据合并进日记来源。",
                    "assistant_result": "远端数据已经合并。",
                    "assistant_process": "工具输出里有大量 JSON，不应写入星图笔记正文。",
                    "start_at": start_b,
                    "end_at": start_b + 35 * 60,
                        "source_entry_id": entry_ids[1],
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

    monkeypatch.setattr("backend.api.notes.collect_multi_codex_daily_summary_source", fake_collect_source)
    monkeypatch.setattr("backend.api.notes._draft_codex_diary_blocks_with_ai", _fake_codex_diary_ai_draft)

    response = client.post(
        "/api/notes/codex-diary/import-runs",
        json={"date": "2026-04-30"},
    )
    assert response.status_code == 200
    started = response.json()
    assert started["status"] == "running"
    assert started["entry_ids"] == entry_ids

    completed = _wait_for_import_run(client, started["id"])
    assert completed["status"] == "completed"
    assert completed["source_turn_count"] == 2
    assert completed["created_note_count"] == 1
    assert len(captured_entry_specs[0]) == 2

    notes = session.exec(
        select(NoteNode)
        .where(NoteNode.id.in_(completed["created_note_ids"]))
        .order_by(NoteNode.start_at)
    ).all()
    assert len(notes) == 1
    assert [note.start_at for note in notes] == [_ts(2026, 4, 30, 9, 0)]
    first_note = notes[0]
    for note in notes:
        assert not note.title.startswith("codeyun：")
        assert not note.title.endswith(("...", "…"))
        assert note.primary_category == "custom_codeyun"
        assert note.note_form == "note"
        assert note.lifecycle_stage == "done"
        assert note.weight == 0
        assert note.weight_mode is None
        assert "<h3>" not in note.content
        custom_fields = note.custom_fields
        assert any(item[0] == "__codex_diary_run_id" and item[2] == completed["id"] for item in custom_fields)
        assert any(item[0] == "__codex_diary_date" and item[2] == "2026-04-30" for item in custom_fields)
        assert any(item[0] == "__codex_source_thread_ids" and len(item[2]) == 2 for item in custom_fields)

    first_plain_content = _plain_text(first_note.content)
    assert "统一 CodeYun 工作目录，并修复缓存接口。" not in first_plain_content
    assert "工具输出里有大量 JSON" not in first_plain_content
    assert "完成工作目录统一和接口修复" in first_plain_content
    assert "codepc_mf" in first_plain_content
    assert "接口" in first_plain_content
    assert "把 codepc_mi15 的 Codex 数据合并进日记来源。" not in first_plain_content
    assert "远端数据已经合并" in first_plain_content
    assert "codepc_mi15" in first_plain_content
    assert any(item[0] == "__completion_progress_expr" and item[2] == "75/120" for item in first_note.custom_fields)


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


def test_codex_diary_import_fails_when_any_device_unavailable(
    client,
    session: Session,
    auth_user,
    monkeypatch,
):
    _create_device_entries(session, auth_user.id)

    def fake_collect_local_source(root_dir, target_date_text, *, user_id, session):
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

    def fake_collect_remote_source(entry_spec, target_date_text, *, user_id, session):
        raise HTTPException(status_code=502, detail="连接远端设备失败：read timeout")

    monkeypatch.setattr(codex_device_summary, "ensure_local_codex_entry", lambda entry: None)
    monkeypatch.setattr(codex_device_summary, "collect_codex_daily_summary_source", fake_collect_local_source)
    monkeypatch.setattr(codex_device_summary, "collect_remote_codex_entry_daily_summary_source", fake_collect_remote_source)
    monkeypatch.setattr("backend.api.notes._draft_codex_diary_blocks_with_ai", _fake_codex_diary_ai_draft)

    response = client.post(
        "/api/notes/codex-diary/import-runs",
        json={"date": "2026-05-03"},
    )
    assert response.status_code == 200
    completed = _wait_for_import_run(client, response.json()["id"])

    assert completed["status"] == "failed"
    assert completed["created_note_count"] == 0
    assert completed["created_note_ids"] == []
    assert completed["stage_label"] == "导入失败"
    assert "设备 codepc_mi15 的 Codex 数据读取失败" in completed["error_message"]
    assert "read timeout" in completed["error_message"]

    note = session.exec(select(NoteNode).where(NoteNode.title == "周日 Codex 日记")).first()
    assert note is None


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


def test_codex_diary_auto_import_job_runs_existing_diary_flow_and_skips_duplicates(
    session: Session,
    engine,
    auth_user,
    monkeypatch,
):
    entries = _create_device_entries(session, auth_user.id)
    collected_dates: list[str] = []

    def fake_collect_source(entry_specs, root_identity, target_date_text, *, user_id, session):
        collected_dates.append(target_date_text)
        start_at = _ts(2026, 5, 4, 1, 30)
        return {
            "date": target_date_text,
            "timezone": ZoneInfo("Asia/Shanghai"),
            "type_items": [],
            "turn_records": [
                {
                    "thread_id": f"{entries[0].entry_id}:auto-diary",
                    "thread_title": "后台 Codex 日记",
                    "project_label": "codeyun",
                    "time_range": "2026-05-04 01:30 ~ 2026-05-04 02:00",
                    "user_request": "给后台任务补 Codex 日记。",
                    "assistant_result": "后台任务已接入 Codex 日记。",
                    "assistant_process": "",
                    "start_at": start_at,
                    "end_at": start_at + 30 * 60,
                    "source_entry_id": entries[0].entry_id,
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

    result = run_codex_diary_auto_import_job(engine, "2026-05-04", trigger_reason="test")

    session.expire_all()
    first_run = session.exec(
        select(CodexDiaryImportRun).where(CodexDiaryImportRun.diary_date == "2026-05-04")
    ).one()
    assert result["queued_run_count"] == 1
    assert result["results"][0]["status"] == "completed"
    assert first_run.status == "completed"
    assert first_run.created_note_count == 1
    assert collected_dates == ["2026-05-04"]

    second_result = run_codex_diary_auto_import_job(engine, "2026-05-04", trigger_reason="test")

    session.expire_all()
    runs = session.exec(
        select(CodexDiaryImportRun)
        .where(CodexDiaryImportRun.diary_date == "2026-05-04")
        .order_by(CodexDiaryImportRun.created_at)
    ).all()
    assert second_result["queued_run_count"] == 0
    assert second_result["results"][0]["status"] == "skipped"
    assert runs[-1].status == "skipped"
    assert runs[-1].duplicate_note_ids == first_run.created_note_ids
    assert collected_dates == ["2026-05-04"]


def test_codex_diary_import_does_not_merge_unrelated_same_category_records(
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
        assert [len(block["records"]) for block in blocks] == [3]
        for block in blocks:
            block["title"] = "Codex 日记工作汇总"
            block["summary_items"] = [str(record["assistant_result"]) for record in block["records"]]
            block["lifecycle_stage"] = "done"
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

    notes = session.exec(
        select(NoteNode)
        .where(NoteNode.id.in_(completed["created_note_ids"]))
        .order_by(NoteNode.start_at)
    ).all()
    assert [note.title for note in notes] == ["Codex 日记工作汇总"]
    assert [note.start_at for note in notes] == [_ts(2026, 5, 2, 9, 0)]


def test_codex_diary_blocks_use_two_hour_target(
    session: Session,
    auth_user,
):
    start_at = _ts(2026, 5, 3, 9, 0)
    source = {
        "date": "2026-05-03",
        "timezone": ZoneInfo("Asia/Shanghai"),
        "type_items": [],
        "turn_records": [
            {
                "thread_id": "codeyun:thread-a",
                "thread_title": "Codex 日记拆块",
                "project_label": "codeyun",
                "time_range": "2026-05-03 09:00 ~ 2026-05-03 10:10",
                "user_request": "调整日记拆块基准。",
                "assistant_result": "日记拆块基准已调整。",
                "assistant_process": "",
                "start_at": start_at,
                "end_at": start_at + 70 * 60,
            },
            {
                "thread_id": "codeyun:thread-a",
                "thread_title": "Codex 日记进度",
                "project_label": "codeyun",
                "time_range": "2026-05-03 10:15 ~ 2026-05-03 11:05",
                "user_request": "按耗时计算日记进度。",
                "assistant_result": "日记进度已改为耗时比例。",
                "assistant_process": "",
                "start_at": start_at + 75 * 60,
                "end_at": start_at + 125 * 60,
            },
        ],
    }

    blocks = _build_codex_diary_blocks(source, user_id=auth_user.id, session=session)

    assert len(blocks) == 1
    assert round(blocks[0]["duration_seconds"] / 60) == 120


def test_codex_diary_completion_progress_uses_two_hour_duration_ratio():
    assert _build_codex_diary_completion_progress_expr({"duration_seconds": 37 * 60}) == "37/120"


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
    assert blocks[0]["note_categories"] == [{"key": "custom_attendance", "weight": 100}]
    assert blocks[1]["note_categories"] == [{"key": "custom_fanxiu", "weight": 100}]
    assert "课程脚本" in blocks[0]["records"][0]["user_request"]
    assert "prayer_cycle" in blocks[1]["records"][0]["user_request"]


def test_codex_diary_blocks_group_topic_before_assigning_category(
    session: Session,
    auth_user,
):
    session.add(
        AppSetting(
            key=build_note_category_palette_setting_key(auth_user.id),
            value={
                "items": [
                    {"key": "general", "label": "综合", "color": "#909399", "order": 0},
                    {"key": "custom_codeyun_note", "label": "CodeYun/笔记", "color": "#446ccf", "order": 10},
                    {"key": "custom_codeyun_resource", "label": "CodeYun/资源", "color": "#6699cc", "order": 20},
                    {"key": "custom_survey", "label": "问卷", "color": "#e6a23c", "order": 30},
                ]
            },
        )
    )
    session.commit()
    start_at = _ts(2026, 5, 4, 9, 0)
    source = {
        "date": "2026-05-04",
        "timezone": ZoneInfo("Asia/Shanghai"),
        "turn_records": [
            {
                "thread_id": "pdf:a",
                "thread_title": "PDF阅读器",
                "project_label": "codeyun",
                "time_range": "2026-05-04 09:00 ~ 2026-05-04 09:30",
                "user_request": "修复 PDF 阅读器资源加载和预览布局。",
                "assistant_result": "PDF 阅读器资源框架与预览布局已调整。",
                "start_at": start_at,
                "end_at": start_at + 30 * 60,
            },
            {
                "thread_id": "pdf:b",
                "thread_title": "页面笔记",
                "project_label": "codeyun",
                "time_range": "2026-05-04 09:35 ~ 2026-05-04 10:05",
                "user_request": "补 PDF 页面笔记批注状态和定位逻辑。",
                "assistant_result": "页面笔记已接入 PDF 阅读器。",
                "start_at": start_at + 35 * 60,
                "end_at": start_at + 65 * 60,
            },
            {
                "thread_id": "pdf:c",
                "thread_title": "PDF用户状态层",
                "project_label": "codeyun",
                "time_range": "2026-05-04 10:10 ~ 2026-05-04 10:30",
                "user_request": "实现 PDF 用户状态层，保存阅读页码和缩放比例。",
                "assistant_result": "PDF 用户状态层已落库。",
                "start_at": start_at + 70 * 60,
                "end_at": start_at + 90 * 60,
            },
            {
                "thread_id": "survey:a",
                "thread_title": "问卷前端",
                "project_label": "codeyun",
                "time_range": "2026-05-04 11:00 ~ 2026-05-04 11:20",
                "user_request": "调整问卷前端入口。",
                "assistant_result": "问卷前端入口已完成。",
                "start_at": start_at + 120 * 60,
                "end_at": start_at + 140 * 60,
            },
        ],
    }

    blocks = _build_codex_diary_blocks(source, user_id=auth_user.id, session=session)

    assert len(blocks) == 2
    assert [block["category_key"] for block in blocks] == ["custom_codeyun_note", "custom_survey"]
    assert [block["note_categories"] for block in blocks] == [
        [{"key": "custom_codeyun_note", "weight": 100}],
        [{"key": "custom_survey", "weight": 100}],
    ]
    assert [len(block["records"]) for block in blocks] == [3, 1]
    assert {record["thread_title"] for record in blocks[0]["records"]} == {"PDF阅读器", "页面笔记", "PDF用户状态层"}


def test_codex_diary_blocks_merge_same_category_even_when_not_adjacent(
    session: Session,
    auth_user,
):
    session.add(
        AppSetting(
            key=build_note_category_palette_setting_key(auth_user.id),
            value={
                "items": [
                    {"key": "general", "label": "综合", "color": "#909399", "order": 0},
                    {"key": "custom_codeyun_note", "label": "CodeYun/笔记", "color": "#446ccf", "order": 10},
                    {"key": "custom_survey", "label": "问卷", "color": "#e6a23c", "order": 20},
                ]
            },
        )
    )
    session.commit()
    start_at = _ts(2026, 5, 4, 9, 0)
    source = {
        "date": "2026-05-04",
        "timezone": ZoneInfo("Asia/Shanghai"),
        "turn_records": [
            {
                "thread_id": "note:a",
                "thread_title": "PDF阅读器",
                "project_label": "codeyun",
                "user_request": "实现 PDF 页面笔记。",
                "assistant_result": "PDF 页面笔记已接入。",
                "start_at": start_at,
                "end_at": start_at + 20 * 60,
            },
            {
                "thread_id": "survey:a",
                "thread_title": "问卷恢复",
                "project_label": "codeyun",
                "user_request": "恢复问卷 652 数据。",
                "assistant_result": "问卷 652 数据已恢复。",
                "start_at": start_at + 30 * 60,
                "end_at": start_at + 50 * 60,
            },
            {
                "thread_id": "note:b",
                "thread_title": "日记导入",
                "project_label": "codeyun",
                "user_request": "优化 Codex 日记导入。",
                "assistant_result": "Codex 日记导入已优化。",
                "start_at": start_at + 60 * 60,
                "end_at": start_at + 80 * 60,
            },
        ],
    }

    blocks = _build_codex_diary_blocks(source, user_id=auth_user.id, session=session)

    assert [block["category_key"] for block in blocks] == ["custom_codeyun_note", "custom_survey"]
    assert [len(block["records"]) for block in blocks] == [2, 1]
    assert {record["thread_title"] for record in blocks[0]["records"]} == {"PDF阅读器", "日记导入"}


def test_codex_diary_questionnaire_records_prefer_attendance_over_codeyun_context(
    session: Session,
    auth_user,
):
    session.add(
        AppSetting(
            key=build_note_category_palette_setting_key(auth_user.id),
            value={
                "items": [
                    {"key": "general", "label": "综合", "color": "#909399", "order": 0},
                    {"key": "legacy_color_e6a23c", "label": "考勤", "color": "#c19a6b", "order": 10},
                    {"key": "custom_codeyun_general", "label": "CodeYun/综合", "color": "#00bfff", "order": 20},
                ]
            },
        )
    )
    session.commit()
    start_at = _ts(2026, 5, 4, 9, 0)
    source = {
        "date": "2026-05-04",
        "timezone": ZoneInfo("Asia/Shanghai"),
        "turn_records": [
            {
                "thread_id": "codeyun:survey-a",
                "thread_title": "CodeYun 问卷采集新条目前插",
                "project_label": "codeyun",
                "user_request": "调整问卷采集写表为按序号降序前插。",
                "assistant_result": "问卷采集新条目前插已完成。",
                "start_at": start_at,
                "end_at": start_at + 20 * 60,
            },
            {
                "thread_id": "codeyun:survey-b",
                "thread_title": "CodeYun 652 截图内容核对",
                "project_label": "codeyun",
                "user_request": "恢复 652、653 记录并核对截图内容。",
                "assistant_result": "652、653 数据恢复并完成截图核对。",
                "start_at": start_at + 30 * 60,
                "end_at": start_at + 50 * 60,
            },
        ],
    }

    blocks = _build_codex_diary_blocks(source, user_id=auth_user.id, session=session)

    assert [block["category_key"] for block in blocks] == ["legacy_color_e6a23c"]
    assert blocks[0]["note_categories"] == [{"key": "legacy_color_e6a23c", "weight": 100}]
    assert {record["thread_title"] for record in blocks[0]["records"]} == {
        "CodeYun 问卷采集新条目前插",
        "CodeYun 652 截图内容核对",
    }


def test_codex_diary_blocks_use_primary_category_only(
    session: Session,
    auth_user,
):
    session.add(
        AppSetting(
            key=build_note_category_palette_setting_key(auth_user.id),
            value={
                "items": [
                    {"key": "general", "label": "综合", "color": "#909399", "order": 0},
                    {"key": "custom_codeyun_note", "label": "CodeYun/笔记", "color": "#446ccf", "order": 10},
                    {"key": "custom_survey", "label": "问卷", "color": "#e6a23c", "order": 20},
                ]
            },
        )
    )
    session.commit()
    start_at = _ts(2026, 5, 4, 9, 0)
    source = {
        "date": "2026-05-04",
        "timezone": ZoneInfo("Asia/Shanghai"),
        "turn_records": [
            {
                "thread_id": "pdf-survey:a",
                "thread_title": "PDF阅读器问卷前插",
                "project_label": "codeyun",
                "time_range": "2026-05-04 09:00 ~ 2026-05-04 09:30",
                "user_request": "修复 PDF 阅读器里的问卷前插和页面笔记。",
                "assistant_result": "PDF 阅读器页面笔记与问卷前插已完成。",
                "start_at": start_at,
                "end_at": start_at + 30 * 60,
            }
        ],
    }

    blocks = _build_codex_diary_blocks(source, user_id=auth_user.id, session=session)

    assert len(blocks) == 1
    assert blocks[0]["category_key"] == "custom_codeyun_note"
    assert blocks[0]["note_categories"] == [{"key": "custom_codeyun_note", "weight": 100}]

    run = CodexDiaryImportRun(user_id=auth_user.id, diary_date="2026-05-04", scope_key="test")
    session.add(run)
    block = dict(blocks[0])
    block["title"] = "PDF阅读器问卷前插"
    block["summary_items"] = ["PDF 阅读器页面笔记与问卷前插已完成。"]
    block["lifecycle_stage"] = "done"
    note = _create_codex_diary_note(session, current_user=auth_user, run=run, block=block)
    session.commit()
    session.refresh(note)

    assert note.primary_category == "custom_codeyun_note"
    assert note.note_categories == blocks[0]["note_categories"]


def test_codex_diary_blocks_filter_blocked_auto_categories(
    session: Session,
    auth_user,
):
    session.add(
        AppSetting(
            key=build_note_category_palette_setting_key(auth_user.id),
            value={
                "items": [
                    {"key": "general", "label": "综合", "color": "#909399", "order": 0},
                    {"key": "project", "label": "项目", "color": "#7b1fa2", "order": 10},
                    {"key": "module", "label": "模块", "color": "#ba68c8", "order": 20},
                    {"key": "task", "label": "任务", "color": "#409eff", "order": 30},
                    {"key": "focus", "label": "重点", "color": "#e6a23c", "order": 40},
                ]
            },
        )
    )
    session.add(
        NoteNode(
            id="hint-focus",
            user_id=auth_user.id,
            title="任务清单",
            primary_category="focus",
            note_categories=[{"key": "focus", "weight": 100}],
        )
    )
    session.commit()
    start_at = _ts(2026, 5, 3, 15, 0)
    source = {
        "date": "2026-05-03",
        "timezone": ZoneInfo("Asia/Shanghai"),
        "turn_records": [
            {
                "thread_id": "blocked:thread",
                "thread_title": "任务清单",
                "project_label": "项目",
                "time_range": "2026-05-03 15:00 ~ 2026-05-03 15:20",
                "user_request": "整理任务清单和模块项目安排。",
                "assistant_result": "已完成重点事项整理。",
                "start_at": start_at,
                "end_at": start_at + 20 * 60,
            }
        ],
    }

    blocks = _build_codex_diary_blocks(source, user_id=auth_user.id, session=session)

    assert [block["category_key"] for block in blocks] == ["general"]
    assert blocks[0]["category_label"] == "综合"


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

    assert len(blocks) == 1
    assert [block["category_key"] for block in blocks] == ["general"]
    assert [len(block["records"]) for block in blocks] == [2]


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
