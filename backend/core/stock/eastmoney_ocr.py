from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OcrLineEntry:
    text: str
    x: float
    x2: float
    y: float
    height: float


_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "发生日期": ("发生日期",),
    "发生时间": ("发生时间",),
    "名称": ("证券名称", "股票名称", "名称"),
    "代码": ("证券代码", "股票代码", "代码"),
    "币种": ("交易币种", "结算币种", "币种"),
    "买卖类别": ("买卖类别", "买卖类別", "买卖类型", "交易类别", "业务名称"),
    "发生金额": ("发生金额", "资金发生金额", "净发生额"),
    "成交金额": ("成交金额", "成交金额(元)", "成交额"),
    "成交数量": ("成交数量", "成交股数", "发生数量"),
    "成交价格": ("成交价格", "成交均价"),
    "印花税": ("印花税",),
    "过户费": ("过户费",),
    "佣金": ("佣金",),
    "其他费用": ("其他费用", "其他费"),
    "流水号": ("流水号", "成交编号", "合同编号", "委托编号", "业务编号"),
    "股东账号": ("股东账号", "股东代码"),
    "股份余额": ("股份余额", "股票余额", "证券余额"),
    "资金余额": ("资金余额",),
    "扩位简称": ("扩位简称",),
}
_ALIAS_TO_FIELD = {
    alias: field
    for field, aliases in _FIELD_ALIASES.items()
    for alias in aliases
}
_FIELD_PATTERN = re.compile(
    "|".join(re.escape(alias) for alias in sorted(_ALIAS_TO_FIELD, key=len, reverse=True))
)
_REQUIRED_FIELDS = ("发生日期", "发生时间", "代码", "名称", "买卖类别", "成交数量", "成交价格", "成交金额")
_FULLWIDTH_TRANSLATION = str.maketrans(
    {
        "０": "0",
        "１": "1",
        "２": "2",
        "３": "3",
        "４": "4",
        "５": "5",
        "６": "6",
        "７": "7",
        "８": "8",
        "９": "9",
        "．": ".",
        "－": "-",
        "＋": "+",
        "，": ",",
        "：": ":",
        "（": "(",
        "）": ")",
    }
)


def parse_mobile_trade_detail_from_ocr_document(preview_document: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    line_entries = extract_ocr_line_entries(preview_document)
    lines = ["".join(entry.text for entry in group) for group in line_entries]
    normalized_lines = [line for line in lines if line]
    return parse_mobile_trade_detail_lines(normalized_lines), normalized_lines


def parse_mobile_trade_detail_lines(lines: list[str]) -> dict[str, str]:
    row: dict[str, str] = {}
    for line in lines:
        for field, value in _extract_line_fields(line).items():
            if value or not row.get(field):
                row[field] = value

    row = {field: _normalize_field_value(field, value) for field, value in row.items() if str(value).strip()}
    missing = [field for field in _REQUIRED_FIELDS if not row.get(field)]
    if missing:
        raise ValueError(f"未能从截图中识别完整交易明细，缺少：{'、'.join(missing)}")
    return row


def extract_ocr_line_entries(preview_document: dict[str, Any]) -> list[list[OcrLineEntry]]:
    raw_shapes = preview_document.get("shapes") or []
    if not isinstance(raw_shapes, list):
        return []

    entries: list[OcrLineEntry] = []
    for shape in raw_shapes:
        if not isinstance(shape, dict):
            continue
        text = _extract_shape_text(shape)
        if not text:
            continue
        rectangle = _extract_shape_rectangle(shape.get("points"))
        if rectangle is None:
            continue
        x1, y1, x2, y2 = rectangle
        entries.append(
            OcrLineEntry(
                text=text,
                x=x1,
                x2=x2,
                y=(y1 + y2) / 2,
                height=max(y2 - y1, 1),
            )
        )

    if not entries:
        return []

    entries.sort(key=lambda item: (item.y, item.x))
    heights = sorted(entry.height for entry in entries)
    median_height = heights[len(heights) // 2]
    tolerance = max(12.0, median_height * 0.75)

    grouped: list[list[OcrLineEntry]] = []
    current_group: list[OcrLineEntry] = []
    current_y = 0.0
    for entry in entries:
        if not current_group:
            current_group = [entry]
            current_y = entry.y
            continue

        if abs(entry.y - current_y) <= tolerance:
            current_group.append(entry)
            current_y = sum(item.y for item in current_group) / len(current_group)
            continue

        grouped.append(sorted(current_group, key=lambda item: item.x))
        current_group = [entry]
        current_y = entry.y

    if current_group:
        grouped.append(sorted(current_group, key=lambda item: item.x))
    return grouped


def _extract_line_fields(line: str) -> dict[str, str]:
    text = _sanitize_ocr_text(line)
    matches = list(_FIELD_PATTERN.finditer(text))
    if not matches:
        return {}

    fields: dict[str, str] = {}
    for index, match in enumerate(matches):
        field = _ALIAS_TO_FIELD[match.group(0)]
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        value = _strip_field_value(text[start:end])
        if value or field not in fields:
            fields[field] = value
    return fields


def _extract_shape_text(shape: dict[str, Any]) -> str:
    raw_label = shape.get("label")
    if isinstance(raw_label, str):
        try:
            payload = json.loads(raw_label)
        except json.JSONDecodeError:
            return _sanitize_ocr_text(raw_label)
        if isinstance(payload, dict):
            return _sanitize_ocr_text(payload.get("text"))
    return ""


def _extract_shape_rectangle(points: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(points, list) or len(points) < 2:
        return None

    flattened: list[tuple[float, float]] = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            return None
        try:
            flattened.append((float(point[0]), float(point[1])))
        except (TypeError, ValueError):
            return None

    xs = [item[0] for item in flattened]
    ys = [item[1] for item in flattened]
    return min(xs), min(ys), max(xs), max(ys)


def _sanitize_ocr_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).translate(_FULLWIDTH_TRANSLATION)


def _strip_field_value(value: str) -> str:
    return re.sub(r"^[：:，,;；|｜/\\-]+", "", str(value or "")).strip()


def _normalize_field_value(field: str, value: str) -> str:
    text = _strip_field_value(_sanitize_ocr_text(value))
    if field == "发生日期":
        match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
        if match:
            return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    if field == "发生时间":
        match = re.search(r"(\d{1,2}):(\d{2}):(\d{2})", text)
        if match:
            return f"{int(match.group(1)):02d}:{match.group(2)}:{match.group(3)}"
    return text
