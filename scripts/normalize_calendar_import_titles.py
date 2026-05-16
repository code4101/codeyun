from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


USER_ID = 2
TZ = dt.timezone(dt.timedelta(hours=8))
CALENDAR_SOURCE_KIND = "calendar_table_cell"
TITLE_PREFIX_RE = re.compile(r"^\s*w\d{6}\s*(?:[:：]\s*|\s+)", re.IGNORECASE)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def default_data_dir() -> Path:
    return Path(os.environ.get("CODEYUN_DATA_DIR", r"D:\home\chenkunze\data\m2603codeyun\codepc_mf"))


def db_path(data_dir: Path) -> Path:
    return data_dir / "codeyun.db"


def safe_json_loads(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return default


def custom_fields_map(raw: str | None) -> dict[str, Any]:
    rows = safe_json_loads(raw, [])
    if isinstance(rows, dict):
        return rows
    result: dict[str, Any] = {}
    for item in rows if isinstance(rows, list) else []:
        if isinstance(item, list) and len(item) >= 3:
            result[str(item[0])] = item[2]
        elif isinstance(item, dict) and item.get("key"):
            result[str(item["key"])] = item.get("value")
    return result


def normalize_title(title: str, fields: dict[str, Any]) -> str:
    cleaned = TITLE_PREFIX_RE.sub("", title or "").strip()
    if cleaned:
        return cleaned
    source_column = str(fields.get("source_column") or "").strip()
    if source_column and not source_column.startswith("周"):
        return source_column
    source_date = str(fields.get("source_date") or "").strip()
    return source_date or (title or "").strip()


def candidates(con: sqlite3.Connection) -> list[tuple[str, int | None, str, str]]:
    rows = con.execute(
        """
        select id,numeric_id,title,custom_fields
        from notenode
        where user_id=? and title glob 'w[0-9][0-9][0-9][0-9][0-9][0-9]*'
        order by start_at,numeric_id,title
        """,
        (USER_ID,),
    ).fetchall()
    result: list[tuple[str, int | None, str, str]] = []
    for row in rows:
        fields = custom_fields_map(row["custom_fields"])
        if str(fields.get("source_kind") or "") != CALENDAR_SOURCE_KIND:
            continue
        old_title = str(row["title"] or "")
        new_title = normalize_title(old_title, fields)
        if new_title and new_title != old_title:
            result.append((str(row["id"]), row["numeric_id"], old_title, new_title))
    return result


def run(args: argparse.Namespace) -> None:
    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    database = db_path(data_dir)
    con = sqlite3.connect(database, timeout=60)
    con.row_factory = sqlite3.Row
    updates = candidates(con)
    summary = {
        "db": str(database),
        "dry_run": not args.apply,
        "candidates": len(updates),
        "preview": [
            {"numeric_id": numeric_id, "old_title": old_title, "new_title": new_title}
            for _, numeric_id, old_title, new_title in updates[: args.preview]
        ],
    }
    if not args.apply:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        con.close()
        return

    backup = Path(args.backup) if args.backup else Path(tempfile.gettempdir()) / (
        f"codeyun_calendar_import_titles_before_{dt.datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.db"
    )
    shutil.copy2(database, backup)
    for node_id, _, _, new_title in updates:
        con.execute(
            "update notenode set title=?,updated_at=? where user_id=? and id=?",
            (new_title, time.time(), USER_ID, node_id),
        )
    con.commit()
    con.close()
    summary.update({"backup": str(backup), "updated": len(updates)})
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove wYYMMDD prefixes from calendar table expansion note titles.")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--preview", type=int, default=40)
    parser.add_argument("--backup", default=None)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
