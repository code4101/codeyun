import base64
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from sqlmodel import Session

from backend.core.ai_app_config import AI_APP_FANXIU_GAME_MACRO_ANNOTATION, resolve_ai_app_runtime_config
from backend.core.ai_chat import chat_with_provider
from backend.core.fanxiu_game_window_models import (
    FanxiuGameWindow2MatchBox,
    FanxiuDataAnnotationMacroAnnotateRequest,
    FanxiuDataAnnotationMacroAnnotateResponse,
    FanxiuDataAnnotationOcrFrameLine,
    FanxiuDataAnnotationOcrFrameResponse,
)
from backend.core.fanxiu_ocr_utils import _extract_ocr_line_entries, _join_ocr_line_entries, _sanitize_ocr_text
from backend.core.ocr_preview import OcrPreviewError, run_paddle_ocr_preview
from backend.models import User


def _extract_game_macro_annotation_json(raw: Any) -> dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("AI 标注结果不是 JSON")
        payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("AI 标注结果必须是 JSON 对象")
    return payload


def _coerce_float(value: Any, fallback: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    if result != result:
        return fallback
    return result


def _clamp_game_macro_box(
    raw_box: Any,
    fallback_box: FanxiuGameWindow2MatchBox,
    frame_width: int,
    frame_height: int,
) -> FanxiuGameWindow2MatchBox:
    if not isinstance(raw_box, dict):
        raw_box = {}
    fallback = fallback_box.model_dump()
    x = _coerce_float(raw_box.get("x"), float(fallback["x"]))
    y = _coerce_float(raw_box.get("y"), float(fallback["y"]))
    w = _coerce_float(raw_box.get("w"), float(fallback["w"]))
    h = _coerce_float(raw_box.get("h"), float(fallback["h"]))
    w = max(1.0, min(w, float(frame_width)))
    h = max(1.0, min(h, float(frame_height)))
    x = max(0.0, min(x, max(0.0, float(frame_width) - w)))
    y = max(0.0, min(y, max(0.0, float(frame_height) - h)))
    return FanxiuGameWindow2MatchBox(
        name=str(raw_box.get("name") or fallback_box.name or ""),
        x=round(x, 3),
        y=round(y, 3),
        w=round(w, 3),
        h=round(h, 3),
    )


def _build_game_macro_annotation_prompt(req: FanxiuDataAnnotationMacroAnnotateRequest) -> str:
    fallback = req.fallback_box.model_dump()
    end_text = "无"
    if req.end:
        end_text = json.dumps(req.end.model_dump(), ensure_ascii=False)
    ocr_context = _build_game_macro_ocr_context(req.image_data_url)
    return "\n".join(
        [
            "请根据截图和用户操作点，判断这次录制宏操作对应的控件 shape 框。",
            "只返回用户点击或拖拽直接作用的按钮、图标、菜单项、滑块、可拖拽控件范围。",
            "不要返回整屏、整张弹窗、背景大区域，也不要为了包含文字说明而扩大到无关区域。",
            "坐标必须使用截图原始像素坐标，左上角为 (0,0)。",
            "如果不能可靠判断，请返回 fallback_box，并把 confidence 设低。",
            "",
            f"截图尺寸：{req.frame_width}x{req.frame_height}",
            f"操作类型：{req.action}",
            f"起点：{json.dumps(req.start.model_dump(), ensure_ascii=False)}",
            f"终点：{end_text}",
            f"方向：{req.direction or 'none'}",
            f"持续时间 ms：{req.duration_ms}",
            f"工程保底框 fallback_box：{json.dumps(fallback, ensure_ascii=False)}",
            f"PaddleOCR 文本参考：{ocr_context}",
            "",
            "严格返回 JSON 对象，格式如下：",
            '{"box":{"x":0,"y":0,"w":50,"h":50},"confidence":0.0,"label":"控件名称","reason":"简短理由"}',
        ]
    )


def _decode_game_macro_data_url_to_bytes(data_url: str) -> bytes:
    text = str(data_url or "").strip()
    if "," in text and text.lower().startswith("data:"):
        text = text.split(",", 1)[1]
    return base64.b64decode("".join(text.split()), validate=False)


def _summarize_game_macro_ocr_document(preview_document: dict[str, Any]) -> str:
    try:
        line_entries = _extract_ocr_line_entries(preview_document)
    except Exception:
        line_entries = []
    lines: list[str] = []
    for entries in line_entries:
        fragments: list[str] = []
        boxes: list[str] = []
        for entry in entries:
            text = _sanitize_ocr_text(entry.get("text"))
            if not text:
                continue
            fragments.append(text)
            x = _coerce_float(entry.get("x"), -1)
            y = _coerce_float(entry.get("y"), -1)
            w = _coerce_float(entry.get("width"), -1)
            h = _coerce_float(entry.get("height"), -1)
            if x >= 0 and y >= 0 and w > 0 and h > 0:
                boxes.append(f"{round(x)},{round(y)},{round(w)},{round(h)}")
        joined = "".join(fragments)
        if joined:
            suffix = f" @{'/'.join(boxes[:3])}" if boxes else ""
            lines.append(f"{joined}{suffix}")
        if len(lines) >= 80:
            break
    if not lines:
        return "无可用 OCR 文本"
    return "；".join(lines)[:4000]


def _build_game_macro_ocr_context(image_data_url: str) -> str:
    temp_path: Path | None = None
    try:
        image_bytes = _decode_game_macro_data_url_to_bytes(image_data_url)
        if not image_bytes:
            return "无可用 OCR 文本"
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as file:
            file.write(image_bytes)
            temp_path = Path(file.name)
        preview = run_paddle_ocr_preview(temp_path, shape_type="rectangle")
        return _summarize_game_macro_ocr_document(preview)
    except Exception as exc:
        return f"OCR 不可用：{exc}"
    finally:
        if temp_path:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _recognize_data_annotation_ocr_frame(image_data_url: str) -> FanxiuDataAnnotationOcrFrameResponse:
    temp_path: Path | None = None
    try:
        image_bytes = _decode_game_macro_data_url_to_bytes(image_data_url)
        if not image_bytes:
            return FanxiuDataAnnotationOcrFrameResponse()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as file:
            file.write(image_bytes)
            temp_path = Path(file.name)
        preview = run_paddle_ocr_preview(temp_path, shape_type="rectangle")
        line_entries = _extract_ocr_line_entries(preview.get("document") or {})
        lines: list[FanxiuDataAnnotationOcrFrameLine] = []
        for entries in line_entries:
            text = _join_ocr_line_entries(entries)
            if not text:
                continue
            left = min(_coerce_float(item.get("x"), 0) for item in entries)
            right = max(_coerce_float(item.get("x2"), _coerce_float(item.get("x"), 0) + _coerce_float(item.get("width"), 1)) for item in entries)
            top = min(_coerce_float(item.get("y"), 0) - _coerce_float(item.get("height"), 1) / 2 for item in entries)
            bottom = max(_coerce_float(item.get("y"), 0) + _coerce_float(item.get("height"), 1) / 2 for item in entries)
            lines.append(
                FanxiuDataAnnotationOcrFrameLine(
                    text=text,
                    x=max(0.0, left),
                    y=max(0.0, top),
                    w=max(1.0, right - left),
                    h=max(1.0, bottom - top),
                )
            )
        return FanxiuDataAnnotationOcrFrameResponse(lines=lines)
    except OcrPreviewError as exc:
        raise RuntimeError(str(exc)) from exc
    finally:
        if temp_path:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _annotate_game_macro_shape_with_ai(
    req: FanxiuDataAnnotationMacroAnnotateRequest,
    *,
    current_user: User,
    session: Session,
) -> FanxiuDataAnnotationMacroAnnotateResponse:
    runtime = resolve_ai_app_runtime_config(
        session=session,
        current_user=current_user,
        app_id=AI_APP_FANXIU_GAME_MACRO_ANNOTATION,
    )
    response = chat_with_provider(
        provider_id=str(runtime["provider"]),
        base_url=runtime["base_url"],
        api_key=runtime["api_key"],
        model=runtime["model"],
        system_prompt="你是手游 GUI 自动化标注助手。只输出严格 JSON，不输出解释性正文。",
        messages=[
            {
                "role": "user",
                "content": _build_game_macro_annotation_prompt(req),
                "images": [req.image_data_url],
            }
        ],
        response_format="json",
        timeout_seconds=180,
        extra_providers=runtime["extra_providers"],
    )
    raw = str(response.get("content") or "")
    payload = _extract_game_macro_annotation_json(raw)
    box = _clamp_game_macro_box(payload.get("box"), req.fallback_box, req.frame_width, req.frame_height)
    confidence = max(0.0, min(1.0, _coerce_float(payload.get("confidence"), 0.0)))
    return FanxiuDataAnnotationMacroAnnotateResponse(
        ok=True,
        used_ai=True,
        box=box,
        confidence=round(confidence, 4),
        label=str(payload.get("label") or "").strip()[:80],
        reason=str(payload.get("reason") or "").strip()[:300],
        raw=raw,
    )
