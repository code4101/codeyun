from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

from sqlmodel import Session, select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.core.notes.sheet_inline_links import canonicalize_sheet_document_inline_links
from backend.db import engine
from backend.models import SheetDocument


def migrate_note_sheet_links_to_inline(*, dry_run: bool = False) -> dict[str, int]:
    stats = {
        "scanned": 0,
        "updated": 0,
        "legacy_links": 0,
        "stripped_meta": 0,
    }
    now = time.time()

    with Session(engine) as session:
        sheets = session.exec(select(SheetDocument)).all()
        for sheet in sheets:
            stats["scanned"] += 1
            document_json = dict(sheet.document_json or {})
            next_document, result = canonicalize_sheet_document_inline_links(
                document_json,
                migrate_legacy_links=True,
                strip_legacy_links=True,
            )
            if not result.get("changed"):
                continue

            stats["updated"] += 1
            stats["legacy_links"] += int(result.get("legacy") or 0)
            stats["stripped_meta"] += int(result.get("stripped_meta") or 0)
            if dry_run:
                continue

            sheet.document_json = next_document
            sheet.version = int(sheet.version or 1) + 1
            sheet.updated_at = now
            session.add(sheet)

        if not dry_run:
            session.commit()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate note sheet hyperlinks into inline cell objects.")
    parser.add_argument("--dry-run", action="store_true", help="Only report changes without writing.")
    args = parser.parse_args()

    stats = migrate_note_sheet_links_to_inline(dry_run=args.dry_run)
    mode = "dry-run" if args.dry_run else "updated"
    print(
        f"{mode}: scanned={stats['scanned']}, updated={stats['updated']}, "
        f"legacy_links={stats['legacy_links']}, stripped_meta={stats['stripped_meta']}"
    )


if __name__ == "__main__":
    main()
