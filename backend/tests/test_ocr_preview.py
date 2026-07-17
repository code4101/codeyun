import os
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image

from backend.api import filesystem as filesystem_api
from backend.core.ocr import preview as ocr_preview
from backend.core.ocr.preview import OcrPreviewError, build_ocr_labelme_document_from_payload


def test_build_ocr_labelme_document_from_payload_preserves_polygon_points_and_text_fields() -> None:
    payload = {
        "dt_polys": [
            [[12, 34], [56, 34], [56, 78], [12, 78]],
        ],
        "rec_texts": ["切磋次数"],
        "rec_scores": [0.98765],
        "textline_orientation_angles": [5.25],
    }

    document = build_ocr_labelme_document_from_payload(
        payload,
        image_path="D:/demo/screenshot.jpg",
        image_width=720,
        image_height=1280,
        shape_type="polygon",
    )

    assert document["imagePath"] == "screenshot.jpg"
    assert document["imageWidth"] == 720
    assert document["imageHeight"] == 1280
    assert len(document["shapes"]) == 1

    shape = document["shapes"][0]
    assert shape["shape_type"] == "polygon"
    assert shape["points"] == [[12.0, 34.0], [56.0, 34.0], [56.0, 78.0], [12.0, 78.0]]
    assert json.loads(shape["label"]) == {
        "text": "切磋次数",
        "score": 0.9877,
        "angle": 5.25,
    }


def test_build_ocr_labelme_document_from_payload_uses_rectangle_boxes() -> None:
    payload = {
        "rec_boxes": [
            [101, 202, 303, 404],
        ],
        "rec_texts": ["邀请灵体"],
        "rec_scores": [0.9],
    }

    document = build_ocr_labelme_document_from_payload(
        payload,
        image_path="invite.png",
        image_width=523,
        image_height=1086,
        shape_type="rectangle",
    )

    assert len(document["shapes"]) == 1
    shape = document["shapes"][0]
    assert shape["shape_type"] == "rectangle"
    assert shape["points"] == [[101.0, 202.0], [303.0, 404.0]]
    assert json.loads(shape["label"]) == {
        "text": "邀请灵体",
        "score": 0.9,
    }


def test_build_ocr_preview_response_wraps_preview_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_path = tmp_path / "ocr-target.png"
    image_path.write_bytes(b"fake-image")

    preview_document = {
        "version": "5.1.7",
        "flags": {},
        "shapes": [
            {
                "label": '{"text":"主页"}',
                "points": [[1, 2], [3, 4]],
                "group_id": None,
                "shape_type": "rectangle",
                "flags": {},
            }
        ],
        "imagePath": image_path.name,
        "imageData": None,
        "imageHeight": 1080,
        "imageWidth": 1920,
    }

    def _fake_preview(target_path: Path, *, shape_type: str = "polygon") -> dict:
        assert target_path == image_path
        assert shape_type == "rectangle"
        return {
            "engine": "paddleocr",
            "shape_type": shape_type,
            "shape_count": 1,
            "document": preview_document,
        }

    monkeypatch.setattr(filesystem_api, "run_paddle_ocr_preview", _fake_preview)

    payload = filesystem_api.build_ocr_preview_response(
        absolute_path=str(image_path),
        shape_type="rectangle",
    )

    assert payload["ok"] is True
    assert payload["path"] == str(image_path)
    assert payload["absolute_path"] == str(image_path.resolve(strict=False))
    assert payload["engine"] == "paddleocr"
    assert payload["shape_type"] == "rectangle"
    assert payload["shape_count"] == 1
    assert payload["document"] == preview_document


def test_build_ocr_preview_response_maps_runtime_failure_to_http_503(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "ocr-target.png"
    image_path.write_bytes(b"fake-image")

    def _raise_preview(_target_path: Path, *, shape_type: str = "polygon") -> dict:
        raise OcrPreviewError("PaddleOCR 不可用")

    monkeypatch.setattr(filesystem_api, "run_paddle_ocr_preview", _raise_preview)

    with pytest.raises(HTTPException) as exc_info:
        filesystem_api.build_ocr_preview_response(absolute_path=str(image_path))

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "PaddleOCR 不可用"


def test_run_paddle_ocr_preview_accepts_json_string_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ocr_preview.ocr_service_manager.reset()
    image_path = tmp_path / "ocr-source.png"
    Image.new("RGB", (40, 20), color=(255, 255, 255)).save(image_path)

    class _FakeResult:
        json = json.dumps(
            {
                "res": {
                    "dt_polys": [
                        [[1, 2], [10, 2], [10, 8], [1, 8]],
                    ],
                    "rec_texts": ["OCR"],
                    "rec_scores": [0.8],
                }
            },
            ensure_ascii=False,
        )

    class _FakeOcr:
        def predict(self, _input: str, **kwargs: object) -> list[object]:
            assert kwargs == {"return_word_box": True}
            return [_FakeResult()]

    monkeypatch.setattr(ocr_preview, "_get_ocr_instance", lambda: _FakeOcr())

    preview = ocr_preview.run_paddle_ocr_preview(image_path, shape_type="polygon")

    assert preview["engine"] == "paddleocr"
    assert preview["shape_type"] == "polygon"
    assert preview["shape_count"] == 1
    assert preview["document"]["imageWidth"] == 40
    assert preview["document"]["imageHeight"] == 20
    assert json.loads(preview["document"]["shapes"][0]["label"]) == {
        "text": "OCR",
        "score": 0.8,
    }
    ocr_preview.ocr_service_manager.reset()


def test_run_paddle_ocr_preview_passes_safe_predict_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ocr_preview.ocr_service_manager.reset()
    image_path = tmp_path / "ocr-source.png"
    Image.new("RGB", (40, 20), color=(255, 255, 255)).save(image_path)
    observed: dict[str, object] = {}

    class _FakeResult:
        res = {
            "rec_boxes": [[1, 2, 10, 8]],
            "rec_texts": ["魔道"],
            "rec_scores": [0.8],
            "rec_word_texts": [["魔", "道"]],
            "rec_word_boxes": [[[[1, 2, 5, 8], [5, 2, 10, 8]]]],
        }

    class _FakeOcr:
        def predict(self, _input: str, **kwargs: object) -> list[object]:
            observed.update(kwargs)
            return [_FakeResult()]

    monkeypatch.setattr(ocr_preview, "_get_ocr_instance", lambda _config=None: _FakeOcr())

    preview = ocr_preview.run_paddle_ocr_preview(
        image_path,
        shape_type="rectangle",
        options={
            "return_word_box": True,
            "text_det_thresh": 0.2,
            "unknown_option": "ignored",
        },
    )

    assert observed == {"return_word_box": True, "text_det_thresh": 0.2}
    assert preview["document"]["flags"]["paddleocr_payload"]["rec_texts"] == ["魔道"]
    assert "unknown_option" not in observed
    ocr_preview.ocr_service_manager.reset()


def test_extract_ocr_tokens_supports_paddlex_3_text_word_fields() -> None:
    from backend.core.ocr.spatial_document import extract_ocr_tokens

    tokens = extract_ocr_tokens(
        {
            "text_word": [["真", "仙", "试", "炼"], ["2", "/", "2"]],
            "text_word_boxes": [
                [[10, 20, 30, 50], [31, 20, 51, 50], [52, 20, 72, 50], [73, 20, 93, 50]],
                [[100, 80, 112, 100], [113, 80, 125, 100], [126, 80, 138, 100]],
            ],
        }
    )

    assert [token["text"] for token in tokens] == ["真", "仙", "试", "炼", "2", "/", "2"]
    assert tokens[0] == {"text": "真", "x": 10.0, "y": 20.0, "w": 20.0, "h": 30.0}
    assert tokens[-1] == {"text": "2", "x": 126.0, "y": 80.0, "w": 12.0, "h": 20.0}


def test_apply_ocr_runtime_environment_disables_mkldnn_by_default_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", raising=False)
    monkeypatch.delenv("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", raising=False)
    monkeypatch.setattr(ocr_preview.sys, "platform", "win32")

    ocr_preview._apply_ocr_runtime_environment(device="gpu")

    assert os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] == "True"
    assert os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] == "False"
