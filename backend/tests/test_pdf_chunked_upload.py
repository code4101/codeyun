import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.api import pdf_documents
from backend.models import User


def _request_with_body(body: bytes) -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({"type": "http", "method": "PUT", "path": "/"}, receive)


def test_chunked_pdf_upload_stays_below_proxy_limit_and_is_user_scoped(tmp_path, monkeypatch):
    def temp_root(name: str):
        target = tmp_path / name
        target.mkdir(parents=True, exist_ok=True)
        return target

    monkeypatch.setattr(pdf_documents, "codeyun_temp_root", temp_root)
    owner = User(id=7, username="owner", hashed_password="test")
    other_user = User(id=8, username="other", hashed_password="test")
    content = b"%PDF-1.7\n" + b"x" * (pdf_documents.PDF_UPLOAD_CHUNK_BYTES + 17)
    upload = pdf_documents.create_pdf_upload_session(
        pdf_documents.PdfUploadSessionCreateRequest(
            filename="book.pdf",
            size_bytes=len(content),
        ),
        current_user=owner,
    )

    first = asyncio.run(pdf_documents.append_pdf_upload_chunk(
        upload.upload_id,
        _request_with_body(content[:pdf_documents.PDF_UPLOAD_CHUNK_BYTES]),
        offset=0,
        current_user=owner,
    ))
    second = asyncio.run(pdf_documents.append_pdf_upload_chunk(
        upload.upload_id,
        _request_with_body(content[pdf_documents.PDF_UPLOAD_CHUNK_BYTES:]),
        offset=first.received_bytes,
        current_user=owner,
    ))

    assert upload.chunk_size == 512 * 1024
    assert second.received_bytes == len(content)
    _metadata, _metadata_path, content_path = pdf_documents._load_pdf_upload_session(
        upload.upload_id,
        owner,
    )
    assert content_path.read_bytes() == content
    with pytest.raises(HTTPException) as error:
        pdf_documents._load_pdf_upload_session(upload.upload_id, other_user)
    assert error.value.status_code == 404


def test_chunked_pdf_upload_rejects_wrong_offset(tmp_path, monkeypatch):
    def temp_root(name: str):
        target = tmp_path / name
        target.mkdir(parents=True, exist_ok=True)
        return target

    monkeypatch.setattr(pdf_documents, "codeyun_temp_root", temp_root)
    owner = User(id=7, username="owner", hashed_password="test")
    upload = pdf_documents.create_pdf_upload_session(
        pdf_documents.PdfUploadSessionCreateRequest(filename="book.pdf", size_bytes=12),
        current_user=owner,
    )

    with pytest.raises(HTTPException) as error:
        asyncio.run(pdf_documents.append_pdf_upload_chunk(
            upload.upload_id,
            _request_with_body(b"%PDF-"),
            offset=3,
            current_user=owner,
        ))
    assert error.value.status_code == 409
