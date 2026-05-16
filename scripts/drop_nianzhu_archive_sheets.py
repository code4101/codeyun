from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

from sqlalchemy import delete
from sqlmodel import Session, select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.db import engine
from backend.models import ResourceAccessGrant, SheetDocument, WorkbookDocument, WorkbookSheetLink


WORKBOOK_NUMERIC_ID = 7
ARCHIVE_SHEET_NUMERIC_IDS = (22, 23)
RESOURCE_TYPE_SHEET = "sheet"


def run(*, apply: bool) -> dict[str, object]:
    with Session(engine) as session:
        workbook = session.exec(
            select(WorkbookDocument).where(WorkbookDocument.numeric_id == WORKBOOK_NUMERIC_ID)
        ).first()
        if workbook is None:
            raise RuntimeError(f"找不到工作簿 numeric_id={WORKBOOK_NUMERIC_ID}")

        sheets = session.exec(
            select(SheetDocument).where(SheetDocument.numeric_id.in_(ARCHIVE_SHEET_NUMERIC_IDS))
        ).all()
        sheet_ids = [sheet.id for sheet in sheets]
        summary = {
            "workbook": workbook.title,
            "found": [(sheet.numeric_id, sheet.title) for sheet in sheets],
            "deleted": len(sheets) if apply else 0,
        }

        if not apply:
            return summary

        if sheet_ids:
            session.exec(delete(WorkbookSheetLink).where(WorkbookSheetLink.sheet_id.in_(sheet_ids)))
            session.exec(
                delete(ResourceAccessGrant)
                .where(ResourceAccessGrant.resource_type == RESOURCE_TYPE_SHEET)
                .where(ResourceAccessGrant.resource_id.in_(sheet_ids))
            )
            for sheet in sheets:
                session.delete(sheet)
            workbook.updated_at = time.time()
            session.add(workbook)
            session.commit()
        return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="删除 20250106 念住闯关已合并归档的旧考勤 sheet。")
    parser.add_argument("--apply", action="store_true", help="实际删除。默认只 dry-run。")
    args = parser.parse_args()

    summary = run(apply=args.apply)
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] 清理念住闯关归档旧sheet")
    for key, value in summary.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
