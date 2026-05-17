from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.yuque_html import normalize_legacy_yuque_lake_html


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

USER_ID = 2
TZ = dt.timezone(dt.timedelta(hours=8))
IMPORT_NAME = "codex-cli-calendar-table-expansion-v1"
EXPANSION_SOURCE = "calendar-table-expansion"
WEEKDAYS = {
    "一": 0,
    "二": 1,
    "三": 2,
    "四": 3,
    "五": 4,
    "六": 5,
    "日": 6,
    "天": 6,
}
WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
WEEK_CODE_RE = re.compile(r"\bw?(\d{6})\b", re.IGNORECASE)
DAY_LABEL_RE = re.compile(r"\bw?(\d{6})\s*(?:周[一二三四五六日天])?", re.IGNORECASE)
INLINE_STYLE_RE = re.compile(r"([a-zA-Z-]+)\s*:\s*([^;]+)")
RESOURCE_SOURCE_TITLE_RE = re.compile(
    r"(?:密码仓库|密码表|软件注册码|注册码|用户码|账号|账户|密钥|license|serial)",
    re.IGNORECASE,
)
RESOURCE_KEYWORD_RE = re.compile(
    r"(?:密码|注册码|用户码|账号|账户|密钥|license|serial|绑定邮箱|Google账户)",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
SECRET_TOKEN_RE = re.compile(r"(?=[A-Za-z0-9+/=_@#$%^&*|~`?.-]{12,})(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9+/=_@#$%^&*|~`?.-]+")
RED_COLOR_RE = re.compile(
    r"^(?:red|#f00|#ff0000|rgb\(\s*255\s*,\s*0\s*,\s*0\s*\)|rgba\(\s*255\s*,\s*0\s*,\s*0\s*,\s*(?:1|1\.0)\s*\))$",
    re.IGNORECASE,
)
ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\ufeff\u2060]")


@dataclass
class GridCell:
    cell: Tag
    origin_row: int
    origin_col: int
    row_span: int
    col_span: int
    is_origin: bool


@dataclass
class CalendarItem:
    source_note_id: str
    source_numeric_id: int | None
    source_title: str
    source_origin: str
    source_import: str
    source_doc_key: str
    source_key_prefix: str
    table_index: int
    row_index: int
    col_index: int
    pattern: str
    date: dt.date
    column_label: str
    week_label: str
    cell: Tag
    content_html: str
    text: str
    weight: int
    style_flags: dict[str, bool]
    category: str
    note_categories: str
    note_types: str
    note_form: str
    private_level: int
    color: str | None


def default_data_dir() -> Path:
    return Path(os.environ.get("CODEYUN_DATA_DIR", r"D:\home\chenkunze\data\m2603codeyun\codepc_mf"))


def default_yuque_remaining_candidates() -> Path:
    return Path(os.environ["TEMP"]) / "codeyun_yuque_remaining_docs" / "candidates.json"


def db_path(data_dir: Path) -> Path:
    return data_dir / "codeyun.db"


def safe_json_loads(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return default


def custom_field_value(custom_fields: list[Any], key: str) -> Any:
    for item in custom_fields:
        if isinstance(item, list) and len(item) >= 3 and item[0] == key:
            return item[2]
        if isinstance(item, dict) and item.get("key") == key:
            return item.get("value")
    return None


def note_categories(category: str) -> str:
    return json.dumps([{"key": category, "weight": 100}], ensure_ascii=False)


def parse_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def parse_span(value: Any) -> int:
    return max(1, parse_int(value, 1))


def text_of(node: Any, separator: str = " ") -> str:
    if not node:
        return ""
    text = node.get_text(separator, strip=True)
    text = ZERO_WIDTH_RE.sub("", text.replace("\xa0", " "))
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def compact_text(text: str, limit: int = 54) -> str:
    value = ZERO_WIDTH_RE.sub("", (text or "").replace("\xa0", " "))
    value = re.sub(r"\s+", " ", value).strip(" ，,。；;、")
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def content_is_empty(cell: Tag) -> bool:
    if cell.find(["img", "video", "audio", "iframe", "object", "embed"]):
        return False
    if cell.find("a", href=True) and text_of(cell):
        return False
    text = text_of(cell)
    if not text:
        return True
    return text in {"-", "—", "----"} and not cell.find(["strong", "b", "span", "a"])


def parse_css(style: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, value in INLINE_STYLE_RE.findall(str(style or "")):
        result[name.lower()] = value.strip()
    return result


def style_for_cell_wrapper(cell: Tag) -> str:
    styles = parse_css(cell.get("style"))
    kept: list[str] = []
    for key in ["background", "background-color", "color", "text-align", "vertical-align"]:
        if styles.get(key):
            kept.append(f"{key}:{styles[key]}")
    if cell.get("bgcolor") and not any(item.startswith("background") for item in kept):
        kept.append(f"background-color:{cell.get('bgcolor')}")
    kept.extend([
        "padding:6px 8px",
        "line-height:1.55",
        "white-space:pre-wrap",
        "tab-size:4",
    ])
    return ";".join(kept)


def normalize_cell_html(cell: Tag) -> str:
    inner = "".join(str(child) for child in cell.contents).strip()
    if not inner:
        inner = html.escape(text_of(cell, "\n"))
    normalized = normalize_legacy_yuque_lake_html(inner)
    soup = BeautifulSoup(normalized or "", "html.parser")
    for link in soup.find_all("a"):
        href = str(link.get("href") or "").strip()
        if not href:
            continue
        link["target"] = "_blank"
        link["rel"] = "noopener noreferrer"
    body = "".join(str(child) for child in soup.contents).strip()
    if not body:
        body = "<p><br></p>"
    wrapper_style = style_for_cell_wrapper(cell)
    return f'<div class="source-calendar-cell" style="{html.escape(wrapper_style, quote=True)}">{body}</div>'


def color_is_red(value: str) -> bool:
    return bool(RED_COLOR_RE.match(value.strip()))


def tag_has_red_text(tag: Tag) -> bool:
    styles = parse_css(tag.get("style"))
    color = styles.get("color", "")
    return color_is_red(color)


def tag_has_bold(tag: Tag) -> bool:
    if tag.name in {"strong", "b"}:
        return True
    styles = parse_css(tag.get("style"))
    weight = styles.get("font-weight", "").lower()
    if weight in {"bold", "bolder"}:
        return True
    if weight.isdigit() and int(weight) >= 600:
        return True
    return False


def style_flags(cell: Tag) -> dict[str, bool]:
    has_red = False
    has_bold = False
    has_link = False
    has_underline = False
    for tag in [cell, *cell.find_all(True)]:
        if isinstance(tag, Tag):
            has_red = has_red or tag_has_red_text(tag)
            has_bold = has_bold or tag_has_bold(tag)
            has_link = has_link or (tag.name == "a" and bool(tag.get("href")))
            styles = parse_css(tag.get("style"))
            has_underline = has_underline or styles.get("text-decoration", "").lower().find("underline") >= 0
    return {
        "red": has_red,
        "bold": has_bold,
        "link": has_link,
        "underline": has_underline,
    }


def weight_from_flags(flags: dict[str, bool]) -> int:
    if flags.get("red") and flags.get("bold"):
        return 3
    if flags.get("red"):
        return 2
    if flags.get("bold") and (flags.get("link") or flags.get("underline")):
        return 2
    if flags.get("bold") or flags.get("link") or flags.get("underline"):
        return 1
    return 0


def yymmdd_date(code: str) -> dt.date | None:
    try:
        return dt.date(2000 + int(code[:2]), int(code[2:4]), int(code[4:6]))
    except ValueError:
        return None


def date_timestamp(day: dt.date) -> float:
    return dt.datetime(day.year, day.month, day.day, 12, 0, 0, tzinfo=TZ).timestamp()


def week_label_for_date(day: dt.date) -> str:
    return WEEKDAY_LABELS[day.weekday()]


def build_grid(table: Tag) -> list[list[GridCell | None]]:
    rows: list[list[GridCell | None]] = []
    pending: dict[tuple[int, int], GridCell] = {}
    trs = table.find_all("tr")
    for row_index, tr in enumerate(trs):
        row: list[GridCell | None] = []
        col_index = 0

        def fill_pending() -> None:
            nonlocal col_index
            while (row_index, col_index) in pending:
                row.append(pending.pop((row_index, col_index)))
                col_index += 1

        fill_pending()
        for cell in tr.find_all(["td", "th"], recursive=False):
            fill_pending()
            row_span = parse_span(cell.get("rowspan"))
            col_span = parse_span(cell.get("colspan"))
            origin = GridCell(cell, row_index, col_index, row_span, col_span, True)
            for dc in range(col_span):
                current = origin if dc == 0 else GridCell(cell, row_index, col_index, row_span, col_span, False)
                row.append(current)
                for dr in range(1, row_span):
                    pending[(row_index + dr, col_index + dc)] = GridCell(
                        cell, row_index, col_index, row_span, col_span, False
                    )
                col_index += 1
            fill_pending()
        while (row_index, col_index) in pending:
            row.append(pending.pop((row_index, col_index)))
            col_index += 1
        rows.append(row)
    return rows


def weekday_from_header(text: str) -> int | None:
    compact = re.sub(r"\s+", "", text)
    match = re.fullmatch(r"(?:周|星期)([一二三四五六日天])", compact)
    if not match:
        return None
    return WEEKDAYS[match.group(1)]


def find_weekday_header(grid: list[list[GridCell | None]]) -> tuple[int, dict[int, int]] | None:
    best: tuple[int, dict[int, int]] | None = None
    for row_index, row in enumerate(grid):
        columns: dict[int, int] = {}
        for col_index, item in enumerate(row):
            if not item or not item.is_origin:
                continue
            weekday = weekday_from_header(text_of(item.cell))
            if weekday is not None:
                columns[col_index] = weekday
        if len(columns) >= 3:
            best = (row_index, columns)
            break
    return best


def is_resource_like_table(source_title: str, table: Tag) -> bool:
    title = str(source_title or "")
    if RESOURCE_SOURCE_TITLE_RE.search(title):
        return True
    text = text_of(table)
    if not RESOURCE_KEYWORD_RE.search(text):
        return False
    token_count = len(EMAIL_RE.findall(text)) + len(SECRET_TOKEN_RE.findall(text))
    keyword_count = len(RESOURCE_KEYWORD_RE.findall(text))
    return token_count >= 2 and keyword_count >= 2


def week_code_from_row(row: list[GridCell | None], max_col: int | None = None) -> str | None:
    limit = len(row) if max_col is None else min(len(row), max_col)
    for item in row[:limit]:
        if not item:
            continue
        match = WEEK_CODE_RE.search(text_of(item.cell))
        if match:
            return match.group(1)
    return None


def day_code_from_row(row: list[GridCell | None]) -> tuple[int, str] | None:
    for col_index, item in enumerate(row[:4]):
        if not item:
            continue
        match = DAY_LABEL_RE.search(text_of(item.cell))
        if match:
            return col_index, match.group(1)
    return None


def header_labels_before_row(grid: list[list[GridCell | None]], row_index: int) -> dict[int, str]:
    for prev_index in range(row_index - 1, -1, -1):
        row = grid[prev_index]
        if day_code_from_row(row):
            continue
        labels: dict[int, str] = {}
        for col_index, item in enumerate(row):
            if not item or not item.is_origin:
                continue
            label = text_of(item.cell)
            if label:
                labels[col_index] = compact_text(label, 28)
        if labels:
            return labels
    return {}


def item_common(
    *,
    source_row: sqlite3.Row,
    source_fields: dict[str, Any],
    source_doc_key: str,
    source_key_prefix: str,
    table_index: int,
    row_index: int,
    col_index: int,
    pattern: str,
    day: dt.date,
    column_label: str,
    week_label: str,
    cell: Tag,
) -> CalendarItem | None:
    if content_is_empty(cell):
        return None
    text = text_of(cell, "\n")
    flags = style_flags(cell)
    category = str(source_row["primary_category"] or source_row["node_type"] or "general")
    note_cats = source_row["note_categories"] or source_row["note_types"] or note_categories(category)
    note_types = source_row["note_types"] or note_cats
    source_numeric = source_row["numeric_id"] if "numeric_id" in source_row.keys() else None
    return CalendarItem(
        source_note_id=str(source_row["id"]),
        source_numeric_id=int(source_numeric) if source_numeric is not None else None,
        source_title=str(source_row["title"] or ""),
        source_origin=str(source_fields.get("source") or ""),
        source_import=str(source_fields.get("source_import") or ""),
        source_doc_key=source_doc_key,
        source_key_prefix=source_key_prefix,
        table_index=table_index,
        row_index=row_index,
        col_index=col_index,
        pattern=pattern,
        date=day,
        column_label=column_label,
        week_label=week_label,
        cell=cell,
        content_html=normalize_cell_html(cell),
        text=text,
        weight=weight_from_flags(flags),
        style_flags=flags,
        category=category,
        note_categories=note_cats,
        note_types=note_types,
        note_form=str(source_row["note_form"] or "note"),
        private_level=parse_int(source_row["private_level"], 0),
        color=source_row["color"],
    )


def extract_weekday_matrix_items(
    source_row: sqlite3.Row,
    source_fields: dict[str, Any],
    source_doc_key: str,
    source_key_prefix: str,
    table: Tag,
    table_index: int,
) -> list[CalendarItem]:
    grid = build_grid(table)
    header = find_weekday_header(grid)
    if not header:
        return []
    header_row, weekday_cols = header
    min_weekday_col = min(weekday_cols)
    items: list[CalendarItem] = []
    for row_index, row in enumerate(grid):
        if row_index <= header_row:
            continue
        code = week_code_from_row(row, min_weekday_col)
        if not code:
            continue
        monday = yymmdd_date(code)
        if not monday:
            continue
        for col_index, weekday in weekday_cols.items():
            if col_index >= len(row):
                continue
            grid_cell = row[col_index]
            if not grid_cell or not grid_cell.is_origin:
                continue
            day = monday + dt.timedelta(days=weekday)
            item = item_common(
                source_row=source_row,
                source_fields=source_fields,
                source_doc_key=source_doc_key,
                source_key_prefix=source_key_prefix,
                table_index=table_index,
                row_index=row_index,
                col_index=col_index,
                pattern="weekday_matrix",
                day=day,
                column_label=WEEKDAY_LABELS[weekday],
                week_label=f"w{code}",
                cell=grid_cell.cell,
            )
            if item:
                items.append(item)
    return items


def extract_daily_row_items(
    source_row: sqlite3.Row,
    source_fields: dict[str, Any],
    source_doc_key: str,
    source_key_prefix: str,
    table: Tag,
    table_index: int,
) -> list[CalendarItem]:
    grid = build_grid(table)
    if find_weekday_header(grid):
        return []
    items: list[CalendarItem] = []
    active_day: dt.date | None = None
    active_date_col = 0
    for row_index, row in enumerate(grid):
        date_match = day_code_from_row(row)
        if date_match:
            active_date_col, code = date_match
            active_day = yymmdd_date(code)
        if not active_day:
            continue
        labels = header_labels_before_row(grid, row_index)
        for col_index, grid_cell in enumerate(row):
            if col_index <= active_date_col:
                continue
            if not grid_cell or not grid_cell.is_origin:
                continue
            label = labels.get(col_index, "")
            item = item_common(
                source_row=source_row,
                source_fields=source_fields,
                source_doc_key=source_doc_key,
                source_key_prefix=source_key_prefix,
                table_index=table_index,
                row_index=row_index,
                col_index=col_index,
                pattern="daily_rows",
                day=active_day,
                column_label=label,
                week_label=f"w{active_day:%y%m%d}",
                cell=grid_cell.cell,
            )
            if item:
                items.append(item)
    return items


def custom_fields_map(raw: str | None) -> dict[str, Any]:
    rows = safe_json_loads(raw, [])
    result: dict[str, Any] = {}
    if isinstance(rows, dict):
        return rows
    for item in rows if isinstance(rows, list) else []:
        if isinstance(item, list) and len(item) >= 3:
            result[str(item[0])] = item[2]
        elif isinstance(item, dict) and item.get("key"):
            result[str(item["key"])] = item.get("value")
    return result


def source_doc_key_for(row: sqlite3.Row, fields: dict[str, Any]) -> str:
    value = fields.get("source_doc_key") or fields.get("source_key") or row["id"]
    text = str(value)
    if fields.get("source_doc_key"):
        return text
    if text.startswith("calendar-table:"):
        return text
    parts = text.split("/")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return "/".join(parts[:2])
    return text


def source_key_prefix_for(row: sqlite3.Row, fields: dict[str, Any]) -> str:
    doc_key = source_doc_key_for(row, fields)
    if doc_key:
        return doc_key
    return str(row["numeric_id"] or row["id"])


def load_yuque_candidate_html(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text("utf-8"))
    result: dict[str, str] = {}
    for item in payload.get("candidates", []):
        key = str(item.get("source_key") or "")
        content = str(item.get("content") or "")
        if key and content:
            result[key] = content
    return result


def source_html_for_row(row: sqlite3.Row, fields: dict[str, Any], yuque_html: dict[str, str]) -> tuple[str, str]:
    doc_key = source_doc_key_for(row, fields)
    source = str(fields.get("source") or "")
    if source == "yuque-desktop-cache" and doc_key in yuque_html:
        return normalize_legacy_yuque_lake_html(yuque_html[doc_key]), "yuque_candidate"
    return str(row["content"] or ""), "db_content"


def source_rows(con: sqlite3.Connection, source_note_id: str | None = None) -> list[sqlite3.Row]:
    if source_note_id:
        return con.execute(
            """
            select id,numeric_id,title,content,start_at,weight,custom_fields,node_type,note_types,
                   note_categories,primary_category,note_form,private_level,color
            from notenode
            where user_id=? and (id=? or cast(numeric_id as text)=?)
            """,
            (USER_ID, source_note_id, source_note_id),
        ).fetchall()
    return con.execute(
        """
        select id,numeric_id,title,content,start_at,weight,custom_fields,node_type,note_types,
               note_categories,primary_category,note_form,private_level,color
        from notenode
        where user_id=? and content like '%<table%'
        """,
        (USER_ID,),
    ).fetchall()


def extract_items_from_row(row: sqlite3.Row, yuque_html: dict[str, str]) -> tuple[list[CalendarItem], str]:
    fields = custom_fields_map(row["custom_fields"])
    doc_key = source_doc_key_for(row, fields)
    key_prefix = source_key_prefix_for(row, fields)
    html_text, html_source = source_html_for_row(row, fields, yuque_html)
    soup = BeautifulSoup(html_text or "", "html.parser")
    items: list[CalendarItem] = []
    for table_index, table in enumerate(soup.find_all("table")):
        if is_resource_like_table(str(row["title"] or ""), table):
            continue
        matrix_items = extract_weekday_matrix_items(row, fields, doc_key, key_prefix, table, table_index)
        if matrix_items:
            items.extend(matrix_items)
            continue
        items.extend(extract_daily_row_items(row, fields, doc_key, key_prefix, table, table_index))
    return items, html_source


def filter_date_range(
    items: list[CalendarItem],
    *,
    min_year: int,
    max_year: int,
) -> tuple[list[CalendarItem], int]:
    kept = [item for item in items if min_year <= item.date.year <= max_year]
    return kept, len(items) - len(kept)


def item_source_key(item: CalendarItem) -> str:
    return (
        f"calendar-table:{item.source_key_prefix}:"
        f"t{item.table_index}:r{item.row_index}:c{item.col_index}"
    )


def item_text_hash(item: CalendarItem) -> str:
    return hashlib.sha1(item.content_html.encode("utf-8")).hexdigest()


def title_for_item(item: CalendarItem) -> str:
    label = item.column_label.strip()
    excerpt = compact_text(item.text)
    if label and not label.startswith("周"):
        compact_label = compact_text(label, 40)
        if excerpt and normalized_dedupe_text(item).startswith(re.sub(r"\s+", "", label)[:80]):
            return excerpt
        return f"{compact_label}: {excerpt}" if excerpt else compact_label
    if excerpt:
        return excerpt
    return f"{item.date:%Y-%m-%d} {week_label_for_date(item.date)}"


def fields_for_item(item: CalendarItem) -> str:
    flags = item.style_flags
    rows: list[list[Any]] = [
        ["source", "string", EXPANSION_SOURCE],
        ["source_import", "string", IMPORT_NAME],
        ["source_kind", "string", "calendar_table_cell"],
        ["source_key", "string", item_source_key(item)],
        ["source_doc_key", "string", item.source_doc_key],
        ["source_parent_note_id", "string", item.source_note_id],
        ["source_parent_numeric_id", "number", item.source_numeric_id or 0],
        ["source_parent_title", "string", item.source_title],
        ["source_pattern", "string", item.pattern],
        ["source_date", "string", item.date.isoformat()],
        ["source_week_label", "string", item.week_label],
        ["source_column", "string", item.column_label],
        ["source_table_index", "number", item.table_index],
        ["source_row_index", "number", item.row_index],
        ["source_col_index", "number", item.col_index],
        ["source_content_hash", "string", item_text_hash(item)],
        ["source_rich_weight", "number", item.weight],
        ["source_has_red", "number", 1 if flags.get("red") else 0],
        ["source_has_bold", "number", 1 if flags.get("bold") else 0],
        ["source_has_link", "number", 1 if flags.get("link") else 0],
        ["source_has_underline", "number", 1 if flags.get("underline") else 0],
    ]
    if flags.get("red") and flags.get("bold"):
        reason = "red+bold"
    elif flags.get("red"):
        reason = "red"
    elif flags.get("bold") and (flags.get("link") or flags.get("underline")):
        reason = "bold+link_or_underline"
    elif flags.get("bold"):
        reason = "bold"
    elif flags.get("link"):
        reason = "link"
    elif flags.get("underline"):
        reason = "underline"
    else:
        reason = "default"
    rows.append(["source_weight_reason", "string", reason])
    return json.dumps(rows, ensure_ascii=False)


def existing_expanded_nodes(con: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    rows = con.execute(
        """
        select id,numeric_id,title,custom_fields
        from notenode
        where user_id=? and custom_fields like ?
        """,
        (USER_ID, f"%{IMPORT_NAME}%"),
    ).fetchall()
    result: dict[str, sqlite3.Row] = {}
    for row in rows:
        key = custom_fields_map(row["custom_fields"]).get("source_key")
        if key:
            result[str(key)] = row
    return result


def next_note_numeric_id(con: sqlite3.Connection) -> int:
    row = con.execute("select coalesce(max(numeric_id), 0) + 1 from notenode").fetchone()
    return int(row[0] or 1)


def insert_edge(con: sqlite3.Connection, source_id: str, target_id: str) -> bool:
    if not source_id or not target_id or source_id == target_id:
        return False
    exists = con.execute(
        "select 1 from noteedge where user_id=? and source_id=? and target_id=? limit 1",
        (USER_ID, source_id, target_id),
    ).fetchone()
    if exists:
        return False
    con.execute(
        "insert into noteedge(id,user_id,source_id,target_id,label,created_at) values (?,?,?,?,?,?)",
        (str(uuid.uuid4()), USER_ID, source_id, target_id, None, time.time()),
    )
    return True


def insert_item(con: sqlite3.Connection, item: CalendarItem, numeric_id: int) -> str:
    node_id = str(uuid.uuid4())
    now = time.time()
    con.execute(
        """
        insert into notenode(
            id,numeric_id,user_id,title,content,created_at,updated_at,weight,start_at,task_status,history,
            node_type,node_status,custom_fields,private_level,color,note_kind,weight_mode,
            note_types,note_categories,primary_category,note_form,lifecycle_stage,note_scene
        ) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            node_id,
            numeric_id,
            USER_ID,
            title_for_item(item),
            item.content_html,
            now,
            now,
            item.weight,
            date_timestamp(item.date),
            None,
            "[]",
            item.category,
            "done",
            fields_for_item(item),
            item.private_level,
            item.color,
            "note",
            None,
            item.note_types,
            item.note_categories,
            item.category,
            item.note_form,
            "done",
            "note",
        ),
    )
    return node_id


def update_item(con: sqlite3.Connection, item: CalendarItem, existing_id: str) -> None:
    now = time.time()
    con.execute(
        """
        update notenode
        set title=?,content=?,updated_at=?,weight=?,start_at=?,custom_fields=?,
            node_type=?,note_types=?,note_categories=?,primary_category=?,note_form=?,
            private_level=?,color=?
        where id=? and user_id=?
        """,
        (
            title_for_item(item),
            item.content_html,
            now,
            item.weight,
            date_timestamp(item.date),
            fields_for_item(item),
            item.category,
            item.note_types,
            item.note_categories,
            item.category,
            item.note_form,
            item.private_level,
            item.color,
            existing_id,
            USER_ID,
        ),
    )


def summarize_items(items: list[CalendarItem]) -> dict[str, Any]:
    by_pattern = Counter(item.pattern for item in items)
    weights = Counter(item.weight for item in items)
    style_counts = Counter()
    by_year = Counter(item.date.year for item in items)
    by_source_origin = Counter(item.source_origin or "unknown" for item in items)
    per_day: dict[str, int] = defaultdict(int)
    for item in items:
        for key, value in item.style_flags.items():
            if value:
                style_counts[key] += 1
        per_day[item.date.isoformat()] += 1
    busiest = sorted(per_day.items(), key=lambda pair: (-pair[1], pair[0]))[:12]
    samples = []
    for item in sorted(items, key=lambda obj: (obj.date, -obj.weight, obj.source_title))[:30]:
        samples.append(
            {
                "date": item.date.isoformat(),
                "title": title_for_item(item),
                "weight": item.weight,
                "flags": item.style_flags,
                "source": item.source_title,
                "source_key": item_source_key(item),
            }
        )
    high_samples = []
    for item in sorted(items, key=lambda obj: (-obj.weight, obj.date, obj.source_title))[:30]:
        high_samples.append(
            {
                "date": item.date.isoformat(),
                "title": title_for_item(item),
                "weight": item.weight,
                "flags": item.style_flags,
                "source": item.source_title,
            }
        )
    return {
        "items": len(items),
        "by_pattern": dict(sorted(by_pattern.items())),
        "weights": {str(k): weights[k] for k in sorted(weights)},
        "style_counts": dict(sorted(style_counts.items())),
        "by_year": {str(k): by_year[k] for k in sorted(by_year)},
        "by_source_origin": dict(sorted(by_source_origin.items())),
        "busiest_days": busiest,
        "samples": samples,
        "high_weight_samples": high_samples,
    }


def source_richness_score(items: list[CalendarItem]) -> int:
    score = 0
    for item in items:
        flags = item.style_flags
        if flags.get("red"):
            score += 30
        if flags.get("bold"):
            score += 10
        if flags.get("underline"):
            score += 4
        if flags.get("link"):
            score += 3
        score += item.weight
    return score


def item_richness_score(item: CalendarItem) -> int:
    return source_richness_score([item])


def normalized_dedupe_text(item: CalendarItem) -> str:
    return re.sub(r"\s+", "", item.text or "")[:80]


def dedupe_source_titles(items: list[CalendarItem]) -> tuple[list[CalendarItem], int]:
    by_title: dict[str, list[CalendarItem]] = defaultdict(list)
    for item in items:
        by_title[item.source_title].append(item)

    kept: list[CalendarItem] = []
    skipped = 0
    for title_items in by_title.values():
        by_source_note: dict[str, list[CalendarItem]] = defaultdict(list)
        for item in title_items:
            by_source_note[item.source_note_id].append(item)
        if len(by_source_note) <= 1:
            kept.extend(title_items)
            continue

        def rank_item(item: CalendarItem) -> tuple[int, int, int]:
            return (
                item_richness_score(item),
                int(item.source_origin == "onenote-section-file"),
                len(item.text or ""),
            )

        by_cell: dict[tuple[Any, ...], list[CalendarItem]] = defaultdict(list)
        for item in title_items:
            by_cell[
                (
                    item.pattern,
                    item.table_index,
                    item.row_index,
                    item.col_index,
                    item.date.isoformat(),
                    item.column_label,
                )
            ].append(item)

        selected_items: list[CalendarItem] = []
        for cell_items in by_cell.values():
            selected = max(cell_items, key=rank_item)
            selected_items.append(selected)
            skipped += len(cell_items) - 1

        by_text: dict[tuple[str, str], list[CalendarItem]] = defaultdict(list)
        for item in selected_items:
            by_text[(item.date.isoformat(), normalized_dedupe_text(item))].append(item)
        for text_items in by_text.values():
            selected = max(text_items, key=rank_item)
            kept.append(selected)
            skipped += len(text_items) - 1
    return kept, skipped


def run(args: argparse.Namespace) -> None:
    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    con = sqlite3.connect(db_path(data_dir), timeout=60)
    con.row_factory = sqlite3.Row
    yuque_path = Path(args.yuque_candidates) if args.yuque_candidates else default_yuque_remaining_candidates()
    yuque_html = load_yuque_candidate_html(yuque_path)

    all_items: list[CalendarItem] = []
    html_sources = Counter()
    rows_with_items = 0
    for row in source_rows(con, args.source_note):
        items, html_source = extract_items_from_row(row, yuque_html)
        html_sources[html_source] += 1
        if items:
            rows_with_items += 1
            all_items.extend(items)

    raw_item_count = len(all_items)
    duplicate_source_items_skipped = 0
    if not args.include_duplicate_sources:
        all_items, duplicate_source_items_skipped = dedupe_source_titles(all_items)

    all_items, out_of_range_items_skipped = filter_date_range(
        all_items,
        min_year=args.min_year,
        max_year=args.max_year,
    )

    existing = existing_expanded_nodes(con)
    todo_insert = [item for item in all_items if item_source_key(item) not in existing]
    todo_update = [item for item in all_items if item_source_key(item) in existing]
    summary = summarize_items(all_items)
    summary.update(
        {
            "db": str(db_path(data_dir)),
            "yuque_candidates": str(yuque_path),
            "source_rows_with_items": rows_with_items,
            "html_sources": dict(sorted(html_sources.items())),
            "raw_items_before_source_dedupe": raw_item_count,
            "duplicate_source_items_skipped": duplicate_source_items_skipped,
            "out_of_range_items_skipped": out_of_range_items_skipped,
            "date_range": [args.min_year, args.max_year],
            "existing": len(todo_update),
            "to_insert": len(todo_insert),
            "to_update": len(todo_update) if args.update_existing else 0,
            "dry_run": bool(args.dry_run),
        }
    )

    if args.dry_run:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        con.close()
        return

    backup = Path(args.backup) if args.backup else Path(tempfile.gettempdir()) / (
        f"codeyun_calendar_table_expansion_before_{dt.datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.db"
    )
    shutil.copy2(db_path(data_dir), backup)

    next_numeric_id = next_note_numeric_id(con)
    inserted = 0
    updated = 0
    edges = 0
    for item in todo_insert:
        node_id = insert_item(con, item, next_numeric_id)
        next_numeric_id += 1
        inserted += 1
        if insert_edge(con, item.source_note_id, node_id):
            edges += 1
    if args.update_existing:
        for item in todo_update:
            row = existing[item_source_key(item)]
            update_item(con, item, row["id"])
            updated += 1
            if insert_edge(con, item.source_note_id, row["id"]):
                edges += 1

    con.commit()
    con.close()
    summary.update(
        {
            "backup": str(backup),
            "inserted": inserted,
            "updated": updated,
            "edges": edges,
        }
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Expand calendar-like rich-text tables into dated CodeYun note nodes."
    )
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--yuque-candidates", default=None)
    parser.add_argument("--source-note", default=None, help="Optional note UUID or numeric_id to process only one source note.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--update-existing", action="store_true")
    parser.add_argument("--include-duplicate-sources", action="store_true")
    parser.add_argument("--min-year", type=int, default=2000)
    parser.add_argument("--max-year", type=int, default=dt.datetime.now(TZ).year)
    parser.add_argument("--backup", default=None)
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
