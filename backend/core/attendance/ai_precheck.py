from __future__ import annotations

import importlib
import json
import math
import re
import sys
import time
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlmodel import Session, select

from backend.core.ai.chat import OllamaClientError, chat_with_provider
from backend.core.settings import ROOT_DIR
from backend.models import AttendanceWjxDataEntry, SheetDocument


REFUND_RECONCILIATION_SKILL = "refund_total_reconciliation_v1"
GENERAL_TRIAGE_SKILL = "general_readonly_triage_v1"
ATTENDANCE_PRECHECK_DEEPSEEK_PROVIDER_ID = "deepseek"
ATTENDANCE_PRECHECK_DEEPSEEK_MODEL = "deepseek-v4-pro"
ATTENDANCE_PRECHECK_DEEPSEEK_TIMEOUT_SECONDS = 90

VIDEO_REFUND_UNIT = 27
COLEARN_REFUND_UNIT = 10
ZEN_DOOR_REFUND_RULE = "打卡满 1/3 次分别对应 20/40元"

ATTENDANCE_FACT_FIELDS = [
    "学号",
    "姓名",
    "昵称",
    "用户ID",
    "商户订单号",
    "视频应返款",
    "共学打卡",
    "共修打卡-随念门",
    "共修打卡-忏悔门",
    "共修打卡-崇拜祈祝",
    "打卡应返款",
    "总应返款",
    "当前应返款",
    "已返款",
]

ZEN_DOOR_FIELDS = [
    ("共修随念门打卡", "共修打卡-随念门"),
    ("共修忏悔门打卡", "共修打卡-忏悔门"),
    ("共修崇拜祈祝打卡", "共修打卡-崇拜祈祝"),
]


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, Decimal):
        numeric = float(value)
        return int(numeric) if numeric.is_integer() else numeric
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def _number(value: Any) -> float | None:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, int | float):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    text = _text(value).replace(",", "")
    if not text:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


def _int_or_zero(value: Any) -> int:
    numeric = _number(value)
    if numeric is None:
        return 0
    return int(round(numeric))


def _money(value: Any) -> float | None:
    return _number(value)


def _format_money(value: Any) -> str:
    numeric = _number(value)
    if numeric is None:
        return _text(value) or "0"
    if float(numeric).is_integer():
        return str(int(numeric))
    return f"{numeric:.2f}".rstrip("0").rstrip(".")


def _format_count(value: int | None) -> str:
    return str(int(value or 0))


def _normalize_student_number(value: Any) -> str:
    text = _text(value)
    numbers = [str(int(part)) for part in re.findall(r"\d+", text)]
    return "-".join(numbers)


def _strip_legacy_order_prefix(value: Any) -> str:
    return _text(value).lstrip("`'").strip()


def _entry_payload(entry: AttendanceWjxDataEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "activity_id": entry.activity_id,
        "seq": entry.seq,
        "submitted_at_text": entry.submitted_at_text,
        "course_name": entry.course_name,
        "student_id_text": entry.student_id_text,
        "student_name": entry.student_name,
        "correction_request": entry.correction_request,
        "extra_note": entry.extra_note,
        "process_status": entry.process_status,
    }


def _classify_skill(entry: AttendanceWjxDataEntry) -> str:
    text = " ".join([entry.correction_request or "", entry.extra_note or ""])
    if any(keyword in text for keyword in ("返款", "退款", "收到", "金额", "元")):
        return REFUND_RECONCILIATION_SKILL
    return GENERAL_TRIAGE_SKILL


def _row_to_mapping(columns: list[str], row: Any) -> dict[str, str]:
    if isinstance(row, dict):
        return {column: _text(row.get(column)) for column in columns}
    source = list(row) if isinstance(row, list | tuple) else []
    return {
        column: _text(source[index] if index < len(source) else "")
        for index, column in enumerate(columns)
    }


def _collect_course_summary(session: Session, course_name: str) -> dict[str, Any] | None:
    document = session.exec(
        select(SheetDocument).where(SheetDocument.numeric_id == 4)
    ).first()
    if document is None:
        return None
    document_json = dict(document.document_json or {})
    columns = [str(item or "").strip() for item in document_json.get("columns") or []]
    if not columns:
        return None
    normalized_course_name = _text(course_name)
    for row in document_json.get("rows") or []:
        mapping = _row_to_mapping(columns, row)
        candidates = [
            mapping.get("课程名称", ""),
            mapping.get("在线考勤表", ""),
        ]
        if normalized_course_name in candidates:
            return mapping
        if any(candidate and candidate in normalized_course_name for candidate in candidates):
            return mapping
    return None


def _ensure_xlproject_env_loaded(warnings: list[str]) -> None:
    xlproject_src = ROOT_DIR.parent / "xlproject" / "src"
    if xlproject_src.exists():
        source = str(xlproject_src)
        if source not in sys.path:
            sys.path.insert(0, source)
    try:
        import xlproject.loadenv  # noqa: F401
    except Exception as exc:  # pragma: no cover - optional local integration
        warnings.append(f"未能加载 xlproject 环境变量：{exc}")


def _course_script_stem(course_name: str, course_summary: dict[str, Any] | None = None) -> str:
    candidates = [
        _text(course_name),
        _text((course_summary or {}).get("在线考勤表")),
    ]
    for candidate in candidates:
        match = re.match(r"^20(?P<date>\d{6})(?P<name>.+)$", candidate)
        if match:
            return f"d{match.group('date')}{match.group('name')}"
        if re.match(r"^d\d{6}.+", candidate):
            return candidate
    return ""


def _import_course_class(course_name: str, course_summary: dict[str, Any] | None, warnings: list[str]) -> Any | None:
    stem = _course_script_stem(course_name, course_summary)
    if not stem:
        warnings.append("未能从课程名称推断考勤课程脚本")
        return None

    for module_name in (
        f"xlsln.kq5034.courses.{stem}",
        f"xlsln.kq5034.courses.已完结.{stem}",
    ):
        try:
            module = importlib.import_module(module_name)
            course_class = getattr(module, "考勤课程", None)
            if course_class is not None:
                return course_class
        except ModuleNotFoundError:
            continue
        except Exception as exc:
            warnings.append(f"加载课程脚本失败：{module_name}，{exc}")
            return None
    warnings.append(f"未找到课程脚本：{stem}")
    return None


def _match_attendance_record(records: list[dict[str, Any]], entry: AttendanceWjxDataEntry) -> dict[str, Any] | None:
    student_name = _text(entry.student_name)
    student_number = _normalize_student_number(entry.student_id_text)
    if student_name:
        name_matches = [record for record in records if _text(record.get("姓名")) == student_name]
        if len(name_matches) == 1:
            return name_matches[0]
        if len(name_matches) > 1 and student_number:
            for record in name_matches:
                if _normalize_student_number(record.get("学号")) == student_number:
                    return record
            return name_matches[0]

    if student_number:
        for record in records:
            if _normalize_student_number(record.get("学号")) == student_number:
                return record
    return None


def _collect_online_attendance_row(
    entry: AttendanceWjxDataEntry,
    course_summary: dict[str, Any] | None,
    warnings: list[str],
) -> dict[str, Any] | None:
    _ensure_xlproject_env_loaded(warnings)
    course_class = _import_course_class(entry.course_name, course_summary, warnings)
    if course_class is None:
        return None

    try:
        course = course_class()
        dataframe = course.wb.sql_select("考勤表", ATTENDANCE_FACT_FIELDS, 4)
        records = [
            {str(key): _json_safe(value) for key, value in record.items()}
            for record in dataframe.to_dict("records")
        ]
    except Exception as exc:
        warnings.append(f"读取在线考勤表失败：{exc}")
        return None

    matched = _match_attendance_record(records, entry)
    if matched is None:
        warnings.append("在线考勤表中未匹配到该学员")
        return None
    return matched


def _collect_order_refunds(merchant_order_id: str, warnings: list[str]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    merchant_order_id = _strip_legacy_order_prefix(merchant_order_id)
    if not merchant_order_id:
        return None, []

    try:
        from sqlmodel import Session

        from backend.core.attendance.master_data import (
            lookup_payment_order,
            payment_refund_rows,
        )
        from backend.db import engine
    except Exception as exc:
        warnings.append(f"加载 CodeYun 订单主数据工具失败：{exc}")
        return None, []

    try:
        with Session(engine) as session:
            order_record = _json_safe(lookup_payment_order(merchant_order_id, session=session))
            wechat_order_id = _strip_legacy_order_prefix((order_record or {}).get("微信支付订单号"))
            raw_refunds = payment_refund_rows(
                session,
                merchant_order_id=merchant_order_id,
                wechat_order_id=wechat_order_id,
            )
    except Exception as exc:
        warnings.append(f"查询 CodeYun 订单主数据失败：{exc}")
        return None, []

    refunds = []
    for row in raw_refunds:
        item = _json_safe(dict(row))
        amount = abs(_number(item.get("money")) or 0)
        refunds.append(
            {
                "submitted_at": item.get("datetime") or "",
                "refund_amount": amount,
                "wechat_refund_order_id": item.get("business_order") or "",
                "wechat_order_id": item.get("flow_order") or "",
                "voucher_id": item.get("voucher_id") or "",
            }
        )
    return order_record, refunds


def _zen_door_refund(count: int) -> int:
    if count >= 3:
        return 40
    if count >= 1:
        return 20
    return 0


def _build_refund_calculation(attendance_row: dict[str, Any] | None) -> dict[str, Any]:
    row = attendance_row or {}
    video_refund = _money(row.get("视频应返款")) or 0
    lesson_count = int(round(video_refund / VIDEO_REFUND_UNIT)) if video_refund else 0

    colearn_count = _int_or_zero(row.get("共学打卡"))
    colearn_refund = colearn_count * COLEARN_REFUND_UNIT
    door_items = []
    for label, field in ZEN_DOOR_FIELDS:
        count = _int_or_zero(row.get(field))
        door_items.append(
            {
                "label": label,
                "field": field,
                "count": count,
                "refund": _zen_door_refund(count),
            }
        )

    total_due = _money(row.get("总应返款"))
    card_due = _money(row.get("打卡应返款"))
    refunded_total = _money(row.get("已返款"))
    current_due = _money(row.get("当前应返款"))
    component_total = video_refund + colearn_refund + sum(item["refund"] for item in door_items)

    return {
        "video_lesson_count": lesson_count,
        "video_unit": VIDEO_REFUND_UNIT,
        "video_refund": video_refund,
        "colearn_count": colearn_count,
        "colearn_unit": COLEARN_REFUND_UNIT,
        "colearn_refund": colearn_refund,
        "door_items": door_items,
        "card_due": card_due if card_due is not None else colearn_refund + sum(item["refund"] for item in door_items),
        "total_due": total_due if total_due is not None else component_total,
        "refunded_total": refunded_total,
        "current_due": current_due,
        "component_total": component_total,
    }


def _refund_amounts(refunds: list[dict[str, Any]]) -> list[float]:
    return [float(_number(item.get("refund_amount")) or 0) for item in refunds]


def _refund_total(refunds: list[dict[str, Any]], order_record: dict[str, Any] | None) -> float | None:
    amounts = _refund_amounts(refunds)
    if amounts:
        return sum(amounts)
    if order_record:
        return _money(order_record.get("已返款"))
    return None


def _refund_timestamp_to_minute(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    return text[:16]


def _latest_refund_group(refunds: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    if not refunds:
        return "", []
    by_day: dict[str, list[dict[str, Any]]] = {}
    for item in refunds:
        day = _text(item.get("submitted_at"))[:10]
        by_day.setdefault(day, []).append(item)
    latest_day = sorted(by_day)[-1]
    return latest_day, by_day[latest_day]


def _extract_week_label(entry: AttendanceWjxDataEntry) -> str:
    text = " ".join([entry.correction_request or "", entry.extra_note or ""])
    match = re.search(r"第\s*(\d+|[一二三四五六七八九十]+)\s*周", text)
    if not match:
        return "本周"
    raw = match.group(1)
    if raw.isdigit():
        return f"第 {int(raw)} 周"
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if raw == "十":
        return "第 10 周"
    if raw.startswith("十"):
        return f"第 {10 + digits.get(raw[1:], 0)} 周"
    if "十" in raw:
        left, _, right = raw.partition("十")
        return f"第 {digits.get(left, 0) * 10 + digits.get(right, 0)} 周"
    return f"第 {digits.get(raw, 0)} 周" if raw in digits else "本周"


def _build_refund_reconciliation_report(
    entry: AttendanceWjxDataEntry,
    facts: dict[str, Any],
    calculation: dict[str, Any],
) -> tuple[str, str, str, float]:
    attendance_row = facts.get("online_attendance_row")
    refunds = list(facts.get("refund_orders") or [])
    order_record = facts.get("order_record")

    if not attendance_row:
        summary = "需要补充考勤表数据"
        report = (
            "1、当前只读取到问卷反馈，暂时没有匹配到在线考勤表中的学员行。\n"
            f"课程：{entry.course_name}\n"
            f"学号：{entry.student_id_text}\n"
            f"姓名：{entry.student_name}\n\n"
            "2、这一类问题适合走“累计应返款 vs 累计已返款”的排查逻辑，但需要先读取考勤表中的课次、打卡次数、总应返款、已返款和商户订单号。\n\n"
            "3、当前初判：资料不足，建议先人工确认该学员在在线考勤表中的姓名、学号或商户订单号是否能匹配。"
        )
        return "needs_more_data", summary, report, 0.35

    video = calculation["video_refund"]
    colearn_refund = calculation["colearn_refund"]
    door_items = calculation["door_items"]
    total_due = calculation["total_due"]
    refunded_total = _refund_total(refunds, order_record)
    sheet_refunded_total = calculation.get("refunded_total")
    if refunded_total is None:
        refunded_total = sheet_refunded_total

    component_values = [
        video,
        colearn_refund,
        *(item["refund"] for item in door_items),
    ]
    component_expr = " + ".join(_format_money(value) for value in component_values)

    lines: list[str] = []
    lines.append("1、请您先核对目前如下考勤数据是否有问题：")
    lines.append(
        f"视频课程：已完成 {_format_count(calculation['video_lesson_count'])} 课，"
        f"{_format_count(calculation['video_lesson_count'])} * {calculation['video_unit']}元 = {_format_money(video)} 元"
    )
    lines.append(
        f"共学打卡：{_format_count(calculation['colearn_count'])} 次，"
        f"{_format_count(calculation['colearn_count'])} * {calculation['colearn_unit']}元 = {_format_money(colearn_refund)} 元"
    )
    for item in door_items:
        lines.append(
            f"{item['label']}：{_format_count(item['count'])} 次，按规则计 {_format_money(item['refund'])} 元"
        )
    lines.append("所以目前累计应返款为：")
    lines.append(f"{component_expr} = {_format_money(total_due)} 元")
    lines.append("")

    lines.append("2、请您再核对以下订单已返款清单，确认当前累计已返款是否一致：")
    if refunds:
        for item in refunds:
            timestamp = _refund_timestamp_to_minute(item.get("submitted_at"))
            amount = _format_money(item.get("refund_amount"))
            lines.append(f"- {timestamp}，返款 {amount} 元")
        refund_expr = " + ".join(_format_money(amount) for amount in _refund_amounts(refunds))
        lines.append("累计已返款：")
        lines.append(f"{refund_expr} = {_format_money(refunded_total)} 元")
    elif refunded_total is not None:
        lines.append(f"订单汇总已返款：{_format_money(refunded_total)} 元")
    else:
        lines.append("暂未读取到订单返款明细，需要继续调用订单工具或人工核对。")
    lines.append("")

    week_label = _extract_week_label(entry)
    latest_day, latest_refunds = _latest_refund_group(refunds)
    if latest_refunds:
        latest_amounts = _refund_amounts(latest_refunds)
        latest_total = sum(latest_amounts)
        previous_total = (refunded_total or 0) - latest_total
        latest_expr = " + ".join(_format_money(amount) for amount in latest_amounts)
        lines.append(f"3、关于您反馈的{week_label}只收到 {latest_expr} = {_format_money(latest_total)} 元：")
        lines.append(
            f"{latest_day} 这次返款对应的是：前面累计应返 {_format_money(previous_total)} 元，"
            f"本次更新后累计应返 {_format_money(total_due)} 元，"
            f"所以本次补齐差额 {_format_money(total_due)} - {_format_money(previous_total)} = {_format_money(latest_total)} 元。"
        )
    else:
        lines.append(f"3、关于您反馈的{week_label}收到金额：")
        lines.append("当前还缺少逐笔返款时间明细，暂时只能核对考勤表的累计应返款和订单汇总已返款。")
    lines.append("")

    aligned = (
        refunded_total is not None
        and total_due is not None
        and abs(float(refunded_total) - float(total_due)) < 0.01
    )
    if aligned:
        summary = f"累计应返 {_format_money(total_due)} 元，累计已返 {_format_money(refunded_total)} 元，当前对齐"
        status = "aligned"
        confidence = 0.9 if refunds else 0.78
    else:
        summary = (
            f"累计应返 {_format_money(total_due)} 元，"
            f"累计已返 {_format_money(refunded_total) if refunded_total is not None else '未确认'} 元，需继续核对"
        )
        status = "needs_review"
        confidence = 0.62

    lines.append("4、初步判断和说明：")
    if aligned:
        lines.append(
            f"只要上面的考勤次数没有异议，且您实际累计收到 {_format_money(refunded_total)} 元，"
            "那当前返款就是对齐的，不是单看某一周固定应该收到多少。"
        )
    else:
        lines.append("目前还不能直接判定已完全对齐，需要优先核对上面的考勤次数和订单累计已返款。")
    lines.append(
        "返款逻辑是每次按“当前累计应返款 - 当前已返款”补齐；有些打卡是阶梯规则，"
        f"例如{ZEN_DOOR_REFUND_RULE}，中间次数不一定每次都会新增返款。"
    )
    lines.append("如果前面某次统计有错误或修正，后面也会按累计金额多退少补。")

    return status, summary, "\n".join(lines), confidence


def _build_general_triage_report(entry: AttendanceWjxDataEntry, facts: dict[str, Any]) -> tuple[str, str, str, float]:
    summary = "暂未命中专用排查 skill"
    report = (
        "1、当前反馈暂未命中已内置的专用排查逻辑。\n"
        f"课程：{entry.course_name}\n"
        f"学号：{entry.student_id_text}\n"
        f"姓名：{entry.student_name}\n"
        f"反馈：{entry.correction_request}\n\n"
        "2、系统只做只读预处理，不会修改考勤表、订单或处理状态。\n\n"
        "3、建议人工先判断该问题属于返款对账、打卡缺失、课程数据延迟、订单异常还是其他类型；后续可以把对应处理范式补充成新的考勤 skill。"
    )
    return "unsupported", summary, report, 0.25


def _extract_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        payload = json.loads(stripped)
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", stripped, re.S)
    if not match:
        return None
    try:
        payload = json.loads(match.group())
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _try_deepseek_report(precheck: dict[str, Any], warnings: list[str]) -> dict[str, str] | None:
    prompt = (
        "你是 CodeYun 考勤问卷的只读 AI 初判写作器。\n"
        "只能基于下面 FACTS 和 DRAFT_REPORT 改写，不要自行假设数据，不要调用外部工具，不要建议修改原始数据。\n"
        "输出 JSON：{\"summary\":\"...\", \"report\":\"...\"}。\n"
        "report 必须保持 1、2、3、4 四块编号；乘法要保留单位，例如 16 * 27元 = 432 元；返款订单时间保留到分钟。\n\n"
        "FACTS:\n"
        f"{json.dumps(precheck.get('facts') or {}, ensure_ascii=False, indent=2)}\n\n"
        "DRAFT_REPORT:\n"
        f"{precheck.get('report') or ''}\n"
    )

    try:
        response = chat_with_provider(
            provider_id=ATTENDANCE_PRECHECK_DEEPSEEK_PROVIDER_ID,
            model=ATTENDANCE_PRECHECK_DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": prompt}],
            system_prompt="你是只读考勤问卷初判写作器，只返回 JSON。",
            response_format="json",
            temperature=0.2,
            timeout_seconds=ATTENDANCE_PRECHECK_DEEPSEEK_TIMEOUT_SECONDS,
        )
    except OllamaClientError as exc:
        warnings.append(f"调用 DeepSeek 失败，已使用确定性报告：{exc}")
        return None

    payload = _extract_json_object(str(response.get("content") or ""))
    if not payload:
        warnings.append("DeepSeek 未返回可解析 JSON，已使用确定性报告")
        return None
    summary = _text(payload.get("summary"))
    report = _text(payload.get("report"))
    if not summary or not report:
        warnings.append("DeepSeek 返回内容缺少 summary/report，已使用确定性报告")
        return None
    return {
        "summary": summary,
        "report": report,
        "model": str(response.get("model") or ATTENDANCE_PRECHECK_DEEPSEEK_MODEL),
    }


def build_attendance_wjx_ai_precheck(
    entry: AttendanceWjxDataEntry,
    session: Session,
    *,
    use_codex_cli: bool = True,
) -> dict[str, Any]:
    warnings: list[str] = []
    skill = _classify_skill(entry)
    facts: dict[str, Any] = {
        "entry": _entry_payload(entry),
    }

    course_summary = _collect_course_summary(session, entry.course_name)
    if course_summary:
        facts["course_summary"] = course_summary

    if skill == REFUND_RECONCILIATION_SKILL:
        attendance_row = _collect_online_attendance_row(entry, course_summary, warnings)
        if attendance_row:
            facts["online_attendance_row"] = attendance_row
            merchant_order_id = _strip_legacy_order_prefix(attendance_row.get("商户订单号"))
            if merchant_order_id:
                order_record, refunds = _collect_order_refunds(merchant_order_id, warnings)
                if order_record:
                    facts["order_record"] = order_record
                facts["refund_orders"] = refunds
        calculation = _build_refund_calculation(attendance_row)
        facts["calculation"] = calculation
        status, summary, report, confidence = _build_refund_reconciliation_report(entry, facts, calculation)
    else:
        status, summary, report, confidence = _build_general_triage_report(entry, facts)

    precheck: dict[str, Any] = {
        "version": 1,
        "skill": skill,
        "status": status,
        "summary": summary,
        "report": report,
        "confidence": confidence,
        "read_only": True,
        "generated_at": time.time(),
        "facts": facts,
        "warnings": warnings,
        "deepseek": {
            "requested": bool(use_codex_cli),
            "used": False,
            "model": ATTENDANCE_PRECHECK_DEEPSEEK_MODEL,
        },
    }

    if use_codex_cli:
        deepseek_result = _try_deepseek_report(precheck, warnings)
        if deepseek_result:
            precheck["summary"] = deepseek_result["summary"]
            precheck["report"] = deepseek_result["report"]
            precheck["deepseek"]["used"] = True
            precheck["deepseek"]["model"] = deepseek_result["model"]
        precheck["warnings"] = warnings

    return precheck
