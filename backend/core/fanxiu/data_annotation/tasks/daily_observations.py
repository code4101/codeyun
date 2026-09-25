"""日常页面观测值的纯解析契约。

输入是已取得的 OCR 文本或场景观测，输出是业务值或 None（无法识别）。零值是有效
事实，不代表识别失败。本模块不捕获画面、不执行动作、不依赖执行器；
仙府与首领任务直接复用，不再从巨型执行器导入私有解析函数。
"""
from __future__ import annotations

import re
from pyxllib.autogui import View
from typing import Any

from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.data_annotation.ocr_values import (
    FULLWIDTH_DIGIT_TRANSLATION, parse_ocr_values,
)


def parse_xianfu_visit_cd_seconds(text: Any) -> int | None:
    normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
    if not normalized:
        return None
    normalized = normalized.replace("：", ":").replace("O", "0").replace("o", "0")
    match = re.search(r"(\d{1,2}):(\d{1,2}):(\d{1,2})", normalized)
    if match:
        hours, minutes, seconds = (int(match.group(index)) for index in range(1, 4))
        return hours * 3600 + minutes * 60 + seconds
    match = re.search(r"(\d{1,2}):(\d{1,2})", normalized)
    if match:
        minutes, seconds = (int(match.group(index)) for index in range(1, 3))
        return minutes * 60 + seconds
    hours = minutes = seconds = 0
    matched = False
    for value, unit in re.findall(r"(\d{1,3})(时|小时|分|分钟|秒)", normalized):
        matched = True
        if unit in {"时", "小时"}:
            hours = int(value)
        elif unit in {"分", "分钟"}:
            minutes = int(value)
        elif unit == "秒":
            seconds = int(value)
    if matched:
        return hours * 3600 + minutes * 60 + seconds
    if "免费" in normalized and not re.search(r"\d", normalized):
        return 0
    return None


def parse_xianfu_skill_cd_seconds(text: Any) -> int | None:
    seconds = parse_xianfu_visit_cd_seconds(text)
    if seconds is not None:
        return seconds
    normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
    if not normalized:
        return None
    normalized = normalized.replace("：", ":").replace("O", "0").replace("o", "0")
    if "免费抽取" in normalized or "免费领悟" in normalized:
        return 0
    return None


def parse_daily_boss_cd_seconds(text: Any) -> int | None:
    seconds = parse_xianfu_visit_cd_seconds(text)
    if seconds is None or not 0 <= seconds <= 1800:
        return None
    return seconds


def parse_daily_boss_cd_seconds_from_six_digits(text: Any) -> int | None:
    normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
    digits = re.findall(r"\d", normalized)
    if len(digits) < 6:
        return None
    compact = "".join(digits[:6])
    hours = int(compact[:2])
    minutes = int(compact[2:4])
    seconds = int(compact[4:6])
    if minutes >= 60 or seconds >= 60:
        return None
    total_seconds = hours * 3600 + minutes * 60 + seconds
    return total_seconds if total_seconds <= 1800 else None


def parse_daily_boss_reward_remaining(text: Any) -> int | None:
    normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
    if not normalized:
        return None
    match = re.search(r"剩余奖励次数[:：]?(.*)", normalized)
    if match is None:
        return None
    tail = match.group(1)
    fraction = parse_ocr_values(tail, expected_count=2, allow_extra_numbers=True)
    if fraction is not None:
        return fraction[0]
    single = parse_ocr_values(tail, expected_count=1)
    return single[0] if single is not None else None


def parse_daily_boss_hp_percent(text: Any) -> int | None:
    normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
    matches = [int(value) for value in re.findall(r"(\d{1,3})%", normalized)]
    valid = [value for value in matches if 0 <= value <= 100]
    return min(valid) if valid else None


def parse_first_int(text: Any) -> int | None:
    normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
    values = parse_ocr_values(normalized)
    return values[0] if values is not None else None


def observed_scene_id(value: Any) -> int | None:
    """Normalize an already observed View/id without sampling or changing the game."""
    if isinstance(value, View):
        return int(value.id) if value.id is not None else None
    if hasattr(value, "id"):
        value = getattr(value, "id")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
