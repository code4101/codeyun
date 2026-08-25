from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

from filelock import FileLock

from backend.core.freebill.core import (
    _update_raw_import_status,
    archive_freebill_raw_bytes,
    get_freebill_work_dir,
    import_bill_records,
)
from backend.core.settings import get_settings


WECHAT_LOCAL_SNAPSHOT_FORMAT = "wechat-local-payment-db-v1"
WECHAT_PAYMENT_ACCOUNT_NAME = "微信支付"
WECHAT_LOCAL_RAW_SEQUENCE_PREFIX = "wechat-biz:"
WECHAT_CHAT_RAW_SEQUENCE_PREFIX = "wechat-chat:"
WECHAT_LOCAL_SYNC_STATE_VERSION = 1
WECHAT_TRANSFER_LOCAL_TYPE = (2000 << 32) | 49


def resolve_wechat_db_storage_root() -> Path:
    env_path = (os.environ.get("CODEYUN_WECHAT_DB_STORAGE") or "").strip()
    if env_path:
        return Path(env_path).expanduser()
    legacy_path = Path(r"D:\home\chenkunze\data\d2605微信逆向\decrypted\db_storage")
    if legacy_path.exists():
        return legacy_path
    return get_settings().data_dir / "wechat_db" / "decrypted" / "db_storage"


def _connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _decode_wechat_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if not isinstance(value, bytes):
        return str(value)
    data = value
    if data.startswith(b"\x28\xb5\x2f\xfd"):
        try:
            import zstandard as zstd

            data = zstd.ZstdDecompressor().decompress(data, max_output_size=8 * 1024 * 1024)
        except Exception:
            return ""
    for encoding in ("utf-8", "gb18030"):
        try:
            text = data.decode(encoding).strip("\x00\r\n\t ")
        except UnicodeDecodeError:
            continue
        if text:
            return text
    return ""


def _find_wechat_payment_account(contact_db: Path) -> tuple[str, str]:
    with _connect_readonly(contact_db) as conn:
        row = conn.execute(
            """
            SELECT
                contact.username,
                COALESCE(NULLIF(contact.remark, ''), NULLIF(contact.nick_name, ''),
                         NULLIF(contact.alias, ''), contact.username) AS display_name
            FROM contact
            WHERE contact.remark = ? OR contact.nick_name = ? OR contact.alias = ?
            ORDER BY
                CASE WHEN contact.nick_name = ? THEN 0 WHEN contact.remark = ? THEN 1 ELSE 2 END,
                contact.id
            LIMIT 1
            """,
            (
                WECHAT_PAYMENT_ACCOUNT_NAME,
                WECHAT_PAYMENT_ACCOUNT_NAME,
                WECHAT_PAYMENT_ACCOUNT_NAME,
                WECHAT_PAYMENT_ACCOUNT_NAME,
                WECHAT_PAYMENT_ACCOUNT_NAME,
            ),
        ).fetchone()
        if row is not None and isinstance(row["username"], int):
            mapped = conn.execute(
                "SELECT username FROM name2id WHERE rowid = ?",
                (row["username"],),
            ).fetchone()
            if mapped is not None:
                row = {"username": mapped["username"], "display_name": row["display_name"]}
    if row is None:
        raise ValueError("微信数据库中未找到“微信支付”服务号")
    return str(row["username"]), str(row["display_name"] or WECHAT_PAYMENT_ACCOUNT_NAME)


def _message_table_name(username: str) -> str:
    return "Msg_" + hashlib.md5(username.encode("utf-8")).hexdigest()


def _xml_text(root: ET.Element, path: str) -> str:
    return (root.findtext(path) or "").strip()


def _parse_amount(value: str) -> float | None:
    match = re.search(r"[¥￥]?\s*([0-9][0-9,]*(?:\.\d{1,2})?)\s*(?:元)?", value or "")
    if not match:
        return None
    try:
        return abs(float(match.group(1).replace(",", "")))
    except ValueError:
        return None


def _payment_line(root: ET.Element) -> tuple[str, float | None]:
    for top_line in root.findall(".//mmreader/template_detail/line_content/topline"):
        key = "".join((word.text or "") for word in top_line.findall("./key/word")).strip()
        value = "".join((word.text or "") for word in top_line.findall("./value/word")).strip()
        amount = _parse_amount(value)
        if amount is not None:
            return key, amount
    return "", None


def _payment_instrument(payment_label: str) -> tuple[str | None, str | None]:
    label = payment_label.strip()
    if label == "使用零钱支付":
        return "零钱", "零钱"
    if label == "通过零钱免密支付":
        return "零钱", "零钱免密"
    match = re.fullmatch(r"使用(.+)支付", label)
    if match:
        instrument = match.group(1).strip()
        return instrument or None, "银行卡" if "银行" in instrument else instrument or None
    return label or None, None


def _parse_message_root(row: sqlite3.Row) -> ET.Element | None:
    text = (
        _decode_wechat_text(row["message_content"])
        or _decode_wechat_text(row["compress_content"])
        or _decode_wechat_text(row["source"])
    )
    start = text.find("<msg")
    if start < 0:
        return None
    try:
        return ET.fromstring(text[start:])
    except ET.ParseError:
        return None


def parse_wechat_local_payment_db(
    db_storage_root: str | Path,
) -> dict[str, Any]:
    root = Path(db_storage_root)
    contact_db = root / "contact" / "contact.db"
    biz_message_db = root / "message" / "biz_message_0.db"
    username, account_name = _find_wechat_payment_account(contact_db)
    table_name = _message_table_name(username)

    with _connect_readonly(biz_message_db) as conn:
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        if table_exists is None:
            raise ValueError("微信支付业务消息表不存在")
        rows = conn.execute(
            f"""
            SELECT
                local_id, server_id, local_type, create_time,
                message_content, compress_content, source
            FROM "{table_name}"
            ORDER BY create_time, local_id
            """
        ).fetchall()

    source_mtime = biz_message_db.stat().st_mtime
    records: list[dict[str, Any]] = []
    skipped_non_transaction = 0
    skipped_missing_trade_no = 0
    skipped_missing_amount = 0
    seen_trade_nos: set[str] = set()
    duplicate_trade_nos = 0

    for row in rows:
        xml_root = _parse_message_root(row)
        if xml_root is None:
            skipped_non_transaction += 1
            continue
        payment_label, amount = _payment_line(xml_root)
        trade_no = _xml_text(xml_root, ".//mmreader/template_header/transaction_id")
        if amount is None:
            skipped_non_transaction += 1
            continue
        if not trade_no:
            skipped_missing_trade_no += 1
            continue
        if trade_no in seen_trade_nos:
            duplicate_trade_nos += 1
            continue
        seen_trade_nos.add(trade_no)

        create_timestamp = int(row["create_time"] or 0)
        if create_timestamp <= 0:
            skipped_non_transaction += 1
            continue
        counterparty = _xml_text(xml_root, ".//mmreader/template_header/display_name") or account_name
        account_no, cash_type = _payment_instrument(payment_label)
        create_time = datetime.fromtimestamp(create_timestamp).strftime("%Y-%m-%d %H:%M:%S")
        raw_message_id = row["server_id"] or row["local_id"]
        records.append(
            {
                "source": "微信",
                "trade_no": trade_no,
                "merchant_order_no": None,
                "create_time": create_time,
                "pay_time": create_time,
                "modify_time": create_time,
                "location": None,
                "type": "消费",
                "counterparty": counterparty,
                "product_name": None,
                "amount": amount,
                "direction": "支出",
                "status": "支付成功",
                "service_fee": 0,
                "refund_amount": 0,
                "remark": "微信本地支付通知",
                "fund_status": "支付成功",
                "account_no": account_no,
                "currency": "CNY",
                "cash_type": cash_type,
                "account_balance": None,
                "raw_sequence": f"{WECHAT_LOCAL_RAW_SEQUENCE_PREFIX}{raw_message_id}",
                "standard_nature": None,
                "standard_direction": None,
                "imported_at": source_mtime,
            }
        )

    return {
        "format": WECHAT_LOCAL_SNAPSHOT_FORMAT,
        "source_db": str(biz_message_db),
        "source_modified_at": source_mtime,
        "account_username": username,
        "account_name": account_name,
        "table_name": table_name,
        "scanned": len(rows),
        "parsed": len(records),
        "skipped_non_transaction": skipped_non_transaction,
        "skipped_missing_trade_no": skipped_missing_trade_no,
        "skipped_missing_amount": skipped_missing_amount,
        "duplicate_trade_nos": duplicate_trade_nos,
        "records": records,
    }


def parse_wechat_local_snapshot_bytes(content: bytes) -> tuple[list[dict[str, Any]], str]:
    payload = json.loads(content.decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("format") != WECHAT_LOCAL_SNAPSHOT_FORMAT:
        raise ValueError("不是可识别的微信本地支付快照")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("微信本地支付快照缺少 records")
    return [dict(item) for item in records if isinstance(item, dict)], WECHAT_LOCAL_SNAPSHOT_FORMAT


def _sync_state_path(work_dir: Path) -> Path:
    return work_dir / "wechat_local_sync.state.json"


def _load_incremental_state(path: Path, db_storage_root: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        payload = {}
    if (
        not isinstance(payload, dict)
        or payload.get("version") != WECHAT_LOCAL_SYNC_STATE_VERSION
        or payload.get("db_storage_root") != str(db_storage_root.resolve())
    ):
        return {
            "version": WECHAT_LOCAL_SYNC_STATE_VERSION,
            "db_storage_root": str(db_storage_root.resolve()),
            "watermarks": {},
        }
    payload.setdefault("watermarks", {})
    return payload


def _save_incremental_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _watermark_tuple(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, dict):
        return None
    try:
        return int(value.get("create_time") or 0), int(value.get("local_id") or 0)
    except (TypeError, ValueError):
        return None


def _watermark_payload(value: tuple[int, int] | None) -> dict[str, int] | None:
    if value is None:
        return None
    return {"create_time": value[0], "local_id": value[1]}


def _table_max_watermark(conn: sqlite3.Connection, table_name: str) -> tuple[int, int] | None:
    row = conn.execute(
        f'SELECT MAX(create_time) AS create_time, MAX(local_id) AS local_id FROM "{table_name}"'
    ).fetchone()
    if row is None or row["create_time"] is None or row["local_id"] is None:
        return None
    return int(row["create_time"] or 0), int(row["local_id"] or 0)


def _watermark_where(watermark: tuple[int, int] | None) -> tuple[str, list[int]]:
    if watermark is None:
        return "", []
    return (
        " AND (create_time > ? OR local_id > ?)",
        [watermark[0], watermark[1]],
    )


def _prepare_table_watermark(
    conn: sqlite3.Connection,
    table_name: str,
    stored: Any,
) -> tuple[tuple[int, int] | None, tuple[int, int] | None, bool]:
    previous = _watermark_tuple(stored)
    current_max = _table_max_watermark(conn, table_name)
    reset = bool(
        previous is not None
        and (
            current_max is None
            or current_max[0] < previous[0]
            or current_max[1] < previous[1]
        )
    )
    return (None if reset else previous), current_max, reset


def _payment_record_from_row(row: sqlite3.Row, account_name: str, source_mtime: float) -> dict[str, Any] | None:
    xml_root = _parse_message_root(row)
    if xml_root is None:
        return None
    payment_label, amount = _payment_line(xml_root)
    trade_no = _xml_text(xml_root, ".//mmreader/template_header/transaction_id")
    create_timestamp = int(row["create_time"] or 0)
    if amount is None or not trade_no or create_timestamp <= 0:
        return None
    counterparty = _xml_text(xml_root, ".//mmreader/template_header/display_name") or account_name
    account_no, cash_type = _payment_instrument(payment_label)
    create_time = datetime.fromtimestamp(create_timestamp).strftime("%Y-%m-%d %H:%M:%S")
    raw_message_id = row["server_id"] or row["local_id"]
    return {
        "source": "微信",
        "trade_no": trade_no,
        "merchant_order_no": None,
        "create_time": create_time,
        "pay_time": create_time,
        "modify_time": create_time,
        "location": None,
        "type": "消费",
        "counterparty": counterparty,
        "product_name": None,
        "amount": amount,
        "direction": "支出",
        "status": "支付成功",
        "service_fee": 0,
        "refund_amount": 0,
        "remark": "微信本地支付通知",
        "fund_status": "支付成功",
        "account_no": account_no,
        "currency": "CNY",
        "cash_type": cash_type,
        "account_balance": None,
        "raw_sequence": f"{WECHAT_LOCAL_RAW_SEQUENCE_PREFIX}{raw_message_id}",
        "standard_nature": None,
        "standard_direction": None,
        "imported_at": source_mtime,
    }


def _parse_incremental_payment_records(
    root: Path,
    watermarks: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, int]]:
    contact_db = root / "contact" / "contact.db"
    biz_message_db = root / "message" / "biz_message_0.db"
    username, account_name = _find_wechat_payment_account(contact_db)
    table_name = _message_table_name(username)
    watermark_key = f"biz:{table_name}"
    source_mtime = biz_message_db.stat().st_mtime
    stats = {"scanned": 0, "parsed": 0, "ignored": 0, "reset_tables": 0}
    with _connect_readonly(biz_message_db) as conn:
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone() is None:
            raise ValueError("微信支付业务消息表不存在")
        previous, current_max, reset = _prepare_table_watermark(conn, table_name, watermarks.get(watermark_key))
        if reset:
            stats["reset_tables"] += 1
        where_sql, params = _watermark_where(previous)
        rows = conn.execute(
            f"""
            SELECT local_id, server_id, local_type, create_time,
                   message_content, compress_content, source
            FROM "{table_name}"
            WHERE 1 = 1 {where_sql}
            ORDER BY create_time, local_id
            """,
            params,
        ).fetchall()
    records = []
    for row in rows:
        record = _payment_record_from_row(row, account_name, source_mtime)
        if record is None:
            stats["ignored"] += 1
        else:
            records.append(record)
    stats["scanned"] = len(rows)
    stats["parsed"] = len(records)
    next_watermarks = dict(watermarks)
    if current_max is not None:
        next_watermarks[watermark_key] = _watermark_payload(current_max)
    return records, next_watermarks, stats


def _wechat_self_username(root: Path) -> str:
    env_value = (os.environ.get("CODEYUN_WECHAT_SELF_USERNAME") or "").strip()
    if env_value:
        return env_value
    sync_state_path = root.parent / "sync_state.json"
    try:
        sync_state = json.loads(sync_state_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        sync_state = {}
    live_account_root = str(sync_state.get("live_account_root") or "").strip()
    if live_account_root:
        account_dir = Path(live_account_root).name
        if account_dir.startswith("wxid_") and "_" in account_dir:
            return account_dir.rsplit("_", 1)[0]
    raise ValueError("无法从微信同步状态确定本机微信账号")


def _contact_display_names(contact_db: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    with _connect_readonly(contact_db) as conn:
        rows = conn.execute(
            "SELECT username, remark, nick_name, alias FROM contact"
        ).fetchall()
        numeric_ids = [int(row["username"]) for row in rows if isinstance(row["username"], int)]
        mapped: dict[int, str] = {}
        if numeric_ids:
            placeholders = ",".join("?" for _ in numeric_ids)
            mapped = {
                int(row["rowid"]): str(row["username"])
                for row in conn.execute(
                    f"SELECT rowid, username FROM name2id WHERE rowid IN ({placeholders})",
                    numeric_ids,
                )
            }
        for row in rows:
            raw_username = row["username"]
            username = mapped.get(int(raw_username), "") if isinstance(raw_username, int) else str(raw_username or "")
            if not username:
                continue
            display_name = str(row["remark"] or row["nick_name"] or row["alias"] or username)
            result[username] = display_name
    return result


def _parse_incremental_chat_transfers(
    root: Path,
    watermarks: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, int]]:
    message_db = root / "message" / "message_0.db"
    self_username = _wechat_self_username(root)
    display_names = _contact_display_names(root / "contact" / "contact.db")
    source_mtime = message_db.stat().st_mtime
    stats = {"scanned": 0, "parsed": 0, "ignored": 0, "reset_tables": 0, "advanced_tables": 0}
    records: list[dict[str, Any]] = []
    seen_trade_nos: set[str] = set()
    next_watermarks = dict(watermarks)

    with _connect_readonly(message_db) as conn:
        id_to_username = {
            int(row["rowid"]): str(row["user_name"])
            for row in conn.execute("SELECT rowid, user_name FROM Name2Id")
        }
        table_to_username = {
            _message_table_name(username): username
            for username in id_to_username.values()
        }
        table_names = [
            str(row["name"])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
            )
        ]
        for table_name in table_names:
            columns = {
                str(row["name"])
                for row in conn.execute(f'PRAGMA table_info("{table_name}")')
            }
            if not {"local_id", "local_type", "real_sender_id", "create_time"}.issubset(columns):
                continue
            watermark_key = f"chat:{table_name}"
            previous, current_max, reset = _prepare_table_watermark(
                conn,
                table_name,
                watermarks.get(watermark_key),
            )
            if reset:
                stats["reset_tables"] += 1
            if current_max is None:
                continue
            where_sql, params = _watermark_where(previous)
            rows = conn.execute(
                f"""
                SELECT local_id, server_id, local_type, real_sender_id, create_time,
                       message_content, compress_content, source
                FROM "{table_name}"
                WHERE local_type = ? {where_sql}
                ORDER BY create_time, local_id
                """,
                [WECHAT_TRANSFER_LOCAL_TYPE, *params],
            ).fetchall()
            stats["scanned"] += len(rows)
            conversation_username = table_to_username.get(table_name, table_name)
            for row in rows:
                xml_root = _parse_message_root(row)
                if xml_root is None or _xml_text(xml_root, ".//appmsg/type") != "2000":
                    stats["ignored"] += 1
                    continue
                if _xml_text(xml_root, ".//wcpayinfo/paysubtype") != "3":
                    continue
                trade_no = _xml_text(xml_root, ".//wcpayinfo/transcationid")
                transfer_id = _xml_text(xml_root, ".//wcpayinfo/transferid")
                amount = _parse_amount(_xml_text(xml_root, ".//wcpayinfo/feedesc"))
                sender_username = id_to_username.get(int(row["real_sender_id"] or 0), "")
                if not trade_no or not transfer_id or amount is None or not sender_username:
                    stats["ignored"] += 1
                    continue
                if trade_no in seen_trade_nos:
                    continue
                seen_trade_nos.add(trade_no)
                begin_timestamp_text = _xml_text(xml_root, ".//wcpayinfo/begintransfertime")
                try:
                    begin_timestamp = int(begin_timestamp_text or row["create_time"] or 0)
                except ValueError:
                    begin_timestamp = int(row["create_time"] or 0)
                if begin_timestamp <= 0:
                    stats["ignored"] += 1
                    continue
                create_time = datetime.fromtimestamp(begin_timestamp).strftime("%Y-%m-%d %H:%M:%S")
                direction = "收入" if sender_username == self_username else "支出"
                records.append(
                    {
                        "source": "微信",
                        "trade_no": trade_no,
                        "merchant_order_no": transfer_id,
                        "create_time": create_time,
                        "pay_time": create_time,
                        "modify_time": datetime.fromtimestamp(int(row["create_time"])).strftime("%Y-%m-%d %H:%M:%S"),
                        "location": None,
                        "type": "转账",
                        "counterparty": display_names.get(conversation_username, conversation_username),
                        "product_name": "微信转账",
                        "amount": amount,
                        "direction": direction,
                        "status": "已收款",
                        "service_fee": 0,
                        "refund_amount": 0,
                        "remark": "微信聊天转账",
                        "fund_status": "已收款",
                        "account_no": "零钱",
                        "currency": "CNY",
                        "cash_type": "零钱",
                        "account_balance": None,
                        "raw_sequence": f"{WECHAT_CHAT_RAW_SEQUENCE_PREFIX}{transfer_id}:accepted",
                        "standard_nature": None,
                        "standard_direction": None,
                        "imported_at": source_mtime,
                    }
                )
            next_watermarks[watermark_key] = _watermark_payload(current_max)
            if previous != current_max:
                stats["advanced_tables"] += 1
    stats["parsed"] = len(records)
    return records, next_watermarks, stats


def parse_wechat_local_increment(
    db_storage_root: str | Path,
    state: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    root = Path(db_storage_root)
    watermarks = dict(state.get("watermarks") or {})
    payment_records, watermarks, payment_stats = _parse_incremental_payment_records(root, watermarks)
    transfer_records, watermarks, transfer_stats = _parse_incremental_chat_transfers(root, watermarks)
    records = payment_records + transfer_records
    snapshot = {
        "format": WECHAT_LOCAL_SNAPSHOT_FORMAT,
        "mode": "incremental",
        "source_db": str(root / "message"),
        "source_modified_at": max(
            (root / "message" / "biz_message_0.db").stat().st_mtime,
            (root / "message" / "message_0.db").stat().st_mtime,
        ),
        "payment": payment_stats,
        "chat_transfer": transfer_stats,
        "parsed": len(records),
        "records": records,
    }
    next_state = {
        **state,
        "version": WECHAT_LOCAL_SYNC_STATE_VERSION,
        "db_storage_root": str(root.resolve()),
        "watermarks": watermarks,
    }
    return snapshot, next_state


def sync_wechat_local_db_to_freebill(
    *,
    db_storage_root: str | Path | None = None,
    work_dir: Path | None = None,
    refresh_source: bool = True,
) -> dict[str, Any]:
    root = Path(db_storage_root) if db_storage_root is not None else resolve_wechat_db_storage_root()
    resolved_work_dir = work_dir or get_freebill_work_dir()
    state_path = _sync_state_path(resolved_work_dir)
    with FileLock(str(state_path) + ".lock", timeout=120):
        source_sync_error: str | None = None
        source_sync_result: dict[str, Any] | None = None
        if refresh_source:
            try:
                from pyxllib.autogui.wechat_db import WeChatDbStorage

                source_sync_result = WeChatDbStorage(root).sync_from_live(export_media=False)
            except Exception as exc:
                source_sync_error = str(exc)

        state = _load_incremental_state(state_path, root)
        snapshot, next_state = parse_wechat_local_increment(root, state)
        archive: dict[str, Any] | None = None
        if snapshot["records"]:
            snapshot_content = json.dumps(
                snapshot,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            archive = archive_freebill_raw_bytes(
                "微信本地账单增量.json",
                snapshot_content,
                source="微信",
                source_path=str(snapshot["source_db"]),
                modified_at=float(snapshot["source_modified_at"]),
                work_dir=resolved_work_dir,
                import_status="parsing",
                note=WECHAT_LOCAL_SNAPSHOT_FORMAT,
            )
        try:
            result = import_bill_records(
                snapshot["records"],
                filename="微信本地账单增量.json",
                work_dir=resolved_work_dir,
            )
            if archive is not None:
                _update_raw_import_status(archive["sha256"], "imported", work_dir=resolved_work_dir)
            next_state["last_success_at"] = time.time()
            next_state["last_result"] = {
                "processed": result["processed"],
                "inserted": result["inserted"],
                "updated": result["updated"],
                "skipped": result["skipped"],
                "payment": snapshot["payment"],
                "chat_transfer": snapshot["chat_transfer"],
            }
            _save_incremental_state(state_path, next_state)
        except Exception as exc:
            if archive is not None:
                _update_raw_import_status(
                    archive["sha256"],
                    "error",
                    work_dir=resolved_work_dir,
                    note=str(exc),
                )
            raise

        return {
            **result,
            "format": WECHAT_LOCAL_SNAPSHOT_FORMAT,
            "mode": "incremental",
            "raw_file": archive,
            "state_path": str(state_path),
            "source_db": snapshot["source_db"],
            "source_modified_at": snapshot["source_modified_at"],
            "scanned": snapshot["payment"]["scanned"] + snapshot["chat_transfer"]["scanned"],
            "parsed": snapshot["parsed"],
            "ignored": snapshot["payment"]["ignored"] + snapshot["chat_transfer"]["ignored"],
            "payment": snapshot["payment"],
            "chat_transfer": snapshot["chat_transfer"],
            "source_sync": source_sync_result,
            "source_sync_error": source_sync_error,
        }
