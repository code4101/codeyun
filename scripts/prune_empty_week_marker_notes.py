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
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

try:
    from resource_identity_sqlite import note_public_ref
except ImportError:  # pragma: no cover - supports package imports in tests/tools
    from scripts.resource_identity_sqlite import note_public_ref


USER_ID = 2
TZ = dt.timezone(dt.timedelta(hours=8))
WEEK_TITLE_RE = re.compile(r"^w\d{6}$", re.IGNORECASE)
EMPTY_WEEK_SOURCE_KINDS = {"yuque_legacy_week"}

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
    result: dict[str, Any] = {}
    for item in rows if isinstance(rows, list) else []:
        if isinstance(item, list) and len(item) >= 3:
            result[str(item[0])] = item[2]
        elif isinstance(item, dict) and item.get("key"):
            result[str(item["key"])] = item.get("value")
    return result


def text_of_content(content: str) -> str:
    return re.sub(r"\s+", "", BeautifulSoup(content or "", "html.parser").get_text("", strip=True))


def is_empty_week_marker(row: sqlite3.Row) -> bool:
    title = str(row["title"] or "").strip()
    if not WEEK_TITLE_RE.fullmatch(title):
        return False
    fields = custom_fields_map(row["custom_fields"])
    if str(fields.get("source_kind") or "") not in EMPTY_WEEK_SOURCE_KINDS:
        return False
    text = text_of_content(str(row["content"] or ""))
    return text == title


def find_candidates(con: sqlite3.Connection) -> list[sqlite3.Row]:
    rows = con.execute(
        """
        select id,numeric_id,title,content,weight,start_at,custom_fields
        from notenode
        where user_id=? and title glob 'w[0-9][0-9][0-9][0-9][0-9][0-9]'
        order by start_at,title
        """,
        (USER_ID,),
    ).fetchall()
    return [row for row in rows if is_empty_week_marker(row)]


def note_edge_ref(con: sqlite3.Connection, node_id: str) -> str:
    row = con.execute("select numeric_id from notenode where id=? limit 1", (node_id,)).fetchone()
    numeric_id = int((row["numeric_id"] if row is not None else 0) or 0)
    if numeric_id > 0:
        return str(numeric_id)
    return note_public_ref(con, node_id)


def child_count(con: sqlite3.Connection, node_id: str) -> int:
    node_ref = note_edge_ref(con, node_id)
    return int(con.execute("select count(*) from noteedge where user_id=? and source_id=?", (USER_ID, node_ref)).fetchone()[0])


def parent_count(con: sqlite3.Connection, node_id: str) -> int:
    node_ref = note_edge_ref(con, node_id)
    return int(con.execute("select count(*) from noteedge where user_id=? and target_id=?", (USER_ID, node_ref)).fetchone()[0])


def run(args: argparse.Namespace) -> None:
    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    con = sqlite3.connect(db_path(data_dir), timeout=60)
    con.row_factory = sqlite3.Row
    candidates = find_candidates(con)
    edge_ids = [row["id"] for row in candidates]
    outgoing_edges = sum(child_count(con, node_id) for node_id in edge_ids)
    incoming_edges = sum(parent_count(con, node_id) for node_id in edge_ids)
    summary = {
        "db": str(db_path(data_dir)),
        "dry_run": bool(args.dry_run),
        "candidates": len(candidates),
        "incoming_edges_to_delete": incoming_edges,
        "outgoing_edges_to_delete": outgoing_edges,
        "sample": [
            {
                "id": row["id"],
                "numeric_id": row["numeric_id"],
                "title": row["title"],
                "weight": row["weight"],
                "children": child_count(con, row["id"]),
                "parents": parent_count(con, row["id"]),
            }
            for row in candidates[:30]
        ],
    }
    if args.dry_run:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        con.close()
        return

    backup = Path(args.backup) if args.backup else Path(tempfile.gettempdir()) / (
        f"codeyun_prune_empty_week_markers_before_{dt.datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.db"
    )
    shutil.copy2(db_path(data_dir), backup)
    for node_id in edge_ids:
        node_ref = note_edge_ref(con, node_id)
        con.execute("delete from noteedge where user_id=? and (source_id=? or target_id=?)", (USER_ID, node_ref, node_ref))
        con.execute("delete from notenode where user_id=? and id=?", (USER_ID, node_id))
    con.commit()
    con.close()
    summary["backup"] = str(backup)
    summary["deleted_nodes"] = len(candidates)
    summary["deleted_edges"] = incoming_edges + outgoing_edges
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete empty legacy week marker notes after calendar table expansion.")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--backup", default=None)
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
