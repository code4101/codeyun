from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable


@dataclass(frozen=True)
class ThresholdRefundRule:
    threshold: float
    refund_amount: float


@dataclass(frozen=True)
class PercentageRefundRule:
    threshold_percent: float
    refund_amount: float


REFUND_PROGRESS_FULL_COLOR = "#80FF80"


def sheet_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    try:
        repaired = text.encode("latin1").decode("gbk")
    except UnicodeError:
        return text
    if any("\u4e00" <= char <= "\u9fff" for char in repaired):
        return repaired.strip()
    return text


def parse_compact_refund_rules(text: Any, *, default: dict[str, int] | None = None) -> dict[str, int]:
    normalized = sheet_text(text)
    match = re.search(r'["“](\d+(?:[\/／]\d+)*)["”]', normalized)
    if not match:
        return dict(default or {})

    refund_rules: dict[str, int] = {}
    for index, raw_value in enumerate(re.split(r"[\/／]", match.group(1))):
        try:
            refund_amount = int(raw_value)
        except ValueError:
            continue
        if refund_amount == 0:
            continue
        key = "当堂" if index == 0 else f"第{index}天"
        refund_rules[key] = refund_amount
    refund_rules["回放"] = 0
    return refund_rules


def parse_threshold_refund_rules(text: Any) -> list[ThresholdRefundRule]:
    normalized = sheet_text(text)
    groups = re.findall(r'["“](\d+(?:[\/／]\d+)*)["”]', normalized)
    if len(groups) < 2:
        return []
    thresholds = _parse_number_group(groups[0])
    refund_amounts = _parse_number_group(groups[1])
    result = [
        ThresholdRefundRule(threshold=threshold, refund_amount=refund_amount)
        for threshold, refund_amount in zip(thresholds, refund_amounts, strict=False)
        if threshold > 0 and refund_amount > 0
    ]
    return sorted(result, key=lambda rule: rule.threshold)


def highlight_text_refund_progress(refund_rules: dict[str, int], text: Any) -> tuple[float, str | None]:
    normalized = sheet_text(text)
    sorted_rules = sorted(refund_rules.items(), key=lambda item: item[1], reverse=True)
    refund_amount: int | None = None
    for key, amount in sorted_rules:
        if key and key in normalized:
            refund_amount = amount
            break
    if refund_amount is None:
        return 0, None

    positive_amounts = _positive_amounts(refund_rules.values())
    color = refund_progress_color(
        refund_amount,
        positive_amounts=positive_amounts,
        progress_weight=progress_weight_from_percent_text(normalized),
    )
    return refund_amount, color


def highlight_threshold_refund_progress(rules: list[ThresholdRefundRule], value: Any) -> tuple[float, str | None]:
    numeric_value = _to_float(value)
    if numeric_value <= 0 or not rules:
        return 0, None

    refund_amount = 0.0
    for rule in rules:
        if numeric_value >= rule.threshold:
            refund_amount = rule.refund_amount

    max_threshold = max(rule.threshold for rule in rules)
    progress_weight = numeric_value / max_threshold * 100 if max_threshold > 0 else 100
    color = refund_progress_color(
        refund_amount,
        positive_amounts=[rule.refund_amount for rule in rules],
        progress_weight=progress_weight,
    )
    return refund_amount, color


def highlight_percentage_refund_progress(rules: list[PercentageRefundRule], text: Any) -> tuple[float, str | None]:
    progress_percent = parse_progress_percent(text)
    if progress_percent is None or progress_percent <= 0 or not rules:
        return 0, None

    refund_amount = 0.0
    for rule in sorted(rules, key=lambda item: item.threshold_percent):
        if progress_percent >= rule.threshold_percent:
            refund_amount = rule.refund_amount

    color = refund_progress_color(
        refund_amount,
        positive_amounts=[rule.refund_amount for rule in rules],
        progress_weight=progress_percent,
    )
    return refund_amount, color


def highlight_presence_progress(text: Any) -> str | None:
    progress_percent = parse_progress_percent(text)
    if progress_percent is None or progress_percent <= 0:
        return None
    if progress_percent >= 100:
        return REFUND_PROGRESS_FULL_COLOR
    return partial_refund_progress_color(progress_percent / 100)


def refund_progress_color(
    refund_amount: float,
    *,
    positive_amounts: Iterable[float],
    progress_weight: float = 100,
) -> str | None:
    amounts = _positive_amounts(positive_amounts)
    if refund_amount <= 0 or not amounts:
        return None

    max_refund = amounts[0]
    if refund_amount >= max_refund:
        return REFUND_PROGRESS_FULL_COLOR
    return partial_refund_progress_color(refund_amount / max_refund)


def partial_refund_progress_color(ratio: float) -> str:
    normalized = max(0.0, min(float(ratio), 1.0))
    return rgb_to_hex([255, 255, 255 * (1 - normalized)])


def progress_weight_from_percent_text(text: Any) -> float:
    return parse_progress_percent(text) or 100.0


def parse_progress_percent(text: Any) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", sheet_text(text))
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def rgb_to_hex(color: Iterable[float]) -> str:
    values = [max(0, min(255, int(round(component)))) for component in color]
    return "#" + "".join(f"{value:02X}" for value in values)


def set_cell_background(
    cell_meta: dict[str, Any],
    *,
    document_row: int,
    column_index: int,
    color: str | None,
) -> bool:
    key = f"{document_row}:{column_index}"
    previous_meta = cell_meta.get(key)
    next_meta = dict(previous_meta) if isinstance(previous_meta, dict) else {}
    style = dict(next_meta.get("style")) if isinstance(next_meta.get("style"), dict) else {}
    previous_color = style.get("background_color")

    if color:
        style["background_color"] = color
    else:
        style.pop("background_color", None)

    if style:
        next_meta["style"] = style
    else:
        next_meta.pop("style", None)

    if next_meta:
        cell_meta[key] = next_meta
    else:
        cell_meta.pop(key, None)
    return previous_color != color


def _parse_number_group(text: str) -> list[float]:
    result = []
    for item in re.split(r"[\/／]", text):
        try:
            result.append(float(item))
        except ValueError:
            continue
    return result


def _positive_amounts(values: Iterable[float]) -> list[float]:
    return sorted({float(value) for value in values if float(value) > 0}, reverse=True)


def _to_float(value: Any) -> float:
    if isinstance(value, int | float):
        return float(value)
    text = sheet_text(value).replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return 0.0
    try:
        return float(match.group(0))
    except ValueError:
        return 0.0
