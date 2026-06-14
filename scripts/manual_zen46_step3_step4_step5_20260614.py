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
SUMMARY_PATH = RUN_DIR / "d260301禅宗46期五阶_step3_step4_step5_summary.json"

COURSE_MODULE = "xlsln.kq5034.courses.d260301禅宗46期五阶"
FORCE_TAG = "manual260614w14z46"


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


def _refund_snapshot(kq) -> dict[str, Any]:
    raw_lines = kq.wb_get_column_list("返款配置")
    valid_lines = kq.过滤有效返款促学金(raw_lines)
    parsed = [kq.解析返款促学金行(line) for line in valid_lines]
    return {
        "raw_line_count": len(raw_lines),
        "valid_lines": valid_lines,
        "valid_line_count": len(valid_lines),
        "valid_line_sum": round(sum(float(item["金额"]) for item in parsed), 2),
        "amount_distribution": dict(sorted(Counter(float(item["金额"]) for item in parsed).items())),
        "duplicate_business_ids": {k: v for k, v in Counter(item["业务单号"] for item in parsed).items() if v > 1},
    }


def _positive_configured_due_sum(kq, valid_lines: list[str]) -> float:
    df = kq._读取返款额度校验表()
    order_ids = {kq.解析返款促学金行(line)["订单号"] for line in valid_lines}
    total = 0.0
    for _, row in df.iterrows():
        order_id = str(row.get("商户订单号") or "").strip().lstrip("`'")
        if order_id in order_ids:
            current_due = _sheet_number(row.get("当前应返款"))
            if current_due > 0:
                total += current_due
    return round(total, 2)


def main() -> dict[str, Any]:
    os.chdir(KQ_WORK_ROOT)
    RUN_DIR.mkdir(parents=True, exist_ok=True)

    course_mod = importlib.import_module(COURSE_MODULE)
    kq = course_mod.考勤课程()

    status_before = kq.get_status()
    try:
        sheet_week_before = int(float(kq.wb.run_func("get禅宗周次") or 0))
    except Exception as exc:
        sheet_week_before = f"{type(exc).__name__}: {exc}"
    refund_week = kq._delayed_stage5_refund_week()
    lesson_columns = kq._delayed_stage5_lesson_columns(refund_week)

    before_money = _money_snapshot(kq)
    before_refund = _refund_snapshot(kq)

    kq._step3_delayed_stage5_video_refund(status=3)
    kq.status = None
    after_step3_status = kq.get_status()
    after_step3_money = _money_snapshot(kq)
    after_step3_refund = _refund_snapshot(kq)

    valid_lines = after_step3_refund["valid_lines"]
    if not valid_lines:
        summary = {
            "course": kq.course_name,
            "generated_at": dt.datetime.now(),
            "force_tag": FORCE_TAG,
            "status_before": status_before,
            "after_step3_status": after_step3_status,
            "refund_week": refund_week,
            "sheet_week_before": sheet_week_before,
            "lesson_column_count": len(lesson_columns),
            "before_money": before_money,
            "before_refund": {k: v for k, v in before_refund.items() if k != "valid_lines"},
            "after_step3_money": after_step3_money,
            "after_step3_refund": {k: v for k, v in after_step3_refund.items() if k != "valid_lines"},
            "step4_result": None,
            "step5_mode": "skipped_no_valid_lines",
        }
        SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default))
        return summary

    current_due_after_step3 = _positive_configured_due_sum(kq, valid_lines)
    refund_line_sum = after_step3_refund["valid_line_sum"]
    if abs(float(current_due_after_step3) - float(refund_line_sum)) > 0.01:
        raise RuntimeError(
            f"step3 后正向配置应返款合计 {current_due_after_step3} 与返款配置合计 {refund_line_sum} 不一致，已停止 step4"
        )

    kq._校验返款配置不超过剩余额度(valid_lines)
    result = kq.自动返款促学金(
        valid_lines,
        kq.weipay,
        force_submit=True,
        force_tag=FORCE_TAG,
    )
    if not result or not result.get("submitted"):
        raise RuntimeError(f"step4 未返回已提交状态，停止 step5：{result!r}")

    if kq.get_status() < 4:
        kq.set_status(4)
        kq.status = 4
    kq.step5()
    kq.status = None

    after_step5_money = _money_snapshot(kq)
    after_step5_refund = _refund_snapshot(kq)
    status_after_step5 = kq.get_status()

    submitted_file = Path(result.get("file", ""))
    submitted_lines = submitted_file.read_text(encoding="utf-8").splitlines() if submitted_file.exists() else []
    submitted_items = [kq.解析返款促学金行(line) for line in submitted_lines]

    summary = {
        "course": kq.course_name,
        "generated_at": dt.datetime.now(),
        "force_tag": FORCE_TAG,
        "status_before": status_before,
        "after_step3_status": after_step3_status,
        "status_after_step5": status_after_step5,
        "refund_week": refund_week,
        "sheet_week_before": sheet_week_before,
        "lesson_column_count": len(lesson_columns),
        "lesson_column_tail": lesson_columns[-8:],
        "before_money": before_money,
        "before_refund": {k: v for k, v in before_refund.items() if k != "valid_lines"},
        "after_step3_money": after_step3_money,
        "after_step3_refund": {k: v for k, v in after_step3_refund.items() if k != "valid_lines"},
        "after_step5_money": after_step5_money,
        "after_step5_refund": {k: v for k, v in after_step5_refund.items() if k != "valid_lines"},
        "duplicate_business_ids_after_submit": {
            k: v for k, v in Counter(item["业务单号"] for item in submitted_items).items() if v > 1
        },
        "step4_result": result,
        "step5_mode": "native_step5",
        "submitted_file": str(submitted_file),
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default))
    return summary


if __name__ == "__main__":
    main()
