from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

from sqlmodel import Session

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.attendance.nianzhu_course_sheets import (
    NIANZHU_ATTENDANCE_SHEET_NUMERIC_ID,
    NIANZHU_COURSE_NAME,
    normalize_nianzhu_course_sheet_names,
)
from backend.db import engine


def run(
    *,
    apply: bool,
    attendance_sheet_id: int,
    course_name: str,
) -> dict[str, Any]:
    with Session(engine) as session:
        summary = normalize_nianzhu_course_sheet_names(
            session,
            attendance_sheet_id=attendance_sheet_id,
            course_name=course_name,
        )
        if apply:
            session.commit()
        else:
            session.rollback()
        return {
            "mode": "APPLY" if apply else "DRY-RUN",
            **summary,
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="原地精简念住闯关课程 sheet 里的旧课程名前缀，不重建数据。",
    )
    parser.add_argument("--apply", action="store_true", help="实际写入数据库；默认只 dry-run。")
    parser.add_argument("--attendance-sheet-id", type=int, default=NIANZHU_ATTENDANCE_SHEET_NUMERIC_ID)
    parser.add_argument("--course-name", default=NIANZHU_COURSE_NAME)
    args = parser.parse_args()

    summary = run(
        apply=args.apply,
        attendance_sheet_id=args.attendance_sheet_id,
        course_name=args.course_name,
    )
    print(f"[{summary['mode']}] 念住闯关课程 sheet 名称前缀精简")
    print(f"attendance_sheet_id: {summary['attendance_sheet_id']}")
    print(f"course_name: {summary['course_name']}")
    print(f"changed_cells: {summary['changed_cells']}")
    for item in summary["sheets"]:
        print(item)


if __name__ == "__main__":
    main()
