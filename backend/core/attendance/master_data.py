from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable

from filelock import FileLock
from sqlmodel import Session, select

from backend.core.settings import get_settings
from backend.models import (
    AttendanceDataImport,
    AttendancePaymentLedger,
    AttendancePaymentOrder,
    AttendanceUser,
)


USER_DATASET = "xiaoe_users"
PAYMENT_DATASET = "wechat_pay_ledger"
SUPPORTED_DATASETS = {USER_DATASET, PAYMENT_DATASET}
DEFAULT_MERCHANT_ID = "1599622041"
_USER_REFRESH_SEEN_INTERVAL_SECONDS = 6 * 24 * 60 * 60

_USER_FIELD_ALIASES = {
    "用户ID": "xiaoe_user_id",
    "user_id2": "xiaoe_user_id",
    "xiaoe_user_id": "xiaoe_user_id",
    "昵称": "nickname",
    "user_nickname": "nickname",
    "nickname": "nickname",
    "姓名": "real_name",
    "name": "real_name",
    "real_name": "real_name",
    "来源渠道": "from_channel",
    "from_channel": "from_channel",
    "账户绑定手机号": "bind_phone",
    "bind_phone": "bind_phone",
    "最近采集手机号": "collect_phone",
    "collect_phone": "collect_phone",
    "最近收货手机号": "last_receive_phone",
    "last_receive_phone": "last_receive_phone",
    "账号状态": "account_status",
    "is_seal": "account_status",
    "account_status": "account_status",
    "备注名": "remark_name",
    "remark_name": "remark_name",
    "注册时间": "registered_at",
    "user_created_at": "registered_at",
    "registered_at": "registered_at",
}
_USER_CORE_FIELDS = tuple(dict.fromkeys(_USER_FIELD_ALIASES.values()))


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\ufeff", "").strip()


def _strip_legacy_prefix(value: Any) -> str:
    return _text(value).lstrip("`'")


def _json_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _decode_csv(content: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("CSV 编码无法识别，仅支持 UTF-8/GB18030")


def _read_csv_rows(content: bytes) -> list[dict[str, str]]:
    text = _decode_csv(content)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV 缺少表头")
    return [
        {
            _text(key): _text(value)
            for key, value in row.items()
            if key is not None
        }
        for row in reader
        if any(_text(value) for value in row.values())
    ]


def _safe_filename(value: str) -> str:
    name = Path(_text(value) or "source.csv").name
    return re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "_", name)[:180] or "source.csv"


def _raw_file_path(dataset_type: str, scope_key: str, sha256: str, source_filename: str) -> Path:
    safe_scope = re.sub(r"[^0-9A-Za-z._-]+", "_", scope_key)
    root = get_settings().data_dir / "attendance" / "master-data" / "raw" / dataset_type / safe_scope
    root.mkdir(parents=True, exist_ok=True)
    suffix = Path(_safe_filename(source_filename)).suffix.lower() or ".csv"
    return root / f"{sha256}{suffix}"


def _atomic_write(path: Path, content: bytes) -> None:
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    temp_path.write_bytes(content)
    os.replace(temp_path, path)


def _batch_payload(batch: AttendanceDataImport, *, duplicate: bool = False) -> dict[str, Any]:
    return {
        "import_id": batch.id,
        "dataset_type": batch.dataset_type,
        "scope_key": batch.scope_key,
        "status": batch.status,
        "duplicate": duplicate,
        "total_rows": batch.total_rows,
        "inserted_rows": batch.inserted_rows,
        "updated_rows": batch.updated_rows,
        "unchanged_rows": batch.unchanged_rows,
        "skipped_rows": batch.skipped_rows,
        "conflict_rows": batch.conflict_rows,
        "content_sha256": batch.content_sha256,
        "byte_size": batch.byte_size,
        "error_summary": batch.error_summary,
    }


def ingest_master_data_file(
    session: Session,
    *,
    dataset_type: str,
    scope_key: str,
    source_filename: str,
    content: bytes,
    collector_device: str = "",
    collected_at: float | None = None,
) -> dict[str, Any]:
    """Serialize imports across workers, then ingest one immutable source file."""

    lock_dir = get_settings().data_dir / "attendance" / "master-data"
    lock_dir.mkdir(parents=True, exist_ok=True)
    with FileLock(lock_dir / ".import.lock", timeout=600):
        return _ingest_master_data_file(
            session,
            dataset_type=dataset_type,
            scope_key=scope_key,
            source_filename=source_filename,
            content=content,
            collector_device=collector_device,
            collected_at=collected_at,
        )


def _ingest_master_data_file(
    session: Session,
    *,
    dataset_type: str,
    scope_key: str,
    source_filename: str,
    content: bytes,
    collector_device: str = "",
    collected_at: float | None = None,
) -> dict[str, Any]:
    dataset = _text(dataset_type).lower()
    scope = _text(scope_key)
    if dataset not in SUPPORTED_DATASETS:
        raise ValueError(f"不支持的考勤主数据类型：{dataset_type}")
    if not scope:
        raise ValueError("考勤主数据 scope_key 不能为空")
    if not content:
        raise ValueError("考勤主数据文件为空")

    sha256 = hashlib.sha256(content).hexdigest()
    existing_batch = session.exec(
        select(AttendanceDataImport)
        .where(AttendanceDataImport.dataset_type == dataset)
        .where(AttendanceDataImport.scope_key == scope)
        .where(AttendanceDataImport.content_sha256 == sha256)
    ).first()
    if existing_batch is not None and existing_batch.status == "completed":
        return _batch_payload(existing_batch, duplicate=True)

    raw_path = _raw_file_path(dataset, scope, sha256, source_filename)
    if not raw_path.exists():
        _atomic_write(raw_path, content)

    batch = existing_batch or AttendanceDataImport(
        dataset_type=dataset,
        scope_key=scope,
        source_filename=_safe_filename(source_filename),
        content_sha256=sha256,
        byte_size=len(content),
        raw_file_path=str(raw_path),
        collector_device=_text(collector_device),
        collected_at=float(collected_at or 0),
    )
    batch.status = "processing"
    batch.error_summary = ""
    session.add(batch)
    session.commit()
    session.refresh(batch)

    try:
        rows = _read_csv_rows(content)
        if not rows:
            raise ValueError("考勤主数据 CSV 没有有效数据行")
        if dataset == USER_DATASET:
            summary = _ingest_user_rows(session, batch, rows)
        else:
            summary = _ingest_payment_rows(session, batch, rows)
        for field, value in summary.items():
            setattr(batch, field, int(value))
        batch.status = "completed"
        batch.completed_at = time.time()
        session.add(batch)
        session.commit()
        session.refresh(batch)
        return _batch_payload(batch)
    except Exception as exc:
        session.rollback()
        failed_batch = session.get(AttendanceDataImport, batch.id)
        if failed_batch is not None:
            failed_batch.status = "failed"
            failed_batch.completed_at = time.time()
            failed_batch.error_summary = str(exc)[:2000]
            session.add(failed_batch)
            session.commit()
        raise


def _scope_shop_id(scope_key: str) -> int:
    match = re.search(r"(\d+)$", scope_key)
    shop_id = int(match.group(1)) if match else 0
    if shop_id not in {1, 2}:
        raise ValueError(f"用户数据 scope_key 无法识别店铺：{scope_key}")
    return shop_id


def _scope_merchant_id(scope_key: str) -> str:
    merchant_id = scope_key.split(":", 1)[-1].strip()
    if not merchant_id:
        raise ValueError(f"支付数据 scope_key 无法识别商户：{scope_key}")
    return merchant_id


def _normalized_user_row(source: dict[str, Any]) -> dict[str, Any]:
    row = {field: "" for field in _USER_CORE_FIELDS}
    for source_field, target_field in _USER_FIELD_ALIASES.items():
        value = source.get(source_field)
        if value not in (None, ""):
            row[target_field] = _strip_legacy_prefix(value) if "phone" in target_field else _text(value)
    row["source_payload_json"] = {
        _text(key): _text(value)
        for key, value in source.items()
        if _text(key)
    }
    return row


def _merge_duplicate_user_rows(current: dict[str, Any], incoming: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    merged = dict(current)
    conflict = False
    for field in _USER_CORE_FIELDS:
        old_value = _text(current.get(field))
        new_value = _text(incoming.get(field))
        if old_value and new_value and old_value != new_value:
            conflict = True
        if new_value:
            merged[field] = new_value
    payload = dict(current.get("source_payload_json") or {})
    payload.update(incoming.get("source_payload_json") or {})
    merged["source_payload_json"] = payload
    return merged, conflict


def _ingest_user_rows(
    session: Session,
    batch: AttendanceDataImport,
    source_rows: list[dict[str, str]],
) -> dict[str, int]:
    shop_id = _scope_shop_id(batch.scope_key)
    collapsed: dict[str, dict[str, Any]] = {}
    conflict_rows = 0
    skipped_rows = 0
    for source in source_rows:
        row = _normalized_user_row(source)
        user_id = _text(row.get("xiaoe_user_id"))
        if not user_id:
            skipped_rows += 1
            continue
        if user_id in collapsed:
            collapsed[user_id], conflict = _merge_duplicate_user_rows(collapsed[user_id], row)
            conflict_rows += int(conflict)
        else:
            collapsed[user_id] = row

    if not collapsed:
        raise ValueError("用户 CSV 缺少关键列“用户ID”或没有有效用户")

    existing_users = {
        item.xiaoe_user_id: item
        for item in session.exec(
            select(AttendanceUser).where(AttendanceUser.shop_id == shop_id)
        ).all()
    }
    now = time.time()
    inserted_rows = 0
    updated_rows = 0
    unchanged_rows = 0
    for user_id, row in collapsed.items():
        hash_payload = {
            field: row.get(field, "")
            for field in _USER_CORE_FIELDS
        }
        hash_payload["source_payload_json"] = row["source_payload_json"]
        row_hash = _json_hash(hash_payload)
        existing = existing_users.get(user_id)
        if existing is None:
            existing = AttendanceUser(
                shop_id=shop_id,
                xiaoe_user_id=user_id,
                row_hash=row_hash,
                first_seen_at=now,
                last_seen_at=now,
                last_import_id=batch.id,
            )
            for field in _USER_CORE_FIELDS:
                if field != "xiaoe_user_id":
                    setattr(existing, field, _text(row.get(field)))
            existing.source_payload_json = dict(row["source_payload_json"])
            session.add(existing)
            existing_users[user_id] = existing
            inserted_rows += 1
            continue
        if existing.row_hash == row_hash:
            unchanged_rows += 1
            if now - float(existing.last_seen_at or 0) >= _USER_REFRESH_SEEN_INTERVAL_SECONDS:
                existing.last_seen_at = now
                existing.last_import_id = batch.id
                session.add(existing)
            continue
        for field in _USER_CORE_FIELDS:
            if field != "xiaoe_user_id":
                setattr(existing, field, _text(row.get(field)))
        existing.source_payload_json = dict(row["source_payload_json"])
        existing.row_hash = row_hash
        existing.last_seen_at = now
        existing.last_import_id = batch.id
        existing.updated_at = now
        session.add(existing)
        updated_rows += 1

    return {
        "total_rows": len(source_rows),
        "inserted_rows": inserted_rows,
        "updated_rows": updated_rows,
        "unchanged_rows": unchanged_rows,
        "skipped_rows": skipped_rows,
        "conflict_rows": conflict_rows,
    }


def _money_to_cents(value: Any) -> int:
    text = _strip_legacy_prefix(value).replace(",", "")
    if not text:
        return 0
    try:
        return int((Decimal(text) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except InvalidOperation as exc:
        raise ValueError(f"金额无法识别：{value}") from exc


def _normalized_payment_row(source: dict[str, Any]) -> dict[str, Any] | None:
    if "voucher_id" in source or "flow_order" in source:
        money_cents = _money_to_cents(source.get("money"))
        return {
            "event_time": _text(source.get("datetime") or source.get("event_time")),
            "business_order": _strip_legacy_prefix(source.get("business_order")),
            "flow_order": _strip_legacy_prefix(source.get("flow_order")),
            "money_cents": money_cents,
            "balance_cents": _money_to_cents(source.get("balance")),
            "submitter": _text(source.get("submitter")),
            "fee_cents": _money_to_cents(source.get("fee")),
            "voucher_id": _strip_legacy_prefix(source.get("voucher_id")),
            "source_payload_json": dict(source),
        }

    business_type = _text(source.get("业务类型"))
    if business_type not in {"交易", "退款"}:
        return None
    remark = _text(source.get("备注"))
    fee_match = re.search(r"含手续费\s*(\d+(?:\.\d+)?)", remark)
    fee_cents = _money_to_cents(fee_match.group(1) if fee_match else "0")
    if business_type == "交易":
        money_cents = _money_to_cents(source.get("收支金额(元)"))
        fee_cents = -abs(fee_cents)
    else:
        total_match = re.search(r"总金额\s*(\d+(?:\.\d+)?)", remark)
        amount = total_match.group(1) if total_match else source.get("收支金额(元)")
        money_cents = -abs(_money_to_cents(amount))

    return {
        "event_time": _text(source.get("记账时间")),
        "business_order": _strip_legacy_prefix(source.get("微信支付业务单号")),
        "flow_order": _strip_legacy_prefix(source.get("资金流水单号")),
        "money_cents": money_cents,
        "balance_cents": _money_to_cents(source.get("账户结余(元)")),
        "submitter": _text(source.get("资金变更提交申请人")),
        "fee_cents": fee_cents,
        "voucher_id": _strip_legacy_prefix(source.get("业务凭证号")),
        "source_payload_json": dict(source),
    }


def _ingest_payment_rows(
    session: Session,
    batch: AttendanceDataImport,
    source_rows: list[dict[str, str]],
) -> dict[str, int]:
    merchant_id = _scope_merchant_id(batch.scope_key)
    collapsed: dict[str, dict[str, Any]] = {}
    skipped_rows = 0
    conflict_rows = 0
    for source in source_rows:
        row = _normalized_payment_row(source)
        if row is None:
            skipped_rows += 1
            continue
        voucher_id = _text(row.get("voucher_id"))
        flow_order = _text(row.get("flow_order"))
        if not voucher_id or not flow_order:
            skipped_rows += 1
            continue
        row["row_hash"] = _json_hash({key: value for key, value in row.items() if key != "source_payload_json"})
        previous = collapsed.get(voucher_id)
        if previous is not None and previous["row_hash"] != row["row_hash"]:
            conflict_rows += 1
        collapsed[voucher_id] = row
    if not collapsed:
        raise ValueError("微信支付 CSV 没有可导入的交易或退款流水")

    existing_rows = {
        item.voucher_id: item
        for item in session.exec(
            select(AttendancePaymentLedger).where(AttendancePaymentLedger.merchant_id == merchant_id)
        ).all()
    }
    affected_flows: set[str] = set()
    inserted_rows = 0
    updated_rows = 0
    unchanged_rows = 0
    now = time.time()
    for voucher_id, row in collapsed.items():
        existing = existing_rows.get(voucher_id)
        if existing is None:
            item = AttendancePaymentLedger(
                merchant_id=merchant_id,
                last_import_id=batch.id,
                created_at=now,
                updated_at=now,
                **row,
            )
            session.add(item)
            existing_rows[voucher_id] = item
            affected_flows.add(row["flow_order"])
            inserted_rows += 1
            continue
        if existing.row_hash == row["row_hash"]:
            unchanged_rows += 1
            continue
        affected_flows.add(existing.flow_order)
        for field in (
            "event_time",
            "business_order",
            "flow_order",
            "money_cents",
            "balance_cents",
            "submitter",
            "fee_cents",
            "voucher_id",
            "row_hash",
            "source_payload_json",
        ):
            setattr(existing, field, row[field])
        existing.last_import_id = batch.id
        existing.updated_at = now
        session.add(existing)
        affected_flows.add(existing.flow_order)
        updated_rows += 1

    session.flush()
    _rebuild_payment_orders(session, merchant_id=merchant_id, flow_orders=affected_flows)
    return {
        "total_rows": len(source_rows),
        "inserted_rows": inserted_rows,
        "updated_rows": updated_rows,
        "unchanged_rows": unchanged_rows,
        "skipped_rows": skipped_rows,
        "conflict_rows": conflict_rows,
    }


def _chunks(values: list[str], size: int = 500) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def _rebuild_payment_orders(
    session: Session,
    *,
    merchant_id: str,
    flow_orders: set[str],
) -> None:
    flow_list = sorted(value for value in flow_orders if value)
    if not flow_list:
        return
    ledger_rows: list[AttendancePaymentLedger] = []
    for chunk in _chunks(flow_list):
        ledger_rows.extend(
            session.exec(
                select(AttendancePaymentLedger)
                .where(AttendancePaymentLedger.merchant_id == merchant_id)
                .where(AttendancePaymentLedger.flow_order.in_(chunk))
            ).all()
        )
    grouped: dict[str, list[AttendancePaymentLedger]] = {}
    for row in ledger_rows:
        grouped.setdefault(row.flow_order, []).append(row)

    existing_orders: dict[str, AttendancePaymentOrder] = {}
    for chunk in _chunks(flow_list):
        for order in session.exec(
            select(AttendancePaymentOrder)
            .where(AttendancePaymentOrder.merchant_id == merchant_id)
            .where(AttendancePaymentOrder.flow_order.in_(chunk))
        ).all():
            existing_orders[order.flow_order] = order

    now = time.time()
    for flow_order, rows in grouped.items():
        rows.sort(key=lambda item: (item.event_time, int(item.id or 0)))
        paid = sum(max(int(item.money_cents or 0), 0) for item in rows)
        refunded = -sum(min(int(item.money_cents or 0), 0) for item in rows)
        order = existing_orders.get(flow_order) or AttendancePaymentOrder(
            merchant_id=merchant_id,
            flow_order=flow_order,
        )
        order.merchant_order_id = rows[0].voucher_id
        order.paid_at = rows[0].event_time
        order.paid_amount_cents = paid
        order.refunded_amount_cents = refunded
        order.net_amount_cents = paid - refunded
        order.last_event_at = rows[-1].event_time
        order.updated_at = now
        session.add(order)


def _cents_text(value: int) -> str:
    amount = Decimal(int(value or 0)) / 100
    return format(amount.quantize(Decimal("0.01")), "f").rstrip("0").rstrip(".") or "0"


def lookup_payment_order(
    order_id: Any,
    *,
    session: Session | None = None,
    merchant_id: str = DEFAULT_MERCHANT_ID,
    **_ignored: Any,
) -> dict[str, Any]:
    normalized = _strip_legacy_prefix(order_id)
    if not normalized or session is None:
        return {}
    order = session.exec(
        select(AttendancePaymentOrder)
        .where(AttendancePaymentOrder.merchant_id == merchant_id)
        .where(
            (AttendancePaymentOrder.flow_order == normalized)
            | (AttendancePaymentOrder.merchant_order_id == normalized)
        )
    ).first()
    if order is None:
        return {}
    date_digits = re.sub(r"\D+", "", order.paid_at)
    return {
        "微信支付订单号": order.flow_order,
        "订单日期": date_digits[:8] if len(date_digits) >= 8 else "",
        "商户订单号": order.merchant_order_id,
        "订单金额": _cents_text(order.paid_amount_cents),
        "已返款": _cents_text(order.refunded_amount_cents),
    }


def lookup_registration_user(
    names: Any = None,
    phones: Any = None,
    *,
    shop_id: int = 1,
    return_mode: int = 1,
    session: Session | None = None,
    **_ignored: Any,
) -> Any:
    if session is None:
        return ("", -1) if return_mode == 1 else ""
    name_values = [_text(value) for value in (names if isinstance(names, (list, tuple)) else [names]) if _text(value)]
    phone_values = [
        _strip_legacy_prefix(value)
        for value in (phones if isinstance(phones, (list, tuple)) else [phones])
        if _strip_legacy_prefix(value) and _strip_legacy_prefix(value).lower() != "none"
    ]
    if not phone_values:
        return ("", -1) if return_mode == 1 else ""

    rows = session.exec(
        select(AttendanceUser)
        .where(AttendanceUser.shop_id == int(shop_id))
        .where(AttendanceUser.account_status != "已注销")
        .where(
            AttendanceUser.bind_phone.in_(phone_values)
            | AttendanceUser.collect_phone.in_(phone_values)
            | AttendanceUser.last_receive_phone.in_(phone_values)
        )
    ).all()
    unique_rows = {row.xiaoe_user_id: row for row in rows}
    candidates = list(unique_rows.values())
    for field in ("nickname", "real_name"):
        if len(candidates) <= 1 or not name_values:
            break
        matched = [row for row in candidates if _text(getattr(row, field)) in name_values]
        if len(matched) == 1:
            candidates = matched
            break
    if len(candidates) != 1:
        return ("", len(candidates)) if return_mode == 1 else ""

    candidate = candidates[0]
    is_placeholder = (
        candidate.from_channel == "B端手机号导入"
        and candidate.account_status.startswith("待注册")
        and candidate.nickname.startswith("手机尾号")
    )
    if is_placeholder and name_values:
        named_rows = session.exec(
            select(AttendanceUser)
            .where(AttendanceUser.shop_id == int(shop_id))
            .where(AttendanceUser.account_status != "已注销")
            .where(~AttendanceUser.account_status.startswith("待注册"))
            .where(
                AttendanceUser.nickname.in_(name_values)
                | AttendanceUser.real_name.in_(name_values)
            )
        ).all()
        named_unique = {row.xiaoe_user_id: row for row in named_rows}
        if len(named_unique) == 1:
            user_id = next(iter(named_unique))
            return (user_id, 95) if return_mode == 1 else user_id
    return (candidate.xiaoe_user_id, 90) if return_mode == 1 else candidate.xiaoe_user_id


def find_user_ids_by_phone(
    session: Session,
    phone: str,
    *,
    limit: int = 50,
) -> list[str]:
    normalized = _strip_legacy_prefix(phone)
    if not normalized:
        return []
    rows = session.exec(
        select(AttendanceUser)
        .where(
            (AttendanceUser.bind_phone == normalized)
            | (AttendanceUser.collect_phone == normalized)
        )
        .limit(limit)
    ).all()
    return list(dict.fromkeys(row.xiaoe_user_id for row in rows if row.xiaoe_user_id))


def find_active_user_ids_by_names(
    session: Session,
    names: Iterable[Any],
    *,
    limit: int = 100,
) -> list[str]:
    normalized_names = list(dict.fromkeys(_text(value) for value in names if _text(value)))
    if not normalized_names:
        return []
    rows = session.exec(
        select(AttendanceUser)
        .where(AttendanceUser.account_status != "已注销")
        .where(~AttendanceUser.account_status.startswith("待注册"))
        .where(
            AttendanceUser.nickname.in_(normalized_names)
            | AttendanceUser.real_name.in_(normalized_names)
        )
        .limit(limit)
    ).all()
    return list(dict.fromkeys(row.xiaoe_user_id for row in rows if row.xiaoe_user_id))


def payment_refund_rows(
    session: Session,
    *,
    merchant_order_id: str,
    wechat_order_id: str = "",
    merchant_id: str = DEFAULT_MERCHANT_ID,
) -> list[dict[str, Any]]:
    normalized_merchant = _strip_legacy_prefix(merchant_order_id)
    normalized_wechat = _strip_legacy_prefix(wechat_order_id)
    query = (
        select(AttendancePaymentLedger)
        .where(AttendancePaymentLedger.merchant_id == merchant_id)
        .where(AttendancePaymentLedger.money_cents < 0)
    )
    if normalized_wechat:
        query = query.where(AttendancePaymentLedger.flow_order == normalized_wechat)
    else:
        query = query.where(AttendancePaymentLedger.voucher_id.startswith(normalized_merchant))
    rows = session.exec(query.order_by(AttendancePaymentLedger.event_time)).all()
    return [
        {
            "datetime": row.event_time,
            "business_order": row.business_order,
            "flow_order": row.flow_order,
            "money": _cents_text(row.money_cents),
            "balance": _cents_text(row.balance_cents),
            "submitter": row.submitter,
            "fee": _cents_text(row.fee_cents),
            "voucher_id": row.voucher_id,
        }
        for row in rows
    ]


__all__ = [
    "DEFAULT_MERCHANT_ID",
    "PAYMENT_DATASET",
    "SUPPORTED_DATASETS",
    "USER_DATASET",
    "find_user_ids_by_phone",
    "find_active_user_ids_by_names",
    "ingest_master_data_file",
    "lookup_payment_order",
    "lookup_registration_user",
    "payment_refund_rows",
]
