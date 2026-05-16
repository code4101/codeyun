from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.core.settings import get_settings


TZ = ZoneInfo("Asia/Shanghai")
MEMO_KEY_RE = re.compile(r"^\d{4}-\d{2}$")
WEEK_TITLE_RE = re.compile(r"^w\d{6}\s*[:：]\s*", re.IGNORECASE)
WHITESPACE_RE = re.compile(r"\s+")
SETTING_KEY_TEMPLATE = "note.calendar.year_month_memos.user.{user_id}"


def default_db_path() -> Path:
    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        raise RuntimeError(f"Only sqlite database URLs are supported: {url}")
    return Path(url.removeprefix("sqlite:///"))


def load_json(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return fallback
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return fallback
    return fallback


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def clean_text(value: Any, *, max_len: int = 80) -> str:
    text = WHITESPACE_RE.sub(" ", str(value or "")).strip()
    if not text or text == "-":
        return ""
    text = WEEK_TITLE_RE.sub("", text).strip()
    return text[:max_len]


def custom_field_map(value: Any) -> dict[str, Any]:
    fields = load_json(value, [])
    if isinstance(fields, dict):
        return dict(fields)
    result: dict[str, Any] = {}
    if not isinstance(fields, list):
        return result
    for item in fields:
        if isinstance(item, list) and len(item) >= 3:
            result[str(item[0])] = item[2]
    return result


def load_category_labels(con: sqlite3.Connection, user_id: int) -> dict[str, str]:
    labels = {
        "general": "综合",
        "task": "任务",
        "project": "项目",
        "legacy_color_e6a23c": "考勤",
        "legacy_color_67c23a": "凡修",
    }
    for key in (f"note.category_palette.user.{user_id}", f"note.type_palette.user.{user_id}"):
        row = con.execute("select value from appsetting where key = ?", (key,)).fetchone()
        payload = load_json(row["value"], {}) if row else {}
        for item in payload.get("items", []) if isinstance(payload, dict) else []:
            if not isinstance(item, dict):
                continue
            item_key = str(item.get("key") or "").strip()
            item_label = str(item.get("label") or "").strip()
            if item_key and item_label:
                labels[item_key] = item_label
    return labels


def note_month(start_at: Any) -> str | None:
    try:
        ts = float(start_at)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return datetime.fromtimestamp(ts, TZ).strftime("%Y-%m")


def note_date(start_at: Any) -> str:
    try:
        ts = float(start_at)
    except (TypeError, ValueError):
        return ""
    if ts <= 0:
        return ""
    return datetime.fromtimestamp(ts, TZ).strftime("%m-%d")


def build_note_item(row: sqlite3.Row, labels: dict[str, str]) -> dict[str, Any] | None:
    title = clean_text(row["title"])
    if not title:
        return None
    fields = custom_field_map(row["custom_fields"])
    category_key = str(row["primary_category"] or "general").strip() or "general"
    source_kind = str(fields.get("source_kind") or fields.get("source_import") or "").strip()
    progress = str(fields.get("__completion_progress_expr") or "").strip()
    return {
        "date": note_date(row["start_at"]),
        "title": title,
        "weight": int(row["weight"] or 0),
        "category": labels.get(category_key, category_key),
        "form": str(row["note_form"] or "note"),
        "source_kind": source_kind,
        "progress": progress,
    }


def sort_note_item(item: dict[str, Any]) -> tuple[int, str, str]:
    progress_bonus = 1 if item.get("progress") else 0
    return (-(int(item.get("weight") or 0) * 10 + progress_bonus), str(item.get("date") or ""), str(item.get("title") or ""))


def pick_representative_notes(notes: list[dict[str, Any]], *, max_notes: int) -> list[dict[str, Any]]:
    picked: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for item in sorted(notes, key=sort_note_item):
        if int(item.get("weight") or 0) >= 2:
            signature = (str(item.get("date")), str(item.get("title")))
            if signature not in seen:
                picked.append(item)
                seen.add(signature)

    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in notes:
        by_date[str(item.get("date") or "")].append(item)
    for day in sorted(by_date):
        for item in sorted(by_date[day], key=sort_note_item)[:3]:
            signature = (str(item.get("date")), str(item.get("title")))
            if signature in seen:
                continue
            picked.append(item)
            seen.add(signature)
            if len(picked) >= max_notes:
                return picked

    return picked[:max_notes]


def extract_context(args: argparse.Namespace) -> None:
    db_path = Path(args.db_path) if args.db_path else default_db_path()
    con = connect(db_path)
    labels = load_category_labels(con, args.user_id)
    start_month = f"{args.start_year:04d}-01"
    end_month = f"{args.end_year:04d}-12"

    months: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rows = con.execute(
        """
        select title, weight, primary_category, note_form, start_at, custom_fields
        from notenode
        where user_id = ? and start_at > 0
        order by start_at asc, weight desc
        """,
        (args.user_id,),
    ).fetchall()
    for row in rows:
        ym = note_month(row["start_at"])
        if not ym or ym < start_month or ym > end_month:
            continue
        item = build_note_item(row, labels)
        if item:
            months[ym].append(item)

    payload_months: list[dict[str, Any]] = []
    for ym in sorted(months):
        notes = months[ym]
        categories = Counter(str(item.get("category") or "综合") for item in notes)
        source_kinds = Counter(str(item.get("source_kind") or "manual") for item in notes)
        payload_months.append(
            {
                "month": ym,
                "count": len(notes),
                "categories": [{"name": key, "count": count} for key, count in categories.most_common(8)],
                "sources": [{"name": key, "count": count} for key, count in source_kinds.most_common(6)],
                "notes": pick_representative_notes(notes, max_notes=args.max_notes),
            }
        )

    payload = {
        "user_id": args.user_id,
        "range": {"start_year": args.start_year, "end_year": args.end_year},
        "months": payload_months,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(payload_months)} month contexts to {output}")


def normalize_memos(value: Any) -> dict[str, str]:
    payload = load_json(value, {})
    raw = payload.get("memos") if isinstance(payload, dict) and isinstance(payload.get("memos"), dict) else payload
    if not isinstance(raw, dict):
        return {}
    memos: dict[str, str] = {}
    for key, text in raw.items():
        memo_key = str(key or "").strip()
        if not MEMO_KEY_RE.fullmatch(memo_key):
            continue
        memo_text = WHITESPACE_RE.sub(" ", str(text or "")).strip()
        if memo_text:
            memos[memo_key] = memo_text[:200]
    return dict(sorted(memos.items()))


def apply_memos(args: argparse.Namespace) -> None:
    db_path = Path(args.db_path) if args.db_path else default_db_path()
    input_path = Path(args.input)
    memos = normalize_memos(input_path.read_text(encoding="utf-8"))
    if not memos:
        raise RuntimeError(f"No valid memos found in {input_path}")

    backup_path = None
    if not args.no_backup:
        backup_path = Path(tempfile.gettempdir()) / f"codeyun_calendar_month_memos_backup_{datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.db"
        shutil.copy2(db_path, backup_path)

    con = connect(db_path)
    setting_key = SETTING_KEY_TEMPLATE.format(user_id=args.user_id)
    existing_row = con.execute("select value from appsetting where key = ?", (setting_key,)).fetchone()
    existing = normalize_memos(existing_row["value"]) if existing_row and not args.replace else {}
    merged = dict(sorted({**existing, **memos}.items()))
    now = time.time()
    payload = {
        "version": 1,
        "source": "codex-cli-calendar-month-memos",
        "generated_at": now,
        "memos": merged,
    }
    con.execute(
        """
        insert into appsetting(key, value, updated_at)
        values (?, ?, ?)
        on conflict(key) do update set value = excluded.value, updated_at = excluded.updated_at
        """,
        (setting_key, json.dumps(payload, ensure_ascii=False), now),
    )
    con.commit()
    print(f"Applied {len(memos)} memos to {setting_key}; stored {len(merged)} total.")
    if backup_path:
        print(f"Backup: {backup_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser("extract")
    extract_parser.add_argument("--db-path")
    extract_parser.add_argument("--user-id", type=int, default=2)
    extract_parser.add_argument("--start-year", type=int, default=2016)
    extract_parser.add_argument("--end-year", type=int, default=2026)
    extract_parser.add_argument("--max-notes", type=int, default=45)
    extract_parser.add_argument("--output", required=True)
    extract_parser.set_defaults(func=extract_context)

    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--db-path")
    apply_parser.add_argument("--user-id", type=int, default=2)
    apply_parser.add_argument("--input", required=True)
    apply_parser.add_argument("--replace", action="store_true")
    apply_parser.add_argument("--no-backup", action="store_true")
    apply_parser.set_defaults(func=apply_memos)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
