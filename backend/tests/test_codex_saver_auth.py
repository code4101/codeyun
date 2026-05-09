from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from backend.api import codex_saver as codex_saver_api
from backend.core.codex_saver.mcp_server import CodexSaverTokenMiddleware


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(codex_saver_api.router, prefix="/api/codex-saver")
    session = _session()

    def override_get_session():
        yield session

    app.dependency_overrides[codex_saver_api.get_session] = override_get_session
    return TestClient(app)


def test_device_token_can_preview_codex_saver_route(monkeypatch) -> None:
    monkeypatch.setattr(
        codex_saver_api,
        "ensure_feature_access",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(HTTPException(status_code=403, detail="denied")),
    )
    monkeypatch.setattr(
        codex_saver_api,
        "validate_api_token_value",
        lambda token: object() if token == "device-token" else (_ for _ in ()).throw(HTTPException(status_code=401)),
    )

    response = _client().post(
        "/api/codex-saver/route-preview",
        headers={"X-Device-Token": "device-token"},
        json={"task": "write docs", "input_kinds": ["text"]},
    )

    assert response.status_code == 200
    assert response.json()["decision"] == "deepseek"


def test_device_token_does_not_grant_codex_saver_config_access(monkeypatch) -> None:
    monkeypatch.setattr(
        codex_saver_api,
        "validate_api_token_value",
        lambda _token: object(),
    )

    response = _client().get("/api/codex-saver/config", headers={"X-Device-Token": "device-token"})

    assert response.status_code == 403


def test_codex_saver_execute_requires_feature_or_token(monkeypatch) -> None:
    monkeypatch.setattr(
        codex_saver_api,
        "ensure_feature_access",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(HTTPException(status_code=403, detail="denied")),
    )

    response = _client().post(
        "/api/codex-saver/route-preview",
        json={"task": "write docs", "input_kinds": ["text"]},
    )

    assert response.status_code == 401


def test_codex_saver_mcp_http_requires_token(monkeypatch) -> None:
    async def downstream(_scope, _receive, send):
        body = b'{"ok":true}'
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json"), (b"content-length", b"11")],
            }
        )
        await send({"type": "http.response.body", "body": body})

    app = CodexSaverTokenMiddleware(downstream)
    client = TestClient(app)

    denied = client.post("/")
    assert denied.status_code == 401

    monkeypatch.setattr(
        "backend.core.codex_saver.mcp_server.validate_api_token_value",
        lambda token: object() if token == "device-token" else (_ for _ in ()).throw(HTTPException(status_code=401)),
    )
    allowed = client.post("/", headers={"Authorization": "Bearer device-token"})
    assert allowed.status_code == 200
    assert allowed.json() == {"ok": True}
