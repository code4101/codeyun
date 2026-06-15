from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.notes.yuque_html import looks_like_legacy_yuque_lake_html, normalize_legacy_yuque_lake_html


def default_data_dir() -> Path:
    return Path(os.environ.get("CODEYUN_DATA_DIR", r"D:\home\chenkunze\data\m2603codeyun\codepc_mf"))


def db_path(data_dir: Path) -> Path:
    return data_dir / "codeyun.db"


def scan_updates(con: sqlite3.Connection) -> list[tuple[str, str, str, int, int]]:
    rows = con.execute("select id,title,content from notenode").fetchall()
    updates: list[tuple[str, str, str, int, int]] = []
    for node_id, title, content in rows:
        if not looks_like_legacy_yuque_lake_html(content):
            continue
        normalized = normalize_legacy_yuque_lake_html(content)
        if normalized == (content or ""):
            continue
        updates.append((str(node_id), str(title or ""), normalized, len(content or ""), len(normalized)))
    return updates


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize legacy Yuque Lake HTML stored in CodeYun note content.")
    parser.add_argument("--data-dir", default="", help="CodeYun data directory. Defaults to CODEYUN_DATA_DIR or local codepc_mf data.")
    parser.add_argument("--dry-run", action="store_true", help="Only report how many notes would be changed.")
    args = parser.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    db = db_path(data_dir)
    con = sqlite3.connect(db, timeout=60)
    try:
        updates = scan_updates(con)
        summary = {
            "database": str(db),
            "dry_run": bool(args.dry_run),
            "matched": len(updates),
            "samples": [
                {"id": node_id, "title": title, "old_len": old_len, "new_len": new_len}
                for node_id, title, _content, old_len, new_len in updates[:20]
            ],
        }
        if args.dry_run or not updates:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return

        backup = Path(tempfile.gettempdir()) / f"codeyun_yuque_lake_backup_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        shutil.copy2(db, backup)
        for node_id, _title, content, _old_len, _new_len in updates:
            con.execute("update notenode set content=? where id=?", (content, node_id))
        con.commit()
        summary["backup"] = str(backup)
        summary["updated"] = len(updates)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        con.close()


if __name__ == "__main__":
    main()
