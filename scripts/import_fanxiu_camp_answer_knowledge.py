from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlmodel import Session

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.fanxiu.choice_knowledge.reverse_camp_answer import (
    import_reverse_camp_answer_knowledge,
)
from backend.db import engine


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import active reverse-engineered CampAnswer rows into CodeYun quiz knowledge."
    )
    parser.add_argument(
        "--export-root",
        type=Path,
        help="Fanxiu reverse export root; defaults to FANXIU_RESOURCE_EXPORT_ROOT.",
    )
    args = parser.parse_args()
    with Session(engine) as session:
        stats = import_reverse_camp_answer_knowledge(
            session,
            export_root=args.export_root,
        )
    print(json.dumps(stats.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
