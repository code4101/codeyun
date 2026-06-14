from __future__ import annotations

import datetime as dt
import importlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from openpyxl.utils import get_column_letter


XLPROJECT_ROOT = Path(r"C:\home\chenkunze\slns\xlproject")
KQ_WORK_ROOT = Path(r"C:\home\chenkunze\data\m2112kq5034")
RUN_DIR = KQ_WORK_ROOT / "manual_runs" / "zen_refund_patch_20260614"
SUMMARY_PATH = RUN_DIR / "d260301禅宗46期五阶_step3_only_batched_summary.json"

COURSE_MODULE = "xlsln.kq5034.courses.d260301禅宗46期五阶"


for path in [XLPROJECT_ROOT / "src", XLPROJECT_ROOT / "src" / "xlsln"]:
    text = os.fspath(path)
    if text not in sys.path:
        sys.path.insert(0, text)

import xlproject.loadenv  # noqa: E402,F401


def _json_default(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat(sep=" ")
    return str(value)


def _sheet_number(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("￥", "").replace("元", "")
    if not text or text in {"--", "nan", "NaN"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _snapshot(kq, label: str) -> dict[str, Any]:
    fields = ["视频应返款", "打卡应返款", "总应返款", "当前应返款", "已返款", "返款配置"]
    df = kq.wb.sql_select("考勤表", fields, 4, False)
    valid = kq.过滤有效返款促学金(df["返款配置"].tolist())
    return {
        "label": label,
        "status": kq.get_status(),
        "video_sum": round(sum(_sheet_number(x) for x in df["视频应返款"].tolist()), 2),
        "clockin_sum": round(sum(_sheet_number(x) for x in df["打卡应返款"].tolist()), 2),
        "total_due_sum": round(sum(_sheet_number(x) for x in df["总应返款"].tolist()), 2),
        "refunded_sum": round(sum(_sheet_number(x) for x in df["已返款"].tolist()), 2),
        "current_due_sum": round(sum(_sheet_number(x) for x in df["当前应返款"].tolist()), 2),
        "valid_refund_lines": len(valid),
        "valid_refund_sum": round(sum(kq.解析返款促学金行(x)["金额"] for x in valid), 2),
    }


def main() -> dict[str, Any]:
    os.chdir(KQ_WORK_ROOT)
    RUN_DIR.mkdir(parents=True, exist_ok=True)

    course_mod = importlib.import_module(COURSE_MODULE)
    kq = course_mod.考勤课程()

    before = _snapshot(kq, "before")
    refund_week = kq._delayed_stage5_refund_week()
    lesson_columns = kq._delayed_stage5_lesson_columns(refund_week)

    rows = kq.wb.run_func("locateTableRange", "考勤表", 4, ["视频应返款"])[1]
    row_count = int(rows["end"]) - int(rows["start"]) + 1

    totals = [0 for _ in range(row_count)]
    for start in range(0, len(lesson_columns), 8):
        chunk = lesson_columns[start:start + 8]
        df = kq.wb.sql_select("考勤表", chunk, int(rows["start"]), False)
        for idx, row in df.head(row_count).iterrows():
            for lesson in chunk:
                if "准时完成" in str(row.get(lesson) or ""):
                    totals[int(idx)] += 15

    values = [[total] for total in totals]
    video_col = kq.wb.run_func("findCol", "视频应返款", "考勤表!2:2")
    kq.wb.write_arr(values, f"考勤表!{get_column_letter(video_col)}{rows['start']}", 50)
    kq.set_status(3)
    kq.status = None
    time.sleep(30)

    after = _snapshot(kq, "after")
    summary = {
        "course": kq.course_name,
        "generated_at": dt.datetime.now(),
        "operation": "step3_only_batched_delayed_stage5_video_refund",
        "refund_week": refund_week,
        "lesson_column_count": len(lesson_columns),
        "lesson_column_tail": lesson_columns[-10:],
        "row_count": row_count,
        "before": before,
        "after": after,
        "executed_step4": False,
        "executed_step5": False,
        "executed_step6": False,
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default))
    return summary


if __name__ == "__main__":
    main()
