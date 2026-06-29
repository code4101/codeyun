from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.core.settings import get_settings
from backend.core.temp_paths import codeyun_temp_root


GENERAL_CATEGORY = "general"
DEFAULT_WEIGHT = 100


def _json_loads(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _database_path(raw: str | None) -> Path:
    if raw:
        return Path(raw).expanduser().resolve(strict=False)
    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        raise SystemExit(f"Only sqlite database URLs are supported, got: {url}")
    return Path(url.removeprefix("sqlite:///")).expanduser().resolve(strict=False)


def _is_single_general_category(value: Any) -> bool:
    categories = _json_loads(value, [])
    if not isinstance(categories, list) or len(categories) != 1:
        return False
    item = categories[0] if isinstance(categories[0], dict) else {}
    return (
        str(item.get("key") or "").strip() == GENERAL_CATEGORY
        and int(item.get("weight") or DEFAULT_WEIGHT) == DEFAULT_WEIGHT
    )


def _has_explicit_general_history(value: Any) -> bool:
    history = _json_loads(value, [])
    if not isinstance(history, list):
        return False
    for entry in history:
        if not isinstance(entry, dict):
            continue
        field = str(entry.get("f") or "")
        if field not in {"n", "nt"}:
            continue
        if GENERAL_CATEGORY in json.dumps(entry.get("v"), ensure_ascii=False):
            return True
    return False


def _legacy_node_type_for_empty_category(note_form: Any) -> str:
    normalized = str(note_form or "note").strip()
    if normalized == "document":
        return "doc"
    if normalized == "memo":
        return "memo"
    return "note"


def _classify(row: sqlite3.Row, *, include_system: bool) -> tuple[bool, str]:
    if str(row["primary_category"] or "").strip() != GENERAL_CATEGORY:
        return False, "primary_not_general"
    if not _is_single_general_category(row["note_categories"]):
        return False, "not_single_general_category"
    if _has_explicit_general_history(row["history"]):
        return False, "explicit_general_history"
    if not include_system and str(row["note_kind"] or "").startswith("fanxiu_"):
        return False, "system_note_kind"
    return True, "single_general_no_manual_evidence"


def _fetch_rows(conn: sqlite3.Connection, *, include_deleted: bool) -> list[sqlite3.Row]:
    deleted_clause = "" if include_deleted else "WHERE deleted_at IS NULL"
    return conn.execute(
        f"""
        SELECT id, numeric_id, title, note_categories, primary_category, note_types,
               node_type, note_form, note_kind, node_status, lifecycle_stage, color,
               created_at, updated_at, start_at, history, deleted_at
        FROM notenode
        {deleted_clause}
        ORDER BY COALESCE(numeric_id, 0), id
        """
    ).fetchall()


def _backup_database(db_path: Path, backup_dir: Path) -> Path:
    backup_path = backup_dir / f"{db_path.stem}.before-general-category-repair.db"
    source = sqlite3.connect(db_path)
    try:
        target = sqlite3.connect(backup_path)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    return backup_path


def _write_jsonl(path: Path, rows: list[sqlite3.Row]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(dict(row), ensure_ascii=False, default=str) + "\n")


def _print_samples(rows: list[sqlite3.Row], *, limit: int) -> None:
    for row in rows[:limit]:
        public_id = row["numeric_id"] or row["id"]
        title = str(row["title"] or "").replace("\n", " ")[:90]
        signature = (
            f"form={row['note_form'] or ''}, kind={row['note_kind'] or ''}, "
            f"stage={row['lifecycle_stage'] or row['node_status'] or ''}, "
            f"node_type={row['node_type'] or ''}"
        )
        print(f"  {public_id}: {title} | {signature}")


def run() -> None:
    parser = argparse.ArgumentParser(description="Detect or remove historical accidental 综合 category on notes.")
    parser.add_argument("--db", help="SQLite database path. Defaults to CodeYun configured database.")
    parser.add_argument("--apply", action="store_true", help="Apply repair. Without this flag only prints a dry-run report.")
    parser.add_argument("--include-system", action="store_true", help="Also repair special system note kinds such as fanxiu_*.")
    parser.add_argument("--include-deleted", action="store_true", help="Also repair soft-deleted notes.")
    parser.add_argument("--limit", type=int, default=30, help="Sample size to print.")
    args = parser.parse_args()

    db_path = _database_path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Database does not exist: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = _fetch_rows(conn, include_deleted=args.include_deleted)
        candidates: list[sqlite3.Row] = []
        skipped = Counter()
        by_signature = Counter()
        for row in rows:
            selected, reason = _classify(row, include_system=args.include_system)
            if not selected:
                skipped[reason] += 1
                continue
            candidates.append(row)
            by_signature[
                (
                    row["node_type"] or "",
                    row["note_form"] or "",
                    row["note_kind"] or "",
                    row["lifecycle_stage"] or row["node_status"] or "",
                )
            ] += 1

        print(f"Database: {db_path}")
        print(f"Scanned notes: {len(rows)}")
        print(f"Repair candidates: {len(candidates)}")
        print("Top candidate signatures:")
        for signature, count in by_signature.most_common(12):
            print(f"  {count:5d}  node_type={signature[0]} form={signature[1]} kind={signature[2]} stage={signature[3]}")
        print("Skipped:")
        for reason, count in skipped.most_common():
            print(f"  {count:5d}  {reason}")
        print("Samples:")
        _print_samples(candidates, limit=max(args.limit, 0))

        if not args.apply:
            print("Dry-run only. Re-run with --apply to update candidates to no category.")
            return

        if not candidates:
            print("Nothing to repair.")
            return

        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup_dir = codeyun_temp_root("note-general-category-repair", stamp)
        backup_db = _backup_database(db_path, backup_dir)
        row_backup = backup_dir / "candidate_rows.jsonl"
        _write_jsonl(row_backup, candidates)

        with conn:
            for row in candidates:
                conn.execute(
                    """
                    UPDATE notenode
                    SET note_categories = :note_categories,
                        primary_category = NULL,
                        note_types = :note_types,
                        node_type = :node_type
                    WHERE id = :id
                    """,
                    {
                        "note_categories": _json_dumps([]),
                        "note_types": _json_dumps([]),
                        "node_type": _legacy_node_type_for_empty_category(row["note_form"]),
                        "id": row["id"],
                    },
                )

        print(f"Applied repair to {len(candidates)} notes.")
        print(f"Database backup: {backup_db}")
        print(f"Candidate row backup: {row_backup}")
    finally:
        conn.close()


if __name__ == "__main__":
    run()
