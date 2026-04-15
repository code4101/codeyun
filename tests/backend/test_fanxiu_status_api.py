from __future__ import annotations

import json

import pytest

from backend.app import app
from backend.api.fanxiu import get_fanxiu_user
from backend.core.auth import get_current_user_from_token, get_optional_current_user_from_token
from backend.core.fanxiu_status import get_status_config_path


def _override_user(user):
    app.dependency_overrides[get_current_user_from_token] = lambda: user
    app.dependency_overrides[get_optional_current_user_from_token] = lambda: user


def _clear_user_override():
    app.dependency_overrides.pop(get_current_user_from_token, None)
    app.dependency_overrides.pop(get_optional_current_user_from_token, None)


@pytest.fixture(autouse=True)
def isolate_fanxiu_status_config(monkeypatch):
    monkeypatch.setattr("backend.core.fanxiu_status.detect_auto_status_path", lambda: None)
    config_path = get_status_config_path()
    if config_path.exists():
        config_path.unlink()
    yield
    _clear_user_override()
    if config_path.exists():
        config_path.unlink()


def test_fanxiu_status_reports_missing_configuration(client):
    response = client.get("/api/fanxiu/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "unset"
    assert payload["effective_path"] is None
    assert payload["error"] == "未配置 status.json 路径，也没有探测到默认位置。"
    assert payload["accounts"] == []


def test_fanxiu_status_returns_derived_task_snapshot(client, tmp_path):
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "需要回到世界": True,
                "程序初始化": True,
                "当前账号": "羊驼",
                "账号清单": [
                    ["羊驼", "13646032451"],
                    ["驼二", "18850340559"],
                ],
                "羊驼": {
                    "日常_助手": "2000-01-01 00:00:00",
                    "仙府_领悟绝技": "2099-01-01 00:00:00",
                },
                "驼二": {
                    "日常_报名": "2099-01-02 00:00:00",
                },
                "托管重连": "2099-01-01 00:00:00",
                "卡死检测": "2000-01-01 00:00:00",
                "卡死检测_last_hash": "deadbeef",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    get_status_config_path().write_text(
        json.dumps({"status_path": str(status_path)}, ensure_ascii=False),
        encoding="utf-8",
    )

    response = client.get("/api/fanxiu/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "configured"
    assert payload["effective_path"] == str(status_path)
    assert payload["current_account"] == "羊驼"
    assert payload["recommended_account"] == "羊驼"
    assert payload["next_task_name"] == "日常_助手"
    assert payload["need_return_world"] is True
    assert payload["watchdog_hash"] == "deadbeef"
    assert payload["raw_status"]["当前账号"] == "羊驼"

    first_account = payload["accounts"][0]
    assert first_account["name"] == "羊驼"
    assert first_account["is_current"] is True
    assert first_account["has_due_task"] is True
    assert any(task["name"] == "日常_助手" and task["due"] for task in first_account["tasks"])

    runtime_timers = {item["name"]: item for item in payload["runtime_timers"]}
    assert runtime_timers["托管重连"]["due"] is False
    assert runtime_timers["卡死检测"]["due"] is True


def test_fanxiu_status_parse_endpoint_derives_snapshot(client):
    response = client.post(
        "/api/fanxiu/status/parse",
        json={
            "raw_status": {
                "当前账号": "羊驼",
                "账号清单": [["羊驼", "13646032451"]],
                "羊驼": {
                    "日常_助手": "2000-01-01 00:00:00",
                },
            }
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["current_account"] == "羊驼"
    assert payload["next_task_name"] == "日常_助手"
    assert payload["effective_path"] is None


def test_fanxiu_status_config_update_requires_owner_or_admin(client, auth_user):
    response = client.put(
        "/api/fanxiu/status/config",
        json={"status_path": "C:/demo/status.json"},
    )

    assert response.status_code == 403


def test_fanxiu_owner_can_update_status_config(client, session, tmp_path):
    fanxiu_user = get_fanxiu_user(session)
    target_path = tmp_path / "fx-status.json"
    _override_user(fanxiu_user)

    try:
        response = client.put(
            "/api/fanxiu/status/config",
            json={"status_path": str(target_path)},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status_path"] == str(target_path)
        assert payload["effective_path"] == str(target_path)
        assert payload["file_exists"] is False

        get_response = client.get("/api/fanxiu/status/config")
    finally:
        _clear_user_override()

    assert get_response.status_code == 200
    assert get_response.json()["status_path"] == str(target_path)


def test_fanxiu_owner_can_save_status_document(client, session, tmp_path):
    fanxiu_user = get_fanxiu_user(session)
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "当前账号": "羊驼",
                "账号清单": [["羊驼", "13646032451"]],
                "羊驼": {
                    "日常_助手": "2099-01-01 00:00:00",
                    "日常_报名": "2099-01-02 00:00:00",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    get_status_config_path().write_text(
        json.dumps({"status_path": str(status_path)}, ensure_ascii=False),
        encoding="utf-8",
    )
    _override_user(fanxiu_user)

    try:
        response = client.put(
            "/api/fanxiu/status",
            json={
                "raw_status": {
                    "当前账号": "羊驼",
                    "账号清单": [["羊驼", "13646032451"]],
                    "羊驼": {
                        "日常_助手": "2099-01-03 00:00:00",
                        "日常_报名": None,
                    },
                }
            },
        )
    finally:
        _clear_user_override()

    assert response.status_code == 200
    payload = response.json()
    assert payload["raw_status"]["羊驼"]["日常_报名"] is None
    task_names = [task["name"] for task in payload["accounts"][0]["tasks"]]
    assert "日常_报名" not in task_names
    assert json.loads(status_path.read_text(encoding="utf-8"))["羊驼"]["日常_报名"] is None
