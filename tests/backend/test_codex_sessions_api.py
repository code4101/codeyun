from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

from sqlmodel import Session, select

from backend.core.note_semantics import build_note_category_palette_setting_key
from backend.models import (
    AppSetting,
    CodexDailySummaryRun,
    CodexTextCacheMessage,
    CodexTextCacheRoot,
    CodexTextCacheThread,
    CodexTextCacheTurn,
    UserDevice,
)


def _create_local_entry(client) -> str:
    response = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert response.status_code == 200
    return response.json()["id"]


def _write_thread_jsonl(
    path: Path,
    *,
    session_id: str = "thread-1",
    user_text: str,
    commentary_text: str,
    final_text: str,
    user_image_url: str | None = None,
    user_timestamp: str = "2026-04-23T05:00:02.000Z",
    commentary_timestamp: str = "2026-04-23T05:00:03.000Z",
    final_timestamp: str = "2026-04-23T05:00:04.000Z",
) -> None:
    user_content = [{"type": "input_text", "text": user_text}]
    if user_image_url:
        user_content.extend(
            [
                {"type": "input_text", "text": "<image>"},
                {"type": "input_image", "image_url": user_image_url},
                {"type": "input_text", "text": "</image>"},
            ]
        )
    rows = [
        {
            "timestamp": "2026-04-23T05:00:00.000Z",
            "type": "session_meta",
            "payload": {"id": session_id},
        },
        {
            "timestamp": "2026-04-23T05:00:01.000Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "<environment_context>\n  <cwd>ignore</cwd>\n</environment_context>"}],
            },
        },
        {
            "timestamp": user_timestamp,
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": user_content,
            },
        },
        {
            "timestamp": commentary_timestamp,
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "phase": "commentary",
                "content": [{"type": "output_text", "text": commentary_text}],
            },
        },
        {
            "timestamp": final_timestamp,
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": final_text}],
            },
        },
    ]
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _create_codex_root_for_daily_summary(tmp_path: Path) -> Path:
    codex_root = tmp_path / ".codex-daily"
    codex_root.mkdir(parents=True, exist_ok=True)

    codeyun_root = tmp_path / "codeyun"
    fx_root = tmp_path / "xlproject" / "fx"
    codeyun_root.mkdir(parents=True, exist_ok=True)
    fx_root.mkdir(parents=True, exist_ok=True)

    sessions_day_22 = codex_root / "sessions" / "2026" / "04" / "22"
    sessions_day_23 = codex_root / "sessions" / "2026" / "04" / "23"
    sessions_day_22.mkdir(parents=True, exist_ok=True)
    sessions_day_23.mkdir(parents=True, exist_ok=True)

    thread_22a_path = sessions_day_22 / "rollout-thread-22a.jsonl"
    thread_22b_path = sessions_day_22 / "rollout-thread-22b.jsonl"
    thread_23_path = sessions_day_23 / "rollout-thread-23.jsonl"

    _write_thread_jsonl(
        thread_22a_path,
        session_id="thread-22a",
        user_text="修一下 Codex 日报页，先把 4 月 22 日总结打通。",
        commentary_text="我先补接口，再挂一个子页面。",
        final_text="日报页原型已经加上，先能生成 4 月 22 日的总结。",
        user_timestamp="2026-04-22T02:10:00.000Z",
        commentary_timestamp="2026-04-22T02:20:00.000Z",
        final_timestamp="2026-04-22T02:25:00.000Z",
    )
    _write_thread_jsonl(
        thread_22b_path,
        session_id="thread-22b",
        user_text="分析 attendance 菜单命名，先不要改代码。",
        commentary_text="我先核对菜单、页面标题和权限词。",
        final_text="结论是页面语义应以采集配置为准，菜单项再按真实功能收口。",
        user_timestamp="2026-04-22T08:00:00.000Z",
        commentary_timestamp="2026-04-22T08:05:00.000Z",
        final_timestamp="2026-04-22T08:16:00.000Z",
    )
    _write_thread_jsonl(
        thread_23_path,
        session_id="thread-23",
        user_text="处理 4 月 23 日的任务，不应出现在 4 月 22 日日报里。",
        commentary_text="我先看今天的新问题。",
        final_text="这是 4 月 23 日的结果。",
        user_timestamp="2026-04-23T03:00:00.000Z",
        commentary_timestamp="2026-04-23T03:05:00.000Z",
        final_timestamp="2026-04-23T03:10:00.000Z",
    )

    with sqlite3.connect(codex_root / "state_5.sqlite") as conn:
        conn.execute(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                rollout_path TEXT,
                created_at INTEGER,
                updated_at INTEGER,
                cwd TEXT,
                title TEXT,
                archived INTEGER,
                first_user_message TEXT
            )
            """
        )
        rows = [
            (
                "thread-22a",
                str(thread_22a_path),
                100,
                120,
                str(codeyun_root),
                "Codex 日报页",
                0,
                "修一下 Codex 日报页，先把 4 月 22 日总结打通。",
            ),
            (
                "thread-22b",
                str(thread_22b_path),
                130,
                160,
                str(codeyun_root),
                "Attendance 菜单语义",
                0,
                "分析 attendance 菜单命名，先不要改代码。",
            ),
            (
                "thread-23",
                str(thread_23_path),
                170,
                190,
                str(fx_root),
                "次日任务",
                0,
                "处理 4 月 23 日的任务，不应出现在 4 月 22 日日报里。",
            ),
        ]
        conn.executemany(
            """
            INSERT INTO threads (id, rollout_path, created_at, updated_at, cwd, title, archived, first_user_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()

    with (codex_root / "session_index.jsonl").open("w", encoding="utf-8") as handle:
        for thread_id, title in (
            ("thread-22a", "Codex 日报页"),
            ("thread-22b", "Attendance 菜单语义"),
            ("thread-23", "次日任务"),
        ):
            handle.write(json.dumps({"id": thread_id, "thread_name": title}, ensure_ascii=False) + "\n")

    with (codex_root / ".codex-global-state.json").open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "electron-saved-workspace-roots": [
                    str(codeyun_root),
                    str(tmp_path / "xlproject"),
                ]
            },
            handle,
            ensure_ascii=False,
        )

    return codex_root


def _wait_for_daily_summary_run(client, entry_id: str, run_id: str) -> dict[str, object]:
    for _ in range(80):
        response = client.get(f"/api/device-entries/{entry_id}/codex/daily-summary/runs/{run_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"completed", "failed"}:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for daily summary run {run_id}")


def _create_codex_root(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    codex_root = tmp_path / ".codex"
    codex_root.mkdir(parents=True, exist_ok=True)

    workspace_root = tmp_path / "xlproject"
    nested_project = workspace_root / "fx"
    codeyun_root = tmp_path / "codeyun"
    nested_project.mkdir(parents=True, exist_ok=True)
    codeyun_root.mkdir(parents=True, exist_ok=True)

    sessions_dir = codex_root / "sessions" / "2026" / "04" / "23"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    thread_one_path = sessions_dir / "rollout-thread-1.jsonl"
    thread_two_path = sessions_dir / "rollout-thread-2.jsonl"
    _write_thread_jsonl(
        thread_one_path,
        user_text="请分析 fx 调度架构",
        commentary_text="我先看 tick 和 yield。",
        final_text="这是结论。",
    )
    _write_thread_jsonl(
        thread_two_path,
        user_text="看看 codeyun 的 cluster 页面",
        commentary_text="我先定位集群页面。",
        final_text="cluster 页面在这里。",
        user_image_url="data:image/png;base64,QUJD",
        user_timestamp="2026-04-23T05:00:03.000Z",
        commentary_timestamp="2026-04-23T05:00:04.000Z",
        final_timestamp="2026-04-23T05:00:05.000Z",
    )

    with sqlite3.connect(codex_root / "state_5.sqlite") as conn:
        conn.execute(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                rollout_path TEXT,
                created_at INTEGER,
                updated_at INTEGER,
                cwd TEXT,
                title TEXT,
                archived INTEGER,
                first_user_message TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO threads (id, rollout_path, created_at, updated_at, cwd, title, archived, first_user_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "thread-1",
                str(thread_one_path),
                100,
                200,
                str(nested_project),
                "",
                0,
                "请分析 fx 调度架构",
            ),
        )
        conn.execute(
            """
            INSERT INTO threads (id, rollout_path, created_at, updated_at, cwd, title, archived, first_user_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "thread-2",
                str(thread_two_path),
                110,
                220,
                str(codeyun_root),
                "CodeYun Cluster",
                1,
                "看看 codeyun 的 cluster 页面",
            ),
        )
        conn.commit()

    with (codex_root / "session_index.jsonl").open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"id": "thread-1", "thread_name": "FX 架构分析"}, ensure_ascii=False) + "\n")
        handle.write(json.dumps({"id": "thread-2", "thread_name": "CodeYun Cluster"}, ensure_ascii=False) + "\n")

    with (codex_root / ".codex-global-state.json").open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "electron-saved-workspace-roots": [
                    str(workspace_root),
                    str(codeyun_root),
                ]
            },
            handle,
            ensure_ascii=False,
        )

    return codex_root, {
        "workspace_root": str(workspace_root),
        "nested_project": str(nested_project),
        "codeyun_root": str(codeyun_root),
    }


def test_local_device_entry_reads_codex_overview_and_thread_detail(client, session: Session, auth_user, test_device, tmp_path):
    entry_id = _create_local_entry(client)
    codex_root, paths = _create_codex_root(tmp_path)

    overview_response = client.get(
        f"/api/device-entries/{entry_id}/codex/overview",
        params={"root_dir": str(codex_root)},
    )
    assert overview_response.status_code == 200
    overview = overview_response.json()

    assert overview["root_dir"] == str(codex_root.resolve(strict=False))
    assert overview["total_groups"] == 2
    assert overview["total_threads"] == 2
    assert overview["archived_threads"] == 1
    assert [item["label"] for item in overview["groups"]] == ["codeyun", "fx"]

    fx_group = next(item for item in overview["groups"] if item["label"] == "fx")
    assert fx_group["secondary_label"] == "xlproject"
    assert fx_group["thread_count"] == 1
    assert fx_group["threads"][0]["title"] == "FX 架构分析"
    assert fx_group["threads"][0]["workspace_root"] == paths["workspace_root"]

    codeyun_group = next(item for item in overview["groups"] if item["label"] == "codeyun")
    assert codeyun_group["secondary_label"] is None
    assert codeyun_group["threads"][0]["archived"] is True

    detail_response = client.get(
        f"/api/device-entries/{entry_id}/codex/threads/thread-1",
        params={"root_dir": str(codex_root)},
    )
    assert detail_response.status_code == 200
    detail = detail_response.json()

    assert detail["thread"]["title"] == "FX 架构分析"
    assert detail["thread"]["group_label"] == "fx"
    assert detail["thread"]["group_secondary_label"] == "xlproject"
    assert detail["message_count"] == 3
    assert detail["user_message_count"] == 1
    assert detail["assistant_message_count"] == 2
    assert [item["role"] for item in detail["messages"]] == ["user", "assistant", "assistant"]
    assert detail["messages"][0]["text"] == "请分析 fx 调度架构"
    assert detail["messages"][1]["phase"] == "commentary"
    assert detail["messages"][2]["phase"] == "final_answer"

    image_response = client.get(
        f"/api/device-entries/{entry_id}/codex/threads/thread-2/messages/1/images",
        params={"root_dir": str(codex_root)},
    )
    assert image_response.status_code == 200
    image_payload = image_response.json()
    assert image_payload["thread_id"] == "thread-2"
    assert image_payload["message_seq"] == 1
    assert image_payload["images"] == [
        {
            "index": 1,
            "type": "input_image",
            "image_url": "data:image/png;base64,QUJD",
        }
    ]

    workload_response = client.get(
        f"/api/device-entries/{entry_id}/codex/workload",
        params={"root_dir": str(codex_root)},
    )
    assert workload_response.status_code == 200
    workload = workload_response.json()

    assert workload["total_threads"] == 2
    assert workload["total_turns"] == 2
    assert workload["max_concurrency"] == 2
    assert workload["skipped_threads"] == 0
    assert [item["id"] for item in workload["turns"]] == ["thread-1:1", "thread-2:1"]
    assert workload["turns"][0]["start_at"] == 1776920402.0
    assert workload["turns"][0]["end_at"] == 1776920404.0
    assert workload["segments"] == [
        {"start_at": 1776920402.0, "end_at": 1776920403.0, "duration_seconds": 1.0, "concurrency": 1},
        {"start_at": 1776920403.0, "end_at": 1776920404.0, "duration_seconds": 1.0, "concurrency": 2},
        {"start_at": 1776920404.0, "end_at": 1776920405.0, "duration_seconds": 1.0, "concurrency": 1},
    ]

    root_key = os.path.normcase(os.path.normpath(str(codex_root.resolve(strict=False))))
    root_cache = session.get(CodexTextCacheRoot, root_key)
    assert root_cache is not None
    assert root_cache.root_dir == str(codex_root.resolve(strict=False))
    thread_caches = session.exec(
        select(CodexTextCacheThread).where(CodexTextCacheThread.root_key == root_key)
    ).all()
    assert len(thread_caches) == 2
    message_caches = session.exec(
        select(CodexTextCacheMessage).where(CodexTextCacheMessage.root_key == root_key)
    ).all()
    assert len(message_caches) == 6
    turn_caches = session.exec(
        select(CodexTextCacheTurn).where(CodexTextCacheTurn.root_key == root_key)
    ).all()
    assert len(turn_caches) == 2

    updated_thread_one_path = codex_root / "sessions" / "2026" / "04" / "23" / "rollout-thread-1.jsonl"
    _write_thread_jsonl(
        updated_thread_one_path,
        user_text="请分析 fx 调度架构",
        commentary_text="我先看 tick 和 yield。",
        final_text="这是更新后的结论。",
        final_timestamp="2026-04-23T05:00:06.000Z",
    )

    updated_detail_response = client.get(
        f"/api/device-entries/{entry_id}/codex/threads/thread-1",
        params={"root_dir": str(codex_root)},
    )
    assert updated_detail_response.status_code == 200
    updated_detail = updated_detail_response.json()
    assert updated_detail["messages"][2]["text"] == "这是更新后的结论。"

    session.expire_all()
    updated_thread_one_messages = session.exec(
        select(CodexTextCacheMessage)
        .where(
            CodexTextCacheMessage.root_key == root_key,
            CodexTextCacheMessage.thread_id == "thread-1",
        )
        .order_by(CodexTextCacheMessage.seq)
    ).all()
    assert updated_thread_one_messages[2].text == "这是更新后的结论。"

    unchanged_thread_two_messages = session.exec(
        select(CodexTextCacheMessage)
        .where(
            CodexTextCacheMessage.root_key == root_key,
            CodexTextCacheMessage.thread_id == "thread-2",
        )
        .order_by(CodexTextCacheMessage.seq)
    ).all()
    assert unchanged_thread_two_messages[2].text == "cluster 页面在这里。"


def test_local_device_entry_persists_codex_daily_summary_runs(client, session: Session, auth_user, test_device, tmp_path, monkeypatch):
    entry_id = _create_local_entry(client)
    codex_root = _create_codex_root_for_daily_summary(tmp_path)
    session.add(
        AppSetting(
            key=build_note_category_palette_setting_key(auth_user.id),
            value={
                "items": [
                    {"key": "general", "label": "综合", "order": 0},
                    {"key": "project", "label": "项目推进", "order": 10},
                    {"key": "module", "label": "模块", "order": 20},
                    {"key": "task", "label": "任务", "order": 30},
                    {"key": "bug", "label": "缺陷", "order": 40},
                ]
            },
        )
    )
    session.commit()

    captured: list[dict[str, object]] = []

    def fake_chat_with_provider(**kwargs):
        captured.append(kwargs)
        return {
            "model": kwargs.get("model") or "gpt-5.4",
            "content": "\n".join(
                [
                    "1. 项目推进",
                    "1.1 打通 Codex 日报页原型与按天汇总链路，完成页面入口、后端接口和调用路径。",
                    "1.2 梳理 attendance 菜单语义，确认命名应按真实页面功能收口。",
                    "2. 任务",
                    "2.1 为后续文案和结构修正沉淀判断依据。",
                ]
            ),
            "created_at": "2026-04-23T12:00:00+00:00",
            "done_reason": "stop",
        }

    monkeypatch.setattr("backend.core.codex_sessions.chat_with_provider", fake_chat_with_provider)

    start_response = client.post(
        f"/api/device-entries/{entry_id}/codex/daily-summary/runs",
        json={
            "root_dir": str(codex_root),
            "date": "2026-04-22",
        },
    )
    assert start_response.status_code == 200
    first_run = start_response.json()
    assert first_run["date"] == "2026-04-22"
    assert first_run["status"] == "running"
    assert first_run["reused_existing_run"] is False

    completed_payload = _wait_for_daily_summary_run(client, entry_id, first_run["id"])
    assert completed_payload["status"] == "completed"
    assert completed_payload["stage"] == "completed"
    assert completed_payload["generated_by"] == "codex_cli"
    assert completed_payload["thread_count"] == 2
    assert completed_payload["turn_count"] == 2
    assert completed_payload["user_message_count"] == 2
    assert completed_payload["assistant_message_count"] == 4
    assert completed_payload["summary_text"].startswith("1. 项目推进")
    assert completed_payload["result"]["prompt_version"] == "2026-04-23.hierarchical-note-types-v1"
    assert [item["title"] for item in completed_payload["result"]["threads"]] == ["Codex 日报页", "Attendance 菜单语义"]
    assert [item["label"] for item in completed_payload["result"]["type_items"]][:2] == ["综合", "项目推进"]

    latest_response = client.get(
        f"/api/device-entries/{entry_id}/codex/daily-summary/latest",
        params={"root_dir": str(codex_root), "date": "2026-04-22"},
    )
    assert latest_response.status_code == 200
    latest_payload = latest_response.json()
    assert latest_payload["id"] == first_run["id"]
    assert latest_payload["summary_text"].startswith("1. 项目推进")

    reused_response = client.post(
        f"/api/device-entries/{entry_id}/codex/daily-summary/runs",
        json={
            "root_dir": str(codex_root),
            "date": "2026-04-22",
        },
    )
    assert reused_response.status_code == 200
    reused_payload = reused_response.json()
    assert reused_payload["id"] == first_run["id"]
    assert reused_payload["reused_existing_run"] is True
    assert len(captured) == 1

    forced_response = client.post(
        f"/api/device-entries/{entry_id}/codex/daily-summary/runs",
        json={
            "root_dir": str(codex_root),
            "date": "2026-04-22",
            "force": True,
        },
    )
    assert forced_response.status_code == 200
    forced_payload = forced_response.json()
    assert forced_payload["id"] != first_run["id"]
    forced_completed = _wait_for_daily_summary_run(client, entry_id, forced_payload["id"])
    assert forced_completed["status"] == "completed"
    assert len(captured) == 2

    prompt = captured[0]["messages"][0]["content"]
    assert "修一下 Codex 日报页" in prompt
    assert "分析 attendance 菜单命名" in prompt
    assert "处理 4 月 23 日的任务" not in prompt
    assert "项目推进（key=project）" in prompt
    assert "2026年4月22日" in captured[0]["system_prompt"]
    assert captured[0]["provider_id"] == "codex-daily-summary"

    session.expire_all()
    run_rows = session.exec(
        select(CodexDailySummaryRun)
        .where(CodexDailySummaryRun.scope_key == f"entry:{entry_id}")
        .order_by(CodexDailySummaryRun.created_at)
    ).all()
    assert len(run_rows) == 2
    assert run_rows[0].summary_date == "2026-04-22"
    assert run_rows[0].summary_text.startswith("1. 项目推进")
    assert run_rows[0].result_json["type_items"][1]["label"] == "项目推进"


def test_remote_device_entry_proxies_codex_requests(client, session: Session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-device-1",
        mode="remote",
        name="Remote Device",
        server_url="http://remote-device:8000",
        token="remote-token",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    captured: list[dict[str, object]] = []

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

        @property
        def content(self):
            return json.dumps(self._payload).encode("utf-8")

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None, stream=False):
        captured.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "params": params,
                "json": json,
                "timeout": timeout,
                "stream": stream,
            }
        )
        if url.endswith("/api/codex/overview"):
            return FakeResponse({"root_dir": "C:/Users/test/.codex", "default_root_dir": "C:/Users/test/.codex", "state_db_path": "a", "session_index_path": "b", "global_state_path": "c", "total_groups": 0, "total_threads": 0, "archived_threads": 0, "groups": []})
        if url.endswith("/api/codex/threads/thread-1/messages/1/images"):
            return FakeResponse({"root_dir": "C:/Users/test/.codex", "thread_id": "thread-1", "message_seq": 1, "images": [{"index": 1, "type": "input_image", "image_url": "data:image/png;base64,QUJD"}]})
        if url.endswith("/api/codex/workload"):
            return FakeResponse({"root_dir": "C:/Users/test/.codex", "total_threads": 0, "total_turns": 0, "skipped_threads": 0, "max_concurrency": 0, "time_range_start": None, "time_range_end": None, "turns": [], "segments": []})
        if url.endswith("/api/codex/daily-summary/latest"):
            return FakeResponse({
                "id": "run-latest",
                "root_dir": "C:/Users/test/.codex",
                "date": "2026-04-22",
                "timezone": "Asia/Shanghai",
                "provider": "codex-daily-summary",
                "generated_by": "codex_cli",
                "model": "gpt-5.4",
                "prompt_version": "2026-04-23.hierarchical-note-types-v1",
                "force_requested": False,
                "reused_existing_run": False,
                "status": "completed",
                "stage": "completed",
                "stage_label": "已完成",
                "thread_count": 1,
                "turn_count": 1,
                "user_message_count": 1,
                "assistant_message_count": 2,
                "summary_text": "1. 任务\n1.1 日报。",
                "error_message": None,
                "heartbeat_at": 1777000000.0,
                "created_at": 1776999960.0,
                "finished_at": 1777000000.0,
                "updated_at": 1777000000.0,
                "result": {
                    "root_dir": "C:/Users/test/.codex",
                    "date": "2026-04-22",
                    "timezone": "Asia/Shanghai",
                    "generated_at": "2026-04-23T12:00:00+00:00",
                    "generated_by": "codex_cli",
                    "model": "gpt-5.4",
                    "prompt_version": "2026-04-23.hierarchical-note-types-v1",
                    "summary_text": "1. 任务\n1.1 日报。",
                    "thread_count": 1,
                    "turn_count": 1,
                    "user_message_count": 1,
                    "assistant_message_count": 2,
                    "threads": [],
                    "type_items": [],
                },
            })
        if url.endswith("/api/codex/daily-summary/runs"):
            return FakeResponse({
                "id": "run-new",
                "root_dir": "C:/Users/test/.codex",
                "date": "2026-04-22",
                "timezone": "Asia/Shanghai",
                "provider": "codex-daily-summary",
                "generated_by": "codex_cli",
                "model": "gpt-5.4",
                "prompt_version": "2026-04-23.hierarchical-note-types-v1",
                "force_requested": False,
                "reused_existing_run": False,
                "status": "running",
                "stage": "queued",
                "stage_label": "已进入队列",
                "thread_count": 0,
                "turn_count": 0,
                "user_message_count": 0,
                "assistant_message_count": 0,
                "summary_text": "",
                "error_message": None,
                "heartbeat_at": 1776999960.0,
                "created_at": 1776999960.0,
                "finished_at": None,
                "updated_at": 1776999960.0,
                "result": None,
            })
        if url.endswith("/api/codex/daily-summary/runs/run-new"):
            return FakeResponse({
                "id": "run-new",
                "root_dir": "C:/Users/test/.codex",
                "date": "2026-04-22",
                "timezone": "Asia/Shanghai",
                "provider": "codex-daily-summary",
                "generated_by": "codex_cli",
                "model": "gpt-5.4",
                "prompt_version": "2026-04-23.hierarchical-note-types-v1",
                "force_requested": False,
                "reused_existing_run": False,
                "status": "completed",
                "stage": "completed",
                "stage_label": "已完成",
                "thread_count": 1,
                "turn_count": 1,
                "user_message_count": 1,
                "assistant_message_count": 2,
                "summary_text": "1. 任务\n1.1 日报。",
                "error_message": None,
                "heartbeat_at": 1777000000.0,
                "created_at": 1776999960.0,
                "finished_at": 1777000000.0,
                "updated_at": 1777000000.0,
                "result": {
                    "root_dir": "C:/Users/test/.codex",
                    "date": "2026-04-22",
                    "timezone": "Asia/Shanghai",
                    "generated_at": "2026-04-23T12:00:00+00:00",
                    "generated_by": "codex_cli",
                    "model": "gpt-5.4",
                    "prompt_version": "2026-04-23.hierarchical-note-types-v1",
                    "summary_text": "1. 任务\n1.1 日报。",
                    "thread_count": 1,
                    "turn_count": 1,
                    "user_message_count": 1,
                    "assistant_message_count": 2,
                    "threads": [],
                    "type_items": [],
                },
            })
        return FakeResponse({"root_dir": "C:/Users/test/.codex", "thread": {"id": "thread-1", "title": "Remote", "project_label": "codeyun", "group_key": "codeyun", "group_label": "codeyun", "archived": False}, "message_count": 0, "user_message_count": 0, "assistant_message_count": 0, "messages": []})

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)

    overview_response = client.get(
        f"/api/device-entries/{entry.entry_id}/codex/overview",
        params={"root_dir": "C:\\Users\\test\\.codex"},
    )
    assert overview_response.status_code == 200
    assert captured[0]["method"] == "GET"
    assert captured[0]["url"] == "http://remote-device:8000/api/codex/overview"
    assert captured[0]["headers"]["Authorization"] == "Bearer remote-token"
    assert captured[0]["params"] == {"root_dir": "C:\\Users\\test\\.codex"}

    detail_response = client.get(
        f"/api/device-entries/{entry.entry_id}/codex/threads/thread-1",
        params={"root_dir": "C:\\Users\\test\\.codex"},
    )
    assert detail_response.status_code == 200
    assert captured[1]["url"] == "http://remote-device:8000/api/codex/threads/thread-1"

    image_response = client.get(
        f"/api/device-entries/{entry.entry_id}/codex/threads/thread-1/messages/1/images",
        params={"root_dir": "C:\\Users\\test\\.codex"},
    )
    assert image_response.status_code == 200
    assert captured[2]["url"] == "http://remote-device:8000/api/codex/threads/thread-1/messages/1/images"

    workload_response = client.get(
        f"/api/device-entries/{entry.entry_id}/codex/workload",
        params={"root_dir": "C:\\Users\\test\\.codex"},
    )
    assert workload_response.status_code == 200
    assert captured[3]["url"] == "http://remote-device:8000/api/codex/workload"

    latest_daily_summary_response = client.get(
        f"/api/device-entries/{entry.entry_id}/codex/daily-summary/latest",
        params={"root_dir": "C:\\Users\\test\\.codex", "date": "2026-04-22"},
    )
    assert latest_daily_summary_response.status_code == 200
    assert captured[4]["method"] == "GET"
    assert captured[4]["url"] == "http://remote-device:8000/api/codex/daily-summary/latest"
    assert captured[4]["params"] == {"root_dir": "C:\\Users\\test\\.codex", "date": "2026-04-22"}

    start_daily_summary_response = client.post(
        f"/api/device-entries/{entry.entry_id}/codex/daily-summary/runs",
        json={"root_dir": "C:\\Users\\test\\.codex", "date": "2026-04-22", "force": False},
    )
    assert start_daily_summary_response.status_code == 200
    assert captured[5]["method"] == "POST"
    assert captured[5]["url"] == "http://remote-device:8000/api/codex/daily-summary/runs"
    assert captured[5]["json"] == {"root_dir": "C:\\Users\\test\\.codex", "date": "2026-04-22", "model": None, "force": False}
    assert captured[5]["timeout"] == 20

    run_response = client.get(
        f"/api/device-entries/{entry.entry_id}/codex/daily-summary/runs/run-new",
    )
    assert run_response.status_code == 200
    assert captured[6]["method"] == "GET"
    assert captured[6]["url"] == "http://remote-device:8000/api/codex/daily-summary/runs/run-new"
    assert captured[6]["timeout"] == 20
