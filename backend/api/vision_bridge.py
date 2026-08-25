from __future__ import annotations

import base64
import binascii
import os
import tempfile
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.core.access.service_tokens import SERVICE_SCOPE_VISION_ANALYZE, require_service_scope
from backend.core.ai.chat import OllamaClientError, chat_with_provider, list_ai_provider_summaries
from backend.core.ocr.preview import OcrPreviewError, OcrShapeType, run_paddle_ocr_preview
from backend.core.ocr.spatial_document import extract_ocr_spatial_document
from backend.core.settings import get_settings
from backend.db import get_session


router = APIRouter()


class VisionAnalyzeRequest(BaseModel):
    image: str = Field(min_length=1)
    mode: Literal["ocr", "vqa", "describe", "auto"] = "auto"
    question: str | None = None
    shape_type: OcrShapeType = "rectangle"
    ocr_options: dict[str, Any] = Field(default_factory=dict)
    vqa_provider_id: str | None = None
    vqa_model: str | None = None
    vqa_system_prompt: str | None = None
    include_document: bool = False


def _decode_request_image(value: str) -> bytes:
    """Decode a base64 or data-url image payload, enforcing the service size limit."""

    payload = (value or "").strip()
    if "," in payload and payload.split(",", 1)[0].lower().startswith("data:"):
        payload = payload.split(",", 1)[1]
    try:
        image_bytes = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="image 必须是 base64 图片字符串") from exc

    max_bytes = get_settings().service_request_max_image_bytes
    if len(image_bytes) > max_bytes:
        raise HTTPException(status_code=413, detail=f"图片超过服务限制：{max_bytes} bytes")
    if not image_bytes:
        raise HTTPException(status_code=400, detail="image 不能为空")
    return image_bytes


def _run_ocr(
    image_bytes: bytes,
    *,
    shape_type: OcrShapeType,
    ocr_options: dict[str, Any],
    include_document: bool,
) -> dict[str, Any]:
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as temp_file:
            temp_file.write(image_bytes)
            temp_path = temp_file.name
        preview = run_paddle_ocr_preview(Path(temp_path), shape_type=shape_type, options=ocr_options)
    except OcrPreviewError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    document = preview.get("document") or {}
    payload = (document.get("flags") or {}).get("paddleocr_payload") or {}
    spatial = extract_ocr_spatial_document(payload) if payload else {"lines": [], "tokens": []}
    lines = list(spatial.get("lines") or [])
    text = "\n".join(
        str(line.get("text") or "") for line in lines if str(line.get("text") or "").strip()
    )

    result: dict[str, Any] = {
        "engine": preview.get("engine") or "paddleocr",
        "shape_type": preview.get("shape_type") or shape_type,
        "shape_count": preview.get("shape_count") or len(lines),
        "lines": lines,
        "text": text,
    }
    if include_document:
        result["document"] = document
    return result


def _pick_vision_provider_id(provider_id: str | None) -> str:
    if provider_id and provider_id.strip():
        return provider_id.strip()
    for item in list_ai_provider_summaries():
        if item.get("supports_vision") and item.get("configured"):
            return str(item["id"])
    raise HTTPException(
        status_code=503,
        detail="没有已配置的支持视觉的 AI 来源（ollama / codex-cli），请先在 AI 配置中启用",
    )


def _run_vqa(
    image_bytes: bytes,
    *,
    question: str | None,
    provider_id: str | None,
    model: str | None,
    system_prompt: str | None,
) -> dict[str, Any]:
    resolved_provider_id = _pick_vision_provider_id(provider_id)
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    messages = [
        {
            "role": "user",
            "content": (question or "").strip() or "请详细描述这张图片的内容，包括其中可见的文字。",
            "images": [image_b64],
        }
    ]
    try:
        result = chat_with_provider(
            provider_id=resolved_provider_id,
            model=model,
            system_prompt=system_prompt,
            messages=messages,
        )
    except OllamaClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "provider_id": resolved_provider_id,
        "model": result.get("model") or model or "",
        "content": result.get("content") or "",
    }


@router.post(
    "/analyze",
    dependencies=[Depends(require_service_scope(SERVICE_SCOPE_VISION_ANALYZE))],
)
def analyze_vision(req: VisionAnalyzeRequest) -> dict[str, Any]:
    """Unified image understanding endpoint for DSH-style external agents.

    ``mode=ocr`` returns PaddleOCR text lines with coordinates plus a
    concatenated reading-order ``text``.  ``mode=vqa`` forwards the image to a
    configured vision-capable AI provider (ollama / codex-cli) and returns its
    answer.  ``mode=describe`` combines both: OCR text (best effort) plus a
    vision-model description.  ``mode=auto`` picks vqa when a question is
    supplied, otherwise ocr.
    """

    image_bytes = _decode_request_image(req.image)
    mode = req.mode
    if mode == "auto":
        mode = "vqa" if (req.question or "").strip() else "ocr"

    if mode == "ocr":
        return {
            "ok": True,
            "mode": "ocr",
            **_run_ocr(
                image_bytes,
                shape_type=req.shape_type,
                ocr_options=req.ocr_options,
                include_document=req.include_document,
            ),
        }

    if mode == "vqa":
        return {
            "ok": True,
            "mode": "vqa",
            **_run_vqa(
                image_bytes,
                question=req.question,
                provider_id=req.vqa_provider_id,
                model=req.vqa_model,
                system_prompt=req.vqa_system_prompt,
            ),
        }

    if mode == "describe":
        # OCR text extraction + vision-model description in one call.  OCR is a
        # best-effort enrichment: when the OCR service is unavailable the
        # description alone is returned with an ocr_error note instead of
        # failing the whole request.
        ocr_result: dict[str, Any] = {}
        try:
            ocr_result = _run_ocr(
                image_bytes,
                shape_type=req.shape_type,
                ocr_options=req.ocr_options,
                include_document=req.include_document,
            )
        except HTTPException as exc:
            ocr_result = {"ocr_error": str(exc.detail)}
        vqa_result = _run_vqa(
            image_bytes,
            question=req.question
            or "请用中文详细描述这张图片的内容，包括图中可见的所有文字（如有）。",
            provider_id=req.vqa_provider_id,
            model=req.vqa_model,
            system_prompt=req.vqa_system_prompt,
        )
        return {
            "ok": True,
            "mode": "describe",
            "ocr": ocr_result,
            "provider_id": vqa_result["provider_id"],
            "model": vqa_result["model"],
            "description": vqa_result["content"],
        }

    raise HTTPException(status_code=400, detail=f"不支持的 mode：{mode}")
