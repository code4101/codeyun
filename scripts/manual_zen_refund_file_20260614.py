from __future__ import annotations

import csv
import datetime as dt
import importlib
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any


XLPROJECT_ROOT = Path(r"C:\home\chenkunze\slns\xlproject")
KQ_WORK_ROOT = Path(r"C:\home\chenkunze\data\m2112kq5034")
RUN_DIR = KQ_WORK_ROOT / "manual_runs" / "zen_refund_patch_20260614"
SUMMARY_PATH = RUN_DIR / "d260308禅宗1至3期五阶_summary.json"

COURSE_MODULE = "xlsln.kq5034.courses.d260308禅宗1至3期五阶"
FORCE_TAG = "manual260614w14"


for path in [XLPROJECT_ROOT / "src", XLPROJECT_ROOT / "src" / "xlsln"]:
    text = os.fspath(path)
    if text not in sys.path:
        sys.path.insert(0, text)

import xlproject.loadenv  # noqa: E402,F401


class DryRunWeipay:
    def request_file_refund(self, file=None):
        return {
            "submitted": False,
            "completed": False,
            "reason": "dry_run_no_weipay_submit",
            "file": str(file),
        }


def _json_default(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat(sep=" ")
    return str(value)


def _parse_line(line: str) -> list[str]:
    return next(csv.reader([line]))


def main() -> dict[str, Any]:
    os.chdir(KQ_WORK_ROOT)
    RUN_DIR.mkdir(parents=True, exist_ok=True)

    course_mod = importlib.import_module(COURSE_MODULE)
    kq = course_mod.考勤课程()

    raw_lines = kq.wb_get_column_list("返款配置")
    valid_lines = kq.过滤有效返款促学金(raw_lines)
    kq._校验返款配置不超过剩余额度(valid_lines)

    parsed_before = [kq.解析返款促学金行(line) for line in valid_lines]
    total_amount = round(sum(float(item["金额"]) for item in parsed_before), 2)
    amount_counter = Counter(float(item["金额"]) for item in parsed_before)
    order_counter = Counter(item["订单号"] for item in parsed_before)
    business_counter_before = Counter(item["业务单号"] for item in parsed_before)

    result = kq.自动返款促学金(
        valid_lines,
        DryRunWeipay(),
        force_submit=True,
        force_tag=FORCE_TAG,
    )

    file = Path(result["file"])
    output_lines = file.read_text(encoding="utf-8").splitlines()
    parsed_after = [kq.解析返款促学金行(line) for line in output_lines]
    business_counter_after = Counter(item["业务单号"] for item in parsed_after)

    summary = {
        "course": kq.course_name,
        "generated_at": dt.datetime.now(),
        "force_tag": FORCE_TAG,
        "source": "online_sheet_返款配置_column_after_step3",
        "raw_line_count": len(raw_lines),
        "valid_line_count": len(valid_lines),
        "total_amount": total_amount,
        "amount_distribution": dict(sorted(amount_counter.items())),
        "duplicate_orders": {k: v for k, v in order_counter.items() if v > 1},
        "duplicate_business_ids_before": {k: v for k, v in business_counter_before.items() if v > 1},
        "duplicate_business_ids_after": {k: v for k, v in business_counter_after.items() if v > 1},
        "output_file": str(file),
        "dry_run_result": result,
        "sample_before": parsed_before[:5],
        "sample_after": parsed_after[:5],
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default))
    return summary


if __name__ == "__main__":
    main()
