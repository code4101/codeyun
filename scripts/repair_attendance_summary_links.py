from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.settings import get_settings


DEFAULT_COURSES_DIR = Path(
    os.environ.get(
        "CODEYUN_KQ5034_COURSES_DIR",
        r"D:\home\chenkunze\slns\kq5034\courses",
    )
)
ONLINE_SHEET_FIELD = "在线考勤表"
KDOCS_TOKEN_RE = re.compile(r"/l/(?P<token>[^/?#]+)")


def _default_db_path() -> Path:
    settings = get_settings()
    return settings.data_dir / "codeyun.db"


def _normalize_stem(value: Any) -> str:
    if isinstance(value, dict) and "value" in value:
        value = value.get("value")
    text = str(value or "").strip()
    text = re.sub(r"\.py$", "", text)
    chinese_month = re.match(r"^(?P<year>\d{4})年(?P<month>\d{1,2})月(?P<body>.+)$", text)
    if chinese_month:
        year = int(chinese_month.group("year")) % 100
        month = int(chinese_month.group("month"))
        body = chinese_month.group("body").strip()
        return f"d{year:02d}{month:02d}{body}".strip(" .")
    compact_month = re.match(r"^(?P<year>\d{4})(?P<month>\d{2})(?P<body>\D.+)$", text)
    if compact_month:
        year = int(compact_month.group("year")) % 100
        month = int(compact_month.group("month"))
        body = compact_month.group("body").strip()
        return f"d{year:02d}{month:02d}{body}".strip(" .")
    text = re.sub(r"^\d{2}(\d{6})", r"d\1", text, count=1)
    text = re.sub(r"^(\d{6})(?=\D|$)", r"d\1", text, count=1)
    text = text.replace(".", "点")
    return text.strip(" .")


def _stem_aliases(stem: str) -> set[str]:
    aliases = {stem}
    parsed = re.match(r"^d(?P<year>\d{2})(?P<month>\d{2})(?P<day>\d{2})(?P<body>.+)$", stem)
    if parsed:
        aliases.add(f"d{parsed.group('year')}{parsed.group('month')}{parsed.group('body')}")
    return aliases


def _url_key(value: Any) -> str:
    text = str(value or "").strip()
    match = KDOCS_TOKEN_RE.search(text)
    if match:
        return f"kdocs:{match.group('token')}"
    match = re.search(r"/workbook/(\d+)\?sheet=(\d+)", text)
    if match:
        return f"codeyun-workbook:{match.group(1)}:{match.group(2)}"
    match = re.search(r"/sheet/(\d+)", text)
    if match:
        return f"codeyun-sheet:{match.group(1)}"
    return text.rstrip("/")


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _constant_string(node: ast.AST) -> str:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""


def _extract_course_script_url(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result = ""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        if _call_name(node.func) == "CodeYunCourseSheets":
            for keyword in node.keywords:
                if keyword.arg != "attendance" or not isinstance(keyword.value, ast.Call):
                    continue
                args = keyword.value.args
                if len(args) >= 2 and all(isinstance(arg, ast.Constant) for arg in args[:2]):
                    result = f"http://localhost:5173/workbook/{args[0].value}?sheet={args[1].value}"
            continue

        if isinstance(node.func, ast.Attribute) and node.func.attr == "__init__":
            args = node.args
            if len(args) >= 3:
                token = _constant_string(args[2])
                if re.fullmatch(r"[A-Za-z0-9]{8,}", token):
                    result = f"https://www.kdocs.cn/l/{token}"
            continue

        if _call_name(node.func) == "KqCourseBook" and node.args:
            token = _constant_string(node.args[0])
            if re.fullmatch(r"[A-Za-z0-9]{8,}", token):
                result = f"https://www.kdocs.cn/l/{token}"

    return result


def _build_expected_links(courses_dir: Path) -> dict[str, dict[str, str]]:
    expected: dict[str, dict[str, str]] = {}
    for directory in (courses_dir, courses_dir / "已完结"):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.py")):
            stem = _normalize_stem(path.stem)
            if not re.match(r"^d\d{6}.+", stem):
                continue
            url = _extract_course_script_url(path)
            if not url or stem in expected:
                continue
            item = {
                "url": url,
                "key": _url_key(url),
                "path": str(path),
            }
            expected[stem] = item
            for alias in _stem_aliases(stem):
                expected.setdefault(alias, item)
    return expected


def _load_document(con: sqlite3.Connection, sheet_id: int, title: str) -> sqlite3.Row:
    row = con.execute(
        "SELECT id, numeric_id, title, version, document_json FROM sheetdocument "
        "WHERE numeric_id=? AND title=?",
        [sheet_id, title],
    ).fetchone()
    if row is None:
        raise RuntimeError(f"sheetdocument not found: numeric_id={sheet_id}, title={title!r}")
    return row


def _cell_link_url(cell: Any) -> str:
    if not isinstance(cell, dict):
        return ""
    link = cell.get("link")
    if not isinstance(link, dict):
        return ""
    return str(link.get("url") or "").strip()


def _set_cell_link(cell: dict[str, Any], url: str) -> None:
    link = cell.get("link") if isinstance(cell.get("link"), dict) else {}
    next_link = dict(link)
    next_link["url"] = url
    cell["link"] = next_link


def _repair_document_links(
    document: dict[str, Any],
    expected: dict[str, dict[str, str]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    columns = list(document.get("columns") or [])
    if ONLINE_SHEET_FIELD not in columns:
        raise RuntimeError(f"document does not contain field {ONLINE_SHEET_FIELD!r}")
    online_index = columns.index(ONLINE_SHEET_FIELD)

    entity_columns = list(document.get("entity_columns") or [])
    entity_rows = list(document.get("entity_rows") or [])
    entity_cells = document.setdefault("entity_cells", {})
    cell_meta = document.setdefault("cell_meta", {})
    data_start = int(document.get("data_start_row") or 0)
    online_col_id = ""
    if online_index < len(entity_columns) and isinstance(entity_columns[online_index], dict):
        online_col_id = str(entity_columns[online_index].get("id") or "").strip()

    fixes: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row_index, row in enumerate(document.get("rows") or []):
        if not isinstance(row, list) or online_index >= len(row):
            continue
        online_text = row[online_index]
        stem = _normalize_stem(online_text)
        expected_item = expected.get(stem)
        if expected_item is None:
            continue

        document_row = data_start + row_index
        expected_url = expected_item["url"]
        current_url = ""
        fixed_entity = False
        if online_col_id and document_row < len(entity_rows):
            entity_row = entity_rows[document_row]
            row_id = str(entity_row.get("id") or "").strip() if isinstance(entity_row, dict) else ""
            if row_id:
                row_cells = entity_cells.setdefault(row_id, {})
                if isinstance(row_cells, dict):
                    cell = row_cells.setdefault(online_col_id, {})
                    if isinstance(cell, dict):
                        current_url = _cell_link_url(cell)
                        if _url_key(current_url) != expected_item["key"]:
                            _set_cell_link(cell, expected_url)
                            fixed_entity = True
                    else:
                        skipped.append({"row": document_row + 1, "online": online_text, "reason": "entity cell is not object"})
                else:
                    skipped.append({"row": document_row + 1, "online": online_text, "reason": "entity row cells is not object"})

        meta_key = f"{document_row}:{online_index}"
        meta = cell_meta.get(meta_key)
        if not isinstance(meta, dict):
            meta = {}
            cell_meta[meta_key] = meta
        meta_url = _cell_link_url(meta)
        fixed_meta = False
        if _url_key(meta_url) != expected_item["key"]:
            _set_cell_link(meta, expected_url)
            fixed_meta = True

        if fixed_entity or fixed_meta:
            fixes.append({
                "row": document_row + 1,
                "online": online_text,
                "old": current_url or meta_url,
                "new": expected_url,
                "fixed_entity": fixed_entity,
                "fixed_cell_meta": fixed_meta,
            })

    return document, fixes, skipped


def _backup_database(db_path: Path) -> Path:
    backup_path = db_path.with_name(f"{db_path.name}.bak.{time.strftime('%Y%m%d-%H%M%S')}.attendance-links")
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    target = sqlite3.connect(backup_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return backup_path


def repair_attendance_summary_links(
    *,
    db_path: Path,
    courses_dir: Path,
    sheet_id: int,
    sheet_title: str,
    apply: bool,
) -> dict[str, Any]:
    expected = _build_expected_links(courses_dir)
    if not expected:
        raise RuntimeError(f"no course links parsed from {courses_dir}")

    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    backup_path = ""
    try:
        row = _load_document(con, sheet_id, sheet_title)
        document = json.loads(row["document_json"])
        next_document, fixes, skipped = _repair_document_links(document, expected)

        if apply and fixes:
            backup_path = str(_backup_database(db_path))
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "UPDATE sheetdocument SET document_json=?, version=?, updated_at=? WHERE id=?",
                [
                    json.dumps(next_document, ensure_ascii=False, separators=(",", ":")),
                    int(row["version"] or 1) + 1,
                    time.time(),
                    row["id"],
                ],
            )
            con.commit()
    finally:
        con.close()

    return {
        "mode": "apply" if apply else "dry-run",
        "db_path": str(db_path),
        "courses_dir": str(courses_dir),
        "sheet_id": sheet_id,
        "sheet_title": sheet_title,
        "expected_course_count": len(expected),
        "fix_count": len(fixes),
        "skipped": skipped,
        "backup": backup_path,
        "first_fixes": fixes[:20],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair attendance summary online-sheet links from kq5034 course scripts.")
    parser.add_argument("--db", type=Path, default=_default_db_path(), help="Path to codeyun.db.")
    parser.add_argument("--courses-dir", type=Path, default=DEFAULT_COURSES_DIR, help="Path to kq5034/courses.")
    parser.add_argument("--sheet-id", type=int, default=4, help="Numeric sheet id for the attendance summary course sheet.")
    parser.add_argument("--sheet-title", default="课程", help="Sheet title guard.")
    parser.add_argument("--apply", action="store_true", help="Write fixes and create a sqlite backup. Omit for dry-run.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = repair_attendance_summary_links(
        db_path=args.db,
        courses_dir=args.courses_dir,
        sheet_id=args.sheet_id,
        sheet_title=args.sheet_title,
        apply=bool(args.apply),
    )
    print(json.dumps(summary, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
