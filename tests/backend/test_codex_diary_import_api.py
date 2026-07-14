import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlmodel import Session, select

from backend.api import device_entries as device_entries_api
from backend.core.devices import codex_summary as codex_device_summary
from backend.core.notes.semantics import build_note_category_palette_setting_key
from backend.core.notes.progress import get_completion_progress_expr
from backend.api.notes import (
    CODEX_DIARY_STALE_HEARTBEAT_SECONDS,
    _build_codex_diary_body_html,
    _build_codex_diary_blocks,
    _build_codex_diary_completion_progress_expr,
    _build_codex_diary_title,
    _create_codex_diary_note,
    _create_codex_diary_import_run_record,
    _draft_codex_diary_blocks_in_batches,
    _extract_codex_diary_ai_json,
    _find_active_codex_diary_category_note_ids,
    _is_codex_diary_synthetic_turn,
    _normalize_codex_diary_day_progress,
    _normalize_codex_diary_ai_summary_items,
    _normalize_codex_diary_ai_title,
    _repair_codex_diary_body_number_prefixes,
    _run_codex_diary_import_worker,
    mark_stale_codex_diary_import_runs,
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
        if response.status_code == 404:
            time.sleep(0.05)
            continue
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] != "running":
            return payload
    raise AssertionError(f"Codex diary import run did not finish: {payload}")


def _notes_by_public_ids(session: Session, public_ids: list[str]) -> list[NoteNode]:
    numeric_ids = [int(note_id) for note_id in public_ids if str(note_id).isdigit()]
    if not numeric_ids:
        return []
    return session.exec(
        select(NoteNode)
        .where(NoteNode.numeric_id.in_(numeric_ids))
        .order_by(NoteNode.start_at)
    ).all()


def _fake_codex_diary_ai_draft(source, blocks, *args, **kwargs):
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


def test_codex_diary_ai_draft_runs_in_batches(session: Session, auth_user, monkeypatch):
    captured_batch_sizes: list[int] = []

    def fake_draft(source, blocks, *, current_user, session):
        captured_batch_sizes.append(len(blocks))
        for block in blocks:
            block["title"] = f"草案 {block['block_key']}"
            block["summary_items"] = ["分批生成成功。"]
            block["lifecycle_stage"] = "done"
        return blocks

    monkeypatch.setattr("backend.api.notes._draft_codex_diary_blocks_with_ai", fake_draft)
    blocks = [
        {
            "block_key": f"block-{index}",
            "records": [{"assistant_result": "done"}],
        }
        for index in range(26)
    ]

    drafted = _draft_codex_diary_blocks_in_batches(
        {"date": "2026-05-03", "timezone": ZoneInfo("Asia/Shanghai")},
        blocks,
        current_user=auth_user,
        session=session,
    )

    assert captured_batch_sizes == [12, 12, 2]
    assert [block["title"] for block in drafted] == [f"草案 block-{index}" for index in range(26)]


def test_codex_diary_ai_draft_retries_failed_batch_by_splitting(session: Session, auth_user, monkeypatch):
    captured_batches: list[list[str]] = []

    def flaky_draft(source, blocks, *, current_user, session):
        keys = [block["block_key"] for block in blocks]
        captured_batches.append(keys)
        if len(blocks) == 4:
            raise ValueError("AI 日记草案缺少 block：block-3")
        for block in blocks:
            block["title"] = f"草案 {block['block_key']}"
            block["summary_items"] = ["拆分重试成功。"]
            block["lifecycle_stage"] = "done"
        return blocks

    monkeypatch.setattr("backend.api.notes.CODEX_DIARY_DRAFT_BATCH_SIZE", 4)
    monkeypatch.setattr("backend.api.notes._draft_codex_diary_blocks_with_ai", flaky_draft)
    blocks = [
        {
            "block_key": f"block-{index}",
            "records": [{"assistant_result": "done"}],
        }
        for index in range(4)
    ]

    drafted = _draft_codex_diary_blocks_in_batches(
        {"date": "2026-06-01", "timezone": ZoneInfo("Asia/Shanghai")},
        blocks,
        current_user=auth_user,
        session=session,
    )

    assert captured_batches == [
        ["block-0", "block-1", "block-2", "block-3"],
        ["block-0", "block-1"],
        ["block-2", "block-3"],
    ]
    assert [block["title"] for block in drafted] == [f"草案 block-{index}" for index in range(4)]


def test_codex_diary_ai_draft_failure_uses_rule_fallback(session: Session, auth_user, monkeypatch):
    def failing_draft(source, blocks, *, current_user, session):
        raise ValueError("Codex CLI 调用失败：in `service_tier`")

    monkeypatch.setattr("backend.api.notes._draft_codex_diary_blocks_with_ai", failing_draft)
    blocks = [
        {
            "block_key": "block-1",
            "records": [
                {
                    "thread_title": "统计汇总修复",
                    "user_request": "检查统计汇总缺失。",
                    "assistant_result": "已定位并修复导入失败路径。",
                }
            ],
        }
    ]

    drafted = _draft_codex_diary_blocks_in_batches(
        {"date": "2026-06-01", "timezone": ZoneInfo("Asia/Shanghai")},
        blocks,
        current_user=auth_user,
        session=session,
    )

    assert len(drafted) == 1
    assert drafted[0]["title"]
    assert drafted[0]["summary_items"] == ["已定位并修复导入失败路径"]
    assert drafted[0]["lifecycle_stage"] == "done"


def test_codex_diary_ai_json_allows_trailing_commas():
    payload = _extract_codex_diary_ai_json(
        '{"blocks":[{"block_key":"a","title":"邮件标识场景标注","summary_items":["完成"],},],}'
    )

    assert payload["blocks"][0]["title"] == "邮件标识场景标注"


def test_codex_diary_import_worker_heartbeats_while_drafting(session: Session, engine, auth_user, monkeypatch):
    entries = _create_device_entries(session, auth_user.id)
    run, entry_specs, root_identity, should_run = _create_codex_diary_import_run_record(
        session,
        current_user=auth_user,
        diary_date_text="2026-06-01",
        entry_ids=[entry.entry_id for entry in entries],
    )
    assert should_run is True
    start_at = _ts(2026, 6, 1, 9, 0)

    def fake_collect_source(entry_specs, root_identity, target_date_text, *, user_id, session):
        return {
            "date": target_date_text,
            "timezone": ZoneInfo("Asia/Shanghai"),
            "type_items": [],
            "turn_records": [
                {
                    "thread_id": "local:long-draft",
                    "thread_title": "长日记草案",
                    "project_label": "codeyun",
                    "time_range": "2026-06-01 09:00 ~ 2026-06-01 09:30",
                    "user_request": "整理长日记草案。",
                    "assistant_result": "长日记草案已整理。",
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

    heartbeat_seen = False

    def slow_ai_draft(source, blocks, *args, **kwargs):
        nonlocal heartbeat_seen
        with Session(engine) as probe:
            initial_heartbeat = probe.get(CodexDiaryImportRun, run.id).heartbeat_at or 0
        deadline = time.time() + 5
        while time.time() < deadline:
            time.sleep(0.1)
            with Session(engine) as probe:
                current_run = probe.get(CodexDiaryImportRun, run.id)
                current_heartbeat = current_run.heartbeat_at if current_run else 0
            if current_heartbeat and current_heartbeat > initial_heartbeat:
                heartbeat_seen = True
                break
        assert heartbeat_seen
        return _fake_codex_diary_ai_draft(source, blocks, *args, **kwargs)

    monkeypatch.setattr("backend.api.notes._collect_codex_diary_source", fake_collect_source)
    monkeypatch.setattr("backend.api.notes._draft_codex_diary_blocks_with_ai", slow_ai_draft)
    monkeypatch.setattr(
        "backend.api.notes.resolve_ai_app_runtime_config",
        lambda **kwargs: {"provider": "deepseek", "model": "deepseek-v4-pro"},
    )

    _run_codex_diary_import_worker(
        engine,
        run_id=run.id,
        user_id=auth_user.id,
        entry_specs=entry_specs,
        root_identity=root_identity,
    )

    session.expire_all()
    completed = session.get(CodexDiaryImportRun, run.id)
    assert heartbeat_seen is True
    assert completed.status == "completed"
    assert completed.created_note_count == 1


def test_codex_diary_import_creates_notes_from_all_active_devices(
    session: Session,
    engine,
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
                    {"key": "custom_codeyun", "label": "CodeYun/笔记", "color": "#409eff", "order": 10},
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

    run, entry_specs, root_identity, should_run = _create_codex_diary_import_run_record(
        session,
        current_user=auth_user,
        diary_date_text="2026-04-30",
    )
    assert should_run is True
    assert run.status == "running"
    assert run.entry_ids == entry_ids

    _run_codex_diary_import_worker(
        engine,
        run_id=run.id,
        user_id=auth_user.id,
        entry_specs=entry_specs,
        root_identity=root_identity,
    )

    session.expire_all()
    completed = session.get(CodexDiaryImportRun, run.id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.source_turn_count == 2
    assert completed.created_note_count == 1
    assert all(str(note_id).isdigit() for note_id in completed.created_note_ids)
    assert completed.result_json["draft_generator"] == "category-bucket-json-v1"
    assert completed.result_json["draft_provider"] == "codex-cli"
    assert completed.result_json["draft_model"] == "gpt-5.3-codex-spark"
    assert len(captured_entry_specs[0]) == 2

    notes = _notes_by_public_ids(session, completed.created_note_ids)
    assert len(notes) == 1
    assert [note.start_at for note in notes] == [_ts(2026, 4, 30, 9, 0)]
    first_note = notes[0]
    assert first_note.numeric_id is not None
    assert first_note.numeric_id > 0
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
        assert any(item[0] == "__codex_diary_run_id" and item[2] == completed.id for item in custom_fields)
        assert any(item[0] == "__codex_diary_date" and item[2] == "2026-04-30" for item in custom_fields)
        assert any(item[0] == "__codex_source_thread_ids" and len(item[2]) == 2 for item in custom_fields)
        worklog = next(item[2] for item in custom_fields if item[0] == "__codex_diary_worklog")
        assert worklog["date"] == "2026-04-30"
        assert worklog["duration_seconds"] == 75 * 60
        assert worklog["turn_count"] == 2
        assert worklog["source_devices"] == ["codepc_mf", "codepc_mi15"]

    first_plain_content = _plain_text(first_note.content)
    assert "统一 CodeYun 工作目录，并修复缓存接口。" not in first_plain_content
    assert "工具输出里有大量 JSON" not in first_plain_content
    assert "完成工作目录统一和接口修复" in first_plain_content
    assert "codepc_mf" in first_plain_content
    assert "接口" in first_plain_content
    assert "把 codepc_mi15 的 Codex 数据合并进日记来源。" not in first_plain_content
    assert "远端数据已经合并" in first_plain_content
    assert "codepc_mi15" in first_plain_content
    assert any(item[0] == "__completion_progress_expr" and item[2] == "75/75" for item in first_note.custom_fields)


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


def test_codex_diary_import_continues_when_one_device_unavailable(
    session: Session,
    engine,
    auth_user,
    monkeypatch,
):
    _create_device_entries(session, auth_user.id)

    def fake_collect_local_source(root_dir, target_date_text, *, user_id, session):
        start_at = _ts(2026, 5, 3, 9, 0)
        return {
            "date": target_date_text,
            "timezone": ZoneInfo("Asia/Shanghai"),
            "type_items": [],
            "turn_records": [
                {
                    "thread_id": "local-thread-1",
                    "thread_title": "周日 Codex 日记",
                    "project_label": "codeyun",
                    "time_range": "2026-05-03 09:00 ~ 2026-05-03 09:30",
                    "user_request": "整理周日 Codex 日记。",
                    "assistant_result": "周日 Codex 日记来源已整理。",
                    "assistant_process": "",
                    "start_at": start_at,
                    "end_at": start_at + 30 * 60,
                }
            ],
            "threads": [],
            "thread_count": 1,
            "turn_count": 1,
            "user_message_count": 1,
            "assistant_message_count": 1,
        }

    def fake_collect_remote_source(entry_spec, target_date_text, *, user_id, session):
        raise HTTPException(status_code=502, detail="连接远端设备失败：read timeout")

    monkeypatch.setattr(codex_device_summary, "ensure_local_codex_entry", lambda entry: None)
    monkeypatch.setattr(codex_device_summary, "collect_codex_daily_summary_source", fake_collect_local_source)
    monkeypatch.setattr(codex_device_summary, "collect_remote_codex_entry_daily_summary_source", fake_collect_remote_source)
    monkeypatch.setattr("backend.api.notes._draft_codex_diary_blocks_with_ai", _fake_codex_diary_ai_draft)

    run, entry_specs, root_identity, should_run = _create_codex_diary_import_run_record(
        session,
        current_user=auth_user,
        diary_date_text="2026-05-03",
    )
    assert should_run is True

    _run_codex_diary_import_worker(
        engine,
        run_id=run.id,
        user_id=auth_user.id,
        entry_specs=entry_specs,
        root_identity=root_identity,
    )

    session.expire_all()
    completed = session.get(CodexDiaryImportRun, run.id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.source_turn_count == 1
    assert completed.created_note_count == 1
    assert completed.error_message is None
    assert completed.result_json["source"]["source_failures"][0]["device_name"] == "codepc_mi15"
    assert "read timeout" in completed.result_json["source"]["source_failures"][0]["error"]

    note = session.exec(select(NoteNode).where(NoteNode.title == "周日 Codex 日记")).first()
    assert note is not None


def test_codex_diary_import_fails_when_all_devices_unavailable(
    client,
    session: Session,
    auth_user,
    monkeypatch,
):
    _create_device_entries(session, auth_user.id)

    def fake_collect_local_source(root_dir, target_date_text, *, user_id, session):
        raise HTTPException(status_code=500, detail="本机 Codex 缓存读取失败")

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
    assert "所有设备 Codex 数据读取失败" in completed["error_message"]
    assert "codepc_mf" in completed["error_message"]
    assert "codepc_mi15" in completed["error_message"]


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
    assert all(isinstance(note_id, int) for note_id in duplicate_response.json()["detail"]["duplicate_note_ids"])
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
    session.expire_all()
    first_notes = _notes_by_public_ids(session, first_completed["created_note_ids"])
    confirmed_notes = _notes_by_public_ids(session, confirmed_completed["created_note_ids"])
    assert len(first_notes) == 1 and first_notes[0].deleted_at
    assert len(confirmed_notes) == 1 and not confirmed_notes[0].deleted_at


def test_codex_diary_replace_existing_rebuilds_day_category_blocks(
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
                    {"key": "legacy_color_67c23a", "label": "凡修", "color": "#67c23a", "order": 10},
                    {"key": "custom_codeyun_general", "label": "CodeYun/综合", "color": "#00bfff", "order": 20},
                ]
            },
        )
    )
    session.commit()

    old_run, _entry_specs, _root_identity, _should_run = _create_codex_diary_import_run_record(
        session,
        current_user=auth_user,
        diary_date_text="2026-06-28",
        entry_ids=[],
    )
    start_at = _ts(2026, 6, 28, 17, 19)
    old_codeyun_note = _create_codex_diary_note(
        session,
        current_user=auth_user,
        run=old_run,
        block={
            "title": "洞天福地返回闭环",
            "category_key": "custom_codeyun_general",
            "note_categories": [{"key": "custom_codeyun_general", "weight": 100}],
            "records": [
                {
                    "thread_id": "fanxiu:dongtian",
                    "thread_title": "洞天福地返回闭环",
                    "assistant_result": "洞天福地返回闭环已验证。",
                    "start_at": start_at,
                    "end_at": start_at + 3 * 60,
                    "duration_seconds": 3 * 60,
                    "source_device_name": "codepc_mf",
                }
            ],
            "duration_seconds": 3 * 60,
            "start_at": start_at,
            "end_at": start_at + 3 * 60,
        },
    )
    old_fanxiu_note = _create_codex_diary_note(
        session,
        current_user=auth_user,
        run=old_run,
        block={
            "title": "邮件清理链路收敛",
            "category_key": "legacy_color_67c23a",
            "note_categories": [{"key": "legacy_color_67c23a", "weight": 100}],
            "records": [
                {
                    "thread_id": "fanxiu:mail",
                    "thread_title": "邮件清理链路收敛",
                    "assistant_result": "邮件清理链路已收敛。",
                    "start_at": start_at + 10 * 60,
                    "end_at": start_at + 30 * 60,
                    "duration_seconds": 20 * 60,
                    "source_device_name": "codepc_mf",
                }
            ],
            "duration_seconds": 20 * 60,
            "start_at": start_at + 10 * 60,
            "end_at": start_at + 30 * 60,
        },
    )
    old_run.status = "completed"
    old_run.stage = "completed"
    old_run.created_note_ids = [_note_public_id for _note_public_id in [str(old_codeyun_note.numeric_id), str(old_fanxiu_note.numeric_id)]]
    old_run.created_note_count = 2
    session.add(old_run)
    session.commit()

    def fake_collect_source(entry_specs, root_identity, target_date_text, *, user_id, session):
        return {
            "date": target_date_text,
            "timezone": ZoneInfo("Asia/Shanghai"),
            "turn_records": [
                {
                    "thread_id": "fanxiu:dongtian",
                    "thread_title": "凡修行为树洞天任务",
                    "project_label": "codeyun",
                    "user_request": "洞天福地领取后需要补 #279 返回并等待 #34 世界。",
                    "assistant_result": "在 daily_foundation.py 补 #279[返回] -> 等待 #34 世界，形成稳定回归锚点。",
                    "start_at": start_at,
                    "end_at": start_at + 3 * 60,
                    "source_entry_id": entries[0].entry_id,
                    "source_device_name": "codepc_mf",
                },
                {
                    "thread_id": "fanxiu:mail",
                    "thread_title": "邮件清理链路收敛",
                    "project_label": "codeyun",
                    "user_request": "继续调试优化邮件清理功能。",
                    "assistant_result": "在 mail.py 收敛邮件判定流程，复跑验证邮件_选择性领取结果。",
                    "start_at": start_at + 10 * 60,
                    "end_at": start_at + 30 * 60,
                    "source_entry_id": entries[0].entry_id,
                    "source_device_name": "codepc_mf",
                },
            ],
            "threads": [],
            "thread_count": 2,
            "turn_count": 2,
            "user_message_count": 2,
            "assistant_message_count": 2,
        }

    def fake_draft(source, blocks, *args, **kwargs):
        assert len(blocks) == 1
        assert blocks[0]["category_key"] == "legacy_color_67c23a"
        assert len(blocks[0]["records"]) == 2
        blocks[0]["title"] = "凡修任务闭环整理"
        blocks[0]["summary_items"] = [
            "洞天福地返回闭环已补齐并验证。",
            "邮件清理链路继续收敛。",
        ]
        blocks[0]["lifecycle_stage"] = "done"
        return blocks

    monkeypatch.setattr("backend.api.notes._collect_codex_diary_source", fake_collect_source)
    monkeypatch.setattr("backend.api.notes._draft_codex_diary_blocks_with_ai", fake_draft)

    response = client.post(
        "/api/notes/codex-diary/import-runs",
        json={"date": "2026-06-28", "replace_existing": True},
    )
    assert response.status_code == 200
    completed = _wait_for_import_run(client, response.json()["id"])

    assert completed["status"] == "completed"
    assert completed["replace_existing"] is True
    assert completed["created_note_count"] == 1
    assert completed["duplicate_note_ids"] == [old_codeyun_note.numeric_id, old_fanxiu_note.numeric_id]
    assert completed["result"]["replaced_note_count"] == 2

    session.expire_all()
    old_notes = _notes_by_public_ids(session, completed["duplicate_note_ids"])
    assert len(old_notes) == 2
    assert all(note.deleted_at and note.deleted_at > 0 for note in old_notes)

    new_notes = _notes_by_public_ids(session, completed["created_note_ids"])
    assert len(new_notes) == 1
    assert new_notes[0].title == "凡修任务闭环整理"
    assert new_notes[0].primary_category == "legacy_color_67c23a"
    assert new_notes[0].note_categories == [{"key": "legacy_color_67c23a", "weight": 100}]
    assert "洞天福地返回闭环已补齐" in _plain_text(new_notes[0].content)
    assert "邮件清理链路继续收敛" in _plain_text(new_notes[0].content)


def test_codex_diary_import_rejects_active_same_scope_run(
    session: Session,
    auth_user,
):
    _create_device_entries(session, auth_user.id)

    run, _, _, should_run = _create_codex_diary_import_run_record(
        session,
        current_user=auth_user,
        diary_date_text="2026-05-02",
    )
    assert should_run is True
    assert run.status == "running"

    try:
        _create_codex_diary_import_run_record(
            session,
            current_user=auth_user,
            diary_date_text="2026-05-02",
        )
    except HTTPException as exc:
        assert exc.status_code == 409
        assert exc.detail["code"] == "active_import"
        assert exc.detail["run_id"] == run.id
    else:
        raise AssertionError("expected active Codex diary import to be rejected")


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
                    {"key": "custom_codeyun", "label": "CodeYun/笔记", "color": "#409eff", "order": 10},
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
                        "thread_title": "星图笔记表格清单读取",
                        "project_label": "codeyun",
                        "time_range": "2026-05-02 09:00 ~ 2026-05-02 09:25",
                        "user_request": "改成从表格读取星图笔记清单。",
                        "assistant_result": "已改成从星图笔记表格读取清单。",
                        "assistant_process": "",
                        "start_at": morning,
                        "end_at": morning + 25 * 60,
                    "source_entry_id": entries[0].entry_id,
                    "source_device_name": "codepc_mf",
                },
                    {
                        "thread_id": "codeyun:afternoon",
                        "thread_title": "日历节点菜单合并",
                        "project_label": "codeyun",
                        "time_range": "2026-05-02 15:00 ~ 2026-05-02 15:20",
                        "user_request": "统一星图笔记日历节点菜单。",
                        "assistant_result": "已合并日历节点菜单入口。",
                        "assistant_process": "",
                        "start_at": afternoon,
                        "end_at": afternoon + 20 * 60,
                    "source_entry_id": entries[0].entry_id,
                    "source_device_name": "codepc_mf",
                },
                    {
                        "thread_id": "codeyun:evening",
                        "thread_title": "Codex 日记全量分页",
                        "project_label": "codeyun",
                        "time_range": "2026-05-02 20:00 ~ 2026-05-02 20:18",
                        "user_request": "Codex 日记列表改成全量分页。",
                        "assistant_result": "Codex 日记列表已改成全量时间线分页加载。",
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

    def fake_draft(source, blocks, *args, **kwargs):
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

    notes = _notes_by_public_ids(session, completed["created_note_ids"])
    assert [note.title for note in notes] == ["Codex 日记工作汇总"]
    assert [note.start_at for note in notes] == [_ts(2026, 5, 2, 9, 0)]


def test_codex_diary_blocks_keep_single_category_even_over_ten_hours(
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
                "time_range": "2026-05-03 09:00 ~ 2026-05-03 13:00",
                "user_request": "调整日记拆块基准。",
                "assistant_result": "日记拆块基准已调整。",
                "assistant_process": "",
                "start_at": start_at,
                "end_at": start_at + 4 * 60 * 60,
            },
            {
                "thread_id": "codeyun:thread-b",
                "thread_title": "Codex 日记进度",
                "project_label": "codeyun",
                "time_range": "2026-05-03 13:10 ~ 2026-05-03 17:10",
                "user_request": "按耗时计算日记进度。",
                "assistant_result": "日记进度已改为耗时比例。",
                "assistant_process": "",
                "start_at": start_at + 4 * 60 * 60 + 10 * 60,
                "end_at": start_at + 8 * 60 * 60 + 10 * 60,
            },
            {
                "thread_id": "codeyun:thread-c",
                "thread_title": "Codex 日记提示词",
                "project_label": "codeyun",
                "time_range": "2026-05-03 17:20 ~ 2026-05-03 21:20",
                "user_request": "标题突出核心突破。",
                "assistant_result": "日记提示词已调整为突出核心工作。",
                "assistant_process": "",
                "start_at": start_at + 8 * 60 * 60 + 20 * 60,
                "end_at": start_at + 12 * 60 * 60 + 20 * 60,
            },
        ],
    }

    blocks = _build_codex_diary_blocks(source, user_id=auth_user.id, session=session)

    assert len(blocks) == 1
    assert [round(block["duration_seconds"] / 60) for block in blocks] == [720]
    assert len(blocks[0]["records"]) == 3


def test_codex_diary_completion_progress_defaults_to_own_duration_without_day_context():
    assert _build_codex_diary_completion_progress_expr({"duration_seconds": 37 * 60}) == "37/37"


def test_codex_diary_completion_progress_uses_day_max_category_duration(
    session: Session,
    auth_user,
):
    session.add(
        AppSetting(
            key=build_note_category_palette_setting_key(auth_user.id),
            value={
                "items": [
                    {"key": "general", "label": "综合", "color": "#909399", "order": 0},
                    {"key": "legacy_color_67c23a", "label": "凡修", "color": "#67c23a", "order": 10},
                    {"key": "legacy_color_e6a23c", "label": "考勤", "color": "#e6a23c", "order": 20},
                ]
            },
        )
    )
    session.commit()
    start_at = _ts(2026, 6, 28, 0, 0)
    source = {
        "date": "2026-06-28",
        "timezone": ZoneInfo("Asia/Shanghai"),
        "turn_records": [
            {
                "thread_id": "fanxiu:long",
                "thread_title": "凡修调度状态归位",
                "project_label": "凡修",
                "user_request": "修复凡修调度和运行状态。",
                "assistant_result": "凡修调度状态已归位。",
                "start_at": start_at,
                "end_at": start_at + 840 * 60,
            },
            {
                "thread_id": "attendance:short",
                "thread_title": "考勤链路分类策略",
                "project_label": "考勤",
                "user_request": "调整考勤链路分类策略。",
                "assistant_result": "考勤链路分类策略已调整。",
                "start_at": start_at + 900 * 60,
                "end_at": start_at + 960 * 60,
            },
        ],
    }

    blocks = _build_codex_diary_blocks(source, user_id=auth_user.id, session=session)

    assert {block["category_key"] for block in blocks} == {"legacy_color_67c23a", "legacy_color_e6a23c"}
    assert {block["progress_base_minutes"] for block in blocks} == {840}
    assert sorted(_build_codex_diary_completion_progress_expr(block) for block in blocks) == ["60/840", "840/840"]


def test_codex_diary_blocks_merge_same_category_details_without_time_split(
    session: Session,
    auth_user,
):
    session.add(
        AppSetting(
            key=build_note_category_palette_setting_key(auth_user.id),
            value={
                "items": [
                    {"key": "general", "label": "综合", "color": "#909399", "order": 0},
                    {"key": "custom_codeyun_note", "label": "CodeYun/笔记", "color": "#409eff", "order": 10},
                    {"key": "legacy_color_e6a23c", "label": "考勤", "color": "#e6a23c", "order": 20},
                ]
            },
        )
    )
    session.commit()
    start_at = _ts(2026, 6, 4, 9, 0)
    source = {
        "date": "2026-06-04",
        "timezone": ZoneInfo("Asia/Shanghai"),
        "turn_records": [
            {
                "thread_id": "codeyun:core",
                "thread_title": "表格加载性能优化",
                "project_label": "CodeYun",
                "user_request": "修复表格加载性能问题。",
                "assistant_result": "已完成后端接口、缓存策略和前端页面渲染优化，并通过回归测试。",
                "start_at": start_at,
                "end_at": start_at + 5 * 60 * 60,
            },
            {
                "thread_id": "codeyun:tiny",
                "thread_title": "临时说明补充",
                "project_label": "CodeYun",
                "user_request": "顺手补一下交付说明。",
                "assistant_result": "已补充交付边界。",
                "start_at": start_at + 5 * 60 * 60 + 5 * 60,
                "end_at": start_at + 5 * 60 * 60 + 10 * 60,
            },
        ],
    }

    blocks = _build_codex_diary_blocks(source, user_id=auth_user.id, session=session)

    assert len(blocks) == 1
    assert round(blocks[0]["duration_seconds"] / 60) == 305
    assert blocks[0]["category_key"] == "custom_codeyun_note"
    assert len(blocks[0]["records"]) == 2


def test_codex_diary_blocks_ignore_injected_runtime_context(
    session: Session,
    auth_user,
):
    start_at = _ts(2026, 7, 12, 9, 0)
    source = {
        "date": "2026-07-12",
        "timezone": ZoneInfo("Asia/Shanghai"),
        "turn_records": [
            {
                "thread_id": "fanxiu:zhenxie",
                "thread_title": "日常镇邪",
                "user_request": "<recommended_plugins>injected plugin catalog</recommended_plugins>",
                "assistant_result": "",
                "start_at": start_at,
                "end_at": start_at,
            },
            {
                "thread_id": "fanxiu:zhenxie",
                "thread_title": "日常镇邪",
                "user_request": '<codex_internal_context source="goal"><objective>继续镇邪</objective></codex_internal_context>',
                "assistant_result": "",
                "start_at": start_at + 60,
                "end_at": start_at + 60,
            },
            {
                "thread_id": "fanxiu:zhenxie",
                "thread_title": "日常镇邪",
                "user_request": "继续运行日常镇邪。",
                "assistant_result": "已验证 #271[参加]。",
                "start_at": start_at + 120,
                "end_at": start_at + 180,
            },
        ],
    }

    blocks = _build_codex_diary_blocks(source, user_id=auth_user.id, session=session)

    assert _is_codex_diary_synthetic_turn(source["turn_records"][0])
    assert _is_codex_diary_synthetic_turn(source["turn_records"][1])
    assert len(blocks) == 1
    assert len(blocks[0]["records"]) == 1
    assert blocks[0]["records"][0]["user_request"] == "继续运行日常镇邪。"


def test_codex_diary_day_category_is_global_and_progress_uses_day_maximum(
    session: Session,
    auth_user,
):
    def add_note(note_id: str, category: str, minutes: int) -> NoteNode:
        note = NoteNode(
            id=note_id,
            user_id=auth_user.id,
            title=note_id,
            primary_category=category,
            note_categories=[{"key": category, "weight": 100}],
            custom_fields=[
                ["__codex_diary_date", "string", "2026-07-12"],
                ["__codex_diary_scope_key", "string", f"scope:{note_id}"],
                ["__codex_diary_worklog", "json", {"duration_seconds": minutes * 60}],
            ],
        )
        session.add(note)
        return note

    fanxiu_a = add_note("fanxiu-a", "fanxiu", 120)
    fanxiu_b = add_note("fanxiu-b", "fanxiu", 60)
    attendance = add_note("attendance", "attendance", 180)
    session.commit()

    assert _find_active_codex_diary_category_note_ids(
        session,
        user_id=auth_user.id,
        diary_date="2026-07-12",
        category_key="fanxiu",
    ) == ["fanxiu-a", "fanxiu-b"]

    denominator = _normalize_codex_diary_day_progress(
        session,
        user_id=auth_user.id,
        diary_date="2026-07-12",
    )
    session.commit()
    session.refresh(fanxiu_a)
    session.refresh(fanxiu_b)
    session.refresh(attendance)

    assert denominator == 180
    assert get_completion_progress_expr(fanxiu_a.custom_fields) == "120/180"
    assert get_completion_progress_expr(fanxiu_b.custom_fields) == "60/180"
    assert get_completion_progress_expr(attendance.custom_fields) == "180/180"


def test_codex_diary_blocks_keep_short_categories_separate(
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

    assert len(blocks) == 2
    assert {block["category_key"] for block in blocks} == {"custom_attendance", "custom_fanxiu"}
    assert all(block["note_categories"] == [{"key": block["category_key"], "weight": 100}] for block in blocks)
    assert sum(round(block["duration_seconds"] / 60) for block in blocks) == 60
    requests = " ".join(record["user_request"] for block in blocks for record in block["records"])
    assert "课程脚本" in requests
    assert "prayer_cycle" in requests


def test_codex_diary_blocks_do_not_absorb_short_project_detail(
    session: Session,
    auth_user,
):
    session.add(
        AppSetting(
            key=build_note_category_palette_setting_key(auth_user.id),
            value={
                "items": [
                    {"key": "general", "label": "综合", "color": "#909399", "order": 0},
                    {"key": "custom_codeyun_general", "label": "CodeYun/综合", "color": "#606266", "order": 10},
                    {"key": "custom_codeyun_note", "label": "CodeYun/笔记", "color": "#409eff", "order": 20},
                    {"key": "project", "label": "项目", "color": "#67c23a", "order": 30},
                ]
            },
        )
    )
    session.commit()
    start_at = _ts(2026, 6, 4, 9, 0)
    source = {
        "date": "2026-06-04",
        "timezone": ZoneInfo("Asia/Shanghai"),
        "turn_records": [
            {
                "thread_id": "codeyun:value-engineering",
                "thread_title": "接口稳定性修复",
                "project_label": "CodeYun",
                "user_request": "修复页面接口和缓存问题。",
                "assistant_result": "已完成后端接口、前端页面、缓存配置和回归测试。",
                "start_at": start_at,
                "end_at": start_at + 45 * 60,
            },
            {
                "thread_id": "codeyun:value-project",
                "thread_title": "交付边界设计",
                "project_label": "CodeYun",
                "user_request": "梳理项目交付流程和规则边界。",
                "assistant_result": "已明确交付策略、流程边界和复盘口径。",
                "start_at": start_at + 60 * 60,
                "end_at": start_at + 90 * 60,
            },
        ],
    }

    blocks = _build_codex_diary_blocks(source, user_id=auth_user.id, session=session)

    assert len(blocks) >= 1
    assert len({block["category_key"] for block in blocks}) == len(blocks)
    assert all(block["category_key"] != "project" for block in blocks)
    assert sum(round(block["duration_seconds"] / 60) for block in blocks) == 75
    combined = " ".join(
        f"{record['thread_title']} {record['assistant_result']}"
        for block in blocks
        for record in block["records"]
    )
    assert "接口" in combined
    assert "交付" in combined


def test_codex_diary_blocks_split_short_mixed_topics_without_import_category(
    session: Session,
    auth_user,
):
    session.add(
        AppSetting(
            key=build_note_category_palette_setting_key(auth_user.id),
            value={
                "items": [
                    {"key": "general", "label": "综合", "color": "#909399", "order": 0},
                    {"key": "custom_ai", "label": "AI", "color": "#6a0dad", "order": 10},
                    {"key": "custom_fanxiu", "label": "凡修", "color": "#67c23a", "order": 20},
                ]
            },
        )
    )
    session.add(
        NoteNode(
            id="wrong-codex-diary-hint",
            user_id=auth_user.id,
            title="小狼毫预测模型与凡修灵脉",
            primary_category="custom_fanxiu",
            note_categories=[{"key": "custom_fanxiu", "weight": 100}],
            custom_fields=[["__codex_diary_date", "string", "2026-05-13"]],
        )
    )
    session.commit()
    start_at = _ts(2026, 5, 13, 10, 0)
    source = {
        "date": "2026-05-13",
        "timezone": ZoneInfo("Asia/Shanghai"),
        "turn_records": [
            {
                "thread_id": "mixed:rime-fanxiu",
                "thread_title": "小狼毫预测模型与凡修灵脉",
                "project_label": "codeyun",
                "user_request": "优化小狼毫 Rime 输入法上下文预测模型和预测索引。",
                "assistant_result": "已完成预编辑、输入历史、自定义短语和预测候选链路。",
                "start_at": start_at,
                "end_at": start_at + 30 * 60,
            },
            {
                "thread_id": "mixed:rime-fanxiu",
                "thread_title": "小狼毫预测模型与凡修灵脉",
                "project_label": "凡修",
                "user_request": "补齐凡修洞天福地收益和造化灵脉清体力任务。",
                "assistant_result": "已确认灵脉探索按钮、快速探索弹窗和每天固定清空的简化模型，并写入 AI_CONTEXT.md。",
                "start_at": start_at + 40 * 60,
                "end_at": start_at + 70 * 60,
            },
        ],
    }

    blocks = _build_codex_diary_blocks(source, user_id=auth_user.id, session=session)

    assert len(blocks) == 2
    assert {block["category_key"] for block in blocks} == {"custom_ai", "custom_fanxiu"}
    assert all(not str(block["category_key"]).startswith("import_") for block in blocks)
    requests = " ".join(record["user_request"] for block in blocks for record in block["records"])
    assert "小狼毫" in requests
    assert "洞天福地" in requests


def test_codex_diary_blocks_keep_tiny_mixed_details_by_category(
    session: Session,
    auth_user,
):
    session.add(
        AppSetting(
            key=build_note_category_palette_setting_key(auth_user.id),
            value={
                "items": [
                    {"key": "general", "label": "综合", "color": "#909399", "order": 0},
                    {"key": "custom_ai", "label": "AI", "color": "#6a0dad", "order": 10},
                    {"key": "custom_fanxiu", "label": "凡修", "color": "#67c23a", "order": 20},
                    {"key": "custom_codeyun_cluster", "label": "CodeYun/集群", "color": "#0067a5", "order": 30},
                    {"key": "custom_codeyun_note", "label": "CodeYun/笔记", "color": "#446ccf", "order": 40},
                    {"key": "custom_attendance", "label": "考勤", "color": "#c19a6b", "order": 50},
                ]
            },
        )
    )
    session.commit()
    start_at = _ts(2026, 5, 16, 0, 0)
    source = {
        "date": "2026-05-16",
        "timezone": ZoneInfo("Asia/Shanghai"),
        "turn_records": [
            {
                "thread_id": "mixed:codex",
                "thread_title": "笔记与凡修OCR整合",
                "project_label": "codeyun",
                "user_request": "监控凡修午夜任务运行，确认行为树仍在计划队列中。",
                "assistant_result": "已修复魔界地图页误判、凡修助手入口等无人值守卡点。",
                "start_at": start_at,
                "end_at": start_at + 10 * 60,
            },
            {
                "thread_id": "mixed:codex",
                "thread_title": "笔记与凡修OCR整合",
                "project_label": "codeyun",
                "user_request": "围绕 CodeYun 集群服务管理梳理 OCR 集中化方案。",
                "assistant_result": "明确账号 token、设备 token、服务 token 的权限边界，并让凡修脚本优先尝试局域网 CodeYun OCR。",
                "start_at": start_at + 20 * 60,
                "end_at": start_at + 30 * 60,
            },
            {
                "thread_id": "mixed:codex",
                "thread_title": "笔记与凡修OCR整合",
                "project_label": "codeyun",
                "user_request": "推进星图笔记大跨度时间视图设计和语雀导入试验。",
                "assistant_result": "已补充章节节点，并讨论文章大纲、独立 /doc 页面和自然序文档 URL。",
                "start_at": start_at + 40 * 60,
                "end_at": start_at + 50 * 60,
            },
            {
                "thread_id": "mixed:codex",
                "thread_title": "笔记与凡修OCR整合",
                "project_label": "codeyun",
                "user_request": "整理念住闯关课程迁移口径。",
                "assistant_result": "明确 A 组冻结历史、B 组继续追踪，并为报名表增加 A/B 排序。",
                "start_at": start_at + 60 * 60,
                "end_at": start_at + 70 * 60,
            },
        ],
    }

    blocks = _build_codex_diary_blocks(source, user_id=auth_user.id, session=session)

    assert len(blocks) >= 2
    assert len({block["category_key"] for block in blocks}) == len(blocks)
    assert {block["category_key"] for block in blocks} <= {
        "custom_fanxiu",
        "custom_codeyun_cluster",
        "custom_codeyun_note",
        "custom_attendance",
    }
    assert all(not str(block["category_key"]).startswith("import_") for block in blocks)
    assert sum(len(block["records"]) for block in blocks) == 4


def test_codex_diary_blocks_do_not_absorb_tiny_topic_tail_across_categories(
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
                    {"key": "legacy_color_e6a23c", "label": "考勤", "color": "#e6a23c", "order": 20},
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
    assert {block["category_key"] for block in blocks} == {"custom_codeyun_note", "legacy_color_e6a23c"}
    assert all(block["note_categories"][0]["key"] == block["category_key"] for block in blocks)
    assert sum(len(block["records"]) for block in blocks) == 4
    assert sum(round(block["duration_seconds"] / 60) for block in blocks) == 100
    assert {record["thread_title"] for block in blocks for record in block["records"]} == {
        "PDF阅读器",
        "页面笔记",
        "PDF用户状态层",
        "问卷前端",
    }


def test_codex_diary_blocks_keep_non_adjacent_tiny_same_day_work_by_category(
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
                    {"key": "legacy_color_e6a23c", "label": "考勤", "color": "#e6a23c", "order": 20},
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

    assert len(blocks) == 2
    assert {block["category_key"] for block in blocks} == {"custom_codeyun_note", "legacy_color_e6a23c"}
    assert sum(len(block["records"]) for block in blocks) == 3
    assert sum(round(block["duration_seconds"] / 60) for block in blocks) == 60
    assert {record["thread_title"] for block in blocks for record in block["records"]} == {"PDF阅读器", "问卷恢复", "日记导入"}


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


def test_codex_diary_report_reconstruction_palette_context_is_codeyun_note(
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
                    {"key": "legacy_color_e6a23c", "label": "考勤", "color": "#c19a6b", "order": 20},
                    {"key": "custom_project", "label": "项目", "color": "#b58900", "order": 30},
                ]
            },
        )
    )
    session.commit()
    start_at = _ts(2026, 6, 28, 9, 16)
    source = {
        "date": "2026-06-28",
        "timezone": ZoneInfo("Asia/Shanghai"),
        "turn_records": [
            {
                "thread_id": "codex-diary:category-policy",
                "thread_title": "考勤链路分类策略",
                "project_label": "codeyun",
                "user_request": (
                    "日报重构应该按连续问答事务归到单一分类桶，"
                    "不要因为提到考勤配色和问卷渲染就归为考勤。"
                ),
                "assistant_result": (
                    "提取并确认 note.category_palette.user.2 下含考勤、CodeYun/笔记、"
                    "CodeYun/集群等颜色映射，作为星图配色基准。给出日报重构方向："
                    "以连续问答事务为最小单元，按单一最相似分类聚合到日内分类桶。"
                    "修正考勤问卷渲染：NoteSheetWorkspace.vue 在无合并单元格时使用 "
                    "mergeCells=false，避免空 merged_cells 传参导致表格异常。"
                ),
                "start_at": start_at,
                "end_at": start_at + 19 * 60,
            }
        ],
    }

    blocks = _build_codex_diary_blocks(source, user_id=auth_user.id, session=session)

    assert len(blocks) == 1
    assert blocks[0]["category_key"] == "custom_codeyun_note"
    assert blocks[0]["note_categories"] == [{"key": "custom_codeyun_note", "weight": 100}]


def test_codex_diary_category_design_examples_do_not_force_attendance(
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
                    {"key": "legacy_color_e6a23c", "label": "考勤", "color": "#c19a6b", "order": 20},
                    {"key": "legacy_color_67c23a", "label": "凡修", "color": "#73b839", "order": 30},
                    {"key": "custom_project", "label": "项目", "color": "#b58900", "order": 40},
                ]
            },
        )
    )
    session.commit()
    start_at = _ts(2026, 6, 28, 9, 42)
    source = {
        "date": "2026-06-28",
        "timezone": ZoneInfo("Asia/Shanghai"),
        "turn_records": [
            {
                "thread_id": "codex-diary:classification-step",
                "thread_title": "星图笔记分类方案",
                "project_label": "codeyun",
                "user_request": "第1步的设计，要分步骤流程，提高精度吧？不能一口气让ai识别太多。",
                "assistant_result": (
                    "第 1 步应该拆成事务分类和候选分类召回。"
                    "例如出现 MuMu/Runtime/行为树先给凡修候选，出现考勤/问卷/返款先给考勤候选，"
                    "但最终由 AI 分类在候选中单选，并结合分类说明提高分类正确性。"
                ),
                "start_at": start_at,
                "end_at": start_at + 2 * 60,
            }
        ],
    }

    blocks = _build_codex_diary_blocks(source, user_id=auth_user.id, session=session)

    assert len(blocks) == 1
    assert blocks[0]["category_key"] == "custom_codeyun_note"


def test_codex_diary_category_description_ai_classification_is_codeyun_note_not_cluster(
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
                    {"key": "custom_codeyun_cluster", "label": "CodeYun/集群", "color": "#0067a5", "order": 20},
                    {"key": "custom_project", "label": "项目", "color": "#b58900", "order": 30},
                ]
            },
        )
    )
    session.commit()
    start_at = _ts(2026, 6, 28, 9, 42)
    source = {
        "date": "2026-06-28",
        "timezone": ZoneInfo("Asia/Shanghai"),
        "turn_records": [
            {
                "thread_id": "codex-diary:category-description",
                "thread_title": "分类描述增强AI归类",
                "project_label": "codeyun",
                "user_request": "补每个分类的解释，不然 AI 分类正确性很低。",
                "assistant_result": (
                    "识别到仅靠短标签导致 AI 分类准确率偏低，给出策略："
                    "为每类补充语义化说明文案，并作为分类阶段的核心上下文输入。"
                    "该策略与问答事务单分类模型一致，先保留规则召回，"
                    "再用清晰语义约束 AI 归档边界，降低跨类误判。"
                ),
                "start_at": start_at,
                "end_at": start_at + 60,
            }
        ],
    }

    blocks = _build_codex_diary_blocks(source, user_id=auth_user.id, session=session)

    assert len(blocks) == 1
    assert blocks[0]["category_key"] == "custom_codeyun_note"


def test_codex_diary_hides_project_for_diary_reconstruction(
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
                    {"key": "project", "label": "项目", "color": "#a57c00", "order": 20},
                ]
            },
        )
    )
    session.commit()
    start_at = _ts(2026, 6, 28, 0, 27)
    source = {
        "date": "2026-06-28",
        "timezone": ZoneInfo("Asia/Shanghai"),
        "turn_records": [
            {
                "thread_id": "codex-diary:rebuild",
                "thread_title": "日记聚合重构",
                "project_label": "codeyun",
                "user_request": "把 Codex 日记生成策略从按时间块切分改成连续问答事务和分类桶聚合。",
                "assistant_result": (
                    "实现规则候选召回后由 AI 单选分类，并补充分类说明输入，"
                    "提升分类稳定性与可解释性。相关机制沉淀在 backend/api/notes.py。"
                ),
                "start_at": start_at,
                "end_at": start_at + 64 * 60,
            }
        ],
    }

    blocks = _build_codex_diary_blocks(source, user_id=auth_user.id, session=session)

    assert len(blocks) == 1
    assert blocks[0]["category_key"] == "custom_codeyun_note"


def test_codex_diary_cluster_service_operations_remain_codeyun_cluster(
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
                    {"key": "custom_codeyun_cluster", "label": "CodeYun/集群", "color": "#0067a5", "order": 20},
                ]
            },
        )
    )
    session.commit()
    start_at = _ts(2026, 6, 28, 10, 0)
    source = {
        "date": "2026-06-28",
        "timezone": ZoneInfo("Asia/Shanghai"),
        "turn_records": [
            {
                "thread_id": "codeyun-cluster:service",
                "thread_title": "CodeYun 集群服务管理",
                "project_label": "codeyun",
                "user_request": "梳理多机器 CodeYun 集群服务管理和作业调度。",
                "assistant_result": "明确设备 token、服务 token、局域网 CodeYun OCR 与 OCR 集中化边界。",
                "start_at": start_at,
                "end_at": start_at + 10 * 60,
            }
        ],
    }

    blocks = _build_codex_diary_blocks(source, user_id=auth_user.id, session=session)

    assert len(blocks) == 1
    assert blocks[0]["category_key"] == "custom_codeyun_cluster"


def test_codex_diary_fanxiu_context_overrides_generic_mail_title(
    session: Session,
    auth_user,
):
    session.add(
        AppSetting(
            key=build_note_category_palette_setting_key(auth_user.id),
            value={
                "items": [
                    {"key": "general", "label": "综合", "color": "#909399", "order": 0},
                    {"key": "legacy_color_67c23a", "label": "凡修", "color": "#67c23a", "order": 10},
                    {"key": "custom_codeyun_note", "label": "CodeYun/笔记", "color": "#446ccf", "order": 20},
                ]
            },
        )
    )
    session.commit()
    start_at = _ts(2026, 6, 28, 0, 5)
    source = {
        "date": "2026-06-28",
        "timezone": ZoneInfo("Asia/Shanghai"),
        "turn_records": [
            {
                "thread_id": "fanxiu:mail-helper",
                "thread_title": "邮件清理可靠性修正",
                "project_label": "CodeYun",
                "user_request": "修正日常_报备调度口径，排除0点误触发，并继续跑通邮件清理链路。",
                "assistant_result": (
                    "持续跑通邮件_选择性领取，将“见到/领取/滚动”接入判断标准；"
                    "识别小助手链路仍有起点漂移，抓包巡检显示 daemon 与 pcap 进程仍在，"
                    "但 packet_worker 有 ok=false、has_unconfirmed_gap 异常。"
                ),
                "start_at": start_at,
                "end_at": start_at + 610 * 60,
            }
        ],
    }

    blocks = _build_codex_diary_blocks(source, user_id=auth_user.id, session=session)

    assert len(blocks) == 1
    assert blocks[0]["category_key"] == "legacy_color_67c23a"
    assert blocks[0]["note_categories"] == [{"key": "legacy_color_67c23a", "weight": 100}]


def test_codex_diary_classification_ignores_agent_boilerplate_for_fanxiu_goal(
    session: Session,
    auth_user,
):
    session.add(
        AppSetting(
            key=build_note_category_palette_setting_key(auth_user.id),
            value={
                "items": [
                    {"key": "general", "label": "综合", "color": "#909399", "order": 0},
                    {"key": "legacy_color_67c23a", "label": "凡修", "color": "#67c23a", "order": 10},
                    {"key": "custom_codeyun_note", "label": "CodeYun/笔记", "color": "#446ccf", "order": 20},
                ]
            },
        )
    )
    session.commit()
    start_at = _ts(2026, 6, 28, 1, 40)
    source = {
        "date": "2026-06-28",
        "timezone": ZoneInfo("Asia/Shanghai"),
        "turn_records": [
            {
                "thread_id": "fanxiu:mail-goal",
                "thread_title": "凡修行为树，还有几个到期的作业没运行？",
                "project_label": "codeyun",
                "user_request": (
                    "# AGENTS.md instructions for D:\\home\\chenkunze\\slns\\codeyun "
                    "<INSTRUCTIONS>CodeYun CodeYun CodeYun uv run pytest</INSTRUCTIONS>"
                    "<codex_internal_context source=\"goal\"><objective>"
                    "你能看到每轮清理的邮件数的吧？和抓包那边联动，好好调试优化邮件清理功能吧。"
                    "哪怕实际跑成功了一轮，也要再跑，直到某一轮全部处理下来，都没有被清理的邮件。"
                    "</objective></codex_internal_context>"
                ),
                "assistant_result": "邮件_选择性领取完成，见到 10 封，领取 0 封，滚动 1 次；doctor 显示 due_tasks=[]。",
                "start_at": start_at,
                "end_at": start_at + 20 * 60,
            }
        ],
    }

    blocks = _build_codex_diary_blocks(source, user_id=auth_user.id, session=session)

    assert len(blocks) == 1
    assert blocks[0]["category_key"] == "legacy_color_67c23a"


def test_codex_diary_mail_selective_claim_runtime_context_is_fanxiu_without_fanxiu_title(
    session: Session,
    auth_user,
):
    session.add(
        AppSetting(
            key=build_note_category_palette_setting_key(auth_user.id),
            value={
                "items": [
                    {"key": "general", "label": "综合", "color": "#909399", "order": 0},
                    {"key": "legacy_color_67c23a", "label": "凡修", "color": "#67c23a", "order": 10},
                    {"key": "project", "label": "项目", "color": "#a57c00", "order": 20},
                ]
            },
        )
    )
    session.commit()
    start_at = _ts(2026, 6, 28, 0, 18)
    source = {
        "date": "2026-06-28",
        "timezone": ZoneInfo("Asia/Shanghai"),
        "turn_records": [
            {
                "thread_id": "mail-selective-claim:runtime",
                "thread_title": "邮件判定流程收敛",
                "project_label": "codeyun",
                "user_request": "收敛邮件判定流程，避免状态误标。",
                "assistant_result": (
                    "在 mail.py 将“翻页到上限未确认到底”从 success 改为 skipped，"
                    "并写入 retry_after。复跑验证清理结果，输出见到 89 封/滚动 24 后，"
                    "记录 mail-selective-claim 为重试态，不推进到明日批次。"
                ),
                "start_at": start_at,
                "end_at": start_at + 70 * 60,
            }
        ],
    }

    blocks = _build_codex_diary_blocks(source, user_id=auth_user.id, session=session)

    assert len(blocks) == 1
    assert blocks[0]["category_key"] == "legacy_color_67c23a"


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
                    {"key": "legacy_color_e6a23c", "label": "考勤", "color": "#e6a23c", "order": 20},
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
    assert blocks[0]["category_key"] == "legacy_color_e6a23c"
    assert blocks[0]["note_categories"] == [{"key": "legacy_color_e6a23c", "weight": 100}]

    run = CodexDiaryImportRun(user_id=auth_user.id, diary_date="2026-05-04", scope_key="test")
    session.add(run)
    block = dict(blocks[0])
    block["title"] = "PDF阅读器问卷前插"
    block["summary_items"] = ["PDF 阅读器页面笔记与问卷前插已完成。"]
    block["lifecycle_stage"] = "done"
    note = _create_codex_diary_note(session, current_user=auth_user, run=run, block=block)
    session.commit()
    session.refresh(note)

    assert note.primary_category == "legacy_color_e6a23c"
    assert note.note_categories == blocks[0]["note_categories"]


def test_codex_diary_note_start_clamps_cross_midnight_to_diary_date(
    session: Session,
    auth_user,
):
    run = CodexDiaryImportRun(user_id=auth_user.id, diary_date="2026-05-04", scope_key="test")
    session.add(run)
    block = {
        "block_key": "cross-midnight",
        "title": "跨午夜 Codex 日记",
        "summary_items": ["跨午夜会话已整理。"],
        "lifecycle_stage": "done",
        "category_key": "general",
        "note_categories": [{"key": "general", "weight": 100}],
        "duration_seconds": 20 * 60,
        "start_at": _ts(2026, 5, 3, 23, 50),
        "end_at": _ts(2026, 5, 4, 0, 10),
        "records": [
            {
                "thread_id": "cross-midnight",
                "assistant_result": "跨午夜会话已整理。",
                "start_at": _ts(2026, 5, 3, 23, 50),
                "end_at": _ts(2026, 5, 4, 0, 10),
            }
        ],
    }

    note = _create_codex_diary_note(session, current_user=auth_user, run=run, block=block)
    session.commit()
    session.refresh(note)

    assert note.start_at == _ts(2026, 5, 4, 0, 0)


def test_codex_diary_marks_stale_running_run_failed(
    session: Session,
    auth_user,
):
    now_ts = _ts(2026, 5, 18, 10, 0)
    stale_ts = now_ts - CODEX_DIARY_STALE_HEARTBEAT_SECONDS - 1
    run = CodexDiaryImportRun(
        user_id=auth_user.id,
        diary_date="2026-05-17",
        scope_key="test",
        status="running",
        stage="drafting",
        stage_label="调用 AI 生成日记草案",
        heartbeat_at=stale_ts,
        created_at=stale_ts,
        updated_at=stale_ts,
    )
    session.add(run)
    session.commit()

    changed_count = mark_stale_codex_diary_import_runs(
        session,
        now_ts=now_ts,
        queue_snapshot={"running": None, "pending": []},
    )

    assert changed_count == 1
    session.expire_all()
    updated = session.get(CodexDiaryImportRun, run.id)
    assert updated.status == "failed"
    assert updated.stage == "stale"
    assert updated.stage_label == "任务心跳超时"
    assert "当前执行队列中没有对应任务" in updated.error_message
    assert updated.finished_at == now_ts


def test_codex_diary_hides_project_but_uses_other_user_preset_builtin_categories(
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

    assert [block["category_key"] for block in blocks] == ["focus"]
    assert blocks[0]["category_label"] == "重点"


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
    assert _normalize_codex_diary_ai_title("修好了") == ""
    assert _normalize_codex_diary_ai_title("读到了") == ""
    assert _normalize_codex_diary_ai_title("你说得对") == ""
    assert _normalize_codex_diary_ai_title("任务树?") == ""
    assert _normalize_codex_diary_ai_title("是的，会话列表全量时间线分页加载") == "会话列表全量时间线分页加载"
    assert _normalize_codex_diary_ai_title("读到了：邮件标识场景标注") == "邮件标识场景标注"
    assert _normalize_codex_diary_ai_title("你说得对、日程条文字排版修复") == "日程条文字排版修复"
    assert _normalize_codex_diary_ai_title("已改、已改好：活动视图里的档期") == "活动视图里的档期"
    assert _normalize_codex_diary_ai_title("综合修复事项") == "修复事项"


def test_codex_diary_ai_title_keeps_single_primary_topic():
    assert _normalize_codex_diary_ai_title("考勤表与问卷链路修复") == "考勤表"
    assert _normalize_codex_diary_ai_title("权限策略和课程数据兜底") == "权限策略"
    assert _normalize_codex_diary_ai_title("data-annotation卡顿、抓包协议与表格序号修复") == "data-annotation卡顿"


def test_codex_diary_fallback_title_does_not_join_two_topics():
    block = {
        "records": [
            {
                "thread_title": "考勤表与问卷链路修复",
                "user_request": "修复考勤表。",
                "assistant_result": "考勤表链路已修复。",
                "start_at": _ts(2026, 5, 1, 9, 0),
                "end_at": _ts(2026, 5, 1, 9, 20),
            },
            {
                "thread_title": "权限策略和课程数据兜底",
                "user_request": "补权限策略。",
                "assistant_result": "权限策略已补齐。",
                "start_at": _ts(2026, 5, 1, 9, 30),
                "end_at": _ts(2026, 5, 1, 9, 50),
            },
        ]
    }

    assert _build_codex_diary_title(block) == "考勤表链路已修复"


def test_codex_diary_fallback_title_skips_low_information_result_phrase():
    block = {
        "records": [
            {
                "thread_title": "凡修邮件前端数量问题",
                "user_request": "小模块先修邮件前端数量。",
                "assistant_result": "读到了；先按你说的小模块先修处理了邮件前端数量问题。",
                "start_at": _ts(2026, 5, 1, 9, 0),
                "end_at": _ts(2026, 5, 1, 9, 20),
            }
        ]
    }

    assert _build_codex_diary_title(block) == "邮件前端数量问题"


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
