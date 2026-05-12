from __future__ import annotations

import io
import os
import hashlib
import shutil
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pandas as pd

from backend.core.settings import get_settings


BILL_RECORD_COLUMNS = [
    "source",
    "trade_no",
    "merchant_order_no",
    "create_time",
    "pay_time",
    "modify_time",
    "location",
    "type",
    "counterparty",
    "product_name",
    "amount",
    "direction",
    "status",
    "service_fee",
    "refund_amount",
    "remark",
    "fund_status",
    "imported_at",
]

TREND_GRANULARITIES = {"day", "week", "month", "year"}
DEFAULT_TREND_LIMITS = {
    "day": 31,
    "week": 26,
    "month": 12,
    "year": None,
}
FREEBILL_PROGRAM_FIELDS = {
    "id": {"sql": "id", "mode": "number"},
    "create_time": {"sql": "create_time", "mode": "date"},
    "pay_time": {"sql": "pay_time", "mode": "date"},
    "modify_time": {"sql": "modify_time", "mode": "date"},
    "source": {"sql": "source", "mode": "text"},
    "direction": {"sql": "TRIM(direction)", "mode": "text"},
    "type": {"sql": "type", "mode": "text"},
    "category": {"sql": "type", "mode": "text"},
    "counterparty": {"sql": "counterparty", "mode": "text"},
    "product_name": {"sql": "product_name", "mode": "text"},
    "amount": {"sql": "amount", "mode": "number"},
    "status": {"sql": "status", "mode": "text"},
    "remark": {"sql": "remark", "mode": "text"},
    "trade_no": {"sql": "trade_no", "mode": "text"},
    "merchant_order_no": {"sql": "merchant_order_no", "mode": "text"},
    "fund_status": {"sql": "fund_status", "mode": "text"},
}
FREEBILL_FULL_TEXT_FIELDS = [
    "trade_no",
    "merchant_order_no",
    "counterparty",
    "product_name",
    "remark",
    "status",
    "fund_status",
]

RAW_BILL_FILE_EXTENSIONS = {
    ".csv",
    ".xlsx",
    ".xlsm",
    ".xls",
    ".zip",
}
RAW_FILE_EXTENSIONS = RAW_BILL_FILE_EXTENSIONS
RAW_FILE_QUERY_EXTENSIONS = tuple(sorted(RAW_BILL_FILE_EXTENSIONS))


def get_freebill_work_dir() -> Path:
    raw_work_dir = (
        os.getenv("CODEYUN_FREEBILL_WORK_DIR")
        or os.getenv("FREEBILL_WORK_DIR")
        or ""
    ).strip()
    if raw_work_dir:
        return Path(raw_work_dir).expanduser().resolve()
    return get_settings().data_dir / "freebill"


def get_freebill_db_path(work_dir: Path | None = None) -> Path:
    return (work_dir or get_freebill_work_dir()) / "bill.db"


@contextmanager
def get_freebill_connection(work_dir: Path | None = None):
    db_path = get_freebill_db_path(work_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_freebill_schema(conn)
        yield conn
    finally:
        conn.close()


def ensure_freebill_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bill_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            trade_no TEXT,
            merchant_order_no TEXT,
            create_time TEXT,
            pay_time TEXT,
            modify_time TEXT,
            location TEXT,
            type TEXT,
            counterparty TEXT,
            product_name TEXT,
            amount REAL,
            direction TEXT,
            status TEXT,
            service_fee REAL,
            refund_amount REAL,
            remark TEXT,
            fund_status TEXT,
            imported_at REAL
        )
        """
    )
    _ensure_column(conn, "bill_records", "imported_at", "REAL")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_freebill_records_source ON bill_records (source)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_freebill_records_trade_no ON bill_records (trade_no)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_freebill_records_create_time ON bill_records (create_time)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_freebill_records_direction ON bill_records (direction)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_freebill_records_type ON bill_records (type)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS freebill_raw_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sha256 TEXT NOT NULL UNIQUE,
            original_name TEXT NOT NULL,
            extension TEXT NOT NULL,
            source TEXT,
            source_path TEXT,
            relative_path TEXT,
            archived_path TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            modified_at REAL,
            first_seen_at REAL NOT NULL,
            last_seen_at REAL NOT NULL,
            import_status TEXT NOT NULL DEFAULT 'archived',
            note TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_freebill_raw_files_source ON freebill_raw_files (source)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_freebill_raw_files_extension ON freebill_raw_files (extension)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_freebill_raw_files_relative_path ON freebill_raw_files (relative_path)")
    conn.commit()


def _ensure_column(
    conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_definition: str,
) -> None:
    existing_columns = {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in existing_columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")


def _normalize_text(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).replace("\t", "").strip()
    return text or None


def _normalize_amount(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, int | float):
        return abs(float(value))
    text = (
        str(value)
        .replace("¥", "")
        .replace("￥", "")
        .replace(",", "")
        .replace("，", "")
        .strip()
    )
    if not text:
        return None
    try:
        return abs(float(text))
    except ValueError:
        return None


def _strip_dataframe_text(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = df.columns.astype(str).str.strip()
    for column in df.columns:
        df[column] = df[column].map(lambda item: item.replace("\t", "").strip() if isinstance(item, str) else item)
    return df


def _infer_raw_source(path_text: str | None, explicit_source: str | None = None) -> str | None:
    if explicit_source:
        return explicit_source
    text = (path_text or "").lower()
    if "支付宝" in text or "alipay" in text:
        return "支付宝"
    if "微信" in text or "wechat" in text:
        return "微信"
    return None


def archive_freebill_raw_bytes(
    filename: str,
    content: bytes,
    *,
    source: str | None = None,
    source_path: str | None = None,
    relative_path: str | None = None,
    modified_at: float | None = None,
    work_dir: Path | None = None,
    import_status: str = "archived",
    note: str | None = None,
) -> dict[str, Any]:
    if not content:
        raise ValueError("原始文件内容为空")

    resolved_work_dir = work_dir or get_freebill_work_dir()
    sha256 = hashlib.sha256(content).hexdigest()
    suffix = Path(filename).suffix.lower()
    archive_dir = resolved_work_dir / "raw" / sha256[:2]
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{sha256}{suffix}"
    if not archive_path.exists():
        archive_path.write_bytes(content)

    now = time.time()
    inferred_source = _infer_raw_source(source_path or relative_path or filename, source)
    with get_freebill_connection(resolved_work_dir) as conn:
        existing = conn.execute(
            "SELECT id, first_seen_at FROM freebill_raw_files WHERE sha256 = ?",
            (sha256,),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE freebill_raw_files
                SET original_name = ?,
                    extension = ?,
                    source = COALESCE(?, source),
                    source_path = COALESCE(?, source_path),
                    relative_path = COALESCE(?, relative_path),
                    archived_path = ?,
                    size_bytes = ?,
                    modified_at = COALESCE(?, modified_at),
                    last_seen_at = ?,
                    import_status = ?,
                    note = COALESCE(?, note)
                WHERE sha256 = ?
                """,
                (
                    filename,
                    suffix,
                    inferred_source,
                    source_path,
                    relative_path,
                    str(archive_path),
                    len(content),
                    modified_at,
                    now,
                    import_status,
                    note,
                    sha256,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO freebill_raw_files (
                    sha256,
                    original_name,
                    extension,
                    source,
                    source_path,
                    relative_path,
                    archived_path,
                    size_bytes,
                    modified_at,
                    first_seen_at,
                    last_seen_at,
                    import_status,
                    note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sha256,
                    filename,
                    suffix,
                    inferred_source,
                    source_path,
                    relative_path,
                    str(archive_path),
                    len(content),
                    modified_at,
                    now,
                    now,
                    import_status,
                    note,
                ),
            )
        conn.commit()

    return {
        "sha256": sha256,
        "filename": filename,
        "source": inferred_source,
        "archived_path": str(archive_path),
        "size_bytes": len(content),
    }


def archive_freebill_raw_file(
    path: str | Path,
    *,
    root_dir: str | Path | None = None,
    source: str | None = None,
    work_dir: Path | None = None,
    import_status: str = "archived",
    note: str | None = None,
) -> dict[str, Any]:
    source_path = Path(path)
    content = source_path.read_bytes()
    relative_path = None
    if root_dir is not None:
        try:
            relative_path = source_path.relative_to(Path(root_dir)).as_posix()
        except ValueError:
            relative_path = None
    return archive_freebill_raw_bytes(
        source_path.name,
        content,
        source=source,
        source_path=str(source_path),
        relative_path=relative_path,
        modified_at=source_path.stat().st_mtime,
        work_dir=work_dir,
        import_status=import_status,
        note=note,
    )


def archive_freebill_raw_directory(
    source_dir: str | Path,
    *,
    work_dir: Path | None = None,
    include_database_snapshot: bool = False,
) -> dict[str, Any]:
    root_dir = Path(source_dir)
    if not root_dir.exists() or not root_dir.is_dir():
        raise ValueError(f"原始目录不存在：{root_dir}")

    archived_items: list[dict[str, Any]] = []
    skipped_items: list[str] = []
    for path in sorted(root_dir.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix == ".db" and not include_database_snapshot:
            skipped_items.append(str(path))
            continue
        if suffix not in RAW_FILE_EXTENSIONS and suffix != ".db":
            skipped_items.append(str(path))
            continue
        note = "legacy-db-snapshot" if suffix == ".db" else None
        archived_items.append(
            archive_freebill_raw_file(
                path,
                root_dir=root_dir,
                work_dir=work_dir,
                import_status="archived",
                note=note,
            )
        )

    return {
        "source_dir": str(root_dir),
        "archived_count": len(archived_items),
        "skipped_count": len(skipped_items),
        "items": archived_items,
    }


def adopt_freebill_legacy_database(
    source_db_path: str | Path,
    *,
    work_dir: Path | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    source_db = Path(source_db_path)
    if not source_db.exists():
        raise ValueError(f"旧账单库不存在：{source_db}")
    target_db = get_freebill_db_path(work_dir)
    target_db.parent.mkdir(parents=True, exist_ok=True)
    if target_db.exists() and not overwrite:
        raise ValueError(f"目标账单库已存在：{target_db}")
    if source_db.resolve() != target_db.resolve():
        shutil.copy2(source_db, target_db)
    with get_freebill_connection(target_db.parent) as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total_records,
                MIN(date(create_time)) AS min_date,
                MAX(date(create_time)) AS max_date
            FROM bill_records
            """
        ).fetchone()
    return {
        "source_db_path": str(source_db),
        "target_db_path": str(target_db),
        "total_records": int(row["total_records"] or 0),
        "min_date": row["min_date"],
        "max_date": row["max_date"],
    }


def _build_alipay_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    df = _strip_dataframe_text(df)
    if "交易订单号" not in df.columns:
        raise ValueError("未找到“交易订单号”列，请确认是标准支付宝交易明细 CSV")

    rename_map = {
        "交易订单号": "trade_no",
        "商家订单号": "merchant_order_no",
        "交易时间": "create_time",
        "交易分类": "type",
        "交易对方": "counterparty",
        "商品说明": "product_name",
        "金额": "amount",
        "收/支": "direction",
        "交易状态": "status",
        "备注": "remark",
    }
    df = df.rename(columns=rename_map)
    df = df[df["trade_no"].notna()]
    df["trade_no"] = df["trade_no"].astype(str).str.strip()
    df = df[df["trade_no"].str.match(r"^\d+$")]

    records: list[dict[str, Any]] = []
    now = time.time()
    for _, row in df.iterrows():
        create_time = _normalize_text(row.get("create_time"))
        records.append(
            {
                "source": "支付宝",
                "trade_no": _normalize_text(row.get("trade_no")),
                "merchant_order_no": _normalize_text(row.get("merchant_order_no")),
                "create_time": create_time,
                "pay_time": create_time,
                "modify_time": create_time,
                "location": None,
                "type": _normalize_text(row.get("type")),
                "counterparty": _normalize_text(row.get("counterparty")),
                "product_name": _normalize_text(row.get("product_name")),
                "amount": _normalize_amount(row.get("amount")),
                "direction": _normalize_text(row.get("direction")),
                "status": _normalize_text(row.get("status")),
                "service_fee": 0,
                "refund_amount": 0,
                "remark": _normalize_text(row.get("remark")),
                "fund_status": None,
                "imported_at": now,
            }
        )
    return records


def _build_wechat_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    df = _strip_dataframe_text(df)
    if "交易单号" not in df.columns:
        raise ValueError("未找到“交易单号”列，请确认是标准微信支付账单 Excel")

    rename_map = {
        "交易单号": "trade_no",
        "商户单号": "merchant_order_no",
        "交易时间": "create_time",
        "交易类型": "type",
        "交易对方": "counterparty",
        "商品": "product_name",
        "金额(元)": "amount",
        "金额（元）": "amount",
        "收/支": "direction",
        "收/ 支": "direction",
        "当前状态": "status",
        "备注": "remark",
    }
    df = df.rename(columns=rename_map)
    df = df[df["trade_no"].notna()]
    df["trade_no"] = df["trade_no"].astype(str).str.strip()

    records: list[dict[str, Any]] = []
    now = time.time()
    for _, row in df.iterrows():
        create_time = _normalize_text(row.get("create_time"))
        status = _normalize_text(row.get("status"))
        records.append(
            {
                "source": "微信",
                "trade_no": _normalize_text(row.get("trade_no")),
                "merchant_order_no": _normalize_text(row.get("merchant_order_no")),
                "create_time": create_time,
                "pay_time": create_time,
                "modify_time": create_time,
                "location": None,
                "type": _normalize_text(row.get("type")),
                "counterparty": _normalize_text(row.get("counterparty")),
                "product_name": _normalize_text(row.get("product_name")),
                "amount": _normalize_amount(row.get("amount")),
                "direction": _normalize_text(row.get("direction")),
                "status": status,
                "service_fee": 0,
                "refund_amount": 0,
                "remark": _normalize_text(row.get("remark")),
                "fund_status": status,
                "imported_at": now,
            }
        )
    return records


def import_alipay_csv_bytes(
    filename: str,
    content: bytes,
    *,
    header_row: int = 24,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    archive = archive_freebill_raw_bytes(
        filename,
        content,
        source="支付宝",
        work_dir=work_dir,
        import_status="parsing",
    )
    last_error: Exception | None = None
    for encoding in ("gbk", "utf-8-sig", "utf-8"):
        try:
            df = pd.read_csv(io.BytesIO(content), skiprows=header_row, encoding=encoding)
            records = _build_alipay_records(df)
            result = import_bill_records(records, filename=filename, work_dir=work_dir)
            result["raw_file"] = archive
            _update_raw_import_status(archive["sha256"], "imported", work_dir=work_dir)
            return result
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    if last_error is not None:
        _update_raw_import_status(archive["sha256"], "error", work_dir=work_dir, note=str(last_error))
        raise ValueError(f"读取支付宝 CSV 编码失败：{last_error}") from last_error
    _update_raw_import_status(archive["sha256"], "error", work_dir=work_dir, note="读取支付宝 CSV 失败")
    raise ValueError("读取支付宝 CSV 失败")


def import_wechat_excel_bytes(
    filename: str,
    content: bytes,
    *,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    archive = archive_freebill_raw_bytes(
        filename,
        content,
        source="微信",
        work_dir=work_dir,
        import_status="parsing",
    )
    try:
        df = pd.read_excel(io.BytesIO(content), header=16)
        records = _build_wechat_records(df)
        result = import_bill_records(records, filename=filename, work_dir=work_dir)
        result["raw_file"] = archive
        _update_raw_import_status(archive["sha256"], "imported", work_dir=work_dir)
        return result
    except Exception as exc:
        _update_raw_import_status(archive["sha256"], "error", work_dir=work_dir, note=str(exc))
        raise


def _update_raw_import_status(
    sha256: str,
    status: str,
    *,
    work_dir: Path | None = None,
    note: str | None = None,
) -> None:
    with get_freebill_connection(work_dir) as conn:
        conn.execute(
            """
            UPDATE freebill_raw_files
            SET import_status = ?,
                note = COALESCE(?, note),
                last_seen_at = ?
            WHERE sha256 = ?
            """,
            (status, note, time.time(), sha256),
        )
        conn.commit()


def import_bill_records(
    records: list[dict[str, Any]],
    *,
    filename: str,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    with get_freebill_connection(work_dir) as conn:
        existing_trade_nos = {
            str(row["trade_no"] or "").strip()
            for row in conn.execute(
                """
                SELECT trade_no
                FROM bill_records
                WHERE trade_no IS NOT NULL AND TRIM(trade_no) NOT IN ('', '/')
                """,
            ).fetchall()
        }

        inserted = 0
        skipped = 0
        placeholders = ", ".join("?" for _ in BILL_RECORD_COLUMNS)
        columns_sql = ", ".join(BILL_RECORD_COLUMNS)
        insert_sql = f"INSERT INTO bill_records ({columns_sql}) VALUES ({placeholders})"

        for record in records:
            source = str(record.get("source") or "")
            trade_no = str(record.get("trade_no") or "").strip()
            if not source or not trade_no or trade_no == "/" or trade_no in existing_trade_nos:
                skipped += 1
                continue
            conn.execute(insert_sql, [record.get(column) for column in BILL_RECORD_COLUMNS])
            existing_trade_nos.add(trade_no)
            inserted += 1
        conn.commit()

        if inserted:
            reset_bill_ids(conn)
            conn.commit()

    return {
        "filename": filename,
        "processed": len(records),
        "inserted": inserted,
        "skipped": skipped,
    }


def reset_bill_ids(conn: sqlite3.Connection) -> None:
    columns_sql = ", ".join(BILL_RECORD_COLUMNS)
    conn.execute(
        f"""
        CREATE TEMP TABLE tmp_freebill_records AS
        SELECT {columns_sql}
        FROM bill_records
        ORDER BY datetime(create_time), create_time, trade_no
        """
    )
    conn.execute("DELETE FROM bill_records")
    conn.execute("DELETE FROM sqlite_sequence WHERE name = 'bill_records'")
    conn.execute(
        f"""
        INSERT INTO bill_records ({columns_sql})
        SELECT {columns_sql}
        FROM tmp_freebill_records
        ORDER BY datetime(create_time), create_time, trade_no
        """
    )
    conn.execute("DROP TABLE tmp_freebill_records")


def deduplicate_freebill_records(work_dir: Path | None = None) -> dict[str, Any]:
    with get_freebill_connection(work_dir) as conn:
        before_row = conn.execute("SELECT COUNT(*) AS total FROM bill_records").fetchone()
        duplicate_trade_nos = [
            str(row["trade_no"])
            for row in conn.execute(
                """
                SELECT trade_no
                FROM bill_records
                WHERE trade_no IS NOT NULL AND TRIM(trade_no) NOT IN ('', '/')
                GROUP BY trade_no
                HAVING COUNT(*) > 1
                """,
            ).fetchall()
        ]
        delete_ids: list[int] = []
        cross_source_groups = 0

        for trade_no in duplicate_trade_nos:
            rows = [
                _row_to_dict(row)
                for row in conn.execute(
                    """
                    SELECT *
                    FROM bill_records
                    WHERE trade_no = ?
                    ORDER BY id
                    """,
                    (trade_no,),
                ).fetchall()
            ]
            if len(rows) <= 1:
                continue
            sources = {str(row.get("source") or "") for row in rows}
            if len(sources) > 1:
                cross_source_groups += 1
            keep_id = int(max(rows, key=_deduplicate_record_score)["id"])
            delete_ids.extend(int(row["id"]) for row in rows if int(row["id"]) != keep_id)

        if delete_ids:
            conn.executemany("DELETE FROM bill_records WHERE id = ?", [(record_id,) for record_id in delete_ids])
            reset_bill_ids(conn)
            conn.commit()

        after_row = conn.execute("SELECT COUNT(*) AS total FROM bill_records").fetchone()

    return {
        "duplicate_trade_no_groups": len(duplicate_trade_nos),
        "cross_source_groups": cross_source_groups,
        "deleted_records": len(delete_ids),
        "before_records": int(before_row["total"] or 0),
        "after_records": int(after_row["total"] or 0),
    }


def _deduplicate_record_score(row: dict[str, Any]) -> tuple[int, int, int]:
    source = str(row.get("source") or "")
    status_text = f"{row.get('status') or ''} {row.get('fund_status') or ''} {row.get('type') or ''}"
    score = 0
    if source == "微信":
        score += 100
    if source == "支付宝" and _is_wechat_bill_text(status_text):
        score -= 100
    if row.get("fund_status"):
        score += 20
    score += sum(1 for column in BILL_RECORD_COLUMNS if _normalize_text(row.get(column)))
    record_id = int(row.get("id") or 0)
    return score, -record_id, record_id


def _is_wechat_bill_text(text: str) -> bool:
    return any(
        keyword in text
        for keyword in (
            "微信",
            "零钱",
            "红包",
            "已存入零钱",
            "对方已收钱",
            "朋友已收钱",
            "已转账",
            "已全额退款",
            "提现已到账",
        )
    )


def get_freebill_status(work_dir: Path | None = None) -> dict[str, Any]:
    resolved_work_dir = work_dir or get_freebill_work_dir()
    db_path = get_freebill_db_path(resolved_work_dir)
    db_exists = db_path.exists()
    with get_freebill_connection(resolved_work_dir) as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total_records,
                MIN(date(create_time)) AS min_date,
                MAX(date(create_time)) AS max_date,
                MAX(imported_at) AS last_imported_at
            FROM bill_records
            """
        ).fetchone()
        raw_file_count = conn.execute(
            f"SELECT COUNT(*) AS total FROM freebill_raw_files WHERE {_build_raw_file_query_where()}",
            _build_raw_file_query_params(),
        ).fetchone()
    return {
        "exists": db_exists,
        "work_dir": str(resolved_work_dir),
        "db_path": str(db_path),
        "total_records": int(row["total_records"] or 0),
        "min_date": row["min_date"],
        "max_date": row["max_date"],
        "last_imported_at": row["last_imported_at"],
        "raw_file_count": int(raw_file_count["total"] or 0),
    }


def _build_filter_conditions(
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    source: str | None = None,
    direction: str | None = None,
    category: str | None = None,
    q: str | None = None,
) -> tuple[str, dict[str, Any]]:
    conditions = ["1 = 1"]
    params: dict[str, Any] = {}
    if start_date:
        conditions.append("date(create_time) >= :start_date")
        params["start_date"] = start_date
    if end_date:
        conditions.append("date(create_time) <= :end_date")
        params["end_date"] = end_date
    if source:
        conditions.append("source = :source")
        params["source"] = source
    if direction:
        conditions.append("TRIM(direction) = :direction")
        params["direction"] = direction
    if category:
        conditions.append("type = :category")
        params["category"] = category
    if q:
        conditions.append(
            """
            (
                trade_no LIKE :keyword
                OR merchant_order_no LIKE :keyword
                OR counterparty LIKE :keyword
                OR product_name LIKE :keyword
                OR remark LIKE :keyword
            )
            """
        )
        params["keyword"] = f"%{q}%"
    return " AND ".join(conditions), params


def _build_program_filter_conditions(
    program: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    if not program:
        return "1 = 1", {}

    params: dict[str, Any] = {}
    decision_sql = "1" if bool(program.get("default")) else "0"
    rules = program.get("rules")
    if not isinstance(rules, list):
        rules = []

    for index, raw_rule in enumerate(rules):
        if not isinstance(raw_rule, dict):
            continue
        action = str(raw_rule.get("action") or "include").strip()
        matcher_sql = _build_program_matcher_sql(raw_rule.get("matcher"), params, f"p{index}")
        matched_sql = f"COALESCE(({matcher_sql}), 0)"
        if action == "filter":
            decision_sql = f"(({decision_sql}) AND ({matched_sql}))"
        elif action == "exclude":
            decision_sql = f"(({decision_sql}) AND NOT ({matched_sql}))"
        else:
            decision_sql = f"(({decision_sql}) OR ({matched_sql}))"

    return f"COALESCE(({decision_sql}), 0) = 1", params


def _build_program_matcher_sql(
    matcher: Any,
    params: dict[str, Any],
    prefix: str,
) -> str:
    if not isinstance(matcher, dict):
        return "1 = 1"

    kind = str(matcher.get("kind") or "all").strip()
    if kind == "all":
        return "1 = 1"
    if kind == "none":
        return "0 = 1"
    if kind == "full_text_contains":
        keyword = str(matcher.get("value") or "").strip()
        if not keyword:
            return "1 = 1"
        param_name = f"{prefix}_keyword"
        params[param_name] = f"%{keyword}%"
        return "(" + " OR ".join(
            f"COALESCE({field}, '') LIKE :{param_name}"
            for field in FREEBILL_FULL_TEXT_FIELDS
        ) + ")"
    if kind != "field":
        raise ValueError(f"不支持的 Freebill 筛选条件：{kind}")

    field = str(matcher.get("field") or "").strip()
    field_config = FREEBILL_PROGRAM_FIELDS.get(field)
    if field_config is None:
        raise ValueError(f"不支持的 Freebill 筛选字段：{field}")

    field_sql = str(field_config["sql"])
    field_mode = str(field_config["mode"])
    op = str(matcher.get("op") or "eq").strip()
    value = matcher.get("value")
    values = matcher.get("values")
    if not isinstance(values, list):
        values = []

    if op in {"in", "not_in"}:
        normalized_values = [item for item in values if item is not None and str(item) != ""]
        if not normalized_values:
            return "1 = 1"
        placeholders: list[str] = []
        for value_index, item in enumerate(normalized_values):
            param_name = f"{prefix}_v{value_index}"
            params[param_name] = _normalize_program_value(item, field_mode)
            placeholders.append(f":{param_name}")
        operator = "IN" if op == "in" else "NOT IN"
        return f"{_program_field_sql(field_sql, field_mode)} {operator} ({', '.join(placeholders)})"

    if op == "between":
        if len(values) < 2 or not str(values[0] or "").strip() or not str(values[1] or "").strip():
            return "1 = 1"
        start_name = f"{prefix}_start"
        end_name = f"{prefix}_end"
        params[start_name] = _normalize_program_value(values[0], field_mode)
        params[end_name] = _normalize_program_value(values[1], field_mode)
        return f"{_program_field_sql(field_sql, field_mode)} BETWEEN :{start_name} AND :{end_name}"

    param_name = f"{prefix}_value"
    if op in {"eq", "neq", "gte", "lte"} and not str(value or "").strip():
        return "1 = 1"
    params[param_name] = _normalize_program_value(value, field_mode)
    comparable_field_sql = _program_field_sql(field_sql, field_mode)
    if op == "neq":
        return f"{comparable_field_sql} != :{param_name}"
    if op == "contains":
        params[param_name] = f"%{str(value or '').strip()}%"
        return f"COALESCE({field_sql}, '') LIKE :{param_name}"
    if op == "not_contains":
        params[param_name] = f"%{str(value or '').strip()}%"
        return f"COALESCE({field_sql}, '') NOT LIKE :{param_name}"
    if op == "gte":
        return f"{comparable_field_sql} >= :{param_name}"
    if op == "lte":
        return f"{comparable_field_sql} <= :{param_name}"
    return f"{comparable_field_sql} = :{param_name}"


def _program_field_sql(field_sql: str, field_mode: str) -> str:
    if field_mode == "date":
        return f"date({field_sql})"
    if field_mode == "number":
        return f"COALESCE({field_sql}, 0)"
    return f"COALESCE({field_sql}, '')"


def _normalize_program_value(value: Any, field_mode: str) -> Any:
    if field_mode == "number":
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0
    if field_mode == "date":
        text = str(value or "").strip().replace("/", "-")
        return text[:10]
    return str(value or "").strip()


def get_freebill_dashboard(
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    source: str | None = None,
    direction: str | None = None,
    category: str | None = None,
    q: str | None = None,
    program: dict[str, Any] | None = None,
    category_limit: int | None = 10,
    monthly_limit: int | None = 12,
    trend_granularity: str = "month",
    work_dir: Path | None = None,
) -> dict[str, Any]:
    trend_granularity = _normalize_trend_granularity(trend_granularity)
    if program is not None:
        where_sql, params = _build_program_filter_conditions(program)
    else:
        where_sql, params = _build_filter_conditions(
            start_date=start_date,
            end_date=end_date,
            source=source,
            direction=direction,
            category=category,
            q=q,
        )
    with get_freebill_connection(work_dir) as conn:
        summary = _row_to_dict(
            conn.execute(
                f"""
                SELECT
                    COALESCE(SUM(CASE WHEN TRIM(direction) = '收入' THEN amount ELSE 0 END), 0) AS total_income,
                    COALESCE(SUM(CASE WHEN TRIM(direction) = '支出' THEN amount ELSE 0 END), 0) AS total_expense,
                    COALESCE(SUM(CASE WHEN TRIM(direction) = '不计收支' THEN amount ELSE 0 END), 0) AS total_ignore,
                    COALESCE(SUM(CASE WHEN TRIM(direction) NOT IN ('收入', '支出', '不计收支') OR direction IS NULL THEN amount ELSE 0 END), 0) AS total_other,
                    COUNT(*) AS total_count
                FROM bill_records
                WHERE {where_sql}
                """,
                params,
            ).fetchone()
        )
        summary["balance"] = float(summary["total_income"] or 0) - float(summary["total_expense"] or 0)

        source_breakdown = [
            _row_to_dict(row)
            for row in conn.execute(
                f"""
                SELECT
                    source,
                    COUNT(*) AS count,
                    COALESCE(SUM(CASE WHEN TRIM(direction) = '收入' THEN amount ELSE 0 END), 0) AS income,
                    COALESCE(SUM(CASE WHEN TRIM(direction) = '支出' THEN amount ELSE 0 END), 0) AS expense
                FROM bill_records
                WHERE {where_sql}
                GROUP BY source
                ORDER BY count DESC, source
                """,
                params,
            ).fetchall()
        ]
        expense_categories = _query_category_stats(conn, where_sql, params, "支出", limit=category_limit)
        income_categories = _query_category_stats(conn, where_sql, params, "收入", limit=category_limit)
        monthly_trend = _query_period_trend(
            conn,
            where_sql,
            params,
            granularity=trend_granularity,
            limit=_resolve_trend_limit(
                granularity=trend_granularity,
                has_date_filter=bool(start_date or end_date) or _program_has_date_rule(program),
                monthly_limit=monthly_limit,
            ),
        )

    return {
        "summary": summary,
        "sources": source_breakdown,
        "expense_categories": expense_categories,
        "income_categories": income_categories,
        "monthly_trend": monthly_trend,
        "trend_granularity": trend_granularity,
    }


def _program_has_date_rule(program: dict[str, Any] | None) -> bool:
    if not program or not isinstance(program.get("rules"), list):
        return False
    for raw_rule in program.get("rules") or []:
        if not isinstance(raw_rule, dict):
            continue
        matcher = raw_rule.get("matcher")
        if not isinstance(matcher, dict) or matcher.get("kind") != "field":
            continue
        field_config = FREEBILL_PROGRAM_FIELDS.get(str(matcher.get("field") or "").strip())
        if field_config and field_config.get("mode") == "date":
            return True
    return False


def _normalize_trend_granularity(value: str | None) -> str:
    granularity = (value or "month").strip().lower()
    if granularity not in TREND_GRANULARITIES:
        raise ValueError(f"不支持的账单趋势粒度：{value}")
    return granularity


def _resolve_trend_limit(
    *,
    granularity: str,
    has_date_filter: bool,
    monthly_limit: int | None,
) -> int | None:
    if has_date_filter:
        return None
    if granularity == "month":
        return monthly_limit
    return DEFAULT_TREND_LIMITS[granularity]


def _query_category_stats(
    conn: sqlite3.Connection,
    where_sql: str,
    params: dict[str, Any],
    direction: str,
    *,
    limit: int | None,
) -> list[dict[str, Any]]:
    query_params = {**params, "category_direction": direction}
    limit_sql = ""
    if limit is not None:
        if limit <= 0:
            return []
        query_params["category_limit"] = int(limit)
        limit_sql = "LIMIT :category_limit"
    return [
        _row_to_dict(row)
        for row in conn.execute(
            f"""
            SELECT COALESCE(NULLIF(type, ''), '未分类') AS name,
                   COALESCE(SUM(amount), 0) AS value,
                   COUNT(*) AS count
            FROM bill_records
            WHERE {where_sql} AND TRIM(direction) = :category_direction
            GROUP BY COALESCE(NULLIF(type, ''), '未分类')
            ORDER BY value DESC, count DESC
            {limit_sql}
            """,
            query_params,
        ).fetchall()
    ]


def _query_period_trend(
    conn: sqlite3.Connection,
    where_sql: str,
    params: dict[str, Any],
    *,
    granularity: str,
    limit: int | None,
) -> list[dict[str, Any]]:
    period_expr = _get_trend_period_sql(granularity)
    query_params = dict(params)
    limit_sql = ""
    if limit is not None:
        if limit <= 0:
            return []
        query_params["trend_limit"] = int(limit)
        limit_sql = "LIMIT :trend_limit"
    rows = [
        _row_to_dict(row)
        for row in conn.execute(
            f"""
            SELECT
                {period_expr} AS month,
                COALESCE(SUM(CASE WHEN TRIM(direction) = '收入' THEN amount ELSE 0 END), 0) AS income,
                COALESCE(SUM(CASE WHEN TRIM(direction) = '支出' THEN amount ELSE 0 END), 0) AS expense,
                COUNT(*) AS count
            FROM bill_records
            WHERE {where_sql}
            GROUP BY {period_expr}
            HAVING month IS NOT NULL
            ORDER BY month DESC
            {limit_sql}
            """,
            query_params,
        ).fetchall()
    ]
    return list(reversed(rows))


def _get_trend_period_sql(granularity: str) -> str:
    if granularity == "day":
        return "date(create_time)"
    if granularity == "week":
        return "date(create_time, '-' || ((CAST(strftime('%w', create_time) AS integer) + 6) % 7) || ' days')"
    if granularity == "year":
        return "strftime('%Y', create_time)"
    return "strftime('%Y-%m', create_time)"


def list_freebill_records(
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    source: str | None = None,
    direction: str | None = None,
    category: str | None = None,
    q: str | None = None,
    limit: int = 80,
    offset: int = 0,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    where_sql, params = _build_filter_conditions(
        start_date=start_date,
        end_date=end_date,
        source=source,
        direction=direction,
        category=category,
        q=q,
    )
    with get_freebill_connection(work_dir) as conn:
        total_row = conn.execute(
            f"SELECT COUNT(*) AS total FROM bill_records WHERE {where_sql}",
            params,
        ).fetchone()
        rows = [
            _row_to_dict(row)
            for row in conn.execute(
                f"""
                SELECT *
                FROM bill_records
                WHERE {where_sql}
                ORDER BY datetime(create_time) DESC, create_time DESC, id DESC
                LIMIT :limit OFFSET :offset
                """,
                {**params, "limit": limit, "offset": offset},
            ).fetchall()
        ]
    return {
        "total": int(total_row["total"] or 0),
        "items": rows,
    }


def list_freebill_filter_options(work_dir: Path | None = None) -> dict[str, list[str]]:
    with get_freebill_connection(work_dir) as conn:
        return {
            "sources": _query_distinct_strings(conn, "source"),
            "directions": _query_distinct_strings(conn, "direction"),
            "categories": _query_distinct_strings(conn, "type"),
        }


def list_freebill_raw_files(
    *,
    limit: int = 10000,
    offset: int = 0,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    with get_freebill_connection(work_dir) as conn:
        raw_file_where = _build_raw_file_query_where()
        raw_file_params = _build_raw_file_query_params()
        total_row = conn.execute(
            f"SELECT COUNT(*) AS total FROM freebill_raw_files WHERE {raw_file_where}",
            raw_file_params,
        ).fetchone()
        rows = [
            _row_to_dict(row)
            for row in conn.execute(
                f"""
                SELECT *
                FROM freebill_raw_files
                WHERE {raw_file_where}
                ORDER BY source, relative_path, original_name, id
                LIMIT :limit OFFSET :offset
                """,
                {**raw_file_params, "limit": limit, "offset": offset},
            ).fetchall()
        ]
    return {
        "total": int(total_row["total"] or 0),
        "items": rows,
    }


def _build_raw_file_query_where() -> str:
    extension_placeholders = ", ".join(f":raw_ext_{index}" for index, _ in enumerate(RAW_FILE_QUERY_EXTENSIONS))
    return f"""
        source IS NOT NULL
        AND TRIM(source) != ''
        AND extension IN ({extension_placeholders})
        AND COALESCE(note, '') != 'legacy-db-snapshot'
    """


def _build_raw_file_query_params() -> dict[str, str]:
    return {
        f"raw_ext_{index}": extension
        for index, extension in enumerate(RAW_FILE_QUERY_EXTENSIONS)
    }


def _query_distinct_strings(conn: sqlite3.Connection, column_name: str) -> list[str]:
    rows = conn.execute(
        f"""
        SELECT DISTINCT {column_name} AS value
        FROM bill_records
        WHERE {column_name} IS NOT NULL AND TRIM({column_name}) != ''
        ORDER BY value
        """
    ).fetchall()
    return [str(row["value"]) for row in rows]


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {key: row[key] for key in row.keys()}
