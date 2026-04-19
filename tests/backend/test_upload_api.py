from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import upload as upload_api


def make_upload_client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setattr(upload_api, "get_attachments_dir", lambda: tmp_path)
    app = FastAPI()
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
    assert data["filename"].endswith(".pdf")
    assert data["url"] == f"/static/attachments/{data['filename']}"
    assert (tmp_path / data["filename"]).read_bytes() == b"%PDF-1.4"


def test_upload_file_stores_dangerous_extension_as_bin(monkeypatch, tmp_path):
    client = make_upload_client(monkeypatch, tmp_path)

    response = client.post(
        "/api/upload/file",
        files={"file": ("note.html", b"<script>alert(1)</script>", "text/html")},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["original_filename"] == "note.html"
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
