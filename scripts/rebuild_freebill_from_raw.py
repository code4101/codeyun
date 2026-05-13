from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.core.freebill import rebuild_freebill_records_from_raw_files


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild Freebill bill_records from archived raw CSV/XLSX files.")
    parser.add_argument("--work-dir", type=Path, default=None, help="Freebill work directory. Defaults to app settings.")
    parser.add_argument("--no-backup", action="store_true", help="Do not create bill.db backup before rewriting records.")
    parser.add_argument("--allow-errors", action="store_true", help="Continue when a supported raw file cannot be parsed.")
    args = parser.parse_args()

    result = rebuild_freebill_records_from_raw_files(
        work_dir=args.work_dir,
        backup=not args.no_backup,
        strict=not args.allow_errors,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
