from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.models import generate_sheet_document_id
from scripts.import_yuque_journal import TZ, USER_ID, db_path, default_data_dir
try:
    from resource_identity_sqlite import allocate_note_numeric_id, allocate_resource_id, insert_note_edge
except ImportError:  # pragma: no cover - supports package imports in tests/tools
    from scripts.resource_identity_sqlite import allocate_note_numeric_id, allocate_resource_id, insert_note_edge


IMPORT_SOURCE = "yuque-table-import"
WORKBOOK_TITLE = "语雀日志结构化表格"
INDEX_NODE_TITLE = "语雀日志结构化表格索引"
DEFAULT_INCLUDED_BUCKETS = {
    "finance_labor_stats",
    "technical_reference",
    "large_structured_table",
}
HIGH_VALUE_TITLES = {
    "TODO",
    "周六 考勤组织架构",
    "第4批数据处理报告",
    "第4批数据处理报告（内部原版）",
    "周二整理测试集",
    "w231225: SenseTableDb",
    "w230206: xlproject",
    "w260216: CodeYun",
}
SENSITIVE_TITLE_RE = re.compile(r"(设备与服务器机器清单)")
SENSITIVE_TEXT_RE = re.compile(r"(账号\s*密码|验证码|password|passwd|secret|token)", re.I)


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


def custom_field_json(fields: list[list[Any]]) -> str:
    return json.dumps(fields, ensure_ascii=False)


def note_categories_json(source_row: sqlite3.Row) -> str:
    value = source_row["note_categories"]
    items = safe_json_loads(value, [])
    if isinstance(items, list) and items:
        return json.dumps(items, ensure_ascii=False)
    primary = str(source_row["primary_category"] or "general").strip() or "general"
    return json.dumps([{"key": primary, "weight": 100}], ensure_ascii=False)


def normalize_cell_text(cell: Tag) -> str:
    text = " ".join(cell.get_text(" ", strip=True).split())
    links: list[str] = []
    for anchor in cell.find_all("a"):
        href = str(anchor.get("href") or "").strip()
        if href and href not in text and href not in links:
            links.append(href)
    if links:
        suffix = " ".join(links)
        text = f"{text} {suffix}".strip()
    if not text:
        image = cell.find("img")
        if image is not None:
            alt = str(image.get("alt") or "").strip()
            src = str(image.get("src") or "").strip()
            text = alt or src
    return text.replace("\u200b", "").strip()


def positive_span(value: Any) -> int:
    try:
        return max(1, int(value or 1))
    except (TypeError, ValueError):
        return 1


def expanded_table_rows(table: Tag) -> list[list[str]]:
    pending: dict[int, tuple[int, str]] = {}
    rows: list[list[str]] = []

    for tr in table.find_all("tr"):
        row: list[str] = []
        col = 0

        def ensure(index: int) -> None:
            while len(row) <= index:
                row.append("")

        def flush_pending_at(index: int) -> bool:
            if index not in pending:
                return False
            remaining, value = pending[index]
            ensure(index)
            row[index] = value
            if remaining > 1:
                pending[index] = (remaining - 1, value)
            else:
                pending.pop(index, None)
            return True

        for cell in tr.find_all(["td", "th"]):
            while flush_pending_at(col):
                col += 1
            value = normalize_cell_text(cell)
            colspan = positive_span(cell.get("colspan"))
            rowspan = positive_span(cell.get("rowspan"))
            for offset in range(colspan):
                index = col + offset
                ensure(index)
                row[index] = value if offset == 0 else ""
                if rowspan > 1:
                    pending[index] = (rowspan - 1, row[index])
            col += colspan

        while any(index >= col for index in pending):
            if flush_pending_at(col):
                col += 1
            else:
                col += 1
            if col > 200:
                break

        while row and not str(row[-1]).strip():
            row.pop()
        if any(str(cell or "").strip() for cell in row):
            rows.append(row)

    width = max((len(row) for row in rows), default=0)
    return [row + [""] * (width - len(row)) for row in rows if width]


def unique_columns(raw: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    columns: list[str] = []
    for index, value in enumerate(raw, 1):
        name = re.sub(r"\s+", " ", str(value or "").strip())
        if not name:
            name = f"列{index}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
        columns.append(name)
    return columns


def infer_header_index(rows: list[list[str]]) -> int:
    if not rows:
        return 0
    for index, row in enumerate(rows[:4]):
        non_empty = sum(1 for cell in row if str(cell or "").strip())
        if non_empty >= 2:
            return index
    return 0


def build_sheet_document(table: Tag) -> dict[str, Any]:
    grid_rows = expanded_table_rows(table)
    if not grid_rows:
        grid_rows = [["列1"]]
    header_index = infer_header_index(grid_rows)
    columns = unique_columns(grid_rows[header_index])
    width = len(columns)
    normalized_grid_rows = [row[:width] + [""] * (width - len(row)) for row in grid_rows]
    data_rows = normalized_grid_rows[header_index + 1 :]
    return {
        "schema_version": 1,
        "columns": columns,
        "rows": data_rows,
        "grid_rows": normalized_grid_rows,
        "data_start_row": header_index + 1,
        "field_row_index": header_index,
        "merged_cells": [],
        "formula_reference_origin": "sheet_v2",
        "view_settings": {
            "show_row_numbers": True,
            "row_marker_numbering": "global",
            "row_marker_origin": "sheet",
            "show_column_markers": True,
            "column_marker_style": "letters",
            "frozen_column_count": 0,
            "pagination": {
                "enabled": len(data_rows) > 80,
                "page_size": 100,
            },
        },
        "column_configs": {
            column: {"display_mode": "wrap" if any(len(str(row[i] or "")) > 40 for row in data_rows for i, col in enumerate(columns) if col == column) else "single_line"}
            for column in columns
        },
    }


def table_summary(rows: list[list[str]]) -> str:
    text = " ".join(" ".join(row) for row in rows[:3])
    return re.sub(r"\s+", " ", text).strip()[:120]


def source_kind(source_row: sqlite3.Row) -> str:
    fields = safe_json_loads(source_row["custom_fields"], [])
    return str(custom_field_value(fields, "source_kind") or "")


def bucket_for_note(title: str, kind: str, tables: list[list[list[str]]]) -> str:
    joined = " ".join(table_summary(rows) for rows in tables)
    max_rows = max((len(rows) for rows in tables), default=0)
    max_cols = max((len(rows[0]) if rows else 0 for rows in tables), default=0)
    haystack = f"{title} {joined}"

    if SENSITIVE_TITLE_RE.search(title) or SENSITIVE_TEXT_RE.search(joined):
        return "asset_inventory_sensitive"
    if kind in {"yuque_week", "yuque_legacy_week"} and max_rows <= 9 and max_cols <= 2:
        return "weekly_diary_matrix"
    if re.search(r"账单|支出|费用|报酬|成本|工时|工作统计|付款|项目账单", haystack):
        return "finance_labor_stats"
    if re.search(r"周报|日报|TODO|组织架构|岗位|职责|项目分组", haystack):
        return "operations_tracking"
    if re.search(r"模型|工具|字段名|功能点|数据库|dify|nginx|frp|目录名|表格整理|去重模式|表达式|模块文件|API|接口|参数", haystack, re.I):
        return "technical_reference"
    if re.search(r"留影|毕业|军训|证件", title):
        return "photo_roster_layout"
    if max_rows >= 20 or max_cols >= 6:
        return "large_structured_table"
    return "small_inline_table"


def sheet_key_for(source_id: str, table_index: int) -> str:
    return f"yuque-table-{source_id}-{table_index}"


def source_doc_ref(source_row: sqlite3.Row) -> str:
    numeric_id = source_row["numeric_id"]
    return str(numeric_id) if numeric_id else str(source_row["id"])


def doc_url(source_row: sqlite3.Row) -> str:
    return f"/doc/{source_doc_ref(source_row)}"


def sheet_title(source_title: str, table_index: int, table_count: int, rows: list[list[str]]) -> str:
    title = re.sub(r"\s+", " ", source_title).strip() or "语雀表格"
    if table_count > 1:
        prefix = table_summary(rows)
        prefix = re.sub(r"[\\/:*?\"<>|]+", " ", prefix).strip()
        suffix = f" T{table_index:02d}"
        if prefix:
            title = f"{title}{suffix} {prefix[:24]}"
        else:
            title = f"{title}{suffix}"
    return title[:120]


def iter_candidates(con: sqlite3.Connection, include_weekly_ops: bool, include_sensitive: bool) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        select id,numeric_id,title,content,start_at,weight,private_level,primary_category,note_categories,custom_fields
        from notenode
        where user_id=? and custom_fields like '%yuque%' and lower(content) like '%<table%'
        order by coalesce(start_at,0), title
        """,
        (USER_ID,),
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        soup = BeautifulSoup(row["content"] or "", "html.parser")
        tables = soup.find_all("table")
        parsed_tables = [expanded_table_rows(table) for table in tables]
        parsed_tables = [table_rows for table_rows in parsed_tables if table_rows]
        if not parsed_tables:
            continue
        kind = source_kind(row)
        bucket = bucket_for_note(str(row["title"] or ""), kind, parsed_tables)
        selected = (
            bucket in DEFAULT_INCLUDED_BUCKETS
            or str(row["title"] or "") in HIGH_VALUE_TITLES
            or (include_weekly_ops and bucket == "operations_tracking")
            or (include_sensitive and bucket == "asset_inventory_sensitive")
        )
        if not selected:
            continue
        if bucket == "asset_inventory_sensitive" and not include_sensitive:
            continue
        source_tables = []
        for index, table in enumerate(tables, 1):
            rows_for_table = expanded_table_rows(table)
            if not rows_for_table:
                continue
            source_tables.append(
                {
                    "index": index,
                    "rows": rows_for_table,
                    "tag": table,
                    "sheet_key": sheet_key_for(str(row["id"]), index),
                    "title": sheet_title(str(row["title"] or ""), index, len(tables), rows_for_table),
                    "summary": table_summary(rows_for_table),
                    "row_count": len(rows_for_table),
                    "column_count": max((len(item) for item in rows_for_table), default=0),
                }
            )
        if source_tables:
            candidates.append({"source": row, "bucket": bucket, "tables": source_tables})
    return candidates


def create_backup(path: Path) -> Path:
    backup = Path(tempfile.gettempdir()) / f"codeyun_yuque_table_import_backup_{dt.datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.db"
    shutil.copy2(path, backup)
    return backup


def next_numeric_id(con: sqlite3.Connection, table_name: str) -> int:
    value = con.execute(f"select coalesce(max(numeric_id), 0) + 1 from {table_name}").fetchone()[0]
    return int(value)


def next_workbook_order(con: sqlite3.Connection, workbook_id: str) -> int:
    value = con.execute(
        "select coalesce(max(order_index), -1) + 1 from workbooksheetlink where workbook_id=?",
        (workbook_id,),
    ).fetchone()[0]
    return int(value)


def get_or_create_workbook(con: sqlite3.Connection, apply: bool) -> dict[str, Any]:
    existing = con.execute(
        "select id,numeric_id,legacy_id,title from workbookdocument where owner_user_id=? and title=? order by created_at limit 1",
        (USER_ID, WORKBOOK_TITLE),
    ).fetchone()
    if existing:
        numeric_id = int(existing["numeric_id"] or 0)
        if numeric_id <= 0:
            numeric_id = next_numeric_id(con, "workbookdocument")
            if apply:
                con.execute("update workbookdocument set numeric_id=? where id=?", (numeric_id, existing["id"]))
        legacy_id = str(existing["legacy_id"] or existing["id"])
        allocate_resource_id(con, "workbook", legacy_id)
        return {"id": str(numeric_id), "numeric_id": numeric_id, "legacy_id": legacy_id, "created": False}
    if not apply:
        return {"id": "", "numeric_id": next_numeric_id(con, "workbookdocument"), "created": True}
    now = time.time()
    workbook_legacy_id = generate_sheet_document_id()
    numeric_id = next_numeric_id(con, "workbookdocument")
    allocate_resource_id(con, "workbook", workbook_legacy_id)
    con.execute(
        """
        insert into workbookdocument(
            id,numeric_id,legacy_id,title,owner_user_id,created_by_user_id,updated_by_user_id,created_at,updated_at
        ) values (?,?,?,?,?,?,?,?,?)
        """,
        (numeric_id, numeric_id, workbook_legacy_id, WORKBOOK_TITLE, USER_ID, USER_ID, USER_ID, now, now),
    )
    return {"id": str(numeric_id), "numeric_id": numeric_id, "legacy_id": workbook_legacy_id, "created": True}


def find_sheet(con: sqlite3.Connection, sheet_key: str) -> sqlite3.Row | None:
    return con.execute(
        "select id,numeric_id,legacy_id,title from sheetdocument where scope='notes' and owner_type=? and owner_key=? and sheet_key=? limit 1",
        (IMPORT_SOURCE, str(USER_ID), sheet_key),
    ).fetchone()


def upsert_sheet(
    con: sqlite3.Connection,
    table_item: dict[str, Any],
    document_json: dict[str, Any],
    workbook: dict[str, Any],
    apply: bool,
) -> dict[str, Any]:
    existing = find_sheet(con, table_item["sheet_key"])
    if existing:
        existing_numeric_id = int(existing["numeric_id"] or 0)
        legacy_id = str(existing["legacy_id"] or existing["id"])
        if existing_numeric_id <= 0:
            numeric_id = allocate_resource_id(con, "sheet", legacy_id)
        else:
            numeric_id = allocate_resource_id(con, "sheet", legacy_id, preferred_id=existing_numeric_id)
        sheet = {"id": str(numeric_id), "numeric_id": numeric_id, "legacy_id": legacy_id, "created": False}
        if apply:
            now = time.time()
            if numeric_id != existing_numeric_id:
                con.execute("update sheetdocument set numeric_id=? where id=?", (numeric_id, existing["id"]))
            con.execute(
                """
                update sheetdocument
                set title=?, document_json=?, version=version+1, updated_by_user_id=?, updated_at=?
                where id=?
                """,
                (table_item["title"], json.dumps(document_json, ensure_ascii=False), USER_ID, now, existing["id"]),
            )
    else:
        sheet_legacy_id = generate_sheet_document_id()
        numeric_id = allocate_resource_id(con, "sheet", sheet_legacy_id)
        sheet = {"id": str(numeric_id), "numeric_id": numeric_id, "legacy_id": sheet_legacy_id, "created": True}
        if apply:
            now = time.time()
            con.execute(
                """
                insert into sheetdocument(
                    id,numeric_id,legacy_id,scope,owner_type,owner_key,sheet_key,title,engine,document_json,version,
                    created_by_user_id,updated_by_user_id,created_at,updated_at,owner_user_id
                ) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    numeric_id,
                    numeric_id,
                    sheet_legacy_id,
                    "notes",
                    IMPORT_SOURCE,
                    str(USER_ID),
                    table_item["sheet_key"],
                    table_item["title"],
                    "handsontable",
                    json.dumps(document_json, ensure_ascii=False),
                    1,
                    USER_ID,
                    USER_ID,
                    now,
                    now,
                    USER_ID,
                ),
            )
    if apply and workbook["id"]:
        linked = con.execute(
            "select 1 from workbooksheetlink where workbook_id=? and sheet_id=? limit 1",
            (str(workbook["numeric_id"]), str(sheet["numeric_id"])),
        ).fetchone()
        if linked is None:
            con.execute(
                "insert into workbooksheetlink(id,workbook_id,sheet_id,order_index,created_at) values (?,?,?,?,?)",
                (
                    uuid.uuid4().hex,
                    str(workbook["numeric_id"]),
                    str(sheet["numeric_id"]),
                    next_workbook_order(con, str(workbook["numeric_id"])),
                    time.time(),
                ),
            )
    return sheet


def find_link_node(con: sqlite3.Connection, source_id: str) -> sqlite3.Row | None:
    return con.execute(
        """
        select id,title from notenode
        where user_id=? and custom_fields like '%yuque_table_sheet_link%' and custom_fields like ?
        limit 1
        """,
        (USER_ID, f"%{source_id}%"),
    ).fetchone()


def build_link_node_content(source_row: sqlite3.Row, workbook_numeric_id: int, table_links: list[dict[str, Any]]) -> str:
    source_title = html.escape(str(source_row["title"] or "源节点"))
    source_href = html.escape(doc_url(source_row))
    workbook_href = html.escape(f"/workbook/{workbook_numeric_id}")
    lines = [
        "<h2>星云表格</h2>",
        f'<p>源节点：<a href="{source_href}">{source_title}</a></p>',
        f'<p>工作簿：<a href="{workbook_href}">{html.escape(WORKBOOK_TITLE)}</a></p>',
        "<ul>",
    ]
    for item in table_links:
        title = html.escape(item["title"])
        workbook_url = html.escape(item["workbook_url"])
        sheet_url = html.escape(item["sheet_url"])
        summary = html.escape(item.get("summary") or "")
        lines.append(f'<li><a href="{workbook_url}">{title}</a> <code>{sheet_url}</code>{f" - {summary}" if summary else ""}</li>')
    lines.extend(["</ul>"])
    return "\n".join(lines)


def upsert_link_node(
    con: sqlite3.Connection,
    source_row: sqlite3.Row,
    workbook_numeric_id: int,
    table_links: list[dict[str, Any]],
    apply: bool,
) -> dict[str, Any]:
    existing = find_link_node(con, str(source_row["id"]))
    title = f"星云表格：{source_row['title']}"[:120]
    content = build_link_node_content(source_row, workbook_numeric_id, table_links)
    fields = custom_field_json(
        [
            ["source", "string", IMPORT_SOURCE],
            ["source_kind", "string", "yuque_table_sheet_link"],
            ["source_node_id", "string", str(source_row["id"])],
            ["source_doc_key", "string", str(custom_field_value(safe_json_loads(source_row["custom_fields"], []), "source_doc_key") or "")],
            ["workbook_numeric_id", "number", workbook_numeric_id],
            ["sheet_numeric_ids", "json", [item["sheet_numeric_id"] for item in table_links]],
            ["sheet_urls", "json", [item["sheet_url"] for item in table_links]],
        ]
    )
    if existing:
        if apply:
            now = time.time()
            con.execute(
                """
                update notenode
                set title=?, content=?, updated_at=?, custom_fields=?
                where id=?
                """,
                (title, content, now, fields, existing["id"]),
            )
        return {"id": existing["id"], "created": False}
    node_id = str(uuid.uuid4())
    if apply:
        numeric_id = allocate_note_numeric_id(con, node_id)
        now = time.time()
        category = str(source_row["primary_category"] or "general").strip() or "general"
        categories = note_categories_json(source_row)
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
                title,
                content,
                now,
                now,
                1,
                source_row["start_at"],
                None,
                "[]",
                category,
                "done",
                fields,
                int(source_row["private_level"] or 0),
                None,
                "note",
                None,
                categories,
                categories,
                category,
                "document",
                "done",
                "note",
            ),
        )
    return {"id": node_id, "created": True}


def ensure_edge(con: sqlite3.Connection, source_id: str, target_id: str, apply: bool) -> bool:
    if not apply:
        return False
    return insert_note_edge(
        con,
        user_id=USER_ID,
        source_id=source_id,
        target_id=target_id,
        edge_id=str(uuid.uuid4()),
    )


def upsert_index_node(
    con: sqlite3.Connection,
    workbook_numeric_id: int,
    link_nodes: list[dict[str, Any]],
    apply: bool,
) -> dict[str, Any]:
    existing = con.execute(
        """
        select id,title from notenode
        where user_id=? and title=? and custom_fields like '%yuque_table_sheet_index%'
        limit 1
        """,
        (USER_ID, INDEX_NODE_TITLE),
    ).fetchone()
    workbook_url = f"/workbook/{workbook_numeric_id}"
    lines = [
        "<h2>语雀日志结构化表格</h2>",
        f'<p><a href="{html.escape(workbook_url)}">打开星云表格工作簿</a></p>',
        "<ul>",
    ]
    for item in link_nodes:
        lines.append(f'<li><a href="{html.escape(doc_url(item["source"]))}">{html.escape(str(item["source"]["title"] or ""))}</a></li>')
    lines.append("</ul>")
    content = "\n".join(lines)
    fields = custom_field_json(
        [
            ["source", "string", IMPORT_SOURCE],
            ["source_kind", "string", "yuque_table_sheet_index"],
            ["workbook_numeric_id", "number", workbook_numeric_id],
            ["workbook_url", "string", workbook_url],
        ]
    )
    if existing:
        if apply:
            con.execute(
                "update notenode set content=?, updated_at=?, custom_fields=? where id=?",
                (content, time.time(), fields, existing["id"]),
            )
        return {"id": existing["id"], "created": False}
    node_id = str(uuid.uuid4())
    if apply:
        numeric_id = allocate_note_numeric_id(con, node_id)
        now = time.time()
        cats = json.dumps([{"key": "general", "weight": 100}], ensure_ascii=False)
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
                INDEX_NODE_TITLE,
                content,
                now,
                now,
                2,
                now,
                None,
                "[]",
                "general",
                "done",
                fields,
                0,
                None,
                "note",
                None,
                cats,
                cats,
                "general",
                "document",
                "done",
                "note",
            ),
        )
    return {"id": node_id, "created": True}


def run(args: argparse.Namespace) -> None:
    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    path = db_path(data_dir)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    candidates = iter_candidates(con, args.include_weekly_ops, args.include_sensitive)

    bucket_counts = Counter(item["bucket"] for item in candidates)
    table_count = sum(len(item["tables"]) for item in candidates)
    summary: dict[str, Any] = {
        "data_dir": str(data_dir),
        "apply": bool(args.apply),
        "source_node_count": len(candidates),
        "sheet_count": table_count,
        "bucket_counts": dict(sorted(bucket_counts.items())),
        "sources": [
            {
                "title": item["source"]["title"],
                "bucket": item["bucket"],
                "tables": len(item["tables"]),
                "max_rows": max(table["row_count"] for table in item["tables"]),
                "max_cols": max(table["column_count"] for table in item["tables"]),
            }
            for item in candidates
        ],
    }

    if not args.apply:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        con.close()
        return

    backup = create_backup(path)
    workbook = get_or_create_workbook(con, apply=True)
    sheet_created = 0
    sheet_updated = 0
    link_node_created = 0
    link_node_updated = 0
    edge_created = 0
    link_node_payloads: list[dict[str, Any]] = []

    try:
        for candidate in candidates:
            source_row = candidate["source"]
            table_links: list[dict[str, Any]] = []
            for table_item in candidate["tables"]:
                document_json = build_sheet_document(table_item["tag"])
                sheet = upsert_sheet(con, table_item, document_json, workbook, apply=True)
                if sheet["created"]:
                    sheet_created += 1
                else:
                    sheet_updated += 1
                sheet_url = f"/sheet/{sheet['numeric_id']}"
                workbook_url = f"/workbook/{workbook['numeric_id']}?sheet={sheet['numeric_id']}"
                table_links.append(
                    {
                        "title": table_item["title"],
                        "summary": table_item["summary"],
                        "sheet_numeric_id": sheet["numeric_id"],
                        "sheet_url": sheet_url,
                        "workbook_url": workbook_url,
                    }
                )

            link_node = upsert_link_node(con, source_row, workbook["numeric_id"], table_links, apply=True)
            if link_node["created"]:
                link_node_created += 1
            else:
                link_node_updated += 1
            if ensure_edge(con, str(source_row["id"]), str(link_node["id"]), apply=True):
                edge_created += 1
            link_node_payloads.append({"source": source_row, "node": link_node})

        index_node = upsert_index_node(con, workbook["numeric_id"], link_node_payloads, apply=True)
        for item in link_node_payloads:
            if ensure_edge(con, str(index_node["id"]), str(item["node"]["id"]), apply=True):
                edge_created += 1

        con.commit()
    except Exception:
        con.rollback()
        con.close()
        raise

    summary.update(
        {
            "backup": str(backup),
            "workbook_numeric_id": workbook["numeric_id"],
            "workbook_url": f"/workbook/{workbook['numeric_id']}",
            "workbook_created": workbook["created"],
            "sheet_created": sheet_created,
            "sheet_updated": sheet_updated,
            "link_node_created": link_node_created,
            "link_node_updated": link_node_updated,
            "index_node_created": index_node["created"],
            "edge_created": edge_created,
        }
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Import selected Yuque HTML tables into CodeYun note sheets.")
    parser.add_argument("--data-dir", default="")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--include-weekly-ops", action="store_true")
    parser.add_argument("--include-sensitive", action="store_true")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
