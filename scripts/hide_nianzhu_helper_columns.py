from __future__ import annotations

import argparse
import copy
from pathlib import Path
import sys
import time
from typing import Any

from sqlmodel import Session, select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.api.note_sheets import _normalize_document_columns
from backend.db import engine
from backend.models import SheetDocument


SHEET_HELPER_COLUMNS = {
    20: ["追踪分组", "追踪状态", "追踪截止日", "冻结时间"],
    21: ["追踪分组", "追踪状态", "追踪截止日", "冻结时间", "规则版本", "来源sheet"],
}


def _hide_columns(document: dict[str, Any], helper_columns: list[str]) -> tuple[dict[str, Any], list[str]]:
    columns = _normalize_document_columns(document)
    configs = copy.deepcopy(document.get("column_configs") or {})
    if not isinstance(configs, dict):
        configs = {}

    changed: list[str] = []
    for column in helper_columns:
        if column not in columns:
            continue
        config = dict(configs.get(column) or {})
        if config.get("hidden") is True:
            continue
        config["hidden"] = True
        configs[column] = config
        changed.append(column)

    next_document = copy.deepcopy(document)
    next_document["column_configs"] = configs
    return next_document, changed


def run(*, apply: bool) -> dict[int, list[str]]:
    summary: dict[int, list[str]] = {}
    with Session(engine) as session:
        for numeric_id, helper_columns in SHEET_HELPER_COLUMNS.items():
            sheet = session.exec(select(SheetDocument).where(SheetDocument.numeric_id == numeric_id)).first()
            if sheet is None:
                continue
            document = copy.deepcopy(dict(sheet.document_json or {}))
            next_document, changed = _hide_columns(document, helper_columns)
            summary[numeric_id] = changed
            if apply and changed:
                sheet.document_json = next_document
                sheet.version = int(sheet.version or 1) + 1
                sheet.updated_at = time.time()
                session.add(sheet)

        if apply:
            session.commit()
        else:
            session.rollback()
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="隐藏 20250106 念住闯关报名/考勤表辅助追踪列。")
    parser.add_argument("--apply", action="store_true", help="实际写入数据库。默认只 dry-run。")
    args = parser.parse_args()

    summary = run(apply=args.apply)
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] 隐藏念住闯关辅助列")
    for numeric_id, changed in summary.items():
        print(f"{numeric_id}: {changed}")


if __name__ == "__main__":
    main()
