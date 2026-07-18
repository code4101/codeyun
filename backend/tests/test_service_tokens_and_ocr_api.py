from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from backend.api import services as services_api
from backend.core.ocr import preview as ocr_preview
from backend.core.ocr.preview import OcrPreviewError, PaddleOcrServiceManager, _build_runtime_config
from backend.core.access.service_tokens import SERVICE_SCOPE_OCR_PREDICT, create_service_access_token, list_service_access_tokens


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
    app.include_router(services_api.router, prefix="/api/services")

    def override_get_session():
        yield session

    app.dependency_overrides[services_api.get_session] = override_get_session
    return TestClient(app)


def _preview_document() -> dict:
    return {
        "version": "5.1.7",
        "flags": {},
        "shapes": [],
        "imagePath": "ocr.png",
        "imageData": None,
        "imageHeight": 10,
        "imageWidth": 20,
    }


def test_service_ocr_api_accepts_authorization_and_legacy_token_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _build_session()
    created = create_service_access_token(session, label="ocr")
    plaintext = created["plaintext_value"]

    def fake_preview(_path: Path, *, shape_type: str = "polygon", options=None):
        assert shape_type == "rectangle"
        assert options == {"lang": "ch"}
        return {
            "engine": "paddleocr",
            "shape_type": shape_type,
            "shape_count": 0,
            "document": _preview_document(),
        }

    monkeypatch.setattr(services_api, "run_paddle_ocr_preview", fake_preview)
    client = _build_client(session)
    image = base64.b64encode(b"fake-png").decode("ascii")

    bearer = client.post(
        "/api/services/ocr/predict",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={"image": image, "shape_type": "rectangle", "options": {"lang": "ch"}},
    )
    legacy = client.post(
        "/api/services/ocr/predict",
        headers={"Token": plaintext},
        json={"image": image, "shape_type": "rectangle", "options": {"lang": "ch"}},
    )

    assert bearer.status_code == 200
    assert legacy.status_code == 200
    assert bearer.json()["document"] == _preview_document()


def test_service_token_scope_and_enabled_state_are_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _build_session()
    no_scope = create_service_access_token(session, label="status-only", scopes=["services.ocr:status"])
    disabled = create_service_access_token(session, label="disabled", enabled=False)
    monkeypatch.setattr(services_api, "run_paddle_ocr_preview", lambda *_args, **_kwargs: {})
    client = _build_client(session)
    image = base64.b64encode(b"fake-png").decode("ascii")

    forbidden = client.post(
        "/api/services/ocr/predict",
        headers={"Authorization": f"Bearer {no_scope['plaintext_value']}"},
        json={"image": image},
    )
    disabled_resp = client.post(
        "/api/services/ocr/predict",
        headers={"Authorization": f"Bearer {disabled['plaintext_value']}"},
        json={"image": image},
    )

    assert forbidden.status_code == 403
    assert disabled_resp.status_code == 403


def test_service_token_mask_shows_front_half_only() -> None:
    session = _build_session()
    created = create_service_access_token(session, label="ocr", plaintext_value="cys-1234-5678-ABCD")
    listed = list_service_access_tokens(session)[0]

    assert created["masked_value"] == "cys-1234-*********"
    assert listed["masked_value"] == "cys-1234-*********"


def test_service_docs_lan_address_prefers_primary_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services_api, "_get_primary_route_ip_address", lambda: "192.168.31.63")
    monkeypatch.setattr(
        services_api.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (services_api.socket.AF_INET, 0, 0, "", ("198.18.0.1", 0)),
            (services_api.socket.AF_INET, 0, 0, "", ("192.168.31.63", 0)),
        ],
    )

    assert services_api._get_lan_ip_addresses() == ["192.168.31.63"]


def test_service_docs_lan_address_filters_reserved_virtual_networks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services_api, "_get_primary_route_ip_address", lambda: None)
    monkeypatch.setattr(
        services_api.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (services_api.socket.AF_INET, 0, 0, "", ("198.18.0.1", 0)),
            (services_api.socket.AF_INET, 0, 0, "", ("192.168.31.63", 0)),
        ],
    )

    assert services_api._get_lan_ip_addresses() == ["192.168.31.63"]


def test_service_docs_lan_label_does_not_repeat_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services_api, "_get_lan_ip_addresses", lambda: ["192.168.31.63"])
    docs = services_api.build_service_docs_response()

    lan = next(item for item in docs["connections"] if item["kind"] == "lan")
    assert lan["label"] == "局域网"
    assert lan["url"] == "http://192.168.31.63:8000/api/services/ocr/predict"


def test_build_service_summary_response_only_collects_requested_services(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _build_session()
    calls: list[str] = []

    monkeypatch.setattr(services_api, "ensure_legacy_service_tokens", lambda _session: None)
    monkeypatch.setattr(
        services_api,
        "get_ocr_service_status",
        lambda: calls.append("ocr") or {"key": "ocr", "title": "OCR"},
    )

    def _unexpected_game_window_status():
        calls.append("fanxiu-game-window")
        raise AssertionError("game window status should not be requested")

    monkeypatch.setattr(services_api, "get_game_window_service_status", _unexpected_game_window_status)

    payload = services_api.build_service_summary_response(session, service_keys=["ocr"])

    assert calls == ["ocr"]
    assert payload["services"] == [{"key": "ocr", "title": "OCR"}]
    assert payload["token_count"] == 0
    assert payload["enabled_token_count"] == 0


def test_ocr_service_manager_reuses_resets_and_cleans_idle_instances(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "ocr.png"
    from PIL import Image

    Image.new("RGB", (10, 8), color=(255, 255, 255)).save(image_path)
    manager = PaddleOcrServiceManager()
    created: list[object] = []

    class FakeOcr:
        def predict(self, _path: str, **_kwargs: object):
            return [{"res": {"dt_polys": [], "rec_texts": [], "rec_scores": []}}]

    def fake_get_instance(_config=None):
        instance = FakeOcr()
        created.append(instance)
        return instance

    monkeypatch.setattr(ocr_preview, "_get_ocr_instance", fake_get_instance)
    monkeypatch.setattr(manager, "_settings_limits", lambda: (1, 600, 0))

    warmup = manager.warmup()
    assert warmup["loaded"] is True
    assert len(created) == 1

    first = manager.predict_file(image_path, shape_type="polygon")
    second = manager.predict_file(image_path, shape_type="polygon")
    assert first["shape_count"] == 0
    assert second["shape_count"] == 0
    assert len(created) == 1

    manager.reset()
    manager.predict_file(image_path, shape_type="polygon")
    assert len(created) == 2

    monkeypatch.setattr(manager, "_settings_limits", lambda: (1, 0, 0))
    assert manager.cleanup_idle() == 1
    assert manager.get_status()["loaded"] is False


def test_ocr_service_manager_enforces_concurrency_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = PaddleOcrServiceManager()
    monkeypatch.setattr(manager, "_settings_limits", lambda: (1, 600, 0))
    with manager._condition:
        manager._total_instances = 1
        manager._active_instances = 1

    with pytest.raises(OcrPreviewError):
        manager._acquire(_build_runtime_config())


def test_ocr_service_manager_discards_failed_instance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "ocr.png"
    from PIL import Image

    Image.new("RGB", (10, 8), color=(255, 255, 255)).save(image_path)
    manager = PaddleOcrServiceManager()
    created: list[object] = []

    class FakeOcr:
        def predict(self, _path: str, **_kwargs: object):
            if len(created) == 1:
                raise RuntimeError("CUDA out of memory")
            return [{"res": {"dt_polys": [], "rec_texts": [], "rec_scores": []}}]

    def fake_get_instance(_config=None):
        instance = FakeOcr()
        created.append(instance)
        return instance

    monkeypatch.setattr(ocr_preview, "_get_ocr_instance", fake_get_instance)
    monkeypatch.setattr(manager, "_settings_limits", lambda: (1, 600, 0))

    with pytest.raises(OcrPreviewError, match="CUDA out of memory"):
        manager.predict_file(image_path)

    assert manager.get_status()["instance_count"] == 0
    result = manager.predict_file(image_path)
    assert result["shape_count"] == 0
    assert len(created) == 2
