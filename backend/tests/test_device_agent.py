import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from backend.core.device_agent import service
from backend.core.device_agent import artifacts
from backend.models import DeviceAgentSession, DeviceAgentTurn


@pytest.fixture
def database(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(service, "engine", engine)
    monkeypatch.setattr(service, "start_device_agent_turn", lambda _: "test-worker")
    return engine


def test_cc_followup_retains_native_context_and_download_snapshot(database, tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path))
    source = tmp_path / "file.txt"
    source.write_text("before")
    calls = []
    def run_agent(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "session_id": "native-test", "text": json.dumps({
            "summary": "done", "attachments": [{"path": str(source)}]})}
    monkeypatch.setattr("backend.core.agents.cli.run_agent", run_agent)
    with Session(database) as session:
        service.save_device_agent_config(session, {"default_provider": "cc"})
        created = service.create_device_agent_session(session, requester={}, request_type="ask", instruction="file")
    first = service.run_device_agent_turn_worker(created["last_turn_id"])
    assert first["status"] == "completed"
    source.write_text("after")
    path, metadata = artifacts.resolve_attachment(first, first["result_report"]["attachments"][0]["id"])
    assert path.read_text() == "before"
    assert metadata["size_bytes"] == 6
    with Session(database) as session:
        followup = service.append_device_agent_turn(session, created["id"], requester={}, request_type="ask", instruction="follow")
        with pytest.raises(service.DeviceAgentBusyError):
            service.append_device_agent_turn(session, created["id"], requester={}, request_type="ask", instruction="duplicate")
    second = service.run_device_agent_turn_worker(followup["id"])
    assert second["status"] == "completed"
    assert calls[0]["session_id"] == ""
    assert calls[1]["session_id"] == "native-test"
    assert calls[1]["session_provider"] == "claude"
    assert calls[1]["directory"] == service.ROOT_DIR
    with pytest.raises(FileNotFoundError):
        artifacts.resolve_attachment(second, metadata["id"])


def test_failure_and_disabled_are_truthful(database, monkeypatch):
    monkeypatch.setattr("backend.core.agents.cli.run_agent", lambda **_: {
        "ok": False, "status": "permission_denied", "error": "permission denied"})
    with Session(database) as session:
        service.save_device_agent_config(session, {"default_provider": "cc"})
        created = service.create_device_agent_session(session, requester={}, request_type="repair", instruction="repair")
    turn = service.run_device_agent_turn_worker(created["last_turn_id"])
    assert turn["status"] == "failed"
    assert turn["error_message"] == "permission denied"
    with Session(database) as session:
        service.save_device_agent_config(session, {"enabled": False})
        with pytest.raises(service.DeviceAgentError, match="未启用"):
            service.create_device_agent_session(session, requester={}, request_type="ask", instruction="ask")


def test_attachment_route_requires_authentication():
    from backend.api.device_agent import router
    app = FastAPI()
    app.include_router(router, prefix="/api/device-agent")
    response = TestClient(app).get("/api/device-agent/turns/" + "a" * 32 + "/attachments/" + "b" * 32)
    assert response.status_code == 401


def test_token_endpoint_uses_registered_local_token(monkeypatch):
    from backend.api import device
    monkeypatch.setattr(device, "get_device_token", lambda: None)
    assert device._effective_entry_token(SimpleNamespace(mode="local", token="registered")) == "registered"


def test_codex_chat_public_session_options(monkeypatch):
    from backend.core.ai import chat
    monkeypatch.setattr(chat, "_chat_with_codex_cli", lambda provider, **kwargs: {
        "workspace_dir": provider.workspace_dir, "session_id": provider.session_id})
    result = chat.chat_with_provider(provider_id="codex-cli", messages=[],
        workspace_dir="workspace", session_id="exact-thread")
    assert result == {"workspace_dir": "workspace", "session_id": "exact-thread"}
