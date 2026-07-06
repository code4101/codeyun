from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlmodel import Session

from backend.api.note_sheets import backfill_default_sheet_page_snapshots
from backend.db import engine, migrate_db


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill default page snapshots for note sheets.")
    parser.add_argument("--sheet-id", action="append", type=int, default=[], help="Numeric sheet id to backfill. Can be repeated.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum candidate sheets to scan.")
    parser.add_argument(
        "--min-document-json-bytes",
        type=int,
        default=1_000_000,
        help="Minimum stored document_json length when --sheet-id is not provided.",
    )
    parser.add_argument("--include-existing", action="store_true", help="Refresh existing snapshots too.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    migrate_db()
    with Session(engine) as session:
        result = backfill_default_sheet_page_snapshots(
            session,
            sheet_ids=args.sheet_id or None,
            limit=args.limit,
            min_document_json_bytes=args.min_document_json_bytes,
            include_existing=args.include_existing,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
