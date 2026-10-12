from datetime import timedelta
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool

from backend.api.admin_downloads import COOKIE_NAME, router
from backend.core.access.auth import create_user_access_token
from backend.core.resources import admin_downloads as bundles
from backend.db import get_session
from backend.models import User


@pytest.fixture
def downloads(tmp_path, monkeypatch):
    monkeypatch.setattr(bundles, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path / "data"))
    source = tmp_path / "完整数据.zip"
    source.write_bytes(b"0123456789")
    bundles.publish_download_bundle("migration", title="迁移数据", files=[source])
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        users = [User(username="admin", hashed_password="unused", is_superuser=True),
                 User(username="member", hashed_password="unused"),
                 User(username="disabled", hashed_password="unused", is_superuser=True, is_active=False)]
        session.add_all(users)
        session.commit()
        tokens = {user.username: create_user_access_token(user, timedelta(minutes=5)) for user in users}
        tokens["expired"] = create_user_access_token(users[0], timedelta(seconds=-1))
    app = FastAPI()
    app.include_router(router, prefix="/api/admin-downloads")
    def sessions():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    with TestClient(app, base_url="https://testserver") as client:
        yield client, tokens, source
    engine.dispose()


def test_data_requires_active_superuser(downloads):
    client, tokens, _ = downloads
    for endpoint in ("metadata", "files/完整数据.zip"):
        url = "/api/admin-downloads/migration/" + endpoint
        assert client.get(url).status_code == 401
        for name in ("member", "disabled"):
            assert client.get(url, headers={"Authorization": "Bearer " + tokens[name]}).status_code == 403
        for name in ("expired",):
            assert client.get(url, headers={"Authorization": "Bearer " + tokens[name]}).status_code == 401
        assert client.get(url, headers={"Authorization": "Bearer invalid"}).status_code == 401
    page = client.get("/api/admin-downloads/migration")
    assert page.status_code == 200
    assert "完整数据.zip" not in page.text


def test_browser_cookie_download_and_range(downloads):
    client, tokens, _ = downloads
    response = client.post("/api/admin-downloads/session", headers={"Authorization": "Bearer " + tokens["admin"]})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie
    assert "Path=/api/admin-downloads" in cookie
    metadata = client.get("/api/admin-downloads/migration/metadata")
    assert metadata.status_code == 200
    assert metadata.json()["files"][0]["bytes"] == 10
    url = "/api/admin-downloads/migration/files/完整数据.zip"
    response = client.get(url)
    assert response.content == b"0123456789"
    assert response.headers["cache-control"] == "private, no-store"
    assert "attachment" in response.headers["content-disposition"]
    response = client.get(url, headers={"Range": "bytes=3-6"})
    assert response.status_code == 206
    assert response.content == b"3456"
    assert response.headers["content-range"] == "bytes 3-6/10"
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, tokens["member"])
    assert client.get(url).status_code == 403


def test_bundle_is_copied_immutable_and_paths_are_confined(downloads, tmp_path):
    client, tokens, source = downloads
    source.write_bytes(b"changed source")
    assert bundles.resolve_download_file("migration", source.name).read_bytes() == b"0123456789"
    with pytest.raises(FileExistsError):
        bundles.publish_download_bundle("migration", title="replaced", files=[source])
    with pytest.raises(ValueError):
        bundles.read_download_bundle("../migration")
    for filename in ("../secret", "manifest.json", "missing"):
        with pytest.raises(FileNotFoundError):
            bundles.resolve_download_file("migration", filename)
    headers = {"Authorization": "Bearer " + tokens["admin"]}
    assert client.get("/api/admin-downloads/missing/metadata", headers=headers).status_code == 404
    assert client.get("/api/admin-downloads/migration/files/manifest.json", headers=headers).status_code == 404
