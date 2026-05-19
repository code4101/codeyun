from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import backend.models  # noqa: F401
from backend.api import upload as upload_api
from backend.core import attachment_resources
from backend.models import DeviceFile


def make_upload_client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setattr(upload_api, "get_attachments_dir", lambda: tmp_path)
    monkeypatch.setattr(attachment_resources, "resolve_attachment_device_id", lambda: "test-device")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def override_session():
        with Session(engine) as session:
            yield session

    app = FastAPI()
    app.dependency_overrides[upload_api.get_session] = override_session
    app.state.engine = engine
    app.include_router(upload_api.router, prefix="/api/upload")
    return TestClient(app)


def test_upload_file_saves_attachment_and_returns_link(monkeypatch, tmp_path):
    client = make_upload_client(monkeypatch, tmp_path)

    response = client.post(
        "/api/upload/file",
        files={"file": ("C:\\tmp\\report.final.pdf", b"%PDF-1.4", "application/pdf")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["errno"] == 0
    data = payload["data"]
    assert data["original_filename"] == "report.final.pdf"
    assert data["name"] == "report.final.pdf"
    assert data["content_type"] == "application/pdf"
    assert data["size"] == 8
    assert isinstance(data["id"], int)
    assert data["filename"].endswith(".pdf")
    assert data["url"] == f"/static/attachments/{data['filename']}"
    assert (tmp_path / data["filename"]).read_bytes() == b"%PDF-1.4"
    with Session(client.app.state.engine) as session:
        record = session.exec(select(DeviceFile).where(DeviceFile.numeric_id == data["id"])).one()
    assert record.media_kind == "pdf"
    assert record.mime_type == "application/pdf"


def test_upload_image_returns_device_file_resource_id(monkeypatch, tmp_path):
    client = make_upload_client(monkeypatch, tmp_path)

    response = client.post(
        "/api/upload/image",
        files={"file": ("cover.png", b"\x89PNG\r\n\x1a\n", "image/png")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["errno"] == 0
    data = payload["data"]
    assert isinstance(data["id"], int)
    assert data["url"] == f"/static/attachments/{data['filename']}"
    with Session(client.app.state.engine) as session:
        record = session.exec(select(DeviceFile).where(DeviceFile.numeric_id == data["id"])).one()
    assert record.media_kind == "image"
    assert record.mime_type == "image/png"


def test_upload_file_stores_dangerous_extension_as_bin(monkeypatch, tmp_path):
    client = make_upload_client(monkeypatch, tmp_path)

    response = client.post(
        "/api/upload/file",
        files={"file": ("note.html", b"<script>alert(1)</script>", "text/html")},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["original_filename"] == "note.html"
    assert isinstance(data["id"], int)
    assert data["filename"].endswith(".bin")
    assert (tmp_path / data["filename"]).read_bytes() == b"<script>alert(1)</script>"


def test_upload_file_rejects_oversized_attachment(monkeypatch, tmp_path):
    client = make_upload_client(monkeypatch, tmp_path)
    monkeypatch.setattr(upload_api, "MAX_ATTACHMENT_UPLOAD_BYTES", 3)

    response = client.post(
        "/api/upload/file",
        files={"file": ("large.txt", b"1234", "text/plain")},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "文件超过 3B"
    assert list(tmp_path.iterdir()) == []
