from __future__ import annotations

import datetime as dt
import importlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from openpyxl.utils import get_column_letter


XLPROJECT_ROOT = Path(r"C:\home\chenkunze\slns\xlproject")
KQ_WORK_ROOT = Path(r"C:\home\chenkunze\data\m2112kq5034")
RUN_DIR = KQ_WORK_ROOT / "manual_runs" / "zen_refund_patch_20260614"
SUMMARY_PATH = RUN_DIR / "d260301禅宗46期五阶_weipay_refunded_reconcile_summary.json"

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


def _normalize_order_id(value: Any) -> str:
    return str(value or "").strip().lstrip("`'")


def _is_valid_order_id(value: str) -> bool:
    return bool(re.fullmatch(r"\w{6}-\w{7}-\w{4}", value) or re.fullmatch(r"MA\d{22}", value))


def _refund_amount(row: dict[str, Any]) -> float:
    return _sheet_number(row.get("退款金额"))


def _sum_successful_refunds(rows: list[dict[str, Any]]) -> tuple[float, list[str], float]:
    total = 0.0
    raw_total = 0.0
    statuses: list[str] = []
    for row in rows:
        amount = _refund_amount(row)
        raw_total += amount
        status = str(row.get("退款状态") or "").strip()
        if status and status not in statuses:
            statuses.append(status)
        if "成功" in status:
            total += amount
    return round(total, 2), statuses, round(raw_total, 2)


def main() -> dict[str, Any]:
    os.chdir(KQ_WORK_ROOT)
    RUN_DIR.mkdir(parents=True, exist_ok=True)

    course_mod = importlib.import_module(COURSE_MODULE)
    kq = course_mod.考勤课程()

    loc = kq.wb.run_func("locateTableRange", "考勤表", 4, ["姓名", "商户订单号", "已返款"])
    rows_range = loc[1]
    cols = loc[2]
    refunded_col_letter = get_column_letter(int(cols["已返款"]))
    data_start_row = int(rows_range["start"])

    df = kq.wb.sql_select("考勤表", ["姓名", "商户订单号", "已返款"], 4, False)
    records: list[dict[str, Any]] = []
    unique_orders: list[str] = []
    for idx, row in df.iterrows():
        order_id = _normalize_order_id(row.get("商户订单号"))
        record = {
            "index": int(idx),
            "row_num": data_start_row + int(idx),
            "name": str(row.get("姓名") or "").strip(),
            "order_id": order_id,
            "sheet_refunded_before": _sheet_number(row.get("已返款")),
            "valid_order": _is_valid_order_id(order_id),
        }
        records.append(record)
        if record["valid_order"] and order_id not in unique_orders:
            unique_orders.append(order_id)

    query_results: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    for pos, order_id in enumerate(unique_orders, start=1):
        try:
            detail_rows = kq.weipay.search_refund_details(order_id, query_type="auto", raise_err=True)
            successful_total, statuses, raw_total = _sum_successful_refunds(detail_rows)
            query_results[order_id] = {
                "ok": True,
                "row_count": len(detail_rows),
                "successful_refund_total": successful_total,
                "raw_refund_total": raw_total,
                "statuses": statuses,
                "rows": detail_rows,
            }
            print(
                json.dumps(
                    {
                        "progress": f"{pos}/{len(unique_orders)}",
                        "order_id": order_id,
                        "successful_refund_total": successful_total,
                        "row_count": len(detail_rows),
                        "statuses": statuses,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        except Exception as exc:
            failure = {
                "order_id": order_id,
                "error": f"{type(exc).__name__}: {exc}",
            }
            query_results[order_id] = {"ok": False, **failure}
            failures.append(failure)
            print(json.dumps({"progress": f"{pos}/{len(unique_orders)}", **failure}, ensure_ascii=False), flush=True)

    if failures:
        summary = {
            "course": kq.course_name,
            "generated_at": dt.datetime.now(),
            "mode": "query_only_write_aborted",
            "reason": "weipay_query_failures",
            "failures": failures,
            "valid_order_count": len(unique_orders),
            "records": records,
            "query_results": query_results,
        }
        SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default))
        return summary

    corrected_values: list[list[Any]] = []
    changes: list[dict[str, Any]] = []
    for record in records:
        order_id = record["order_id"]
        if record["valid_order"]:
            actual = float(query_results[order_id]["successful_refund_total"])
        else:
            actual = record["sheet_refunded_before"]
        corrected_values.append([actual])
        delta = round(actual - float(record["sheet_refunded_before"]), 2)
        record["weipay_refunded_actual"] = actual
        record["delta"] = delta
        if record["valid_order"]:
            record["refund_detail_count"] = int(query_results[order_id]["row_count"])
            record["refund_statuses"] = query_results[order_id]["statuses"]
        if abs(delta) > 0.001:
            changes.append(record)

    kq.wb.write_arr(corrected_values, f"考勤表!{refunded_col_letter}{data_start_row}", 50)
    time.sleep(3)

    df_after = kq.wb.sql_select("考勤表", ["姓名", "商户订单号", "已返款"], 4, False)
    after_by_row: dict[int, float] = {
        data_start_row + int(idx): _sheet_number(row.get("已返款"))
        for idx, row in df_after.iterrows()
    }
    verify_failures = []
    for record in records:
        expected = float(record.get("weipay_refunded_actual", record["sheet_refunded_before"]))
        actual_after = after_by_row.get(int(record["row_num"]), None)
        record["sheet_refunded_after"] = actual_after
        if actual_after is None or abs(float(actual_after) - expected) > 0.001:
            verify_failures.append({
                "row_num": record["row_num"],
                "name": record["name"],
                "order_id": record["order_id"],
                "expected": expected,
                "actual_after": actual_after,
            })

    summary = {
        "course": kq.course_name,
        "generated_at": dt.datetime.now(),
        "mode": "weipay_detail_reconcile_write_l_column",
        "valid_order_count": len(unique_orders),
        "row_count": len(records),
        "refunded_column": refunded_col_letter,
        "written_range": f"考勤表!{refunded_col_letter}{data_start_row}:{refunded_col_letter}{data_start_row + len(records) - 1}",
        "change_count": len(changes),
        "sheet_sum_before": round(sum(float(r["sheet_refunded_before"]) for r in records), 2),
        "weipay_actual_sum": round(sum(float(r.get("weipay_refunded_actual", r["sheet_refunded_before"])) for r in records), 2),
        "sheet_sum_after": round(sum(float(r.get("sheet_refunded_after") or 0) for r in records), 2),
        "changes": changes,
        "verify_failures": verify_failures,
        "records": records,
        "query_results": query_results,
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default))
    return summary


if __name__ == "__main__":
    main()
