from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

from sqlmodel import Session

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.nianzhu_course_sheets import (
    NIANZHU_ATTENDANCE_SHEET_NUMERIC_ID,
    NIANZHU_COURSE_NAME,
    NIANZHU_WORKBOOK_NUMERIC_ID,
    list_nianzhu_course_storage_sheets,
    materialize_nianzhu_course_sheets,
    rebuild_nianzhu_attendance_from_course_sheets,
)
from backend.db import engine


def _print_summary(title: str, summary: dict[str, Any]) -> None:
    print(title)
    for key, value in summary.items():
        print(f"{key}: {value}")


def run(
    *,
    apply: bool,
    workbook_id: int,
    attendance_sheet_id: int,
    course_name: str,
    replace: bool,
    rebuild: bool,
    include_frozen: bool,
) -> dict[str, Any]:
    with Session(engine) as session:
        materialize_summary = materialize_nianzhu_course_sheets(
            session,
            workbook_id=workbook_id,
            attendance_sheet_id=attendance_sheet_id,
            course_name=course_name,
            replace=replace,
        )
        rebuild_summary: dict[str, Any] | None = None
        if rebuild:
            rebuild_summary = rebuild_nianzhu_attendance_from_course_sheets(
                session,
                attendance_sheet_id=attendance_sheet_id,
                active_only=not include_frozen,
            )
        sheet_summary = list_nianzhu_course_storage_sheets(session, workbook_id=workbook_id)

        if apply:
            session.commit()
        else:
            session.rollback()

        return {
            "mode": "APPLY" if apply else "DRY-RUN",
            "materialize": materialize_summary,
            "rebuild": rebuild_summary,
            "workbook_sheets": sheet_summary,
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="把 20250106 念住闯关拆成自包含课程 sheet，并可从 sheet 重算考勤表。",
    )
    parser.add_argument("--apply", action="store_true", help="实际写入数据库；默认只 dry-run。")
    parser.add_argument("--workbook-id", type=int, default=NIANZHU_WORKBOOK_NUMERIC_ID)
    parser.add_argument("--attendance-sheet-id", type=int, default=NIANZHU_ATTENDANCE_SHEET_NUMERIC_ID)
    parser.add_argument("--course-name", default=NIANZHU_COURSE_NAME)
    parser.add_argument("--replace", action="store_true", help="覆盖已存在的视频/打卡配置和数据 sheet。")
    parser.add_argument("--rebuild", action="store_true", help="创建/更新数据 sheet 后，从这些 sheet 重算考勤表。")
    parser.add_argument("--include-frozen", action="store_true", help="重算时也更新非 B 组归档行；默认只更新 B 组。")
    args = parser.parse_args()

    summary = run(
        apply=args.apply,
        workbook_id=args.workbook_id,
        attendance_sheet_id=args.attendance_sheet_id,
        course_name=args.course_name,
        replace=args.replace,
        rebuild=args.rebuild,
        include_frozen=args.include_frozen,
    )
    print(f"[{summary['mode']}] 念住闯关课程 sheet 主存储")
    _print_summary("materialize:", summary["materialize"])
    if summary["rebuild"] is not None:
        _print_summary("rebuild:", summary["rebuild"])
    print("workbook_sheets:")
    for item in summary["workbook_sheets"]:
        print(item)


if __name__ == "__main__":
    main()
