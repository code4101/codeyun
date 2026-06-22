import base64
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from sqlmodel import Session

from backend.core.ai.app_config import AI_APP_FANXIU_GAME_MACRO_ANNOTATION, resolve_ai_app_runtime_config
from backend.core.ai.chat import chat_with_provider
from backend.core.fanxiu.game.window_models import (
    FanxiuGameWindow2MatchBox,
    FanxiuDataAnnotationMacroAnnotateRequest,
    FanxiuDataAnnotationMacroAnnotateResponse,
    FanxiuDataAnnotationOcrFrameLine,
    FanxiuDataAnnotationOcrFrameWord,
    FanxiuDataAnnotationOcrFrameResponse,
)
from backend.core.fanxiu.game.ocr_utils import _extract_ocr_line_entries, _join_ocr_line_entries, _sanitize_ocr_text
from backend.core.ocr.preview import OcrPreviewError, run_paddle_ocr_preview
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


def _ocr_box_to_xywh(value: Any) -> tuple[float, float, float, float] | None:
    if isinstance(value, dict):
        if {"x", "y", "w", "h"} <= set(value):
            x = _coerce_float(value.get("x"), 0)
            y = _coerce_float(value.get("y"), 0)
            w = _coerce_float(value.get("w"), 0)
            h = _coerce_float(value.get("h"), 0)
            return (x, y, w, h) if w > 0 and h > 0 else None
        points = value.get("points") or value.get("poly") or value.get("polygon") or value.get("box")
        return _ocr_box_to_xywh(points)
    if not isinstance(value, (list, tuple)) or not value:
        return None
    if len(value) >= 4 and all(not isinstance(item, (list, tuple, dict)) for item in value[:4]):
        x1 = _coerce_float(value[0], 0)
        y1 = _coerce_float(value[1], 0)
        x2 = _coerce_float(value[2], 0)
        y2 = _coerce_float(value[3], 0)
        return (min(x1, x2), min(y1, y2), max(1.0, abs(x2 - x1)), max(1.0, abs(y2 - y1)))
    points: list[tuple[float, float]] = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        points.append((_coerce_float(item[0], 0), _coerce_float(item[1], 0)))
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), min(ys), max(1.0, max(xs) - min(xs)), max(1.0, max(ys) - min(ys)))


def _iter_ocr_word_candidates(payload: dict[str, Any]) -> list[tuple[str, Any, int | None]]:
    candidates: list[tuple[str, Any, int | None]] = []

    def add_parallel(texts: Any, boxes: Any, line_index: int | None = None) -> None:
        if isinstance(texts, str):
            text_items = list(texts)
        elif isinstance(texts, (list, tuple)):
            text_items = list(texts)
        else:
            return
        box_items = list(boxes) if isinstance(boxes, (list, tuple)) else []
        for index, text in enumerate(text_items):
            if index >= len(box_items):
                break
            clean = _sanitize_ocr_text(text)
            if clean:
                candidates.append((clean, box_items[index], line_index))

    for key in ("rec_word_infos", "rec_word_info", "word_infos", "words"):
        raw_infos = payload.get(key)
        if not isinstance(raw_infos, (list, tuple)):
            continue
        for line_index, info in enumerate(raw_infos):
            if isinstance(info, dict):
                texts = (
                    info.get("texts")
                    or info.get("words")
                    or info.get("chars")
                    or info.get("word_texts")
                    or info.get("char_texts")
                )
                boxes = (
                    info.get("boxes")
                    or info.get("word_boxes")
                    or info.get("char_boxes")
                    or info.get("polys")
                    or info.get("points")
                )
                add_parallel(texts, boxes, line_index)
            elif isinstance(info, (list, tuple)) and len(info) >= 2:
                add_parallel(info[0], info[1], line_index)

    for text_key, box_key in (
        ("rec_words", "rec_word_boxes"),
        ("rec_word_texts", "rec_word_boxes"),
        ("word_texts", "word_boxes"),
        ("char_texts", "char_boxes"),
    ):
        raw_texts = payload.get(text_key)
        raw_boxes = payload.get(box_key)
        if not isinstance(raw_texts, (list, tuple)) or not isinstance(raw_boxes, (list, tuple)):
            continue
        nested = any(isinstance(item, (list, tuple)) and not isinstance(item, str) for item in raw_texts)
        if nested:
            for line_index, (texts, boxes) in enumerate(zip(raw_texts, raw_boxes)):
                add_parallel(texts, boxes, line_index)
        else:
            add_parallel(raw_texts, raw_boxes, None)

    return candidates


def _extract_ocr_words_from_payload(payload: dict[str, Any]) -> list[FanxiuDataAnnotationOcrFrameWord]:
    words: list[FanxiuDataAnnotationOcrFrameWord] = []
    for text, raw_box, line_index in _iter_ocr_word_candidates(payload):
        box = _ocr_box_to_xywh(raw_box)
        if box is None:
            continue
        x, y, w, h = box
        words.append(
            FanxiuDataAnnotationOcrFrameWord(
                text=text,
                x=max(0.0, x),
                y=max(0.0, y),
                w=max(1.0, w),
                h=max(1.0, h),
                line_index=line_index,
            )
        )
    return words


def _recognize_data_annotation_ocr_frame(
    image_data_url: str,
    *,
    options: dict[str, Any] | None = None,
) -> FanxiuDataAnnotationOcrFrameResponse:
    temp_path: Path | None = None
    try:
        image_bytes = _decode_game_macro_data_url_to_bytes(image_data_url)
        if not image_bytes:
            return FanxiuDataAnnotationOcrFrameResponse()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as file:
            file.write(image_bytes)
            temp_path = Path(file.name)
        preview = run_paddle_ocr_preview(temp_path, shape_type="rectangle", options=options)
        document = preview.get("document") or {}
        line_entries = _extract_ocr_line_entries(document)
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
        flags = document.get("flags") if isinstance(document, dict) else {}
        payload = flags.get("paddleocr_payload") if isinstance(flags, dict) else None
        words = _extract_ocr_words_from_payload(payload) if isinstance(payload, dict) else []
        return FanxiuDataAnnotationOcrFrameResponse(lines=lines, words=words)
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
