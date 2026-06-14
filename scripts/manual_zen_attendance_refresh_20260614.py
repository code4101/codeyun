from __future__ import annotations

import datetime as _dt
import importlib
import json
import os
import re
import sys
import traceback
from pathlib import Path
from typing import Any


CODEYUN_ROOT = Path(r"C:\home\chenkunze\slns\codeyun")
XLPROJECT_ROOT = Path(r"C:\home\chenkunze\slns\xlproject")
KQ_WORK_ROOT = Path(r"C:\home\chenkunze\data\m2112kq5034")
RUN_DIR = KQ_WORK_ROOT / "manual_runs" / "zen_refresh_20260614"
SUMMARY_PATH = RUN_DIR / "summary.json"
JSONL_PATH = RUN_DIR / "events.jsonl"

for path in [XLPROJECT_ROOT / "src", XLPROJECT_ROOT / "src" / "xlsln"]:
    text = os.fspath(path)
    if text not in sys.path:
        sys.path.insert(0, text)

import xlproject.loadenv  # noqa: E402,F401

from kq5034.db import get_kqdb  # noqa: E402
from xlsln.kq5034.courses import codeyun_course as cy_course  # noqa: E402


TRADITIONAL_COURSES = [
    "d260308禅宗1至3期五阶",
    "d260301禅宗46期五阶",
    "d260308禅宗8期4点5阶",
    "d260412禅宗9期三阶",
    "d260412禅宗10期三阶",
    "d260412禅宗11期二阶",
    "d260412禅宗12期一阶",
]

CODEYUN_COURSES = [
    "d260517修道班7期5阶",
]


def _json_default(value: Any) -> Any:
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat(sep=" ")
    return str(value)


def log(event: str, **payload: Any) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "event": event,
        **payload,
    }
    line = json.dumps(record, ensure_ascii=False, default=_json_default)
    print(line, flush=True)
    with JSONL_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _sheet_number(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip().replace(",", "").replace("￥", "").replace("元", "")
    if not text or text in {"--", "nan", "NaN"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    return False


def _lesson_week(lesson_name: str) -> int | None:
    match = re.search(r"第\s*(\d+)\s*周", lesson_name)
    return int(match.group(1)) if match else None


def _lesson_short_name(lesson_name: str) -> str:
    return str(lesson_name).split("=", 1)[-1].strip()


def _snapshot_refund_files() -> set[str]:
    root = KQ_WORK_ROOT / "返款表"
    if not root.exists():
        return set()
    items: set[str] = set()
    for path in root.rglob("*"):
        if path.is_file():
            try:
                stat = path.stat()
                rel = path.relative_to(root).as_posix()
                items.add(f"{rel}|{stat.st_size}|{int(stat.st_mtime)}")
            except OSError:
                continue
    return items


def _query_target_lessons(db: Any, course_name: str, target_week: int) -> list[dict[str, Any]]:
    rows = db.exec2dict(
        """
        SELECT *
        FROM lesson_table
        WHERE lesson_name LIKE %s
        ORDER BY lesson_id
        """,
        [f"{course_name}-%"],
    ).fetchall()
    return [dict(row) for row in rows if _lesson_week(str(row["lesson_name"])) == target_week]


def _lesson_data_counts(db: Any, lesson_ids: list[int]) -> dict[int, int]:
    if not lesson_ids:
        return {}
    result: dict[int, int] = {}
    for lesson_id in lesson_ids:
        result[int(lesson_id)] = int(
            db.exec2one(
                "SELECT COUNT(1) FROM lesson_data_table WHERE lesson_id=%s",
                [int(lesson_id)],
            )
            or 0
        )
    return result


def _force_download_traditional_lessons(kq: Any, lessons: list[dict[str, Any]]) -> dict[str, Any]:
    imported: dict[int, int] = {}
    errors: list[str] = []
    if not lessons:
        return {"imported": imported, "errors": ["target lesson list is empty"]}

    if int(getattr(kq, "shop_id", 0)) == 1:
        kq.xe2.switch_shop("5034山中薪")
    elif int(getattr(kq, "shop_id", 0)) == 2:
        kq.xe2.switch_shop("宗门学府")

    for lesson in lessons:
        lesson_id = int(lesson["lesson_id"])
        lesson_name = str(lesson["lesson_name"])
        log("traditional_lesson_export_start", course=kq.course_name, lesson_id=lesson_id, lesson_name=lesson_name)
        try:
            file = kq.xe2.export_lesson_data(lesson)
            if file is None:
                raise RuntimeError("课次数据导出未获得文件")
            kq.kqdb.execute("DELETE FROM lesson_data_table WHERE lesson_id=%s", [lesson_id])
            kq.kqdb.commit()
            count = int(kq.kqdb.update_lesson_data_from_file(lesson_id, file) or 0)
            if count <= 0:
                raise RuntimeError(f"课次数据文件导入0行：{file}")
            next_update = kq._计算课次下一次需要更新的时间点(lesson)
            kq.kqdb.update_row("lesson_table", {"next_update": next_update}, {"lesson_id": lesson_id}, commit=True)
            imported[lesson_id] = count
            log(
                "traditional_lesson_export_done",
                course=kq.course_name,
                lesson_id=lesson_id,
                imported=count,
                next_update=next_update,
            )
        except Exception as exc:
            errors.append(f"{lesson_name}: {type(exc).__name__}: {exc}")
            log("traditional_lesson_export_error", course=kq.course_name, lesson_id=lesson_id, error=errors[-1])
            break
    return {"imported": imported, "errors": errors}


def _read_wps_nonempty_columns(kq: Any, columns: list[str]) -> dict[str, Any]:
    try:
        df = kq.wb.sql_select("考勤表", columns, 4, filter_empty_rows=False)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "nonempty_cells": 0, "row_count": 0}
    nonempty = 0
    for col in columns:
        if col not in df.columns:
            continue
        nonempty += int(sum(0 if _is_blank(value) else 1 for value in df[col].tolist()))
    return {"ok": True, "error": "", "nonempty_cells": nonempty, "row_count": int(len(df))}


def _traditional_refund_summary(kq: Any) -> dict[str, Any]:
    columns = ["姓名", "当前应返款", "返款配置", "视频应返款", "打卡应返款", "总应返款", "已返款"]
    try:
        df = kq.wb.sql_select("考勤表", columns, 4, filter_empty_rows=False)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    current_total = 0.0
    current_rows = 0
    if "当前应返款" in df.columns:
        for value in df["当前应返款"].tolist():
            amount = _sheet_number(value)
            if amount > 0:
                current_rows += 1
                current_total += amount

    config_lines: list[str] = []
    config_total = 0.0
    if "返款配置" in df.columns:
        raw_lines = [str(x).strip() for x in df["返款配置"].tolist() if str(x).strip()]
        try:
            config_lines = list(kq.过滤有效返款促学金(raw_lines))
        except Exception:
            config_lines = raw_lines
        for line in config_lines:
            try:
                config_total += float(kq.解析返款促学金行(line)["金额"])
            except Exception:
                pass
    return {
        "ok": True,
        "current_due_total": round(current_total, 2),
        "current_due_rows": current_rows,
        "refund_config_total": round(config_total, 2),
        "refund_config_rows": len(config_lines),
    }


def run_traditional_course(course_name: str) -> dict[str, Any]:
    log("course_start", course=course_name, mode="traditional")
    module = importlib.import_module(f"xlsln.kq5034.courses.{course_name}")
    kq = module.考勤课程()
    target_week = None
    try:
        if hasattr(kq, "_use_delayed_stage5_video_refund") and kq._use_delayed_stage5_video_refund():
            target_week = int(kq._delayed_stage5_refund_week())
    except Exception:
        target_week = None
    if not target_week:
        target_week = int(float(kq.wb.run_func("get禅宗周次")))

    db = kq.kqdb
    lessons = _query_target_lessons(db, course_name, target_week)
    lesson_ids = [int(row["lesson_id"]) for row in lessons]
    short_columns = [_lesson_short_name(str(row["lesson_name"])) for row in lessons]
    before_counts = _lesson_data_counts(db, lesson_ids)
    log(
        "traditional_target_week",
        course=course_name,
        target_week=target_week,
        lesson_ids=lesson_ids,
        before_counts=before_counts,
        columns=short_columns,
    )

    lesson_result = _force_download_traditional_lessons(kq, lessons)

    clockin_error = ""
    try:
        log("traditional_clockin_start", course=course_name)
        kq.update_clockin(f"{course_name}-*")
        log("traditional_clockin_done", course=course_name)
    except Exception as exc:
        clockin_error = f"{type(exc).__name__}: {exc}"
        log("traditional_clockin_error", course=course_name, error=clockin_error)

    # Force step2 and step3 without entering later steps.
    step_errors: list[str] = []
    try:
        kq.set_status(1)
        kq.status = 1
        log("traditional_step2_start", course=course_name)
        kq.step2()
        kq.status = 2
        log("traditional_step2_done", course=course_name)
        log("traditional_step3_start", course=course_name)
        kq.step3()
        kq.status = 3
        log("traditional_step3_done", course=course_name)
    except Exception as exc:
        step_errors.append(f"{type(exc).__name__}: {exc}")
        log("traditional_step_error", course=course_name, error=step_errors[-1], traceback=traceback.format_exc())

    after_counts = _lesson_data_counts(db, lesson_ids)
    sheet_check = _read_wps_nonempty_columns(kq, short_columns)
    refund = _traditional_refund_summary(kq)
    errors = [*lesson_result["errors"], *step_errors]
    if clockin_error:
        errors.append(f"clockin: {clockin_error}")
    ok = (
        not errors
        and bool(lesson_ids)
        and all(count > 0 for count in after_counts.values())
        and bool(sheet_check.get("ok"))
        and int(sheet_check.get("nonempty_cells") or 0) > 0
    )
    result = {
        "course": course_name,
        "mode": "traditional",
        "ok": ok,
        "target_week": target_week,
        "lesson_ids": lesson_ids,
        "lesson_columns": short_columns,
        "lesson_data_counts_before": before_counts,
        "lesson_data_counts_after": after_counts,
        "lesson_imported": lesson_result["imported"],
        "sheet_check": sheet_check,
        "refund": refund,
        "errors": errors,
    }
    log("course_done", **result)
    return result


def _storage_row_nonempty(kq: Any, table: dict[str, Any]) -> list[dict[str, Any]]:
    return kq._nonempty_storage_rows(table)


def _codeyun_video_counts(rows: list[dict[str, Any]], lesson_ids: list[int]) -> dict[int, int]:
    counts = {int(x): 0 for x in lesson_ids}
    for row in rows:
        lid = int(_sheet_number(row.get("lesson_id")))
        if lid in counts:
            counts[lid] += 1
    return counts


def _codeyun_refund_summary(kq: Any, table: dict[str, Any]) -> dict[str, Any]:
    current_total = 0.0
    current_rows = 0
    for row in table.get("rows", []):
        amount = _sheet_number(row.get("当前应返款"))
        if amount > 0:
            current_rows += 1
            current_total += amount
    config_total = 0.0
    config_rows = 0
    build_error = ""
    try:
        items = kq._build_refund_line_items(table.get("rows", []), table=table)
        config_rows = len(items)
        config_total = sum(float(item.get("amount") or 0) for item in items)
    except Exception as exc:
        build_error = f"{type(exc).__name__}: {exc}"
    return {
        "ok": not build_error,
        "current_due_total": round(current_total, 2),
        "current_due_rows": current_rows,
        "refund_config_total": round(config_total, 2),
        "refund_config_rows": config_rows,
        "build_error": build_error,
    }


def run_codeyun_course(course_name: str) -> dict[str, Any]:
    log("course_start", course=course_name, mode="codeyun")
    module = importlib.import_module(f"xlsln.kq5034.courses.{course_name}")
    kq = module.考勤课程()
    today = _dt.date.today()
    refund_period_day = (today - kq.start_date).days
    target_week = ((refund_period_day - 1) // 7) + 1 if refund_period_day > 0 else 0

    refs = kq._nianzhu_course_storage_refs()
    config_table = kq.get_sheet_table(refs["video_config"])
    data_table = kq.get_sheet_table(refs["video_data"])
    config_rows = _storage_row_nonempty(kq, config_table)
    data_rows = _storage_row_nonempty(kq, data_table)
    lessons = [row for row in config_rows if _lesson_week(str(row.get("lesson_name") or "")) == target_week]
    lesson_ids = [int(_sheet_number(row.get("lesson_id"))) for row in lessons]
    short_columns = [_lesson_short_name(str(row.get("lesson_name") or "")) for row in lessons]
    before_counts = _codeyun_video_counts(data_rows, lesson_ids)

    log(
        "codeyun_target_week",
        course=course_name,
        target_week=target_week,
        lesson_ids=lesson_ids,
        before_counts=before_counts,
        columns=short_columns,
    )

    errors: list[str] = []
    imported: dict[int, int] = {}
    try:
        kq._switch_shop_for_local_step1()
        max_id = max([int(_sheet_number(row.get("lesson_data_id"))) for row in data_rows] or [0])
        kept_rows = [
            row for row in data_rows
            if int(_sheet_number(row.get("lesson_id"))) not in set(lesson_ids)
        ]
        imported_rows_all: list[dict[str, Any]] = []
        config_updates: list[dict[str, Any]] = []
        now = _dt.datetime.now()
        for config_row in lessons:
            local_lesson_id = int(_sheet_number(config_row.get("lesson_id")))
            lesson_name = str(config_row.get("lesson_name") or "")
            log("codeyun_lesson_export_start", course=course_name, lesson_id=local_lesson_id, lesson_name=lesson_name)
            file = kq._export_lesson_data_for_config(config_row)
            parsed: list[dict[str, Any]] = []
            if file is not None:
                parsed = cy_course._parse_nianzhu_lesson_export_rows(
                    file,
                    lesson_id=local_lesson_id,
                    lesson_name=lesson_name,
                    video_duration=_sheet_number(config_row.get("video_duration")),
                    update_time=now,
                )
            if not parsed:
                raise RuntimeError(f"{lesson_name}: 课次数据文件导入0行")
            for row in parsed:
                max_id += 1
                row["lesson_data_id"] = max_id
            imported_rows_all.extend(parsed)
            imported[local_lesson_id] = len(parsed)
            config_updates.append({
                "lesson_id": local_lesson_id,
                "next_update": cy_course._compute_nianzhu_next_lesson_update(
                    config_row,
                    course_name=course_name,
                    now=now,
                ),
            })
            log("codeyun_lesson_export_done", course=course_name, lesson_id=local_lesson_id, imported=len(parsed))

        new_rows = kept_rows + imported_rows_all
        cy_course._renumber_storage_rows(new_rows, "lesson_data_id")
        kq._replace_storage_rows(refs["video_data"], new_rows)
        if config_updates:
            kq.sheet_client.write_fields(
                refs["video_config"],
                key_field="lesson_id",
                fields=["next_update"],
                rows=config_updates,
                expected_version=config_table.get("version"),
            )
    except Exception as exc:
        errors.append(f"video: {type(exc).__name__}: {exc}")
        log("codeyun_lesson_export_error", course=course_name, error=errors[-1], traceback=traceback.format_exc())

    try:
        log("codeyun_clockin_start", course=course_name)
        clockin_summary = kq._update_nianzhu_clockin_storage(refs)
        clockin_errors = [str(x) for x in clockin_summary.get("clockin_errors", [])]
        errors.extend([f"clockin: {x}" for x in clockin_errors])
        log("codeyun_clockin_done", course=course_name, summary=clockin_summary)
    except Exception as exc:
        errors.append(f"clockin: {type(exc).__name__}: {exc}")
        log("codeyun_clockin_error", course=course_name, error=errors[-1])

    step_errors: list[str] = []
    try:
        kq.set_status(1)
        log("codeyun_step2_start", course=course_name)
        kq.step2(status=2)
        log("codeyun_step2_done", course=course_name)
        log("codeyun_step3_start", course=course_name)
        kq.step3(status=3)
        log("codeyun_step3_done", course=course_name)
    except Exception as exc:
        step_errors.append(f"{type(exc).__name__}: {exc}")
        log("codeyun_step_error", course=course_name, error=step_errors[-1], traceback=traceback.format_exc())

    refreshed_data_rows = _storage_row_nonempty(kq, kq.get_sheet_table(refs["video_data"]))
    after_counts = _codeyun_video_counts(refreshed_data_rows, lesson_ids)
    attendance_table = kq.get_sheet_table(kq.sheets.attendance)
    nonempty = 0
    for row in attendance_table.get("rows", []):
        for col in short_columns:
            if not _is_blank(row.get(col)):
                nonempty += 1
    sheet_check = {
        "ok": True,
        "error": "",
        "nonempty_cells": nonempty,
        "row_count": len(attendance_table.get("rows", [])),
    }
    refund = _codeyun_refund_summary(kq, attendance_table)
    errors.extend(step_errors)
    ok = (
        not errors
        and bool(lesson_ids)
        and all(count > 0 for count in after_counts.values())
        and nonempty > 0
    )
    result = {
        "course": course_name,
        "mode": "codeyun",
        "ok": ok,
        "target_week": target_week,
        "lesson_ids": lesson_ids,
        "lesson_columns": short_columns,
        "lesson_data_counts_before": before_counts,
        "lesson_data_counts_after": after_counts,
        "lesson_imported": imported,
        "sheet_check": sheet_check,
        "refund": refund,
        "errors": errors,
    }
    log("course_done", **result)
    return result


def main() -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    os.chdir(KQ_WORK_ROOT)
    before_refund_files = _snapshot_refund_files()
    results: list[dict[str, Any]] = []
    log("run_start", cwd=os.getcwd(), before_refund_files=len(before_refund_files))

    for course_name in TRADITIONAL_COURSES:
        try:
            results.append(run_traditional_course(course_name))
        except Exception as exc:
            result = {
                "course": course_name,
                "mode": "traditional",
                "ok": False,
                "errors": [f"{type(exc).__name__}: {exc}"],
                "traceback": traceback.format_exc(),
            }
            log("course_fatal", **result)
            results.append(result)

    for course_name in CODEYUN_COURSES:
        try:
            results.append(run_codeyun_course(course_name))
        except Exception as exc:
            result = {
                "course": course_name,
                "mode": "codeyun",
                "ok": False,
                "errors": [f"{type(exc).__name__}: {exc}"],
                "traceback": traceback.format_exc(),
            }
            log("course_fatal", **result)
            results.append(result)

    after_refund_files = _snapshot_refund_files()
    new_refund_files = sorted(after_refund_files - before_refund_files)
    summary = {
        "started_at": None,
        "finished_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "results": results,
        "new_refund_files": new_refund_files,
        "new_refund_file_count": len(new_refund_files),
        "all_ok": all(item.get("ok") for item in results) and not new_refund_files,
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    log("run_done", all_ok=summary["all_ok"], new_refund_file_count=len(new_refund_files), summary_path=str(SUMMARY_PATH))
    return 0 if summary["all_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
