from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from threading import RLock
from typing import Any, Literal

from PIL import Image, UnidentifiedImageError

from backend.core.settings import get_settings

OcrShapeType = Literal["polygon", "rectangle"]

_ocr_instance_lock = RLock()
_ocr_instance_cache: dict[tuple[str, str, bool, bool, bool], Any] = {}


class OcrPreviewError(RuntimeError):
    pass


def _round_float(value: float, digits: int = 4) -> float:
    return round(float(value), digits)


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _normalize_quad_points(value: Any) -> list[list[float]] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None

    points: list[list[float]] = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            return None
        x = _safe_float(item[0])
        y = _safe_float(item[1])
        if x is None or y is None:
            return None
        points.append([x, y])
    return points


def _normalize_rectangle_points(value: Any) -> list[list[float]] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return None
    x1 = _safe_float(value[0])
    y1 = _safe_float(value[1])
    x2 = _safe_float(value[2])
    y2 = _safe_float(value[3])
    if x1 is None or y1 is None or x2 is None or y2 is None:
        return None
    return [[x1, y1], [x2, y2]]


def _polygon_to_rectangle_points(points: list[list[float]]) -> list[list[float]] | None:
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return [[min(xs), min(ys)], [max(xs), max(ys)]]


def _extract_predict_payload(result: Any) -> dict[str, Any]:
    def _normalize_payload(value: Any) -> dict[str, Any] | None:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return None

        if isinstance(value, dict):
            inner = value.get("res")
            return inner if isinstance(inner, dict) else value

        if isinstance(value, list) and value:
            first_item = value[0]
            if isinstance(first_item, dict):
                inner = first_item.get("res")
                return inner if isinstance(inner, dict) else first_item

        return None

    if hasattr(result, "json"):
        payload = getattr(result, "json")
        if callable(payload):
            payload = payload()
        normalized = _normalize_payload(payload)
        if normalized is not None:
            return normalized

    if hasattr(result, "res"):
        normalized = _normalize_payload(getattr(result, "res"))
        if normalized is not None:
            return normalized

    normalized = _normalize_payload(result)
    if normalized is not None:
        return normalized

    raise OcrPreviewError("PaddleOCR 返回结果格式不支持")


def _build_shape_label_text(payload: dict[str, Any], index: int) -> str:
    texts = payload.get("rec_texts") or []
    scores = payload.get("rec_scores") or []
    angles = payload.get("textline_orientation_angles") or []

    label: dict[str, Any] = {
        "text": texts[index] if index < len(texts) else "",
    }
    if index < len(scores):
        score = _safe_float(scores[index])
        if score is not None:
            label["score"] = _round_float(score)
    if index < len(angles):
        angle = _safe_float(angles[index])
        if angle is not None:
            label["angle"] = _round_float(angle, digits=2)
    return json.dumps(label, ensure_ascii=False)


def build_ocr_labelme_document_from_payload(
    payload: dict[str, Any],
    *,
    image_path: str,
    image_width: int,
    image_height: int,
    shape_type: OcrShapeType = "polygon",
) -> dict[str, Any]:
    shapes: list[dict[str, Any]] = []
    raw_polygons = payload.get("dt_polys") or payload.get("rec_polys") or []
    raw_boxes = payload.get("rec_boxes") or []

    if shape_type == "rectangle":
        for index, raw_box in enumerate(raw_boxes):
            rectangle_points = _normalize_rectangle_points(raw_box)
            if rectangle_points is None:
                polygon_points = _normalize_quad_points(raw_polygons[index]) if index < len(raw_polygons) else None
                rectangle_points = _polygon_to_rectangle_points(polygon_points or [])
            if rectangle_points is None:
                continue
            shapes.append(
                {
                    "label": _build_shape_label_text(payload, index),
                    "points": rectangle_points,
                    "group_id": None,
                    "shape_type": "rectangle",
                    "flags": {},
                }
            )
    else:
        for index, raw_polygon in enumerate(raw_polygons):
            polygon_points = _normalize_quad_points(raw_polygon)
            if polygon_points is None:
                continue
            shapes.append(
                {
                    "label": _build_shape_label_text(payload, index),
                    "points": polygon_points,
                    "group_id": None,
                    "shape_type": "polygon",
                    "flags": {},
                }
            )

    return {
        "version": "5.1.7",
        "flags": {},
        "shapes": shapes,
        "imagePath": Path(image_path).name,
        "imageData": None,
        "imageHeight": image_height,
        "imageWidth": image_width,
    }


def _apply_ocr_runtime_environment(*, device: str) -> None:
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    if device == "cpu" and sys.platform.startswith("win"):
        os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")


def _get_ocr_instance() -> Any:
    settings = get_settings()
    cache_key = (
        settings.ocr_device,
        settings.ocr_lang,
        settings.ocr_use_doc_orientation_classify,
        settings.ocr_use_doc_unwarping,
        settings.ocr_use_textline_orientation,
    )

    with _ocr_instance_lock:
        cached = _ocr_instance_cache.get(cache_key)
        if cached is not None:
            return cached

        _apply_ocr_runtime_environment(device=settings.ocr_device)
        try:
            from paddleocr import PaddleOCR
        except Exception as exc:  # pragma: no cover - depends on runtime env
            raise OcrPreviewError("PaddleOCR 不可用，请先完成 codeyun backend 的 OCR 依赖安装") from exc

        instance = PaddleOCR(
            lang=settings.ocr_lang,
            device=settings.ocr_device,
            use_doc_orientation_classify=settings.ocr_use_doc_orientation_classify,
            use_doc_unwarping=settings.ocr_use_doc_unwarping,
            use_textline_orientation=settings.ocr_use_textline_orientation,
        )
        _ocr_instance_cache[cache_key] = instance
        return instance


def run_paddle_ocr_preview(image_path: Path, *, shape_type: OcrShapeType = "polygon") -> dict[str, Any]:
    try:
        with Image.open(image_path) as image:
            image_width, image_height = image.size
    except FileNotFoundError as exc:
        raise OcrPreviewError("图片文件不存在") from exc
    except UnidentifiedImageError as exc:
        raise OcrPreviewError("目标文件不是可识别图片") from exc
    except OSError as exc:
        raise OcrPreviewError(f"读取图片失败：{exc}") from exc

    try:
        ocr_instance = _get_ocr_instance()
        results = ocr_instance.predict(str(image_path))
    except OcrPreviewError:
        raise
    except Exception as exc:  # pragma: no cover - depends on runtime env
        raise OcrPreviewError(f"OCR 识别失败：{exc}") from exc

    result = results[0] if isinstance(results, list) and results else {}
    payload = _extract_predict_payload(result) if result else {}
    document = build_ocr_labelme_document_from_payload(
        payload,
        image_path=str(image_path),
        image_width=image_width,
        image_height=image_height,
        shape_type=shape_type,
    )
    return {
        "engine": "paddleocr",
        "shape_type": shape_type,
        "shape_count": len(document["shapes"]),
        "document": document,
    }
