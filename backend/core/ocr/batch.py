"""Shared bounded batch HTTP contract for the API and local OCR daemon."""
import base64
import binascii
import tempfile
from pathlib import Path
from typing import Any
from fastapi import HTTPException
from pydantic import BaseModel, Field
from backend.core.ocr.preview import OcrPreviewError, OcrShapeType
from backend.core.settings import get_settings
from backend.core.temp_paths import codeyun_temp_root

class OcrBatchRequest(BaseModel):
    images: list[str] = Field(min_length=1, max_length=8)
    shape_type: OcrShapeType = "rectangle"
    options: dict[str, Any] = Field(default_factory=dict)


def predict_encoded_batch(req: OcrBatchRequest, predict):
    """Validate the entire bounded input before inference; results preserve input order."""
    limit = get_settings().service_request_max_image_bytes
    decoded = []
    total = 0
    for value in req.images:
        value = value.strip()
        if value.startswith("data:") and "," in value:
            value = value.split(",", 1)[1]
        if len(value) > (limit + 2) // 3 * 4:
            raise HTTPException(413, "OCR batch 图片总量超过限制")
        try:
            image = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise HTTPException(400, "images 必须是 base64 图片列表") from exc
        if not image:
            raise HTTPException(400, "图片不能为空")
        total += len(image)
        if total > limit:
            raise HTTPException(413, "OCR batch 图片总量超过限制")
        decoded.append(image)
    with tempfile.TemporaryDirectory(dir=codeyun_temp_root("ocr-batch")) as directory:
        paths = []
        for index, image in enumerate(decoded):
            path = Path(directory) / f"{index}.png"
            path.write_bytes(image)
            paths.append(path)
        try:
            results = predict(paths, shape_type=req.shape_type, options=req.options)
        except OcrPreviewError as exc:
            raise HTTPException(503, str(exc)) from exc
    return {"ok": True, "results": results}
