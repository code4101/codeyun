"""周常活跃度的帧内 OCR 布局与图像状态解析，不负责读取画面或点击。"""
from __future__ import annotations
import base64
import re
from typing import Any, Mapping
from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.data_annotation.ocr_spatial import group_ocr_tokens, query_spatial_ocr
from backend.core.fanxiu.data_annotation.ocr_values import FULLWIDTH_DIGIT_TRANSLATION
from backend.core.fanxiu.catalog.weekly_activity import WEEKLY_ACTIVITY_REWARD_MILESTONES


WEEKLY_ACTIVITY_REWARD_Y_RATIO = 270.0 / 1600.0

WEEKLY_ACTIVITY_LABEL_BAND = (0.205, 0.245)

def weekly_activity_pending_badge_present(
    tokens: list[dict[str, Any]],
    *,
    frame_width: int,
    frame_height: int,
) -> bool:
    """Return whether the selected 周常 tab still shows its local ``领`` badge."""

    badge_box = {
        "x": frame_width * 0.82,
        "y": frame_height * 0.84,
        "w": frame_width * 0.16,
        "h": frame_height * 0.09,
    }
    spatial = query_spatial_ocr(tokens or [], badge_box)
    return any(
        _sanitize_ocr_text(fragment.get("text")) == "领"
        for fragment in spatial.get("fragments") or []
        if isinstance(fragment, dict)
    )

def weekly_activity_reward_layout_from_ocr(
    tokens: list[dict[str, Any]],
    *,
    frame_width: int,
    frame_height: int,
) -> dict[int, dict[str, Any]]:
    """Map the currently visible #402 milestone labels to their reward icons.

    The reward rail scrolls horizontally as activity grows, so a screen x
    coordinate never identifies a fixed milestone.  The numeric labels under
    the rail are the frame-local source of truth; their x centres project
    vertically to the icons above them.
    """

    if frame_width <= 0 or frame_height <= 0:
        raise RuntimeError("周常_活跃度：#402 当前帧尺寸无效")
    min_y = frame_height * WEEKLY_ACTIVITY_LABEL_BAND[0]
    max_y = frame_height * WEEKLY_ACTIVITY_LABEL_BAND[1]
    min_x = frame_width * 0.25
    max_x = frame_width * 0.96
    layout: dict[int, dict[str, Any]] = {}
    for token in group_ocr_tokens(tokens or []):
        if not isinstance(token, dict):
            continue
        text = str(token.get("text") or "").translate(FULLWIDTH_DIGIT_TRANSLATION)
        match = re.fullmatch(r"\s*([1-9]\d{2,3})\s*", text)
        if match is None:
            continue
        x = float(token.get("x") or 0)
        y = float(token.get("y") or 0)
        w = float(token.get("w") or 0)
        h = float(token.get("h") or 0)
        center_x = x + w / 2
        center_y = y + h / 2
        milestone = int(match.group(1))
        if (
            w <= 0
            or h <= 0
            or not min_x <= center_x <= max_x
            or not min_y <= center_y <= max_y
            or milestone % 100 != 0
        ):
            continue
        if milestone not in WEEKLY_ACTIVITY_REWARD_MILESTONES:
            # 横向滚动时单帧 OCR 可能把 1200 漏读为 200。让调用方取
            # 新帧复核；持续未知仍报错，不能把未知档位当作领取事实。
            raise RuntimeError(f"周常_活跃度：奖励轨道档位标签识别到未知档 {milestone}")
        if milestone in layout:
            raise RuntimeError(f"周常_活跃度：档位标签 {milestone} OCR 重复，拒绝投影")
        layout[milestone] = {
            "point": (center_x, frame_height * WEEKLY_ACTIVITY_REWARD_Y_RATIO),
            "label_box": (x, y, w, h),
        }

    ordered = sorted(layout.items(), key=lambda item: item[1]["point"][0])
    if not ordered:
        raise RuntimeError("周常_活跃度：未识别到奖励轨道档位标签，拒绝使用固定坐标")
    milestones = [milestone for milestone, _row in ordered]
    if milestones != sorted(milestones) or len(milestones) < 2:
        raise RuntimeError(f"周常_活跃度：奖励轨道档位标签不完整或顺序异常：{milestones}")
    return dict(ordered)

def detect_weekly_activity_reward_states(
    frame_data_url: str,
    reward_layout: Mapping[int, Mapping[str, Any]],
) -> dict[int, dict[str, Any]]:
    """Classify the frame-local #402 milestones as claimed/claimable/unknown."""

    import cv2
    import numpy as np

    payload = str(frame_data_url or "")
    if "," not in payload:
        raise RuntimeError("周常_活跃度：#402 当前帧不是有效 data URL")
    try:
        image = cv2.imdecode(
            np.frombuffer(base64.b64decode(payload.split(",", 1)[1]), dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
    except Exception as exc:
        raise RuntimeError(f"周常_活跃度：#402 当前帧解码失败：{exc}") from exc
    if image is None or image.size == 0:
        raise RuntimeError("周常_活跃度：#402 当前帧解码为空")

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    height, width = hsv.shape[:2]
    radius = max(18, int(round(width * 48.0 / 900.0)))
    states: dict[int, dict[str, Any]] = {}
    for milestone, layout_row in reward_layout.items():
        point = layout_row.get("point")
        if not isinstance(point, (tuple, list)) or len(point) != 2:
            raise RuntimeError(f"周常_活跃度：{milestone} 档缺少 OCR 投影点")
        x = int(round(float(point[0])))
        y = int(round(float(point[1])))
        x1, x2 = max(0, x - radius), min(width, x + radius)
        y1, y2 = max(0, y - radius), min(height, y + radius)
        crop = hsv[y1:y2, x1:x2]
        if crop.size == 0:
            raise RuntimeError(f"周常_活跃度：{milestone} 档奖励框超出当前帧")
        green = cv2.inRange(crop, (35, 50, 50), (100, 255, 255))
        bright_gold = cv2.inRange(crop, (10, 20, 180), (40, 255, 255))
        green_ratio = float(np.count_nonzero(green)) / float(green.size)
        bright_gold_ratio = float(np.count_nonzero(bright_gold)) / float(bright_gold.size)
        if green_ratio >= 0.05:
            state = "claimed"
        elif bright_gold_ratio >= 0.25:
            state = "claimable"
        else:
            state = "unknown"
        states[milestone] = {
            "state": state,
            "point": (float(x), float(y)),
            "green_ratio": green_ratio,
            "bright_gold_ratio": bright_gold_ratio,
        }
    return states
