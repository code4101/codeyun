from __future__ import annotations

from sqlmodel import select

from backend.api import device_entries as device_entries_api
from backend.core.codex.sessions import (
    build_remote_codex_cache_root_key,
    cache_remote_codex_thread_detail,
)
from backend.models import (
    CodexTextCacheMessage,
    CodexTextCacheRoot,
    CodexTextCacheThread,
    CodexTextCacheTurn,
    UserDevice,
)


def _remote_overview_payload() -> dict:
    return {
        "root_dir": r"C:\Users\chen\.codex",
        "default_root_dir": r"C:\Users\chen\.codex",
        "state_db_path": r"C:\Users\chen\.codex\state_5.sqlite",
        "session_index_path": r"C:\Users\chen\.codex\session_index.jsonl",
        "global_state_path": r"C:\Users\chen\.codex\.codex-global-state.json",
        "total_groups": 1,
        "total_threads": 1,
        "archived_threads": 0,
        "groups": [
            {
                "key": r"C:\home\chenkunze\slns\codeyun",
                "label": "codeyun",
                "secondary_label": None,
                "cwd": r"C:\home\chenkunze\slns\codeyun",
                "workspace_root": r"C:\home\chenkunze\slns\codeyun",
                "thread_count": 1,
                "archived_thread_count": 0,
                "latest_updated_at": 2000.0,
                "threads": [
                    {
                        "id": "thread-1",
                        "title": "远端会话",
                        "preview": "先看这里",
                        "cwd": r"C:\home\chenkunze\slns\codeyun",
                        "original_cwd": r"C:\home\chenkunze\slns\codeyun",
                        "rollout_path": r"C:\Users\chen\.codex\sessions\thread-1.jsonl",
                        "created_at": 1000.0,
                        "updated_at": 2000.0,
                        "archived": False,
                        "project_label": "codeyun",
                        "project_secondary_label": None,
                        "workspace_root": r"C:\home\chenkunze\slns\codeyun",
                    }
                ],
            }
        ],
    }


def test_remote_codex_overview_endpoint_caches_payload(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-mi15",
        name="codepc_mi15",
        mode="remote",
        server_url="http://remote-device",
        token="remote-token",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    def fake_fetch_remote_json(remote_entry, method, path, **kwargs):
        assert remote_entry.entry_id == entry.entry_id
        assert method == "GET"
        assert path == "/codex/overview"
        return _remote_overview_payload(), None

    monkeypatch.setattr(device_entries_api, "_fetch_remote_json", fake_fetch_remote_json)

    response = client.get(f"/api/device-entries/{entry.entry_id}/codex/overview")

    assert response.status_code == 200
    assert response.json()["total_threads"] == 1
    root_key = build_remote_codex_cache_root_key(entry.entry_id, r"C:\Users\chen\.codex")
    root_row = session.get(CodexTextCacheRoot, root_key)
    assert root_row is not None
    assert root_row.root_dir == r"C:\Users\chen\.codex"
    thread_row = session.exec(
        select(CodexTextCacheThread).where(
            CodexTextCacheThread.root_key == root_key,
            CodexTextCacheThread.thread_id == "thread-1",
        )
    ).first()
    assert thread_row is not None
    assert thread_row.project_label == "codeyun"


def test_remote_codex_thread_detail_cache_writes_messages_and_turns(session):
    detail_payload = {
        "root_dir": r"C:\Users\chen\.codex",
        "thread": {
            "id": "thread-2",
            "title": "远端详情",
            "preview": "用户问题",
            "cwd": r"C:\home\chenkunze\slns\codeyun",
            "created_at": 1000.0,
            "updated_at": 1010.0,
            "archived": False,
            "project_label": "codeyun",
            "project_secondary_label": None,
            "workspace_root": r"C:\home\chenkunze\slns\codeyun",
        },
        "messages": [
            {
                "seq": 1,
                "timestamp": "1970-01-01T00:16:40+00:00",
                "role": "user",
                "phase": None,
                "text": "用户问题",
            },
            {
                "seq": 2,
                "timestamp": "1970-01-01T00:16:50+00:00",
                "role": "assistant",
                "phase": "final_answer",
                "text": "助手回答",
            },
        ],
    }

    result = cache_remote_codex_thread_detail("entry-mi15", detail_payload, session=session)

    root_key = result["root_key"]
    messages = session.exec(
        select(CodexTextCacheMessage)
        .where(CodexTextCacheMessage.root_key == root_key)
        .order_by(CodexTextCacheMessage.seq)
    ).all()
    turns = session.exec(select(CodexTextCacheTurn).where(CodexTextCacheTurn.root_key == root_key)).all()
    assert [message.text for message in messages] == ["用户问题", "助手回答"]
    assert len(turns) == 1
    assert turns[0].duration_seconds == 10.0
