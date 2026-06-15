from pathlib import Path

from backend.core.runtime import ocr_service as ocr_runtime


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"unexpected status {self.status_code}")

    def json(self):
        return self._payload


def test_ocr_service_status_rebinds_existing_http_daemon(monkeypatch):
    monkeypatch.setattr(
        ocr_runtime,
        "list_ocr_service_processes",
        lambda: [{"pid": 2233, "name": "python.exe", "cmdline": "python -m backend.services.ocr_daemon"}],
    )

    def fake_get(url, *, timeout):
        assert url == "http://127.0.0.1:8765/api/services/ocr/status"
        return _FakeResponse({
            "ok": True,
            "service": {
                "key": "ocr",
                "title": "OCR",
                "running": True,
                "loaded": False,
                "state": "cold",
            },
        })

    monkeypatch.setattr(ocr_runtime.requests, "get", fake_get)

    status = ocr_runtime.get_ocr_service_status()

    assert status["running"] is True
    assert status["pids"] == [2233]
    assert status["url"] == "http://127.0.0.1:8765"


def test_predict_via_ocr_service_posts_image_to_external_daemon(tmp_path: Path, monkeypatch):
    image_path = tmp_path / "ocr.png"
    image_path.write_bytes(b"png-bytes")
    captured = {}

    monkeypatch.setattr(ocr_runtime, "ensure_ocr_service_running", lambda: {"running": True})

    def fake_post(url, *, json, timeout):
        captured["url"] = url
        captured["shape_type"] = json["shape_type"]
        captured["image"] = json["image"]
        return _FakeResponse({
            "ok": True,
            "engine": "paddleocr",
            "shape_type": "rectangle",
            "shape_count": 0,
            "document": {"shapes": []},
        })

    monkeypatch.setattr(ocr_runtime.requests, "post", fake_post)

    result = ocr_runtime.predict_via_ocr_service(image_path, shape_type="rectangle")

    assert captured == {
        "url": "http://127.0.0.1:8765/api/services/ocr/predict",
        "shape_type": "rectangle",
        "image": "cG5nLWJ5dGVz",
    }
    assert result["document"] == {"shapes": []}
