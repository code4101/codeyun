from __future__ import annotations

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
SUMMARY_PATH = RUN_DIR / "d260308禅宗1至3期五阶_step4_step5_summary.json"

COURSE_MODULE = "xlsln.kq5034.courses.d260308禅宗1至3期五阶"
FORCE_TAG = "manual260614w14"


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


def _money_snapshot(kq) -> dict[str, Any]:
    df = kq._读取返款额度校验表()
    return {
        "rows": int(len(df)),
        "current_due_sum": round(sum(_sheet_number(x) for x in df["当前应返款"].tolist()), 2),
        "refunded_sum": round(sum(_sheet_number(x) for x in df["已返款"].tolist()), 2),
        "total_due_sum": round(sum(_sheet_number(x) for x in df["总应返款"].tolist()), 2)
        if "总应返款" in df.columns
        else None,
    }


def main() -> dict[str, Any]:
    os.chdir(KQ_WORK_ROOT)
    RUN_DIR.mkdir(parents=True, exist_ok=True)

    course_mod = importlib.import_module(COURSE_MODULE)
    kq = course_mod.考勤课程()

    status_before = kq.get_status()
    raw_lines = kq.wb_get_column_list("返款配置")
    valid_lines = kq.过滤有效返款促学金(raw_lines)
    if not valid_lines:
        raise RuntimeError("在线表返款配置列没有有效返款行，已停止 step4/5")
    kq._校验返款配置不超过剩余额度(valid_lines)

    parsed = [kq.解析返款促学金行(line) for line in valid_lines]
    total_amount = round(sum(float(item["金额"]) for item in parsed), 2)
    amount_counter = Counter(float(item["金额"]) for item in parsed)
    business_before = [item["业务单号"] for item in parsed]

    before_money = _money_snapshot(kq)
    result = kq.自动返款促学金(
        valid_lines,
        kq.weipay,
        force_submit=True,
        force_tag=FORCE_TAG,
    )
    if not result or not result.get("submitted"):
        raise RuntimeError(f"step4 未返回已提交状态，停止 step5：{result!r}")

    status_after_step4_submit = kq.get_status()
    if status_before < 4:
        kq.set_status(4)

    step5_mode = "skipped_no_current_due"
    if total_amount > 0:
        if kq.get_status() < 5:
            kq.step5()
            step5_mode = "native_step5"
        else:
            kq.wb.run_func("step5_更新已返款")
            step5_mode = "direct_run_func_status_already_ge_5"
            kq.set_status(max(kq.get_status(), 5))

    after_money = _money_snapshot(kq)
    status_after_step5 = kq.get_status()

    submitted_file = Path(result.get("file", ""))
    submitted_lines = submitted_file.read_text(encoding="utf-8").splitlines() if submitted_file.exists() else []
    submitted_items = [kq.解析返款促学金行(line) for line in submitted_lines]
    business_after = [item["业务单号"] for item in submitted_items]

    summary = {
        "course": kq.course_name,
        "generated_at": dt.datetime.now(),
        "force_tag": FORCE_TAG,
        "status_before": status_before,
        "status_after_step4_submit": status_after_step4_submit,
        "status_after_step5": status_after_step5,
        "valid_line_count": len(valid_lines),
        "total_amount": total_amount,
        "amount_distribution": dict(sorted(amount_counter.items())),
        "duplicate_business_ids_before": {k: v for k, v in Counter(business_before).items() if v > 1},
        "duplicate_business_ids_after": {k: v for k, v in Counter(business_after).items() if v > 1},
        "before_money": before_money,
        "after_money": after_money,
        "step4_result": result,
        "step5_mode": step5_mode,
        "submitted_file": str(submitted_file),
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default))
    return summary


if __name__ == "__main__":
    main()
