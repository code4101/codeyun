from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from backend.api import vision_bridge as vision_api
from backend.core.access.service_tokens import (
    SERVICE_SCOPE_VISION_ANALYZE,
    create_service_access_token,
)


def _build_session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _build_client(session: Session) -> TestClient:
    app = FastAPI()
    app.include_router(vision_api.router, prefix="/api/vision-bridge")

    def override_get_session():
        yield session

    app.dependency_overrides[vision_api.get_session] = override_get_session
    return TestClient(app)


def _image() -> str:
    return base64.b64encode(b"fake-png").decode("ascii")


def _preview_payload() -> dict:
    return {
        "rec_texts": ["你好", "世界"],
        "rec_scores": [0.99, 0.98],
        "rec_boxes": [[0, 0, 50, 20], [0, 30, 50, 20]],
    }


def _fake_preview_response(*_args, **_kwargs) -> dict:
    return {
        "engine": "paddleocr",
        "shape_type": "rectangle",
        "shape_count": 2,
        "document": {
            "version": "5.1.7",
            "flags": {"paddleocr_payload": _preview_payload()},
            "shapes": [],
            "imagePath": "ocr.png",
            "imageData": None,
            "imageHeight": 100,
            "imageWidth": 200,
        },
    }


def test_vision_bridge_ocr_mode_returns_lines_and_text(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _build_session()
    created = create_service_access_token(
        session,
        label="vision",
        scopes=[SERVICE_SCOPE_VISION_ANALYZE],
    )
    monkeypatch.setattr(vision_api, "run_paddle_ocr_preview", _fake_preview_response)
    client = _build_client(session)

    response = client.post(
        "/api/vision-bridge/analyze",
        headers={"Authorization": f"Bearer {created['plaintext_value']}"},
        json={"image": _image(), "mode": "ocr", "shape_type": "rectangle"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["mode"] == "ocr"
    assert payload["engine"] == "paddleocr"
    assert payload["text"] == "你好\n世界"
    assert len(payload["lines"]) == 2
    assert payload["lines"][0]["text"] == "你好"
    assert "document" not in payload


def test_vision_bridge_ocr_mode_can_include_document(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _build_session()
    created = create_service_access_token(session, label="vision", scopes=[SERVICE_SCOPE_VISION_ANALYZE])
    monkeypatch.setattr(vision_api, "run_paddle_ocr_preview", _fake_preview_response)
    client = _build_client(session)

    response = client.post(
        "/api/vision-bridge/analyze",
        headers={"Authorization": f"Bearer {created['plaintext_value']}"},
        json={"image": _image(), "mode": "ocr", "include_document": True},
    )

    assert response.status_code == 200
    assert response.json()["document"]["imageHeight"] == 100


def test_vision_bridge_auto_mode_without_question_runs_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _build_session()
    created = create_service_access_token(session, label="vision", scopes=[SERVICE_SCOPE_VISION_ANALYZE])
    monkeypatch.setattr(vision_api, "run_paddle_ocr_preview", _fake_preview_response)
    client = _build_client(session)

    response = client.post(
        "/api/vision-bridge/analyze",
        headers={"Authorization": f"Bearer {created['plaintext_value']}"},
        json={"image": _image(), "mode": "auto"},
    )

    assert response.status_code == 200
    assert response.json()["mode"] == "ocr"


def test_vision_bridge_auto_mode_with_question_runs_vqa(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _build_session()
    created = create_service_access_token(session, label="vision", scopes=[SERVICE_SCOPE_VISION_ANALYZE])

    def fake_chat_with_provider(*, provider_id, model, system_prompt, messages, **_kwargs):
        assert provider_id == "ollama"
        assert model == "qwen2.5-vl:7b"
        assert len(messages) == 1
        assert messages[0]["images"] == [base64.b64encode(b"fake-png").decode("ascii")]
        return {"model": model, "content": "图里有一轮月亮和两个人"}

    monkeypatch.setattr(vision_api, "chat_with_provider", fake_chat_with_provider)
    monkeypatch.setattr(
        vision_api,
        "list_ai_provider_summaries",
        lambda: [{"id": "ollama", "supports_vision": True, "configured": True}],
    )
    client = _build_client(session)

    response = client.post(
        "/api/vision-bridge/analyze",
        headers={"Authorization": f"Bearer {created['plaintext_value']}"},
        json={
            "image": _image(),
            "mode": "auto",
            "question": "描述这张图",
            "vqa_model": "qwen2.5-vl:7b",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "vqa"
    assert payload["provider_id"] == "ollama"
    assert payload["model"] == "qwen2.5-vl:7b"
    assert payload["content"] == "图里有一轮月亮和两个人"


def test_vision_bridge_vqa_without_vision_provider_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _build_session()
    created = create_service_access_token(session, label="vision", scopes=[SERVICE_SCOPE_VISION_ANALYZE])
    monkeypatch.setattr(
        vision_api,
        "list_ai_provider_summaries",
        lambda: [{"id": "deepseek", "supports_vision": False, "configured": True}],
    )
    client = _build_client(session)

    response = client.post(
        "/api/vision-bridge/analyze",
        headers={"Authorization": f"Bearer {created['plaintext_value']}"},
        json={"image": _image(), "mode": "vqa", "question": "描述这张图"},
    )

    assert response.status_code == 503


def test_vision_bridge_requires_vision_scope_token() -> None:
    session = _build_session()
    ocr_only = create_service_access_token(session, label="ocr-only", scopes=["services.ocr:predict"])
    client = _build_client(session)
    image = _image()

    no_token = client.post("/api/vision-bridge/analyze", json={"image": image, "mode": "ocr"})
    wrong_scope = client.post(
        "/api/vision-bridge/analyze",
        headers={"Authorization": f"Bearer {ocr_only['plaintext_value']}"},
        json={"image": image, "mode": "ocr"},
    )

    assert no_token.status_code == 401
    assert wrong_scope.status_code == 403


def test_vision_bridge_rejects_oversized_image(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _build_session()
    created = create_service_access_token(session, label="vision", scopes=[SERVICE_SCOPE_VISION_ANALYZE])
    monkeypatch.setattr(vision_api, "run_paddle_ocr_preview", _fake_preview_response)
    client = _build_client(session)
    big_image = base64.b64encode(b"\x00" * (30 * 1024 * 1024)).decode("ascii")

    response = client.post(
        "/api/vision-bridge/analyze",
        headers={"Authorization": f"Bearer {created['plaintext_value']}"},
        json={"image": big_image, "mode": "ocr"},
    )

    assert response.status_code == 413


def test_vision_bridge_describe_mode_combines_ocr_and_vqa(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _build_session()
    created = create_service_access_token(session, label="vision", scopes=[SERVICE_SCOPE_VISION_ANALYZE])

    def fake_chat_with_provider(*, provider_id, model, messages, **_kwargs):
        assert messages[0]["content"].strip()  # default describe question used
        return {"model": model, "content": "这是一张白底图片，上面有三个红色圆形和一行文字"}

    monkeypatch.setattr(vision_api, "run_paddle_ocr_preview", _fake_preview_response)
    monkeypatch.setattr(vision_api, "chat_with_provider", fake_chat_with_provider)
    monkeypatch.setattr(
        vision_api,
        "list_ai_provider_summaries",
        lambda: [{"id": "ollama", "supports_vision": True, "configured": True}],
    )
    client = _build_client(session)

    response = client.post(
        "/api/vision-bridge/analyze",
        headers={"Authorization": f"Bearer {created['plaintext_value']}"},
        json={"image": _image(), "mode": "describe"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "describe"
    assert payload["ocr"]["text"] == "你好\n世界"
    assert payload["description"] == "这是一张白底图片，上面有三个红色圆形和一行文字"
    assert payload["provider_id"] == "ollama"


def test_vision_bridge_describe_mode_tolerates_ocr_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _build_session()
    created = create_service_access_token(session, label="vision", scopes=[SERVICE_SCOPE_VISION_ANALYZE])

    def fake_failing_preview(*_args, **_kwargs):
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="OCR 服务不可用")

    def fake_chat_with_provider(*, provider_id, model, **_kwargs):
        return {"model": model, "content": "描述成功"}

    monkeypatch.setattr(vision_api, "run_paddle_ocr_preview", fake_failing_preview)
    monkeypatch.setattr(vision_api, "chat_with_provider", fake_chat_with_provider)
    monkeypatch.setattr(
        vision_api,
        "list_ai_provider_summaries",
        lambda: [{"id": "ollama", "supports_vision": True, "configured": True}],
    )
    client = _build_client(session)

    response = client.post(
        "/api/vision-bridge/analyze",
        headers={"Authorization": f"Bearer {created['plaintext_value']}"},
        json={"image": _image(), "mode": "describe"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ocr"]["ocr_error"] == "OCR 服务不可用"
    assert payload["description"] == "描述成功"
