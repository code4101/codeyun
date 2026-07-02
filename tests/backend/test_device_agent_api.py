import json

from backend.core.ai.app_config import AI_APP_DEVICE_AGENT, save_user_ai_app_config
from backend.core.ai.chat_user_config import save_user_ai_chat_provider_config
from backend.core.device_agent.service import get_device_agent_config
from backend.models import UserDevice


def _add_local_entry(session, auth_user, test_device):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id=test_device["id"],
        name="local",
        mode="local",
        token=test_device["token"],
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def test_device_agent_manifest_requires_device_token(client):
    response = client.get("/api/device-agent/manifest")
    assert response.status_code == 401


def test_device_agent_manifest_with_device_token(client, test_device, monkeypatch):
    monkeypatch.setattr("backend.core.device_agent.service.get_device_id", lambda: test_device["id"])
    monkeypatch.setattr(
        "backend.core.device_agent.service.get_ai_provider_status",
        lambda provider_id: {
            "available": True,
            "configured": True,
            "kind": "codex_cli",
            "label": "Codex CLI",
            "error": None,
        },
    )
    response = client.get("/api/device-agent/manifest", headers={"X-Device-Token": test_device["token"]})

    assert response.status_code == 200
    payload = response.json()
    assert payload["device_id"] == test_device["id"]
    assert payload["agent"]["module"] == "device_agent"
    assert payload["status"] == "available"
    assert payload["ai_provider"]["available"] is True


def test_device_agent_manifest_degraded_when_ai_provider_unavailable(client, test_device, monkeypatch):
    monkeypatch.setattr("backend.core.device_agent.service.get_device_id", lambda: test_device["id"])
    monkeypatch.setattr(
        "backend.core.device_agent.service.get_ai_provider_status",
        lambda provider_id: {
            "available": False,
            "configured": False,
            "kind": "codex_cli",
            "label": "Codex CLI",
            "error": "未找到 Codex CLI 命令",
        },
    )

    response = client.get("/api/device-agent/manifest", headers={"X-Device-Token": test_device["token"]})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["ai_provider"]["error"] == "未找到 Codex CLI 命令"


def test_device_agent_default_model_is_codex_level(session):
    config = get_device_agent_config(session)
    assert config["default_provider"] == "codex-cli"
    assert config["default_model"] == ""


def test_device_agent_manifest_uses_user_configured_model(client, session, auth_user, test_device, monkeypatch):
    entry = _add_local_entry(session, auth_user, test_device)
    monkeypatch.setattr(
        "backend.core.device_agent.service.get_ai_provider_status",
        lambda provider_id: {
            "available": True,
            "configured": True,
            "kind": "codex_cli",
            "label": "Codex CLI",
            "error": None,
        },
    )
    save_user_ai_chat_provider_config(
        session,
        auth_user.id,
        "codex-cli",
        preferred_models=["gpt-5.5"],
    )
    save_user_ai_app_config(session, auth_user.id, AI_APP_DEVICE_AGENT, provider="codex-cli", model="")

    manifest_response = client.get(f"/api/device-entries/{entry.entry_id}/agent/manifest")
    assert manifest_response.status_code == 200
    assert manifest_response.json()["default_model"] == "gpt-5.5"


def test_device_agent_session_runs_turn_and_returns_report(client, session, auth_user, test_device, monkeypatch):
    entry = _add_local_entry(session, auth_user, test_device)
    monkeypatch.setattr("backend.core.device_agent.service.get_device_id", lambda: test_device["id"])
    monkeypatch.setattr("backend.core.device_agent.service.engine", session.get_bind())

    def fake_chat_with_provider(**kwargs):
        assert kwargs["provider_id"] == "codex-cli"
        assert kwargs["model"] == "gpt-5.5"
        assert kwargs["timeout_seconds"] == 300
        return {
            "content": json.dumps(
                {
                    "status": "completed",
                    "summary": "本机检查完成",
                    "findings": [{"title": "ok"}],
                    "actions_taken": [{"name": "inspect"}],
                    "not_verified": [],
                    "suggested_next_steps": ["重试调用"],
                    "final_message": "可以重试。",
                },
                ensure_ascii=False,
            )
        }

    def fake_enqueue(name, func, *args, **kwargs):
        func(*args)
        return "queue-1"

    monkeypatch.setattr("backend.core.device_agent.service.chat_with_provider", fake_chat_with_provider)
    monkeypatch.setattr("backend.core.device_agent.service.background_task_queue.enqueue", fake_enqueue)
    save_user_ai_chat_provider_config(
        session,
        auth_user.id,
        "codex-cli",
        preferred_models=["gpt-5.5"],
    )
    save_user_ai_app_config(session, auth_user.id, AI_APP_DEVICE_AGENT, provider="codex-cli", model="")

    response = client.post(
        f"/api/device-entries/{entry.entry_id}/agent/sessions",
        json={
            "requester": {"kind": "user", "id": "tester", "display_name": "测试"},
            "request_type": "diagnose",
            "instruction": "检查服务",
            "context": {"error": "timeout"},
        },
    )

    assert response.status_code == 200
    created = response.json()
    turn = created["turns"][0]
    assert turn["status"] == "completed"
    assert turn["result_report"]["summary"] == "本机检查完成"

    detail = client.get(f"/api/device-entries/{entry.entry_id}/agent/sessions/{created['id']}")
    assert detail.status_code == 200
    assert detail.json()["turns"][0]["result_report"]["final_message"] == "可以重试。"


def test_device_agent_remote_proxy_forwards_request(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-device",
        name="remote",
        mode="remote",
        server_url="http://mi15:8000",
        token="remote-token",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    captured = {}

    class FakeResponse:
        status_code = 200
        content = b'{"ok": true}'
        headers = {"content-type": "application/json"}

        def json(self):
            return {"ok": True}

    def fake_request(**kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)

    response = client.get(f"/api/device-entries/{entry.entry_id}/agent/manifest")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert captured["url"] == "http://mi15:8000/api/device-agent/manifest"
    assert captured["headers"]["X-Device-Token"] == "remote-token"
