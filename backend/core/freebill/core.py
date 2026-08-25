from __future__ import annotations

import hashlib
import io
import json
import os
import re
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
    "account_no",
    "currency",
    "cash_type",
    "account_balance",
    "raw_sequence",
    "standard_nature",
    "standard_direction",
    "imported_at",
]

TREND_GRANULARITIES = {"day", "week", "month", "year"}
DEFAULT_TREND_LIMITS = {
    "day": 31,
    "week": 26,
    "month": 12,
    "year": None,
}
FREEBILL_CATEGORY_BRANCH_RECORD_SORT_FIELDS = {
    "amount": ("COALESCE(amount, 0)",),
    "create_time": ("datetime(create_time)", "create_time"),
    "source": ("COALESCE(NULLIF(source, ''), '') COLLATE NOCASE",),
    "product_name": ("COALESCE(NULLIF(product_name, ''), '') COLLATE NOCASE",),
    "remark": ("COALESCE(NULLIF(remark, ''), '') COLLATE NOCASE",),
}
FREEBILL_PROGRAM_FIELDS = {
    "id": {"sql": "id", "mode": "number"},
    "create_time": {"sql": "__effective:create_time__", "mode": "date"},
    "pay_time": {"sql": "__effective:pay_time__", "mode": "date"},
    "modify_time": {"sql": "__effective:modify_time__", "mode": "date"},
    "source": {"sql": "__effective:source__", "mode": "text"},
    "direction": {"sql": "__interpreted_direction__", "mode": "text"},
    "standard_direction": {"sql": "__interpreted_direction__", "mode": "text"},
    "standard_nature": {"sql": "__interpreted_type__", "mode": "text"},
    "type": {"sql": "__effective:type__", "mode": "text"},
    "category": {"sql": "__effective:type__", "mode": "text"},
    "counterparty": {"sql": "__effective:counterparty__", "mode": "text"},
    "product_name": {"sql": "__effective:product_name__", "mode": "text"},
    "amount": {"sql": "__effective:amount__", "mode": "number"},
    "status": {"sql": "__effective:status__", "mode": "text"},
    "remark": {"sql": "__effective:remark__", "mode": "text"},
    "trade_no": {"sql": "trade_no", "mode": "text"},
    "merchant_order_no": {"sql": "merchant_order_no", "mode": "text"},
    "fund_status": {"sql": "__effective:fund_status__", "mode": "text"},
    "account_no": {"sql": "__effective:account_no__", "mode": "text"},
    "currency": {"sql": "__effective:currency__", "mode": "text"},
    "cash_type": {"sql": "__effective:cash_type__", "mode": "text"},
    "account_balance": {"sql": "__effective:account_balance__", "mode": "number"},
    "raw_sequence": {"sql": "__effective:raw_sequence__", "mode": "text"},
}
FREEBILL_FULL_TEXT_FIELDS = [
    "trade_no",
    "merchant_order_no",
    "counterparty",
    "product_name",
    "remark",
    "status",
    "fund_status",
    "account_no",
    "raw_sequence",
]

RAW_BILL_FILE_EXTENSIONS = {
    ".csv",
    ".xlsx",
    ".xlsm",
    ".xls",
    ".zip",
}
RAW_FILE_EXTENSIONS = RAW_BILL_FILE_EXTENSIONS
RAW_FILE_QUERY_EXTENSIONS = tuple(sorted({*RAW_BILL_FILE_EXTENSIONS, ".json"}))
CSV_ENCODINGS = ("gb18030", "gbk", "utf-8-sig", "utf-8")
ALIPAY_LEGACY_SOURCE_HINTS = (
    "支付宝",
    "淘宝",
    "天猫",
    "阿里巴巴",
    "其他（包括阿里巴巴和外部商家）",
)
ALIPAY_LEGACY_FUND_STATUSES = {"已支出", "已收入", "资金转移"}
FLOW_OVERRIDE_CATEGORY = "流水"
FLOW_EXPENSE_CATEGORY = "流水支出"
FLOW_INCOME_CATEGORY = "流水收入"
STANDARD_NATURE_REGULAR = "常规"
STANDARD_NATURE_LOAN = "借贷"
STANDARD_NATURE_FINANCE = "理财"
STANDARD_NATURE_TRANSFER = "转账"
STANDARD_NATURE_FLOW = "流水"
STANDARD_DIRECTION_EXPENSE = "支出"
STANDARD_DIRECTION_INCOME = "收入"
STANDARD_DIRECTION_NEUTRAL = "收支"
STANDARD_DIRECTIONS = {"支出", "收入"}
FREEBILL_STANDARD_DIRECTIONS = (
    STANDARD_DIRECTION_EXPENSE,
    STANDARD_DIRECTION_NEUTRAL,
    STANDARD_DIRECTION_INCOME,
)
FREEBILL_STANDARDIZATION_VERSION = 30
FREEBILL_STANDARD_NATURES = (
    STANDARD_NATURE_REGULAR,
    STANDARD_NATURE_LOAN,
    STANDARD_NATURE_FINANCE,
    STANDARD_NATURE_TRANSFER,
    STANDARD_NATURE_FLOW,
)
FREEBILL_INTERPRET_RULE_FIELDS = {
    "source": {"label": "来源", "mode": "text"},
    "direction": {"label": "导入收支", "mode": "text"},
    "standard_direction": {"label": "当前收支", "mode": "text"},
    "standard_nature": {"label": "当前类型", "mode": "text"},
    "type": {"label": "分类", "mode": "text"},
    "counterparty": {"label": "交易对方", "mode": "text"},
    "product_name": {"label": "商品", "mode": "text"},
    "remark": {"label": "备注", "mode": "text"},
    "status": {"label": "状态", "mode": "text"},
    "amount": {"label": "金额", "mode": "number"},
}
FREEBILL_MANUAL_OVERRIDE_FIELDS = {
    "create_time": {"label": "交易时间", "mode": "text"},
    "source": {"label": "来源", "mode": "text"},
    "standard_direction": {"label": "收支", "mode": "direction"},
    "standard_nature": {"label": "类型", "mode": "nature"},
    "type": {"label": "分类", "mode": "text"},
    "counterparty": {"label": "交易对方", "mode": "text"},
    "product_name": {"label": "商品", "mode": "text"},
    "amount": {"label": "金额", "mode": "number"},
    "status": {"label": "状态", "mode": "text"},
    "remark": {"label": "备注", "mode": "text"},
    "pay_time": {"label": "付款时间", "mode": "text"},
    "modify_time": {"label": "修改时间", "mode": "text"},
    "location": {"label": "交易来源地", "mode": "text"},
    "fund_status": {"label": "资金状态", "mode": "text"},
    "service_fee": {"label": "服务费", "mode": "number"},
    "refund_amount": {"label": "退款金额", "mode": "number"},
    "account_no": {"label": "账号", "mode": "text"},
    "currency": {"label": "币种", "mode": "text"},
    "cash_type": {"label": "钞汇", "mode": "text"},
    "account_balance": {"label": "账户余额", "mode": "number"},
    "raw_sequence": {"label": "原始序号", "mode": "text"},
}
FREEBILL_PINNED_MANUAL_OVERRIDE_FIELDS = {"standard_direction", "standard_nature"}
FREEBILL_INTERPRET_RULE_OPERATORS = {
    "eq",
    "neq",
    "contains",
    "not_contains",
    "in",
    "not_in",
    "gt",
    "gte",
    "lt",
    "lte",
}
FREEBILL_BUILT_IN_INTERPRET_RULES = (
    {
        "key": "yuebao-income",
        "name": "余额宝收益",
        "target_nature": STANDARD_NATURE_FINANCE,
        "matcher_text": "商品以“余额宝-”开头，且包含收益/结息/分红",
        "result_text": "收支=收入，类型=理财",
        "note": "收益发放不是普通账户转移。",
    },
    {
        "key": "yuebao-transfer",
        "name": "余额宝转入转出",
        "target_nature": STANDARD_NATURE_TRANSFER,
        "matcher_text": "商品为余额宝-单次转入、余额宝-工资理财，或转出到银行卡等账户移动",
        "result_text": "类型=转账，收支按记录来源参照方向推断",
        "note": "从支付宝记录方看，余额宝单次转入、工资理财属于外部资金转入支付宝，记为转账收入；转出到银行卡记为转账支出。",
    },
    {
        "key": "yuebao-balance-flow",
        "name": "余额宝余额流转",
        "target_nature": STANDARD_NATURE_FLOW,
        "matcher_text": "商品为余额宝-转出到余额",
        "result_text": "类型=流水，收支=收支",
        "note": "余额宝转出到支付宝余额是支付宝应用内账户流转，不计入支出或收入。",
    },
    {
        "key": "regular-income-expense",
        "name": "常规收支",
        "target_nature": STANDARD_NATURE_REGULAR,
        "matcher_text": "导入收支已经是“收入/支出”",
        "result_text": "类型=常规，收支沿用导入值",
        "note": "微信、支付宝原本明确的消费/收入默认不改成转账。",
    },
    {
        "key": "loan-keywords",
        "name": "借贷关键词",
        "target_nature": STANDARD_NATURE_LOAN,
        "matcher_text": "分类、商品、交易对方或备注包含借呗、信用借还、还款、借款、借呗放款等；或建行商品包含支付宝-还款",
        "result_text": "类型=借贷，收支按文本方向推断",
        "note": "别人与自己之间的借还款先归到借贷。",
    },
    {
        "key": "finance-keywords",
        "name": "理财关键词",
        "target_nature": STANDARD_NATURE_FINANCE,
        "matcher_text": "余利宝；银转证/银行转证券等证券划转；或非常规收支文本包含基金、理财、申购、赎回、保险等",
        "result_text": "类型=理财，收支按文本方向推断",
        "note": "投资、赎回、收益类资金流归到理财。",
    },
    {
        "key": "internal-transfer-keywords",
        "name": "账户转移关键词",
        "target_nature": STANDARD_NATURE_TRANSFER,
        "matcher_text": "文本包含零钱充值、提现到银行卡、财付通/微信支付、建设银行等账户移动词；或银联入账且交易对方为支付宝；或建行商品包含支付宝-支付宝-",
        "result_text": "类型=转账，收支按记录来源参照方向推断",
        "note": "自己的不同账户、不同 App 之间的移动归到转账。",
    },
)
FREEBILL_INTERPRET_SETTINGS_META_KEY = "interpret_settings"
CCB_CURRENT_ACCOUNT_PARSER_FORMAT = "ccb-excel"
CCB_CURRENT_ACCOUNT_TITLE = "中国建设银行个人活期账户全部交易明细"
CCB_CURRENT_ACCOUNT_HEADER_MARKERS = ("序号", "交易日期", "交易金额")
CCB_CURRENT_ACCOUNT_REQUIRED_COLUMNS = {
    "序号",
    "摘要",
    "币别",
    "钞汇",
    "交易日期",
    "交易金额",
    "账户余额",
}
CCB_CURRENT_ACCOUNT_COLUMN_MAP = {
    "序号": "raw_sequence",
    "摘要": "type",
    "币别": "currency",
    "钞汇": "cash_type",
    "交易日期": "create_time",
    "交易金额": "signed_amount",
    "账户余额": "account_balance",
    "交易地点/附言": "memo",
    "交易地点／附言": "memo",
    "对方账号与户名": "counterparty",
}
CCB_CURRENT_ACCOUNT_PARSE_STEPS = (
    "读取建行邮件压缩包解出的 xls/xlsx；文件整理层建议统一转成 xlsx。",
    "在前 80 行中定位同时包含“序号、交易日期、交易金额”的表头行。",
    "表头前读取卡号/账号、客户名称、起始日期、结束日期、总收入、总支出等元信息。",
    "从表头行开始读取明细，交易金额保留原始正负号，入库金额取绝对值。",
    "交易金额大于 0 解释为收入，小于 0 解释为支出，等于 0 暂记不计收支。",
    "用账号、日期、序号、摘要、金额、余额、附言、对方账号户名生成稳定去重 key。",
    "如果表头给出了总收入/总支出，导入前用明细合计做一次一致性校验。",
)
FREEBILL_CATEGORY_DIMENSIONS = (
    "standard_direction",
    "standard_nature",
    "type",
    "counterparty",
)
FREEBILL_CATEGORY_DIMENSION_LABELS = {
    "standard_direction": "收支",
    "standard_nature": "类型",
    "type": "分类",
    "counterparty": "交易对方",
}
FREEBILL_CATEGORY_DIMENSION_EMPTY_LABELS = {
    "standard_direction": "(空白)",
    "standard_nature": "(空白)",
    "type": "未分类",
    "counterparty": "未标注交易对方",
}
FREEBILL_CATEGORY_REMAINDER_LABEL = "..."
FLOW_FINANCE_KEYWORDS = (
    "余利宝",
    "零钱通",
    "基金",
    "理财",
    "申购",
    "赎回",
    "收益发放",
    "结息",
    "分红",
    "保险",
    "保费",
)
FLOW_FINANCE_PRIORITY_KEYWORDS = (
    "余利宝",
    "银转证",
    "银行转证券",
    "证转银",
    "证券转银行",
)
FLOW_LOAN_PRIORITY_KEYWORDS = (
    "借呗",
    "微粒贷",
    "花呗",
    "白条",
    "信用借还",
    "借款",
    "借钱",
    "还款",
    "借贷",
    "放款",
    "还贷",
    "贷款",
)
FLOW_INTERNAL_TRANSFER_KEYWORDS = (
    "零钱充值",
    "零钱提现",
    "支付机构提现",
    "提现到银行卡",
    "转出到银行卡",
    "转出至银行卡",
    "转回银行卡",
    "充值到零钱",
    "银行卡转出",
    "建设银行",
    "工商银行",
    "农业银行",
    "中国银行",
    "招商银行",
    "交通银行",
    "邮储银行",
    "网商银行",
    "银行卡",
    "财付通",
    "微信支付",
)
TRANSFER_OUT_OF_RECORD_SOURCE_KEYWORDS = (
    "零钱提现",
    "提现到银行卡",
    "转出到银行卡",
    "转出至银行卡",
    "转回银行卡",
    "支付机构提现",
    "财付通",
    "微信支付",
    "微信转账",
)
TRANSFER_INTO_RECORD_SOURCE_KEYWORDS = (
    "零钱充值",
    "充值到零钱",
    "银行卡转出",
    "银行卡转出到支付宝",
    "转入零钱通",
    "转入至零钱通",
    "自动转入",
    "单次转入",
    "工资理财",
)
STANDARD_DIRECTION_INCOME_KEYWORDS = (
    "退款",
    "赎回",
    "收益发放",
    "结息",
    "分红",
    "卖出",
    "放款",
    "回款",
    "还钱",
    "还款到账",
    "提现",
    "转出",
    "转回",
    "收款",
    "存入",
)
STANDARD_DIRECTION_EXPENSE_KEYWORDS = (
    "买入",
    "申购",
    "投保",
    "保费",
    "还款",
    "借出",
    "借款发放",
    "充值",
    "转入",
    "支付",
    "消费",
)


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
            account_no TEXT,
            currency TEXT,
            cash_type TEXT,
            account_balance REAL,
            raw_sequence TEXT,
            standard_nature TEXT,
            standard_direction TEXT,
            imported_at REAL
        )
        """
    )
    _ensure_column(conn, "bill_records", "imported_at", "REAL")
    _ensure_column(conn, "bill_records", "account_no", "TEXT")
    _ensure_column(conn, "bill_records", "currency", "TEXT")
    _ensure_column(conn, "bill_records", "cash_type", "TEXT")
    _ensure_column(conn, "bill_records", "account_balance", "REAL")
    _ensure_column(conn, "bill_records", "raw_sequence", "TEXT")
    _ensure_column(conn, "bill_records", "standard_nature", "TEXT")
    _ensure_column(conn, "bill_records", "standard_direction", "TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_freebill_records_source ON bill_records (source)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_freebill_records_trade_no ON bill_records (trade_no)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_freebill_records_create_time ON bill_records (create_time)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_freebill_records_direction ON bill_records (direction)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_freebill_records_type ON bill_records (type)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_freebill_records_account_no ON bill_records (account_no)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_freebill_records_standard_nature ON bill_records (standard_nature)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_freebill_records_standard_direction ON bill_records (standard_direction)")
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS freebill_record_overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_no TEXT NOT NULL UNIQUE,
            source TEXT,
            original_direction TEXT,
            original_type TEXT,
            original_standard_nature TEXT,
            original_standard_direction TEXT,
            override_direction TEXT NOT NULL,
            override_type TEXT NOT NULL,
            override_standard_nature TEXT,
            override_standard_direction TEXT,
            manual_overrides_json TEXT,
            note TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    _ensure_column(conn, "freebill_record_overrides", "original_standard_nature", "TEXT")
    _ensure_column(conn, "freebill_record_overrides", "original_standard_direction", "TEXT")
    _ensure_column(conn, "freebill_record_overrides", "override_standard_nature", "TEXT")
    _ensure_column(conn, "freebill_record_overrides", "override_standard_direction", "TEXT")
    _ensure_column(conn, "freebill_record_overrides", "manual_overrides_json", "TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_freebill_record_overrides_trade_no "
        "ON freebill_record_overrides (trade_no)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS freebill_interpret_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            order_index INTEGER NOT NULL DEFAULT 0,
            matcher_json TEXT NOT NULL DEFAULT '{}',
            set_direction TEXT,
            set_nature TEXT,
            note TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_freebill_interpret_rules_enabled_order "
        "ON freebill_interpret_rules (enabled, order_index, id)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS freebill_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    _migrate_freebill_standardization(conn)
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


def _migrate_freebill_standardization(conn: sqlite3.Connection) -> None:
    version_row = conn.execute(
        "SELECT value FROM freebill_meta WHERE key = 'standardization_version'"
    ).fetchone()
    try:
        standardization_version = int(str(version_row["value"])) if version_row else 0
    except (TypeError, ValueError):
        standardization_version = 0
    needs_rule_backfill = standardization_version < FREEBILL_STANDARDIZATION_VERSION
    needs_bill_backfill = conn.execute(
        """
        SELECT 1
        FROM bill_records
        WHERE standard_nature IS NULL
           OR TRIM(COALESCE(standard_nature, '')) = ''
           OR standard_direction IS NULL
           OR TRIM(COALESCE(standard_direction, '')) = ''
        LIMIT 1
        """
    ).fetchone()
    needs_override_backfill = conn.execute(
        """
        SELECT 1
        FROM freebill_record_overrides
        WHERE original_standard_nature IS NULL
           OR TRIM(COALESCE(original_standard_nature, '')) = ''
           OR original_standard_direction IS NULL
           OR TRIM(COALESCE(original_standard_direction, '')) = ''
           OR override_standard_nature IS NULL
           OR TRIM(COALESCE(override_standard_nature, '')) = ''
           OR override_standard_direction IS NULL
           OR TRIM(COALESCE(override_standard_direction, '')) = ''
        LIMIT 1
        """
    ).fetchone()
    has_legacy_mutated_rows = conn.execute(
        """
        SELECT 1
        FROM bill_records
        WHERE trade_no IN (SELECT trade_no FROM freebill_record_overrides)
          AND (
                TRIM(COALESCE(direction, '')) = '不计收支'
                OR TRIM(COALESCE(type, '')) IN (?, ?, ?)
          )
        LIMIT 1
        """,
        (
            FLOW_OVERRIDE_CATEGORY,
            FLOW_EXPENSE_CATEGORY,
            FLOW_INCOME_CATEGORY,
        ),
    ).fetchone()
    if not needs_rule_backfill and not needs_bill_backfill and not needs_override_backfill and not has_legacy_mutated_rows:
        return

    interpret_settings = _load_freebill_interpret_settings(conn)
    interpret_rules = _load_enabled_freebill_interpret_rules(conn)
    override_rows = conn.execute(
        """
        SELECT *
        FROM freebill_record_overrides
        ORDER BY id
        """
    ).fetchall()
    if override_rows:
        conn.executemany(
            """
            UPDATE bill_records
            SET direction = COALESCE(?, direction),
                type = COALESCE(?, type)
            WHERE trade_no = ?
            """,
            [
                (
                    row["original_direction"],
                    row["original_type"],
                    row["trade_no"],
                )
                for row in override_rows
            ],
        )

    bill_rows = conn.execute(
        """
        SELECT *
        FROM bill_records
        ORDER BY id
        """
    ).fetchall()
    if bill_rows:
        conn.executemany(
            """
            UPDATE bill_records
            SET type = ?,
                standard_nature = ?,
                standard_direction = ?
            WHERE id = ?
            """,
            [
                (
                    normalized_record["type"],
                    normalized_record["standard_nature"],
                    normalized_record["standard_direction"],
                    row["id"],
                )
                for row in bill_rows
                for normalized_record in [
                    _apply_freebill_standard_fields(
                        _row_to_dict(row),
                        interpret_rules=interpret_rules,
                        interpret_settings=interpret_settings,
                    )
                ]
            ],
        )

    if override_rows:
        record_map = {
            str(row["trade_no"] or "").strip(): _row_to_dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM bill_records
                WHERE trade_no IN (
                    SELECT trade_no
                    FROM freebill_record_overrides
                )
                """
            ).fetchall()
        }
        conn.executemany(
            """
            UPDATE freebill_record_overrides
            SET override_type = CASE
                    WHEN override_direction = '不计收支'
                     AND override_type IN (?, ?, ?)
                        THEN COALESCE(NULLIF(TRIM(original_type), ''), override_type)
                    ELSE override_type
                END,
                original_standard_nature = ?,
                original_standard_direction = ?,
                override_standard_nature = ?,
                override_standard_direction = ?
            WHERE id = ?
            """,
            [
                (
                    FLOW_OVERRIDE_CATEGORY,
                    FLOW_EXPENSE_CATEGORY,
                    FLOW_INCOME_CATEGORY,
                    *_resolve_freebill_override_standard_fields(
                        record_map.get(str(row["trade_no"] or "").strip(), {}),
                        _row_to_dict(row),
                    ),
                    row["id"],
                )
                for row in override_rows
            ],
        )
    conn.execute(
        """
        INSERT INTO freebill_meta (key, value)
        VALUES ('standardization_version', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (str(FREEBILL_STANDARDIZATION_VERSION),),
    )


def _normalize_text(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).replace("\t", "").strip()
    return text or None


def _normalize_datetime_text(value: Any) -> str | None:
    text = _normalize_text(value)
    if not text:
        return None
    return text.replace("/", "-")


def _normalize_alipay_direction(value: Any) -> str | None:
    text = _normalize_text(value)
    if text == "其他":
        return "不计收支"
    return text


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


def _build_freebill_record_text(record: dict[str, Any], *extra_parts: Any) -> str:
    parts = [
        record.get("type"),
        record.get("product_name"),
        record.get("counterparty"),
        record.get("remark"),
        record.get("status"),
        record.get("fund_status"),
        record.get("location"),
        record.get("source"),
        *extra_parts,
    ]
    return " ".join(part for part in (_normalize_text(item) for item in parts) if part)


def _is_yuebao_record(record: dict[str, Any]) -> bool:
    product_name = _normalize_text(record.get("product_name")) or ""
    record_type = _normalize_text(record.get("type")) or ""
    return product_name.startswith("余额宝-") or record_type == "余额宝"


def _is_yuebao_income_record(record: dict[str, Any]) -> bool:
    if not _is_yuebao_record(record):
        return False
    text = _build_freebill_record_text(record)
    return any(keyword in text for keyword in ("收益发放", "收益", "结息", "分红"))


def _is_yuebao_transfer_record(record: dict[str, Any]) -> bool:
    if not _is_yuebao_record(record):
        return False
    text = _build_freebill_record_text(record)
    return any(keyword in text for keyword in ("单次转入", "工资理财", "转出到银行卡", "转出至银行卡", "转回银行卡"))


def _is_yuebao_balance_flow_record(record: dict[str, Any]) -> bool:
    if not _is_yuebao_record(record):
        return False
    text = _build_freebill_record_text(record)
    return "转出到余额" in text


def _normalize_freebill_record_type(record: dict[str, Any]) -> str | None:
    return _normalize_text(record.get("type"))


def _is_priority_loan_record(record: dict[str, Any]) -> bool:
    record_type = _normalize_text(record.get("type")) or ""
    return (
        any(keyword in record_type for keyword in FLOW_LOAN_PRIORITY_KEYWORDS)
        or _is_ant_loan_deduction_record(record)
        or _is_ccb_alipay_repayment_loan_record(record)
        or _is_alipay_jiebei_disbursement_record(record)
    )


def _is_ant_loan_deduction_record(record: dict[str, Any]) -> bool:
    text = _build_freebill_record_text(record)
    return (
        "花呗借呗扣款" in text
        or "蚂蚁花呗借呗扣款" in text
        or ("借呗扣款" in text and ("蚂蚁消金" in text or "蚂蚁花呗" in text))
    )


def _is_ccb_alipay_repayment_loan_record(record: dict[str, Any]) -> bool:
    source = _normalize_text(record.get("source")) or ""
    if source != "建设银行":
        return False
    text = _build_freebill_record_text(record)
    return "支付宝-还款" in text


def _is_alipay_jiebei_disbursement_record(record: dict[str, Any]) -> bool:
    source = _normalize_text(record.get("source")) or ""
    if source != "支付宝":
        return False
    text = _build_freebill_record_text(record)
    return "借呗" in text and "放款" in text


def _is_priority_internal_transfer_record(record: dict[str, Any]) -> bool:
    record_type = _normalize_text(record.get("type")) or ""
    counterparty = _normalize_text(record.get("counterparty")) or ""
    return (
        ("银联入账" in record_type and "支付宝" in counterparty)
        or _is_ccb_fund_subscription_transfer_record(record)
        or _is_ccb_alipay_account_transfer_record(record)
    )


def _is_ccb_fund_subscription_transfer_record(record: dict[str, Any]) -> bool:
    source = _normalize_text(record.get("source")) or ""
    if source != "建设银行":
        return False
    text = _build_freebill_record_text(record)
    return "基金申购" in text


def _is_ccb_alipay_account_transfer_record(record: dict[str, Any]) -> bool:
    source = _normalize_text(record.get("source")) or ""
    if source != "建设银行":
        return False
    text = _build_freebill_record_text(record)
    return "支付宝-支付宝-" in text


def _infer_freebill_standard_nature(
    record: dict[str, Any],
    *,
    force_flow: bool = False,
    original_direction: str | None = None,
    enabled_built_in_rule_keys: set[str] | None = None,
) -> str:
    enabled_rules = (
        _get_enabled_freebill_built_in_rule_keys(None)
        if enabled_built_in_rule_keys is None
        else enabled_built_in_rule_keys
    )
    normalized_original_direction = _normalize_text(original_direction)
    normalized_direction = _normalize_text(record.get("direction"))
    if "loan-keywords" in enabled_rules and _is_priority_loan_record(record):
        return STANDARD_NATURE_LOAN
    if "yuebao-income" in enabled_rules and _is_yuebao_income_record(record):
        return STANDARD_NATURE_FINANCE
    text = _build_freebill_record_text(record)
    transfer_text = _build_freebill_record_text({**record, "source": None})
    if "finance-keywords" in enabled_rules and any(keyword in text for keyword in FLOW_FINANCE_PRIORITY_KEYWORDS):
        return STANDARD_NATURE_FINANCE
    if "internal-transfer-keywords" in enabled_rules and _is_priority_internal_transfer_record(record):
        return STANDARD_NATURE_TRANSFER
    if "yuebao-transfer" in enabled_rules and _is_yuebao_transfer_record(record):
        return STANDARD_NATURE_TRANSFER
    if "internal-transfer-keywords" in enabled_rules and any(keyword in transfer_text for keyword in FLOW_INTERNAL_TRANSFER_KEYWORDS):
        return STANDARD_NATURE_TRANSFER
    if "yuebao-balance-flow" in enabled_rules and _is_yuebao_balance_flow_record(record):
        return STANDARD_NATURE_FLOW
    if (
        "regular-income-expense" in enabled_rules
        and
        not force_flow
        and (
            normalized_original_direction in STANDARD_DIRECTIONS
            or normalized_direction in STANDARD_DIRECTIONS
        )
    ):
        return STANDARD_NATURE_REGULAR

    if "loan-keywords" in enabled_rules and any(keyword in text for keyword in FLOW_LOAN_PRIORITY_KEYWORDS):
        return STANDARD_NATURE_LOAN
    finance_text = _build_freebill_record_text({**record, "type": None})
    if "finance-keywords" in enabled_rules and any(keyword in finance_text for keyword in FLOW_FINANCE_KEYWORDS):
        return STANDARD_NATURE_FINANCE
    if force_flow:
        return STANDARD_NATURE_FLOW
    return STANDARD_NATURE_REGULAR


def _infer_freebill_standard_direction(
    record: dict[str, Any],
    *,
    standard_nature: str,
    original_direction: str | None = None,
    enabled_built_in_rule_keys: set[str] | None = None,
) -> str:
    enabled_rules = (
        _get_enabled_freebill_built_in_rule_keys(None)
        if enabled_built_in_rule_keys is None
        else enabled_built_in_rule_keys
    )
    text = _build_freebill_record_text(record, original_direction)
    if standard_nature == STANDARD_NATURE_FLOW:
        return STANDARD_DIRECTION_NEUTRAL

    normalized_original_direction = _normalize_text(original_direction)
    if normalized_original_direction in STANDARD_DIRECTIONS:
        return str(normalized_original_direction)

    normalized_direction = _normalize_text(record.get("direction"))
    if normalized_direction in STANDARD_DIRECTIONS:
        return str(normalized_direction)

    normalized_status = _normalize_text(record.get("status")) or ""
    if normalized_direction == "不计收支" and (
        normalized_status == "交易关闭" or normalized_status.endswith("失败")
    ):
        return STANDARD_DIRECTION_NEUTRAL

    if standard_nature == STANDARD_NATURE_FINANCE and (
        "finance-keywords" in enabled_rules or "yuebao-income" in enabled_rules
    ):
        if any(keyword in text for keyword in ("赎回", "收益发放", "结息", "分红", "卖出", "转出")):
            return "收入"
        return "支出"
    if standard_nature == STANDARD_NATURE_LOAN and "loan-keywords" in enabled_rules:
        if any(keyword in text for keyword in ("还款", "扣款", "借出", "放款给", "放贷")):
            return "支出"
        return "收入"
    if standard_nature == STANDARD_NATURE_TRANSFER and (
        "internal-transfer-keywords" in enabled_rules or "yuebao-transfer" in enabled_rules
    ):
        if any(keyword in text for keyword in TRANSFER_OUT_OF_RECORD_SOURCE_KEYWORDS):
            return "支出"
        if any(keyword in text for keyword in TRANSFER_INTO_RECORD_SOURCE_KEYWORDS):
            return "收入"

    if any(keyword in text for keyword in STANDARD_DIRECTION_INCOME_KEYWORDS):
        return "收入"
    if any(keyword in text for keyword in STANDARD_DIRECTION_EXPENSE_KEYWORDS):
        return "支出"
    return "支出"


def _infer_freebill_standard_fields(
    record: dict[str, Any],
    *,
    original_direction: str | None = None,
    enabled_built_in_rule_keys: set[str] | None = None,
) -> tuple[str, str]:
    nature = _infer_freebill_standard_nature(
        record,
        original_direction=original_direction,
        enabled_built_in_rule_keys=enabled_built_in_rule_keys,
    )
    direction = _infer_freebill_standard_direction(
        record,
        standard_nature=nature,
        original_direction=original_direction,
        enabled_built_in_rule_keys=enabled_built_in_rule_keys,
    )
    return nature, direction


def _resolve_freebill_override_standard_fields(
    record: dict[str, Any],
    override_row: dict[str, Any] | None = None,
) -> tuple[str, str, str, str]:
    override = override_row or {}
    original_nature, original_direction = _infer_freebill_standard_fields(
        record,
        original_direction=_normalize_text(override.get("original_direction")),
    )
    override_direction = _normalize_text(override.get("override_direction"))
    override_type = _normalize_text(override.get("override_type"))
    if override_direction == "不计收支":
        override_nature = _infer_freebill_standard_nature(
            {
                **record,
                "type": override.get("original_type") or record.get("type"),
            },
            force_flow=True,
            original_direction=original_direction,
        )
        override_standard_direction = original_direction
    else:
        override_nature = _infer_freebill_standard_nature(
            {
                **record,
                "type": override_type or record.get("type"),
            },
            force_flow=override_direction not in STANDARD_DIRECTIONS,
            original_direction=override_direction or original_direction,
        )
        override_standard_direction = _infer_freebill_standard_direction(
            record,
            standard_nature=override_nature,
            original_direction=override_direction or original_direction,
        )
    return (
        original_nature,
        original_direction,
        override_nature,
        override_standard_direction,
    )


def _load_enabled_freebill_interpret_rules(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return _load_freebill_interpret_rules(conn, enabled_only=True)


def _default_freebill_interpret_settings() -> dict[str, Any]:
    return {
        "signed_category_values": False,
        "built_in_rules": {
            str(item["key"]): True
            for item in FREEBILL_BUILT_IN_INTERPRET_RULES
        },
    }


def _load_freebill_interpret_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    defaults = _default_freebill_interpret_settings()
    row = conn.execute(
        "SELECT value FROM freebill_meta WHERE key = ?",
        (FREEBILL_INTERPRET_SETTINGS_META_KEY,),
    ).fetchone()
    if not row:
        return defaults
    try:
        raw_settings = json.loads(str(row["value"] or "{}"))
    except json.JSONDecodeError:
        return defaults
    if not isinstance(raw_settings, dict):
        return defaults
    built_in_rules = dict(defaults["built_in_rules"])
    raw_built_in_rules = raw_settings.get("built_in_rules")
    if isinstance(raw_built_in_rules, dict):
        for key in built_in_rules:
            if key in raw_built_in_rules:
                built_in_rules[key] = bool(raw_built_in_rules[key])
    return {
        "signed_category_values": bool(raw_settings.get("signed_category_values", defaults["signed_category_values"])),
        "built_in_rules": built_in_rules,
    }


def _save_freebill_interpret_settings(
    conn: sqlite3.Connection,
    settings: dict[str, Any] | None,
) -> dict[str, Any]:
    normalized = _normalize_freebill_interpret_settings(settings)
    conn.execute(
        """
        INSERT INTO freebill_meta (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (
            FREEBILL_INTERPRET_SETTINGS_META_KEY,
            json.dumps(normalized, ensure_ascii=False, sort_keys=True),
        ),
    )
    return normalized


def _normalize_freebill_interpret_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    defaults = _default_freebill_interpret_settings()
    raw_settings = settings if isinstance(settings, dict) else {}
    built_in_rules = dict(defaults["built_in_rules"])
    raw_built_in_rules = raw_settings.get("built_in_rules")
    if isinstance(raw_built_in_rules, dict):
        for key in built_in_rules:
            if key in raw_built_in_rules:
                built_in_rules[key] = bool(raw_built_in_rules[key])
    return {
        "signed_category_values": bool(raw_settings.get("signed_category_values", defaults["signed_category_values"])),
        "built_in_rules": built_in_rules,
    }


def _get_enabled_freebill_built_in_rule_keys(settings: dict[str, Any] | None) -> set[str]:
    normalized = _normalize_freebill_interpret_settings(settings)
    return {
        key
        for key, enabled in normalized["built_in_rules"].items()
        if enabled
    }


def _load_freebill_interpret_rules(
    conn: sqlite3.Connection,
    *,
    enabled_only: bool = False,
) -> list[dict[str, Any]]:
    where_sql = "WHERE enabled = 1" if enabled_only else ""
    rows = conn.execute(
        f"""
        SELECT *
        FROM freebill_interpret_rules
        {where_sql}
        ORDER BY order_index, id
        """
    ).fetchall()
    return [_normalize_freebill_interpret_rule_row(_row_to_dict(row)) for row in rows]


def _normalize_freebill_interpret_rule_row(row: dict[str, Any]) -> dict[str, Any]:
    try:
        matcher = json.loads(str(row.get("matcher_json") or "{}"))
    except json.JSONDecodeError:
        matcher = {}
    return {
        "id": row.get("id"),
        "name": _normalize_text(row.get("name")) or "",
        "enabled": bool(int(row.get("enabled") or 0)),
        "order_index": int(row.get("order_index") or 0),
        "matcher": _normalize_freebill_interpret_matcher(matcher),
        "set_direction": _normalize_freebill_interpret_direction(row.get("set_direction")),
        "set_nature": _normalize_freebill_interpret_nature(row.get("set_nature")),
        "note": _normalize_text(row.get("note")),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _normalize_freebill_interpret_matcher(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    kind = str(value.get("kind") or "field")
    if kind not in {"all", "none", "field", "full_text_contains"}:
        kind = "field"
    if kind in {"all", "none"}:
        return {"kind": kind}
    if kind == "full_text_contains":
        return {
            "kind": kind,
            "value": _normalize_text(value.get("value")) or "",
            "ignore_case": bool(value.get("ignore_case", True)),
        }

    field = str(value.get("field") or "product_name")
    if field not in FREEBILL_INTERPRET_RULE_FIELDS:
        field = "product_name"
    field_mode = str(FREEBILL_INTERPRET_RULE_FIELDS[field]["mode"])
    op = str(value.get("op") or ("eq" if field_mode == "number" else "contains"))
    if op not in FREEBILL_INTERPRET_RULE_OPERATORS:
        op = "eq" if field_mode == "number" else "contains"
    raw_values = value.get("values")
    values = raw_values if isinstance(raw_values, list) else []
    return {
        "kind": "field",
        "field": field,
        "op": op,
        "value": value.get("value"),
        "values": values,
        "ignore_case": bool(value.get("ignore_case", True)),
    }


def _normalize_freebill_interpret_direction(value: Any) -> str | None:
    text = _normalize_text(value)
    return text if text in FREEBILL_STANDARD_DIRECTIONS else None


def _normalize_freebill_interpret_nature(value: Any) -> str | None:
    text = _normalize_text(value)
    return text if text in FREEBILL_STANDARD_NATURES else None


def _normalize_freebill_manual_overrides(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value) if value.strip() else {}
        except json.JSONDecodeError:
            value = {}
    if not isinstance(value, dict):
        return {}

    normalized: dict[str, Any] = {}
    for field, config in FREEBILL_MANUAL_OVERRIDE_FIELDS.items():
        if field not in value:
            continue
        mode = str(config.get("mode") or "text")
        raw_value = value.get(field)
        if mode == "direction":
            direction = _normalize_freebill_interpret_direction(raw_value)
            if direction:
                normalized[field] = direction
            continue
        if mode == "nature":
            nature = _normalize_freebill_interpret_nature(raw_value)
            if nature:
                normalized[field] = nature
            continue
        if mode == "number":
            if raw_value is None or str(raw_value).strip() == "":
                continue
            number = _coerce_interpret_float(raw_value)
            if number is not None:
                normalized[field] = number
            continue
        normalized[field] = "" if raw_value is None else str(raw_value).strip()
    return normalized


def _freebill_manual_override_value_equals_raw(
    *,
    field: str,
    value: Any,
    record: dict[str, Any],
) -> bool:
    mode = str(FREEBILL_MANUAL_OVERRIDE_FIELDS.get(field, {}).get("mode") or "text")
    raw_value = record.get(field)
    if mode == "number":
        if value is None or str(value).strip() == "":
            normalized_value = None
        else:
            normalized_value = _coerce_interpret_float(value)
        if raw_value is None or str(raw_value).strip() == "":
            normalized_raw = None
        else:
            normalized_raw = _coerce_interpret_float(raw_value)
        return normalized_value == normalized_raw
    return _normalize_text(value) == _normalize_text(raw_value)


def _compact_freebill_manual_overrides_for_record(
    record: dict[str, Any],
    overrides: dict[str, Any],
) -> dict[str, Any]:
    compacted: dict[str, Any] = {}
    for field, value in overrides.items():
        if field in FREEBILL_PINNED_MANUAL_OVERRIDE_FIELDS:
            compacted[field] = value
            continue
        if not _freebill_manual_override_value_equals_raw(field=field, value=value, record=record):
            compacted[field] = value
    return compacted


def _merge_freebill_manual_override_patch_for_record(
    record: dict[str, Any],
    existing_overrides: dict[str, Any],
    patch_overrides: dict[str, Any],
) -> dict[str, Any]:
    merged = _compact_freebill_manual_overrides_for_record(record, existing_overrides)
    for field, value in patch_overrides.items():
        if field in FREEBILL_PINNED_MANUAL_OVERRIDE_FIELDS:
            merged[field] = value
            continue
        if _freebill_manual_override_value_equals_raw(field=field, value=value, record=record):
            merged.pop(field, None)
        else:
            merged[field] = value
    return merged


def _apply_freebill_standard_fields(
    record: dict[str, Any],
    *,
    interpret_rules: list[dict[str, Any]] | None = None,
    interpret_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = _apply_freebill_builtin_standard_fields(record, interpret_settings=interpret_settings)
    for rule in interpret_rules or []:
        if not rule.get("enabled"):
            continue
        if not _freebill_interpret_rule_matches(normalized, rule):
            continue
        set_direction = _normalize_freebill_interpret_direction(rule.get("set_direction"))
        set_nature = _normalize_freebill_interpret_nature(rule.get("set_nature"))
        if set_direction:
            normalized["standard_direction"] = set_direction
        if set_nature:
            normalized["standard_nature"] = set_nature
    return normalized


def _apply_freebill_builtin_standard_fields(
    record: dict[str, Any],
    *,
    interpret_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = dict(record)
    normalized["type"] = _normalize_freebill_record_type(normalized)
    standard_nature, standard_direction = _infer_freebill_standard_fields(
        normalized,
        enabled_built_in_rule_keys=_get_enabled_freebill_built_in_rule_keys(interpret_settings),
    )
    normalized["standard_nature"] = standard_nature
    normalized["standard_direction"] = standard_direction
    return normalized


def _freebill_interpret_rule_matches(record: dict[str, Any], rule: dict[str, Any]) -> bool:
    matcher = _normalize_freebill_interpret_matcher(rule.get("matcher"))
    kind = matcher.get("kind")
    if kind == "all":
        return True
    if kind == "none":
        return False
    if kind == "full_text_contains":
        needle = _normalize_interpret_text(matcher.get("value"), ignore_case=bool(matcher.get("ignore_case", True)))
        if not needle:
            return False
        haystack = _normalize_interpret_text(
            _build_freebill_record_text(
                record,
                record.get("standard_direction"),
                record.get("standard_nature"),
            ),
            ignore_case=bool(matcher.get("ignore_case", True)),
        )
        return needle in haystack
    if kind != "field":
        return False

    field = str(matcher.get("field") or "")
    if field not in FREEBILL_INTERPRET_RULE_FIELDS:
        return False
    field_mode = str(FREEBILL_INTERPRET_RULE_FIELDS[field]["mode"])
    op = str(matcher.get("op") or "")
    if op not in FREEBILL_INTERPRET_RULE_OPERATORS:
        return False
    if field_mode == "number":
        return _freebill_interpret_number_rule_matches(record.get(field), matcher)
    return _freebill_interpret_text_rule_matches(record.get(field), matcher)


def _freebill_interpret_text_rule_matches(value: Any, matcher: dict[str, Any]) -> bool:
    ignore_case = bool(matcher.get("ignore_case", True))
    text = _normalize_interpret_text(value, ignore_case=ignore_case)
    op = str(matcher.get("op") or "")
    if op in {"in", "not_in"}:
        candidates = [
            _normalize_interpret_text(item, ignore_case=ignore_case)
            for item in matcher.get("values") or []
            if _normalize_text(item) is not None
        ]
        matched = text in candidates
        return matched if op == "in" else not matched

    candidate = _normalize_interpret_text(matcher.get("value"), ignore_case=ignore_case)
    if op == "eq":
        return text == candidate
    if op == "neq":
        return text != candidate
    if op == "contains":
        return bool(candidate) and candidate in text
    if op == "not_contains":
        return not candidate or candidate not in text
    return False


def _freebill_interpret_number_rule_matches(value: Any, matcher: dict[str, Any]) -> bool:
    left = _coerce_interpret_float(value)
    op = str(matcher.get("op") or "")
    if op in {"in", "not_in"}:
        candidates = [
            number
            for item in matcher.get("values") or []
            for number in [_coerce_interpret_float(item)]
            if number is not None
        ]
        matched = left is not None and left in candidates
        return matched if op == "in" else not matched
    right = _coerce_interpret_float(matcher.get("value"))
    if left is None or right is None:
        return False
    if op == "eq":
        return left == right
    if op == "neq":
        return left != right
    if op == "gt":
        return left > right
    if op == "gte":
        return left >= right
    if op == "lt":
        return left < right
    if op == "lte":
        return left <= right
    return False


def _normalize_interpret_text(value: Any, *, ignore_case: bool) -> str:
    text = _normalize_text(value) or ""
    return text.casefold() if ignore_case else text


def _coerce_interpret_float(value: Any) -> float | None:
    normalized = _normalize_amount(value)
    return float(normalized) if normalized is not None else None


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
    if "建设银行" in text or "建行" in text or "ccb" in text:
        return "建设银行"
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
        create_time = _normalize_datetime_text(row.get("create_time"))
        records.append(
            _apply_freebill_standard_fields({
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
                "direction": _normalize_alipay_direction(row.get("direction")),
                "status": _normalize_text(row.get("status")),
                "service_fee": 0,
                "refund_amount": 0,
                "remark": _normalize_text(row.get("remark")),
                "fund_status": None,
                "imported_at": now,
            })
        )
    return records


def _build_alipay_legacy_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    df = _strip_dataframe_text(df)
    if "交易号" not in df.columns:
        raise ValueError("未找到“交易号”列，请确认是旧版支付宝交易记录明细")

    rename_map = {
        "交易号": "trade_no",
        "商家订单号": "merchant_order_no",
        "交易创建时间": "create_time",
        "付款时间": "pay_time",
        "最近修改时间": "modify_time",
        "交易来源地": "location",
        "类型": "type",
        "交易对方": "counterparty",
        "商品名称": "product_name",
        "金额（元）": "amount",
        "金额(元)": "amount",
        "收/支": "direction",
        "交易状态": "status",
        "服务费（元）": "service_fee",
        "服务费(元)": "service_fee",
        "成功退款（元）": "refund_amount",
        "成功退款(元)": "refund_amount",
        "备注": "remark",
        "资金状态": "fund_status",
        "类别": "source_hint",
    }
    df = df.rename(columns=rename_map)

    records: list[dict[str, Any]] = []
    now = time.time()
    for _, row in df.iterrows():
        if not _is_supported_alipay_legacy_row(row):
            continue
        create_time = _normalize_datetime_text(row.get("create_time"))
        amount = _normalize_amount(row.get("amount"))
        trade_no = _normalize_text(row.get("trade_no"))
        if not trade_no or trade_no == "/" or amount is None or not _looks_like_datetime_text(create_time):
            continue
        pay_time = _normalize_datetime_text(row.get("pay_time"))
        modify_time = _normalize_datetime_text(row.get("modify_time"))
        records.append(
            _apply_freebill_standard_fields({
                "source": "支付宝",
                "trade_no": trade_no,
                "merchant_order_no": _normalize_text(row.get("merchant_order_no")),
                "create_time": create_time,
                "pay_time": pay_time,
                "modify_time": modify_time,
                "location": _normalize_text(row.get("location")),
                "type": _normalize_text(row.get("type")),
                "counterparty": _normalize_text(row.get("counterparty")),
                "product_name": _normalize_text(row.get("product_name")),
                "amount": amount,
                "direction": _resolve_alipay_legacy_direction(row),
                "status": _normalize_text(row.get("status")),
                "service_fee": _normalize_amount(row.get("service_fee")) or 0,
                "refund_amount": _normalize_amount(row.get("refund_amount")) or 0,
                "remark": _normalize_text(row.get("remark")),
                "fund_status": _normalize_text(row.get("fund_status")),
                "imported_at": now,
            })
        )
    return records


def _is_supported_alipay_legacy_row(row: pd.Series) -> bool:
    source_hint = _normalize_text(row.get("source_hint"))
    if source_hint and source_hint != "支付宝":
        return False
    if source_hint == "支付宝":
        return True

    location = _normalize_text(row.get("location")) or ""
    fund_status = _normalize_text(row.get("fund_status")) or ""
    if fund_status in ALIPAY_LEGACY_FUND_STATUSES:
        return True
    return any(hint in location for hint in ALIPAY_LEGACY_SOURCE_HINTS)


def _looks_like_datetime_text(value: str | None) -> bool:
    if not value or len(value) < 10:
        return False
    return value[4] == "-" and value[7] == "-"


def _resolve_alipay_legacy_direction(row: pd.Series) -> str | None:
    direction = _normalize_alipay_direction(row.get("direction"))
    if direction:
        return direction

    fund_status = _normalize_text(row.get("fund_status")) or ""
    product_name = _normalize_text(row.get("product_name")) or ""
    if fund_status == "资金转移":
        return "不计收支"
    if any(keyword in product_name for keyword in ("余额宝", "余利宝", "转入", "转出", "还款")):
        return "不计收支"
    return "不计收支"


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
        create_time = _normalize_datetime_text(row.get("create_time"))
        status = _normalize_text(row.get("status"))
        records.append(
            _apply_freebill_standard_fields({
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
            })
        )
    return records


def _build_ccb_records(df: pd.DataFrame, metadata: dict[str, Any]) -> list[dict[str, Any]]:
    df = _strip_dataframe_text(df)
    missing_columns = CCB_CURRENT_ACCOUNT_REQUIRED_COLUMNS - set(df.columns)
    if missing_columns:
        missing_text = "、".join(sorted(missing_columns))
        raise ValueError(f"未找到建行流水必需列：{missing_text}，请确认是{CCB_CURRENT_ACCOUNT_TITLE}")

    df = df.rename(columns=CCB_CURRENT_ACCOUNT_COLUMN_MAP)

    records: list[dict[str, Any]] = []
    now = time.time()
    account_no = metadata.get("account_no")
    for _, row in df.iterrows():
        raw_sequence = _normalize_text(row.get("raw_sequence"))
        create_time = _normalize_ccb_trade_date(row.get("create_time"))
        signed_amount = _parse_signed_amount(row.get("signed_amount"))
        if not raw_sequence or not create_time or signed_amount is None:
            continue

        amount = abs(signed_amount)
        direction = "收入" if signed_amount > 0 else "支出" if signed_amount < 0 else "不计收支"
        account_balance = _parse_signed_amount(row.get("account_balance"))
        category = _normalize_text(row.get("type"))
        memo = _normalize_text(row.get("memo"))
        counterparty = _normalize_text(row.get("counterparty"))
        records.append(
            _apply_freebill_standard_fields({
                "source": "建设银行",
                "trade_no": _make_ccb_trade_no(
                    account_no=account_no,
                    create_time=create_time,
                    raw_sequence=raw_sequence,
                    category=category,
                    signed_amount=signed_amount,
                    account_balance=account_balance,
                    memo=memo,
                    counterparty=counterparty,
                ),
                "merchant_order_no": raw_sequence,
                "create_time": create_time,
                "pay_time": create_time,
                "modify_time": create_time,
                "location": None,
                "type": category,
                "counterparty": counterparty,
                "product_name": memo or category,
                "amount": amount,
                "direction": direction,
                "status": "已入账",
                "service_fee": 0,
                "refund_amount": 0,
                "remark": memo,
                "fund_status": None,
                "account_no": account_no,
                "currency": _normalize_text(row.get("currency")),
                "cash_type": _normalize_text(row.get("cash_type")),
                "account_balance": account_balance,
                "raw_sequence": raw_sequence,
                "imported_at": now,
            })
        )
    _validate_ccb_reported_totals(records, metadata)
    return records


def _extract_ccb_metadata(preview: pd.DataFrame) -> dict[str, Any]:
    text = " ".join(
        item
        for item in (
            _normalize_text(value)
            for row in preview.itertuples(index=False)
            for value in row
        )
        if item
    )
    account_match = re.search(r"卡号/账号[:：]\s*([0-9*]+)", text)
    customer_match = re.search(r"客户名称[:：]\s*([^\s]+)", text)
    start_match = re.search(r"起始日期[:：]\s*([0-9/-]+)", text)
    end_match = re.search(r"结束日期[:：]\s*([0-9/-]+)", text)
    expense_match = re.search(r"总支出[:：]\s*([+-]?[0-9,，.]+)", text)
    income_match = re.search(r"总收入[:：]\s*([+-]?[0-9,，.]+)", text)
    return {
        "title": CCB_CURRENT_ACCOUNT_TITLE if CCB_CURRENT_ACCOUNT_TITLE in text else None,
        "account_no": account_match.group(1) if account_match else None,
        "customer_name": customer_match.group(1) if customer_match else None,
        "start_date": start_match.group(1) if start_match else None,
        "end_date": end_match.group(1) if end_match else None,
        "reported_total_expense": expense_match.group(1) if expense_match else None,
        "reported_total_income": income_match.group(1) if income_match else None,
    }


def _validate_ccb_reported_totals(records: list[dict[str, Any]], metadata: dict[str, Any]) -> None:
    _validate_ccb_reported_total(records, metadata.get("reported_total_expense"), "支出", "总支出")
    _validate_ccb_reported_total(records, metadata.get("reported_total_income"), "收入", "总收入")


def _validate_ccb_reported_total(
    records: list[dict[str, Any]],
    reported_value: Any,
    direction: str,
    label: str,
) -> None:
    reported_total = _parse_signed_amount(reported_value)
    if reported_total is None:
        return
    detail_total = round(
        sum(float(record.get("amount") or 0) for record in records if record.get("direction") == direction),
        2,
    )
    if abs(detail_total - abs(reported_total)) > 0.01:
        raise ValueError(f"建行流水{label}校验失败：表头 {abs(reported_total):.2f}，明细合计 {detail_total:.2f}")


def _parse_signed_amount(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, int | float):
        return float(value)
    text = (
        str(value)
        .replace("¥", "")
        .replace("￥", "")
        .replace(",", "")
        .replace("，", "")
        .replace("−", "-")
        .strip()
    )
    if not text or text == "/":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _normalize_ccb_trade_date(value: Any) -> str | None:
    text = _normalize_text(value)
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 8:
        year, month, day = digits[:4], digits[4:6], digits[6:8]
        return f"{year}-{month}-{day} 00:00:00"
    normalized = _normalize_datetime_text(text)
    if _looks_like_datetime_text(normalized):
        return f"{normalized[:10]} 00:00:00"
    return None


def _make_ccb_trade_no(
    *,
    account_no: str | None,
    create_time: str,
    raw_sequence: str,
    category: str | None,
    signed_amount: float,
    account_balance: float | None,
    memo: str | None,
    counterparty: str | None,
) -> str:
    trade_date = create_time[:10].replace("-", "")
    account_key = re.sub(r"\s+", "", account_no or "unknown")
    digest_text = "|".join(
        str(item or "")
        for item in (
            account_key,
            trade_date,
            raw_sequence,
            category,
            f"{signed_amount:.2f}",
            "" if account_balance is None else f"{account_balance:.2f}",
            memo,
            counterparty,
        )
    )
    digest = hashlib.sha256(digest_text.encode("utf-8")).hexdigest()[:16]
    return f"ccb:{account_key}:{trade_date}:{raw_sequence}:{digest}"


def _parse_alipay_csv_bytes(
    content: bytes,
    *,
    header_row: int = 24,
) -> tuple[list[dict[str, Any]], str]:
    last_error: Exception | None = None
    for df in _iter_csv_dataframes(
        content,
        preferred_header_rows=(header_row,),
        header_markers=(
            ("交易订单号", "交易时间"),
            ("交易号", "交易创建时间"),
        ),
    ):
        try:
            if "交易订单号" in df.columns:
                return _build_alipay_records(df), "alipay-detail-csv"
            if "交易号" in df.columns:
                return _build_alipay_legacy_records(df), "alipay-legacy-csv"
        except Exception as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    raise ValueError("未找到可识别的支付宝账单表头")


def _parse_alipay_excel_bytes(content: bytes) -> tuple[list[dict[str, Any]], str]:
    records: list[dict[str, Any]] = []
    for df in _iter_excel_dataframes(
        content,
        header_markers=("交易号", "交易创建时间"),
    ):
        records.extend(_build_alipay_legacy_records(df))
    if not records:
        raise ValueError("未找到可识别的旧版支付宝账单工作表")
    return records, "alipay-legacy-excel"


def _parse_wechat_excel_bytes(content: bytes) -> tuple[list[dict[str, Any]], str]:
    records: list[dict[str, Any]] = []
    for df in _iter_excel_dataframes(
        content,
        header_markers=("交易单号", "交易时间"),
    ):
        records.extend(_build_wechat_records(df))
    if not records:
        raise ValueError("未找到可识别的微信支付账单工作表")
    return records, "wechat-excel"


def _parse_ccb_excel_bytes(content: bytes) -> tuple[list[dict[str, Any]], str]:
    records: list[dict[str, Any]] = []
    excel = pd.ExcelFile(io.BytesIO(content))
    for sheet_name in excel.sheet_names:
        header_index = _find_excel_header_row(excel, sheet_name, CCB_CURRENT_ACCOUNT_HEADER_MARKERS)
        if header_index is None:
            continue
        preview = pd.read_excel(excel, sheet_name=sheet_name, header=None, nrows=header_index, dtype=str)
        metadata = _extract_ccb_metadata(preview)
        df = pd.read_excel(excel, sheet_name=sheet_name, header=header_index, dtype=str)
        records.extend(_build_ccb_records(df, metadata))
    if not records:
        raise ValueError("未找到可识别的建行流水工作表")
    return records, CCB_CURRENT_ACCOUNT_PARSER_FORMAT


def _iter_csv_dataframes(
    content: bytes,
    *,
    preferred_header_rows: tuple[int, ...],
    header_markers: tuple[tuple[str, ...], ...],
):
    last_error: Exception | None = None
    for encoding in CSV_ENCODINGS:
        try:
            text = content.decode(encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
        header_rows = _detect_text_header_rows(
            text,
            preferred_header_rows=preferred_header_rows,
            header_markers=header_markers,
        )
        for header_index in header_rows:
            try:
                df = pd.read_csv(
                    io.StringIO(text),
                    skiprows=header_index,
                    dtype=str,
                    engine="python",
                )
                yield _strip_dataframe_text(df)
            except Exception as exc:
                last_error = exc
                continue
        return
    if last_error is not None:
        raise ValueError(f"读取 CSV 编码失败：{last_error}") from last_error


def _detect_text_header_rows(
    text: str,
    *,
    preferred_header_rows: tuple[int, ...],
    header_markers: tuple[tuple[str, ...], ...],
) -> list[int]:
    rows: list[int] = []
    lines = text.splitlines()
    for row_index in preferred_header_rows:
        if 0 <= row_index < len(lines):
            rows.append(row_index)
    for index, line in enumerate(lines[:200]):
        if any(all(marker in line for marker in markers) for markers in header_markers):
            rows.append(index)
    return list(dict.fromkeys(rows))


def _iter_excel_dataframes(
    content: bytes,
    *,
    header_markers: tuple[str, ...],
):
    excel = pd.ExcelFile(io.BytesIO(content))
    for sheet_name in excel.sheet_names:
        header_index = _find_excel_header_row(excel, sheet_name, header_markers)
        if header_index is None:
            continue
        df = pd.read_excel(excel, sheet_name=sheet_name, header=header_index, dtype=str)
        yield _strip_dataframe_text(df)


def _find_excel_header_row(
    excel: pd.ExcelFile,
    sheet_name: str,
    header_markers: tuple[str, ...],
) -> int | None:
    preview = pd.read_excel(excel, sheet_name=sheet_name, header=None, nrows=80, dtype=str)
    for index, row in preview.iterrows():
        values = {_normalize_text(item) for item in row.tolist()}
        if all(marker in values for marker in header_markers):
            return int(index)
    return None


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
    try:
        records, parser_format = _parse_alipay_csv_bytes(content, header_row=header_row)
        result = import_bill_records(records, filename=filename, work_dir=work_dir)
        result["format"] = parser_format
        result["raw_file"] = archive
        _update_raw_import_status(archive["sha256"], "imported", work_dir=work_dir)
        return result
    except Exception as exc:
        _update_raw_import_status(archive["sha256"], "error", work_dir=work_dir, note=str(exc))
        raise


def import_alipay_excel_bytes(
    filename: str,
    content: bytes,
    *,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    archive = archive_freebill_raw_bytes(
        filename,
        content,
        source="支付宝",
        work_dir=work_dir,
        import_status="parsing",
    )
    try:
        records, parser_format = _parse_alipay_excel_bytes(content)
        result = import_bill_records(records, filename=filename, work_dir=work_dir)
        result["format"] = parser_format
        result["raw_file"] = archive
        _update_raw_import_status(archive["sha256"], "imported", work_dir=work_dir)
        return result
    except Exception as exc:
        _update_raw_import_status(archive["sha256"], "error", work_dir=work_dir, note=str(exc))
        raise


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
        records, parser_format = _parse_wechat_excel_bytes(content)
        result = import_bill_records(records, filename=filename, work_dir=work_dir)
        result["format"] = parser_format
        result["raw_file"] = archive
        _update_raw_import_status(archive["sha256"], "imported", work_dir=work_dir)
        return result
    except Exception as exc:
        _update_raw_import_status(archive["sha256"], "error", work_dir=work_dir, note=str(exc))
        raise


def import_ccb_excel_bytes(
    filename: str,
    content: bytes,
    *,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    archive = archive_freebill_raw_bytes(
        filename,
        content,
        source="建设银行",
        work_dir=work_dir,
        import_status="parsing",
    )
    try:
        records, parser_format = _parse_ccb_excel_bytes(content)
        result = import_bill_records(records, filename=filename, work_dir=work_dir)
        result["format"] = parser_format
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
        existing_records = {
            str(row["trade_no"] or "").strip(): _row_to_dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM bill_records
                WHERE trade_no IS NOT NULL AND TRIM(trade_no) NOT IN ('', '/')
                """,
            ).fetchall()
        }

        inserted = 0
        updated = 0
        skipped = 0
        placeholders = ", ".join("?" for _ in BILL_RECORD_COLUMNS)
        columns_sql = ", ".join(BILL_RECORD_COLUMNS)
        insert_sql = f"INSERT INTO bill_records ({columns_sql}) VALUES ({placeholders})"
        interpret_settings = _load_freebill_interpret_settings(conn)
        interpret_rules = _load_enabled_freebill_interpret_rules(conn)

        for record in records:
            normalized_record = _apply_freebill_standard_fields(
                record,
                interpret_rules=interpret_rules,
                interpret_settings=interpret_settings,
            )
            source = str(normalized_record.get("source") or "")
            trade_no = str(normalized_record.get("trade_no") or "").strip()
            if not source or not trade_no or trade_no == "/":
                skipped += 1
                continue
            existing_record = existing_records.get(trade_no)
            if existing_record is not None:
                existing_raw_sequence = str(existing_record.get("raw_sequence") or "")
                incoming_raw_sequence = str(normalized_record.get("raw_sequence") or "")
                existing_is_local_wechat = existing_raw_sequence.startswith("wechat-")
                incoming_is_local_wechat = incoming_raw_sequence.startswith("wechat-")
                if existing_is_local_wechat and not incoming_is_local_wechat:
                    assignments = ", ".join(f"{column} = ?" for column in BILL_RECORD_COLUMNS)
                    conn.execute(
                        f"UPDATE bill_records SET {assignments} WHERE trade_no = ?",
                        [normalized_record.get(column) for column in BILL_RECORD_COLUMNS] + [trade_no],
                    )
                    existing_records[trade_no] = normalized_record
                    updated += 1
                else:
                    skipped += 1
                continue
            conn.execute(insert_sql, [normalized_record.get(column) for column in BILL_RECORD_COLUMNS])
            existing_records[trade_no] = normalized_record
            inserted += 1
        conn.commit()

        if inserted or updated:
            reset_bill_ids(conn)
            conn.commit()

    return {
        "filename": filename,
        "processed": len(records),
        "inserted": inserted,
        "updated": updated,
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


def rebuild_freebill_records_from_raw_files(
    *,
    work_dir: Path | None = None,
    backup: bool = True,
    strict: bool = True,
) -> dict[str, Any]:
    resolved_work_dir = work_dir or get_freebill_work_dir()
    db_path = get_freebill_db_path(resolved_work_dir)
    with get_freebill_connection(resolved_work_dir) as conn:
        raw_files = [
            _row_to_dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM freebill_raw_files
                WHERE source IN ('支付宝', '微信', '建设银行')
                  AND extension IN ('.csv', '.xlsx', '.xlsm', '.xls', '.json')
                  AND COALESCE(note, '') != 'legacy-db-snapshot'
                ORDER BY source, relative_path, original_name, id
                """
            ).fetchall()
        ]

    file_results: list[dict[str, Any]] = []
    record_candidates: list[dict[str, Any]] = []
    for raw_file in raw_files:
        result = _parse_rebuild_raw_file(raw_file)
        file_results.append(result)
        for record in result.get("records") or []:
            record_candidates.append(
                {
                    **record,
                    "_freebill_rebuild_score": _score_rebuild_record(
                        record,
                        raw_file,
                        parser_format=str(result.get("format") or ""),
                    ),
                }
            )

    error_results = [item for item in file_results if item.get("status") == "error"]
    if strict and error_results:
        error_summary = "; ".join(
            f"{item.get('relative_path') or item.get('original_name')}: {item.get('error')}"
            for item in error_results[:5]
        )
        raise ValueError(f"重建前解析失败，未修改账单库：{error_summary}")

    deduped_records = _deduplicate_rebuild_records(record_candidates)
    backup_path: Path | None = None
    if backup and db_path.exists():
        timestamp = time.strftime("%Y%m%d%H%M%S")
        backup_path = db_path.with_name(f"bill.before-raw-rebuild-{timestamp}.db")
        shutil.copy2(db_path, backup_path)

    with get_freebill_connection(resolved_work_dir) as conn:
        before_row = conn.execute("SELECT COUNT(*) AS total FROM bill_records").fetchone()
        conn.execute("BEGIN")
        try:
            conn.execute("DELETE FROM bill_records")
            conn.execute("DELETE FROM sqlite_sequence WHERE name = 'bill_records'")
            _insert_bill_records(conn, deduped_records)
            applied_overrides = int(conn.execute(
                """
                SELECT COUNT(*) AS total
                FROM freebill_record_overrides
                WHERE trade_no IN (
                    SELECT trade_no
                    FROM bill_records
                )
                """
            ).fetchone()["total"] or 0)
            if deduped_records:
                reset_bill_ids(conn)
            _update_rebuild_raw_file_statuses(conn, file_results)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        after_row = conn.execute("SELECT COUNT(*) AS total FROM bill_records").fetchone()

    imported_files = [item for item in file_results if item.get("status") == "imported"]
    skipped_files = [item for item in file_results if item.get("status") == "skipped"]
    return {
        "work_dir": str(resolved_work_dir),
        "db_path": str(db_path),
        "backup_path": str(backup_path) if backup_path else None,
        "before_records": int(before_row["total"] or 0),
        "after_records": int(after_row["total"] or 0),
        "candidate_records": len(record_candidates),
        "duplicate_records": len(record_candidates) - len(deduped_records),
        "applied_overrides": applied_overrides,
        "raw_files": len(raw_files),
        "imported_files": len(imported_files),
        "skipped_files": len(skipped_files),
        "error_files": len(error_results),
        "files": [
            {
                key: value
                for key, value in item.items()
                if key != "records"
            }
            for item in file_results
        ],
    }


def _parse_rebuild_raw_file(raw_file: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(raw_file.get("archived_path") or ""))
    base_result = {
        "raw_file_id": raw_file.get("id"),
        "source": raw_file.get("source"),
        "extension": raw_file.get("extension"),
        "original_name": raw_file.get("original_name"),
        "relative_path": raw_file.get("relative_path"),
    }
    if not path.exists():
        return {
            **base_result,
            "status": "error",
            "processed": 0,
            "error": f"归档文件不存在：{path}",
            "records": [],
        }

    content = path.read_bytes()
    source = str(raw_file.get("source") or "")
    extension = str(raw_file.get("extension") or "").lower()
    try:
        if source == "支付宝" and extension == ".csv":
            records, parser_format = _parse_alipay_csv_bytes(content)
        elif source == "支付宝" and extension in {".xlsx", ".xlsm", ".xls"}:
            records, parser_format = _parse_alipay_excel_bytes(content)
        elif source == "微信" and extension in {".xlsx", ".xlsm", ".xls"}:
            records, parser_format = _parse_wechat_excel_bytes(content)
        elif source == "微信" and extension == ".json" and raw_file.get("note") == "wechat-local-payment-db-v1":
            from backend.core.freebill.wechat_local_db import parse_wechat_local_snapshot_bytes

            records, parser_format = parse_wechat_local_snapshot_bytes(content)
        elif source == "建设银行" and extension in {".xlsx", ".xlsm", ".xls"}:
            records, parser_format = _parse_ccb_excel_bytes(content)
        else:
            return {
                **base_result,
                "status": "skipped",
                "processed": 0,
                "format": None,
                "error": "没有可用解析器",
                "records": [],
            }
        return {
            **base_result,
            "status": "imported",
            "processed": len(records),
            "format": parser_format,
            "error": None,
            "records": records,
        }
    except Exception as exc:
        return {
            **base_result,
            "status": "error",
            "processed": 0,
            "format": None,
            "error": str(exc),
            "records": [],
        }


def _score_rebuild_record(
    record: dict[str, Any],
    raw_file: dict[str, Any],
    *,
    parser_format: str,
) -> tuple[int, int, int]:
    relative_path = str(raw_file.get("relative_path") or "")
    original_name = str(raw_file.get("original_name") or "")
    score = 0
    if "已导入/" in relative_path:
        score += 1000
    if "旧导入/" in relative_path:
        score += 100
    if parser_format in {"alipay-detail-csv", "wechat-excel", "ccb-excel"}:
        score += 1000
    if parser_format == "wechat-local-payment-db-v1":
        score += 200
    if "支付宝交易明细" in original_name or "微信支付账单流水文件" in original_name or "建设银行流水" in original_name:
        score += 1000
    if parser_format.startswith("alipay-legacy"):
        score += 100
    score += sum(1 for column in BILL_RECORD_COLUMNS if _normalize_text(record.get(column)))
    raw_file_id = int(raw_file.get("id") or 0)
    return score, raw_file_id, len(str(record.get("trade_no") or ""))


def _deduplicate_rebuild_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for record in records:
        trade_no = str(record.get("trade_no") or "").strip()
        if not trade_no or trade_no == "/":
            continue
        existing = selected.get(trade_no)
        if existing is None or record["_freebill_rebuild_score"] > existing["_freebill_rebuild_score"]:
            selected[trade_no] = record
    return [
        {column: record.get(column) for column in BILL_RECORD_COLUMNS}
        for record in sorted(
            selected.values(),
            key=lambda item: (
                str(item.get("create_time") or ""),
                str(item.get("trade_no") or ""),
            ),
        )
    ]


def upsert_freebill_record_overrides(
    trade_nos: list[str],
    *,
    direction: str,
    category: str,
    note: str | None = None,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    normalized_trade_nos = _normalize_trade_nos(trade_nos)
    if not normalized_trade_nos:
        raise ValueError("请选择需要人工标记的账单记录")

    override_direction = _normalize_text(direction)
    override_type = _normalize_text(category)
    if not override_direction:
        raise ValueError("收支不能为空")
    if not override_type:
        raise ValueError("分类不能为空")

    now = time.time()
    with get_freebill_connection(work_dir) as conn:
        records = _query_bill_records_by_trade_no(conn, normalized_trade_nos)
        existing_overrides = {
            str(row["trade_no"]): _row_to_dict(row)
            for row in conn.execute(
                f"""
                SELECT *
                FROM freebill_record_overrides
                WHERE trade_no IN ({_sql_placeholders(normalized_trade_nos)})
                """,
                normalized_trade_nos,
            ).fetchall()
        }
        for trade_no, record in records.items():
            existing = existing_overrides.get(trade_no)
            original_direction = (
                existing["original_direction"]
                if existing is not None
                else record.get("direction")
            )
            original_type = (
                existing["original_type"]
                if existing is not None
                else record.get("type")
            )
            base_record = {
                **record,
                "direction": original_direction,
                "type": original_type,
            }
            resolved_override_type = _resolve_freebill_flow_override_type(
                override_type,
                original_type,
            )
            (
                original_standard_nature,
                original_standard_direction,
                override_standard_nature,
                override_standard_direction,
            ) = _resolve_freebill_override_standard_fields(
                base_record,
                {
                    "original_direction": original_direction,
                    "original_type": original_type,
                    "override_direction": override_direction,
                    "override_type": resolved_override_type,
                },
            )
            created_at = float(existing["created_at"]) if existing is not None else now
            conn.execute(
                """
                INSERT INTO freebill_record_overrides (
                    trade_no, source, original_direction, original_type,
                    original_standard_nature, original_standard_direction,
                    override_direction, override_type,
                    override_standard_nature, override_standard_direction,
                    note, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_no) DO UPDATE SET
                    source = excluded.source,
                    original_standard_nature = excluded.original_standard_nature,
                    original_standard_direction = excluded.original_standard_direction,
                    override_direction = excluded.override_direction,
                    override_type = excluded.override_type,
                    override_standard_nature = excluded.override_standard_nature,
                    override_standard_direction = excluded.override_standard_direction,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                (
                    trade_no,
                    record.get("source"),
                    original_direction,
                    original_type,
                    original_standard_nature,
                    original_standard_direction,
                    override_direction,
                    resolved_override_type,
                    override_standard_nature,
                    override_standard_direction,
                    note,
                    created_at,
                    now,
                ),
            )
        updated = len(records)
        conn.commit()

    return {
        "requested": len(normalized_trade_nos),
        "matched": len(records),
        "updated": updated,
        "missing_trade_nos": [trade_no for trade_no in normalized_trade_nos if trade_no not in records],
        "direction": override_direction,
        "category": override_type,
    }


def upsert_freebill_record_manual_overrides(
    trade_no: str,
    overrides: dict[str, Any],
    *,
    note: str | None = None,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    normalized_trade_nos = _normalize_trade_nos([trade_no])
    if not normalized_trade_nos:
        raise ValueError("请选择需要人工修改的账单记录")
    trade_no = normalized_trade_nos[0]
    normalized_overrides = _normalize_freebill_manual_overrides(overrides)
    now = time.time()
    with get_freebill_connection(work_dir) as conn:
        records = _query_bill_records_by_trade_no(conn, [trade_no])
        record = records.get(trade_no)
        if record is None:
            raise ValueError("账单记录不存在或已被重建移除")
        normalized_overrides = _compact_freebill_manual_overrides_for_record(record, normalized_overrides)
        existing = conn.execute(
            """
            SELECT *
            FROM freebill_record_overrides
            WHERE trade_no = ?
            """,
            (trade_no,),
        ).fetchone()
        existing_override = _row_to_dict(existing)
        original_direction = existing_override.get("original_direction") or record.get("direction")
        original_type = existing_override.get("original_type") or record.get("type")
        override_direction = existing_override.get("override_direction") or original_direction or "支出"
        override_type = existing_override.get("override_type") or original_type or "未分类"
        override_standard_nature = (
            normalized_overrides.get("standard_nature")
            if "standard_nature" in normalized_overrides
            else existing_override.get("override_standard_nature")
        )
        override_standard_direction = (
            normalized_overrides.get("standard_direction")
            if "standard_direction" in normalized_overrides
            else existing_override.get("override_standard_direction")
        )
        original_standard_nature, original_standard_direction = _infer_freebill_standard_fields(
            {
                **record,
                "direction": original_direction,
                "type": original_type,
            },
            original_direction=_normalize_text(original_direction),
        )
        created_at = float(existing_override.get("created_at") or now)
        conn.execute(
            """
            INSERT INTO freebill_record_overrides (
                trade_no, source, original_direction, original_type,
                original_standard_nature, original_standard_direction,
                override_direction, override_type,
                override_standard_nature, override_standard_direction,
                manual_overrides_json,
                note, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_no) DO UPDATE SET
                source = excluded.source,
                original_standard_nature = excluded.original_standard_nature,
                original_standard_direction = excluded.original_standard_direction,
                override_direction = excluded.override_direction,
                override_type = excluded.override_type,
                override_standard_nature = excluded.override_standard_nature,
                override_standard_direction = excluded.override_standard_direction,
                manual_overrides_json = excluded.manual_overrides_json,
                note = excluded.note,
                updated_at = excluded.updated_at
            """,
            (
                trade_no,
                record.get("source"),
                original_direction,
                original_type,
                original_standard_nature,
                original_standard_direction,
                override_direction,
                override_type,
                override_standard_nature,
                override_standard_direction,
                json.dumps(normalized_overrides, ensure_ascii=False, sort_keys=True),
                note,
                created_at,
                now,
            ),
        )
        conn.commit()
    return {
        "trade_no": trade_no,
        "updated": 1,
        "overrides": normalized_overrides,
    }


def upsert_freebill_category_branch_manual_overrides(
    *,
    program: dict[str, Any] | None = None,
    programs: list[dict[str, Any]] | None = None,
    path: list[dict[str, Any]] | None = None,
    overrides: dict[str, Any],
    note: str | None = None,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    normalized_overrides = _normalize_freebill_manual_overrides(overrides)
    if not normalized_overrides:
        raise ValueError("请选择至少一个要批量修改的字段")

    branch_where_sql, query_params = _build_category_branch_filter_conditions(
        program=program,
        programs=programs,
        path=path,
    )
    now = time.time()
    with get_freebill_connection(work_dir) as conn:
        rows = [
            _row_to_dict(row)
            for row in conn.execute(
                f"""
                SELECT matched.*,
                       existing.original_direction AS existing_original_direction,
                       existing.original_type AS existing_original_type,
                       existing.override_direction AS existing_override_direction,
                       existing.override_type AS existing_override_type,
                       existing.override_standard_nature AS existing_override_standard_nature,
                       existing.override_standard_direction AS existing_override_standard_direction,
                       existing.manual_overrides_json AS existing_manual_overrides_json,
                       existing.created_at AS existing_created_at
                FROM (
                    SELECT *
                    FROM bill_records
                    WHERE {branch_where_sql}
                      AND COALESCE(NULLIF(TRIM(trade_no), ''), '/') != '/'
                ) AS matched
                LEFT JOIN freebill_record_overrides AS existing
                  ON existing.trade_no = matched.trade_no
                ORDER BY COALESCE(matched.amount, 0) DESC, datetime(matched.create_time) DESC, matched.id DESC
                """,
                query_params,
            ).fetchall()
        ]

        override_rows: list[tuple[Any, ...]] = []
        seen_trade_nos: set[str] = set()
        for record in rows:
            trade_no = str(record.get("trade_no") or "").strip()
            if not trade_no or trade_no == "/" or trade_no in seen_trade_nos:
                continue
            seen_trade_nos.add(trade_no)
            original_direction = record.get("existing_original_direction") or record.get("direction")
            original_type = record.get("existing_original_type") or record.get("type")
            merged_manual_overrides = _merge_freebill_manual_override_patch_for_record(
                record,
                _normalize_freebill_manual_overrides(record.get("existing_manual_overrides_json")),
                normalized_overrides,
            )
            override_direction = record.get("existing_override_direction") or original_direction or "支出"
            override_type = record.get("existing_override_type") or original_type or "未分类"
            override_standard_nature = (
                merged_manual_overrides.get("standard_nature")
                if "standard_nature" in merged_manual_overrides
                else record.get("existing_override_standard_nature")
            )
            override_standard_direction = (
                merged_manual_overrides.get("standard_direction")
                if "standard_direction" in merged_manual_overrides
                else record.get("existing_override_standard_direction")
            )
            original_standard_nature, original_standard_direction = _infer_freebill_standard_fields(
                {
                    **record,
                    "direction": original_direction,
                    "type": original_type,
                },
                original_direction=_normalize_text(original_direction),
            )
            override_rows.append(
                (
                    trade_no,
                    record.get("source"),
                    original_direction,
                    original_type,
                    original_standard_nature,
                    original_standard_direction,
                    override_direction,
                    override_type,
                    override_standard_nature,
                    override_standard_direction,
                    json.dumps(merged_manual_overrides, ensure_ascii=False, sort_keys=True),
                    note,
                    float(record.get("existing_created_at") or now),
                    now,
                )
            )

        if override_rows:
            conn.executemany(
                """
                INSERT INTO freebill_record_overrides (
                    trade_no, source, original_direction, original_type,
                    original_standard_nature, original_standard_direction,
                    override_direction, override_type,
                    override_standard_nature, override_standard_direction,
                    manual_overrides_json,
                    note, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_no) DO UPDATE SET
                    source = excluded.source,
                    original_standard_nature = excluded.original_standard_nature,
                    original_standard_direction = excluded.original_standard_direction,
                    override_direction = excluded.override_direction,
                    override_type = excluded.override_type,
                    override_standard_nature = excluded.override_standard_nature,
                    override_standard_direction = excluded.override_standard_direction,
                    manual_overrides_json = excluded.manual_overrides_json,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                override_rows,
            )
        conn.commit()

    return {
        "matched": len(seen_trade_nos),
        "updated": len(override_rows),
        "overrides": normalized_overrides,
    }


def upsert_freebill_category_branch_overrides(
    *,
    program: dict[str, Any] | None = None,
    programs: list[dict[str, Any]] | None = None,
    path: list[dict[str, Any]] | None = None,
    direction: str | None = None,
    category: str | None = None,
    counterparty: str | None = None,
    override_direction: str,
    override_category: str,
    note: str | None = None,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    override_direction = _normalize_text(override_direction)
    override_type = _normalize_text(override_category)
    if not override_direction:
        raise ValueError("收支不能为空")
    if not override_type:
        raise ValueError("分类不能为空")

    branch_where_sql, query_params = _build_category_branch_filter_conditions(
        program=program,
        programs=programs,
        path=path,
        direction=direction,
        category=category,
        counterparty=counterparty,
    )
    now = time.time()
    with get_freebill_connection(work_dir) as conn:
        rows = [
            _row_to_dict(row)
            for row in conn.execute(
                f"""
                SELECT matched.*,
                       existing.original_direction AS existing_original_direction,
                       existing.original_type AS existing_original_type,
                       existing.original_standard_nature AS existing_original_standard_nature,
                       existing.original_standard_direction AS existing_original_standard_direction,
                       existing.created_at AS existing_created_at
                FROM (
                    SELECT *
                    FROM bill_records
                    WHERE {branch_where_sql}
                      AND COALESCE(NULLIF(TRIM(trade_no), ''), '/') != '/'
                ) AS matched
                LEFT JOIN freebill_record_overrides AS existing
                  ON existing.trade_no = matched.trade_no
                ORDER BY COALESCE(matched.amount, 0) DESC, datetime(matched.create_time) DESC, matched.id DESC
                """,
                query_params,
            ).fetchall()
        ]

        override_rows: list[tuple[Any, ...]] = []
        seen_trade_nos: set[str] = set()
        for record in rows:
            trade_no = str(record.get("trade_no") or "").strip()
            if not trade_no or trade_no == "/" or trade_no in seen_trade_nos:
                continue
            seen_trade_nos.add(trade_no)
            original_direction = record.get("existing_original_direction") or record.get("direction")
            original_type = record.get("existing_original_type") or record.get("type")
            base_record = {
                **record,
                "direction": original_direction,
                "type": original_type,
            }
            resolved_override_type = _resolve_freebill_flow_override_type(
                override_type,
                original_type,
            )
            (
                original_standard_nature,
                original_standard_direction,
                override_standard_nature,
                override_standard_direction,
            ) = _resolve_freebill_override_standard_fields(
                base_record,
                {
                    "original_direction": original_direction,
                    "original_type": original_type,
                    "override_direction": override_direction,
                    "override_type": resolved_override_type,
                },
            )
            created_at = record.get("existing_created_at") or now
            override_rows.append(
                (
                    trade_no,
                    record.get("source"),
                    original_direction,
                    original_type,
                    original_standard_nature,
                    original_standard_direction,
                    override_direction,
                    resolved_override_type,
                    override_standard_nature,
                    override_standard_direction,
                    note,
                    created_at,
                    now,
                )
            )

        if override_rows:
            conn.executemany(
                """
                INSERT INTO freebill_record_overrides (
                    trade_no, source, original_direction, original_type,
                    original_standard_nature, original_standard_direction,
                    override_direction, override_type,
                    override_standard_nature, override_standard_direction,
                    note, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_no) DO UPDATE SET
                    source = excluded.source,
                    original_standard_nature = excluded.original_standard_nature,
                    original_standard_direction = excluded.original_standard_direction,
                    override_direction = excluded.override_direction,
                    override_type = excluded.override_type,
                    override_standard_nature = excluded.override_standard_nature,
                    override_standard_direction = excluded.override_standard_direction,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                override_rows,
            )
            updated = len(override_rows)
        else:
            updated = 0
        conn.commit()

    return {
        "requested": len(seen_trade_nos),
        "matched": len(seen_trade_nos),
        "updated": updated,
        "missing_trade_nos": [],
        "direction": override_direction,
        "category": override_type,
    }


def clear_freebill_record_overrides(
    trade_nos: list[str],
    *,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    normalized_trade_nos = _normalize_trade_nos(trade_nos)
    if not normalized_trade_nos:
        raise ValueError("请选择需要还原的账单记录")

    with get_freebill_connection(work_dir) as conn:
        rows = conn.execute(
            f"""
            SELECT trade_no
            FROM freebill_record_overrides
            WHERE trade_no IN ({_sql_placeholders(normalized_trade_nos)})
            """,
            normalized_trade_nos,
        ).fetchall()
        if rows:
            overridden_trade_nos = [str(row["trade_no"]) for row in rows]
            conn.execute(
                f"""
                DELETE FROM freebill_record_overrides
                WHERE trade_no IN ({_sql_placeholders(overridden_trade_nos)})
                """,
                overridden_trade_nos,
            )
        conn.commit()

    cleared_trade_nos = {str(row["trade_no"]) for row in rows}
    return {
        "requested": len(normalized_trade_nos),
        "cleared": len(rows),
        "missing_trade_nos": [
            trade_no for trade_no in normalized_trade_nos if trade_no not in cleared_trade_nos
        ],
    }


def _resolve_freebill_flow_override_type(category: str, original_type: Any) -> str:
    if category not in {FLOW_OVERRIDE_CATEGORY, FLOW_EXPENSE_CATEGORY, FLOW_INCOME_CATEGORY}:
        return category
    return _normalize_text(original_type) or FLOW_OVERRIDE_CATEGORY


def _normalize_trade_nos(trade_nos: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for trade_no in trade_nos:
        text = str(trade_no or "").strip()
        if not text or text == "/" or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _query_bill_records_by_trade_no(
    conn: sqlite3.Connection,
    trade_nos: list[str],
) -> dict[str, dict[str, Any]]:
    if not trade_nos:
        return {}
    return {
        str(row["trade_no"]): _row_to_dict(row)
        for row in conn.execute(
            f"""
            SELECT *
            FROM bill_records
            WHERE trade_no IN ({_sql_placeholders(trade_nos)})
            """,
            trade_nos,
        ).fetchall()
    }


def _freebill_effective_standard_nature_sql() -> str:
    return """
        COALESCE(
            (
                SELECT NULLIF(TRIM(CAST(
                    CASE
                        WHEN json_valid(COALESCE(freebill_record_overrides.manual_overrides_json, '{}'))
                        THEN json_extract(freebill_record_overrides.manual_overrides_json, '$.standard_nature')
                    END AS TEXT
                )), '')
                FROM freebill_record_overrides
                WHERE freebill_record_overrides.trade_no = bill_records.trade_no
            ),
            (
                SELECT NULLIF(TRIM(freebill_record_overrides.override_standard_nature), '')
                FROM freebill_record_overrides
                WHERE freebill_record_overrides.trade_no = bill_records.trade_no
            ),
            NULLIF(TRIM(standard_nature), ''),
            '常规'
        )
    """


def _freebill_effective_standard_direction_sql() -> str:
    return """
        COALESCE(
            (
                SELECT NULLIF(TRIM(CAST(
                    CASE
                        WHEN json_valid(COALESCE(freebill_record_overrides.manual_overrides_json, '{}'))
                        THEN json_extract(freebill_record_overrides.manual_overrides_json, '$.standard_direction')
                    END AS TEXT
                )), '')
                FROM freebill_record_overrides
                WHERE freebill_record_overrides.trade_no = bill_records.trade_no
            ),
            (
                SELECT NULLIF(TRIM(freebill_record_overrides.override_standard_direction), '')
                FROM freebill_record_overrides
                WHERE freebill_record_overrides.trade_no = bill_records.trade_no
            ),
            NULLIF(TRIM(standard_direction), ''),
            '支出'
        )
    """


def _freebill_manual_override_value_sql(field: str) -> str:
    if field not in FREEBILL_MANUAL_OVERRIDE_FIELDS:
        raise ValueError(f"不支持的人工覆盖字段：{field}")
    return f"""
        (
            SELECT CASE
                WHEN json_valid(COALESCE(freebill_record_overrides.manual_overrides_json, '{{}}'))
                THEN json_extract(freebill_record_overrides.manual_overrides_json, '$.{field}')
            END
            FROM freebill_record_overrides
            WHERE freebill_record_overrides.trade_no = bill_records.trade_no
        )
    """


def _freebill_effective_text_field_sql(field: str) -> str:
    if field in {"standard_nature", "standard_direction"}:
        return _freebill_effective_standard_nature_sql() if field == "standard_nature" else _freebill_effective_standard_direction_sql()
    return f"COALESCE(CAST({_freebill_manual_override_value_sql(field)} AS TEXT), {field})"


def _freebill_effective_number_field_sql(field: str) -> str:
    return f"COALESCE(CAST({_freebill_manual_override_value_sql(field)} AS REAL), {field})"


def _sql_placeholders(values: list[Any]) -> str:
    if not values:
        raise ValueError("SQL 占位列表不能为空")
    return ", ".join("?" for _ in values)


def _insert_bill_records(conn: sqlite3.Connection, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    interpret_settings = _load_freebill_interpret_settings(conn)
    interpret_rules = _load_enabled_freebill_interpret_rules(conn)
    placeholders = ", ".join("?" for _ in BILL_RECORD_COLUMNS)
    columns_sql = ", ".join(BILL_RECORD_COLUMNS)
    normalized_records = [
        _apply_freebill_standard_fields(
            record,
            interpret_rules=interpret_rules,
            interpret_settings=interpret_settings,
        )
        for record in records
    ]
    conn.executemany(
        f"INSERT INTO bill_records ({columns_sql}) VALUES ({placeholders})",
        ([record.get(column) for column in BILL_RECORD_COLUMNS] for record in normalized_records),
    )


def list_freebill_interpret_rules(work_dir: Path | None = None) -> dict[str, Any]:
    with get_freebill_connection(work_dir) as conn:
        settings = _load_freebill_interpret_settings(conn)
        rules = _load_freebill_interpret_rules(conn)
        return {
            "settings": settings,
            "fields": [
                {"value": key, **metadata}
                for key, metadata in FREEBILL_INTERPRET_RULE_FIELDS.items()
            ],
            "operators": [
                {"value": "eq", "label": "="},
                {"value": "neq", "label": "≠"},
                {"value": "contains", "label": "包含"},
                {"value": "not_contains", "label": "不包含"},
                {"value": "in", "label": "属于"},
                {"value": "not_in", "label": "不属于"},
                {"value": "gt", "label": ">"},
                {"value": "gte", "label": "≥"},
                {"value": "lt", "label": "<"},
                {"value": "lte", "label": "≤"},
            ],
            "directions": list(FREEBILL_STANDARD_DIRECTIONS),
            "natures": list(FREEBILL_STANDARD_NATURES),
            "built_in_rules": [
                {**dict(item), "enabled": bool(settings["built_in_rules"].get(str(item["key"]), True))}
                for item in FREEBILL_BUILT_IN_INTERPRET_RULES
            ],
            "rules": _attach_freebill_interpret_rule_match_counts(conn, rules, interpret_settings=settings),
        }


def save_freebill_interpret_rules(
    rules: list[dict[str, Any]],
    *,
    settings: dict[str, Any] | None = None,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    normalized_rules = [
        _normalize_freebill_interpret_rule_for_storage(rule, index)
        for index, rule in enumerate((rules or [])[:100])
    ]
    now = time.time()
    with get_freebill_connection(work_dir) as conn:
        _save_freebill_interpret_settings(conn, settings)
        conn.execute("DELETE FROM freebill_interpret_rules")
        conn.executemany(
            """
            INSERT INTO freebill_interpret_rules (
                name,
                enabled,
                order_index,
                matcher_json,
                set_direction,
                set_nature,
                note,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    rule["name"],
                    1 if rule["enabled"] else 0,
                    rule["order_index"],
                    json.dumps(rule["matcher"], ensure_ascii=False, sort_keys=True),
                    rule["set_direction"],
                    rule["set_nature"],
                    rule["note"],
                    now,
                    now,
                )
                for rule in normalized_rules
            ],
        )
        conn.execute(
            """
            INSERT INTO freebill_meta (key, value)
            VALUES ('interpret_rules_updated_at', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(now),),
        )
        conn.commit()
    return list_freebill_interpret_rules(work_dir=work_dir)


def recompute_freebill_interpretation(work_dir: Path | None = None) -> dict[str, Any]:
    with get_freebill_connection(work_dir) as conn:
        interpret_settings = _load_freebill_interpret_settings(conn)
        interpret_rules = _load_enabled_freebill_interpret_rules(conn)
        rows = conn.execute(
            """
            SELECT *
            FROM bill_records
            ORDER BY id
            """
        ).fetchall()
        updates: list[tuple[Any, Any, Any, Any]] = []
        for row in rows:
            row_dict = _row_to_dict(row)
            normalized_record = _apply_freebill_standard_fields(
                row_dict,
                interpret_rules=interpret_rules,
                interpret_settings=interpret_settings,
            )
            if (
                (_normalize_text(row_dict.get("type")) or None) == (normalized_record.get("type") or None)
                and (_normalize_text(row_dict.get("standard_nature")) or None) == (normalized_record.get("standard_nature") or None)
                and (_normalize_text(row_dict.get("standard_direction")) or None) == (normalized_record.get("standard_direction") or None)
            ):
                continue
            updates.append(
                (
                    normalized_record.get("type"),
                    normalized_record.get("standard_nature"),
                    normalized_record.get("standard_direction"),
                    row_dict["id"],
                )
            )
        if updates:
            conn.executemany(
                """
                UPDATE bill_records
                SET type = ?,
                    standard_nature = ?,
                    standard_direction = ?
                WHERE id = ?
                """,
                updates,
            )
        now = time.time()
        conn.execute(
            """
            INSERT INTO freebill_meta (key, value)
            VALUES ('interpret_rules_recomputed_at', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(now),),
        )
        conn.commit()
    payload = list_freebill_interpret_rules(work_dir=work_dir)
    return {
        "total": len(rows),
        "updated": len(updates),
        "rules": payload["rules"],
        "recomputed_at": now,
    }


def _normalize_freebill_interpret_rule_for_storage(rule: dict[str, Any], index: int) -> dict[str, Any]:
    matcher = _normalize_freebill_interpret_matcher((rule or {}).get("matcher"))
    set_direction = _normalize_freebill_interpret_direction((rule or {}).get("set_direction"))
    set_nature = _normalize_freebill_interpret_nature((rule or {}).get("set_nature"))
    return {
        "name": _normalize_text((rule or {}).get("name")) or _build_freebill_interpret_rule_name(matcher, set_direction, set_nature),
        "enabled": bool((rule or {}).get("enabled", True)),
        "order_index": index,
        "matcher": matcher,
        "set_direction": set_direction,
        "set_nature": set_nature,
        "note": _normalize_text((rule or {}).get("note")),
    }


def _build_freebill_interpret_rule_name(
    matcher: dict[str, Any],
    set_direction: str | None,
    set_nature: str | None,
) -> str:
    matcher_text = str(matcher.get("field") or matcher.get("kind") or "规则")
    result_parts = [part for part in (set_direction, set_nature) if part]
    return f"{matcher_text} -> {'/'.join(result_parts) if result_parts else '不改'}"


def _attach_freebill_interpret_rule_match_counts(
    conn: sqlite3.Connection,
    rules: list[dict[str, Any]],
    *,
    interpret_settings: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not rules:
        return []
    counts = [0 for _ in rules]
    rows = conn.execute(
        """
        SELECT *
        FROM bill_records
        ORDER BY id
        """
    ).fetchall()
    for row in rows:
        normalized_record = _apply_freebill_builtin_standard_fields(
            _row_to_dict(row),
            interpret_settings=interpret_settings,
        )
        for index, rule in enumerate(rules):
            if not _freebill_interpret_rule_matches(normalized_record, rule):
                continue
            counts[index] += 1
            if rule.get("enabled"):
                _apply_freebill_interpret_rule_effect(normalized_record, rule)
    return [
        {**rule, "match_count": counts[index]}
        for index, rule in enumerate(rules)
    ]


def _apply_freebill_interpret_rule_effect(record: dict[str, Any], rule: dict[str, Any]) -> None:
    set_direction = _normalize_freebill_interpret_direction(rule.get("set_direction"))
    set_nature = _normalize_freebill_interpret_nature(rule.get("set_nature"))
    if set_direction:
        record["standard_direction"] = set_direction
    if set_nature:
        record["standard_nature"] = set_nature


def _update_rebuild_raw_file_statuses(
    conn: sqlite3.Connection,
    file_results: list[dict[str, Any]],
) -> None:
    now = time.time()
    for item in file_results:
        raw_file_id = item.get("raw_file_id")
        if not raw_file_id:
            continue
        status = "imported" if item.get("status") == "imported" else str(item.get("status") or "skipped")
        note = item.get("error")
        conn.execute(
            """
            UPDATE freebill_raw_files
            SET import_status = ?,
                note = ?,
                last_seen_at = ?
            WHERE id = ?
            """,
            (status, note, now, raw_file_id),
        )


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
    create_time_sql = _freebill_effective_text_field_sql("create_time")
    source_sql = _freebill_effective_text_field_sql("source")
    type_sql = _freebill_effective_text_field_sql("type")
    if start_date:
        conditions.append(f"date({create_time_sql}) >= :start_date")
        params["start_date"] = start_date
    if end_date:
        conditions.append(f"date({create_time_sql}) <= :end_date")
        params["end_date"] = end_date
    if source:
        conditions.append(f"{source_sql} = :source")
        params["source"] = source
    if direction:
        conditions.append(f"{_freebill_display_direction_sql()} = :direction")
        params["direction"] = direction
    if category:
        conditions.append(f"{type_sql} = :category")
        params["category"] = category
    if q:
        conditions.append(
            f"""
            (
                trade_no LIKE :keyword
                OR merchant_order_no LIKE :keyword
                OR {_freebill_effective_text_field_sql("counterparty")} LIKE :keyword
                OR {_freebill_effective_text_field_sql("product_name")} LIKE :keyword
                OR {_freebill_effective_text_field_sql("remark")} LIKE :keyword
            )
            """
        )
        params["keyword"] = f"%{q}%"
    return " AND ".join(conditions), params


def _build_program_filter_conditions(
    program: dict[str, Any] | None,
    param_prefix: str = "p",
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
        matcher_sql = _build_program_matcher_sql(raw_rule.get("matcher"), params, f"{param_prefix}{index}")
        matched_sql = f"COALESCE(({matcher_sql}), 0)"
        if action == "filter":
            decision_sql = f"(({decision_sql}) AND ({matched_sql}))"
        elif action == "exclude":
            decision_sql = f"(({decision_sql}) AND NOT ({matched_sql}))"
        else:
            decision_sql = f"(({decision_sql}) OR ({matched_sql}))"

    return f"COALESCE(({decision_sql}), 0) = 1", params


def _build_programs_filter_conditions(
    programs: list[dict[str, Any]] | None,
) -> tuple[str, dict[str, Any]]:
    if not programs:
        return "1 = 1", {}

    conditions: list[str] = []
    params: dict[str, Any] = {}
    for index, program in enumerate(programs):
        if not isinstance(program, dict):
            continue
        condition_sql, condition_params = _build_program_filter_conditions(program, f"g{index}_p")
        conditions.append(f"({condition_sql})")
        params.update(condition_params)

    if not conditions:
        return "1 = 1", {}
    return " AND ".join(conditions), params


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

    field_sql = _resolve_freebill_program_field_sql(str(field_config["sql"]))
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

    if op == "year":
        if field_mode != "date":
            return "1 = 1"
        year = _normalize_program_year(value)
        if year is None:
            return "1 = 1"
        year_name = f"{prefix}_year"
        params[year_name] = f"{year:04d}"
        return f"strftime('%Y', {field_sql}) = :{year_name}"

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


def _normalize_program_year(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        year = int(float(text))
    except (TypeError, ValueError):
        return None
    if year < 1 or year > 9999:
        return None
    return year


def _resolve_freebill_program_field_sql(field_sql: str) -> str:
    if field_sql == "__interpreted_direction__":
        return _freebill_display_direction_sql()
    if field_sql == "__interpreted_type__":
        return _freebill_effective_standard_nature_sql()
    if field_sql.startswith("__effective:") and field_sql.endswith("__"):
        field = field_sql.removeprefix("__effective:").removesuffix("__")
        mode = str(FREEBILL_MANUAL_OVERRIDE_FIELDS.get(field, {}).get("mode") or "text")
        if mode == "number":
            return _freebill_effective_number_field_sql(field)
        return _freebill_effective_text_field_sql(field)
    return field_sql


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
    programs: list[dict[str, Any]] | None = None,
    category_limit: int | None = 10,
    category_child_limit: int | None = 10,
    monthly_limit: int | None = 12,
    trend_granularity: str = "month",
    trend_standard_nature: str | None = None,
    category_dimensions: list[str] | tuple[str, ...] | None = None,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    trend_granularity = _normalize_trend_granularity(trend_granularity)
    normalized_category_dimensions = _normalize_freebill_category_dimensions(category_dimensions)
    if programs:
        where_sql, params = _build_programs_filter_conditions(programs)
        has_date_filter = any(_program_has_date_rule(item) for item in programs)
    elif program is not None:
        where_sql, params = _build_program_filter_conditions(program)
        has_date_filter = _program_has_date_rule(program)
    else:
        where_sql, params = _build_filter_conditions(
            start_date=start_date,
            end_date=end_date,
            source=source,
            direction=direction,
            category=category,
            q=q,
        )
        has_date_filter = bool(start_date or end_date)
    display_direction_sql = _freebill_display_direction_sql()
    amount_sql = _freebill_effective_number_field_sql("amount")
    source_sql = _freebill_effective_text_field_sql("source")
    with get_freebill_connection(work_dir) as conn:
        interpret_settings = _load_freebill_interpret_settings(conn)
        category_value_sql = _freebill_category_value_sql(
            signed_category_values=bool(interpret_settings.get("signed_category_values")),
        )
        summary = _row_to_dict(
            conn.execute(
                f"""
                SELECT
                    COALESCE(SUM(CASE WHEN {display_direction_sql} = '收入' THEN {amount_sql} ELSE 0 END), 0) AS total_income,
                    COALESCE(SUM(CASE WHEN {display_direction_sql} = '支出' THEN {amount_sql} ELSE 0 END), 0) AS total_expense,
                    0 AS total_ignore,
                    COALESCE(SUM(CASE WHEN {display_direction_sql} NOT IN ('收入', '支出') THEN {amount_sql} ELSE 0 END), 0) AS total_other,
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
                    {source_sql} AS source,
                    COUNT(*) AS count,
                    COALESCE(SUM(CASE WHEN {display_direction_sql} = '收入' THEN {amount_sql} ELSE 0 END), 0) AS income,
                    COALESCE(SUM(CASE WHEN {display_direction_sql} = '支出' THEN {amount_sql} ELSE 0 END), 0) AS expense
                FROM bill_records
                WHERE {where_sql}
                GROUP BY {source_sql}
                ORDER BY count DESC, source
                """,
                params,
            ).fetchall()
        ]
        direction_category_tree = _query_direction_category_tree(
            conn,
            where_sql,
            params,
            value_sql=category_value_sql,
            category_limit=category_limit,
            category_child_limit=category_child_limit,
        )
        category_tree = _query_category_tree_by_dimensions(
            conn,
            where_sql,
            params,
            value_sql=category_value_sql,
            dimensions=normalized_category_dimensions,
            level_limit=category_limit,
            child_limit=category_child_limit,
        )
        expense_categories = _get_direction_categories(direction_category_tree, "支出")
        income_categories = _get_direction_categories(direction_category_tree, "收入")
        trend_where_sql, trend_params = _build_trend_filter_conditions(
            where_sql,
            params,
            standard_nature=trend_standard_nature,
        )
        monthly_trend = _query_period_trend(
            conn,
            trend_where_sql,
            trend_params,
            granularity=trend_granularity,
            limit=_resolve_trend_limit(
                granularity=trend_granularity,
                has_date_filter=has_date_filter,
                monthly_limit=monthly_limit,
            ),
        )

    return {
        "summary": summary,
        "sources": source_breakdown,
        "expense_categories": expense_categories,
        "income_categories": income_categories,
        "category_tree": category_tree,
        "category_dimensions": normalized_category_dimensions,
        "monthly_trend": monthly_trend,
        "trend_granularity": trend_granularity,
    }


def _build_trend_filter_conditions(
    where_sql: str,
    params: dict[str, Any],
    *,
    standard_nature: str | None = None,
) -> tuple[str, dict[str, Any]]:
    normalized_standard_nature = _normalize_text(standard_nature)
    if not normalized_standard_nature:
        return where_sql, params
    if normalized_standard_nature not in FREEBILL_STANDARD_NATURES:
        raise ValueError(f"不支持的趋势类型：{standard_nature}")
    return (
        f"({where_sql}) AND {_freebill_effective_standard_nature_sql()} = :trend_standard_nature",
        {**params, "trend_standard_nature": normalized_standard_nature},
    )


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


def _freebill_display_direction_sql() -> str:
    standard_direction_sql = _freebill_effective_standard_direction_sql()
    return f"""
        COALESCE(NULLIF(({standard_direction_sql}), ''), '支出')
    """


def _freebill_category_value_sql(*, signed_category_values: bool) -> str:
    amount_sql = _freebill_effective_number_field_sql("amount")
    if not signed_category_values:
        return f"COALESCE({amount_sql}, 0)"
    display_direction_sql = _freebill_display_direction_sql()
    return f"""
        CASE
            WHEN {display_direction_sql} = '支出' THEN -COALESCE({amount_sql}, 0)
            WHEN {display_direction_sql} = '收入' THEN COALESCE({amount_sql}, 0)
            WHEN {display_direction_sql} = '收支' THEN 0
            ELSE 0
        END
    """


def _freebill_category_branch_value_sql(value_sql: str) -> str:
    """Keep neutral records out of parent net values while showing their branch amount."""
    amount_sql = _freebill_effective_number_field_sql("amount")
    display_direction_sql = _freebill_display_direction_sql()
    return f"""
        CASE
            WHEN {display_direction_sql} = '收支' THEN COALESCE({amount_sql}, 0)
            ELSE ({value_sql})
        END
    """


def _normalize_freebill_category_dimensions(
    dimensions: list[str] | tuple[str, ...] | None,
) -> list[str]:
    values = list(dimensions or FREEBILL_CATEGORY_DIMENSIONS)
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        value = str(raw_value or "").strip()
        if not value or value in seen:
            continue
        if value not in FREEBILL_CATEGORY_DIMENSIONS:
            raise ValueError(f"不支持的分类维度：{value}")
        normalized.append(value)
        seen.add(value)
    return normalized or list(FREEBILL_CATEGORY_DIMENSIONS)


def _normalize_freebill_category_path(path: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    normalized_path: list[dict[str, str]] = []
    seen_dimensions: set[str] = set()
    for raw_item in path or []:
        if not isinstance(raw_item, dict):
            continue
        dimension = str(raw_item.get("dimension") or "").strip()
        value = str(raw_item.get("value") or "").strip()
        if not dimension or not value:
            continue
        if dimension not in FREEBILL_CATEGORY_DIMENSIONS:
            raise ValueError(f"不支持的分类维度：{dimension}")
        if dimension in seen_dimensions:
            raise ValueError(f"分类路径里重复了同一个维度：{dimension}")
        normalized_path.append({"dimension": dimension, "value": value})
        seen_dimensions.add(dimension)
    return normalized_path


def _freebill_category_dimension_sql(dimension: str) -> str:
    empty_label = FREEBILL_CATEGORY_DIMENSION_EMPTY_LABELS[dimension]
    if dimension == "standard_direction":
        return f"COALESCE(NULLIF(({_freebill_display_direction_sql()}), ''), '{empty_label}')"
    if dimension == "standard_nature":
        return f"COALESCE(NULLIF(({_freebill_effective_standard_nature_sql()}), ''), '{empty_label}')"
    if dimension == "type":
        return f"COALESCE(NULLIF({_freebill_effective_text_field_sql('type')}, ''), '{empty_label}')"
    if dimension == "counterparty":
        return f"COALESCE(NULLIF({_freebill_effective_text_field_sql('counterparty')}, ''), '{empty_label}')"
    raise ValueError(f"不支持的分类维度：{dimension}")


def _build_category_path_filter_conditions(
    path: list[dict[str, str]],
    *,
    prefix: str,
) -> tuple[list[str], dict[str, Any]]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    for index, item in enumerate(path):
        dimension = item["dimension"]
        value = item["value"]
        param_name = f"{prefix}_{index}"
        conditions.append(f"{_freebill_category_dimension_sql(dimension)} = :{param_name}")
        params[param_name] = value
    return conditions, params


def _build_category_group_order_sql(dimension: str, expression_sql: str) -> str:
    if dimension == "standard_direction":
        return f"""
            CASE {expression_sql}
                WHEN '支出' THEN 0
                WHEN '收支' THEN 1
                WHEN '收入' THEN 2
                ELSE 4
            END,
            ABS(value) DESC,
            count DESC,
            name
        """
    if dimension == "standard_nature":
        return f"""
            CASE {expression_sql}
                WHEN '{STANDARD_NATURE_REGULAR}' THEN 0
                WHEN '{STANDARD_NATURE_LOAN}' THEN 1
                WHEN '{STANDARD_NATURE_FINANCE}' THEN 2
                WHEN '{STANDARD_NATURE_TRANSFER}' THEN 3
                WHEN '{STANDARD_NATURE_FLOW}' THEN 4
                ELSE 5
            END,
            ABS(value) DESC,
            count DESC,
            name
        """
    return "ABS(value) DESC, count DESC, name"


def _collapse_category_tail(
    rows: list[dict[str, Any]],
    limit: int | None,
) -> list[dict[str, Any]]:
    """Keep the largest groups visible and preserve the complete smallest suffix."""
    if limit is None or len(rows) <= limit:
        return rows
    if limit <= 0:
        return []

    visible_rows = rows[:limit]
    remainder_rows = rows[limit:]
    remainder_value = sum(float(row.get("value") or 0) for row in remainder_rows)
    if remainder_value.is_integer():
        remainder_value = int(remainder_value)
    return [
        *visible_rows,
        {
            "name": FREEBILL_CATEGORY_REMAINDER_LABEL,
            "value": remainder_value,
            "count": sum(int(row.get("count") or 0) for row in remainder_rows),
            "group_count": len(remainder_rows),
            "is_remainder": True,
            "remainder_items": remainder_rows,
        },
    ]


def _query_category_tree_by_dimensions(
    conn: sqlite3.Connection,
    where_sql: str,
    params: dict[str, Any],
    *,
    value_sql: str,
    dimensions: list[str],
    level_limit: int | None,
    child_limit: int | None,
) -> list[dict[str, Any]]:
    normalized_dimensions = _normalize_freebill_category_dimensions(dimensions)
    return _query_category_tree_level(
        conn,
        where_sql,
        params,
        value_sql=value_sql,
        dimensions=normalized_dimensions,
        path=[],
        depth=0,
        level_limit=level_limit,
        child_limit=child_limit,
    )


def _query_category_tree_level(
    conn: sqlite3.Connection,
    where_sql: str,
    params: dict[str, Any],
    *,
    value_sql: str,
    dimensions: list[str],
    path: list[dict[str, str]],
    depth: int,
    level_limit: int | None,
    child_limit: int | None,
) -> list[dict[str, Any]]:
    if depth >= len(dimensions):
        return []

    dimension = dimensions[depth]
    dimension_sql = _freebill_category_dimension_sql(dimension)
    has_direction_branch = dimension == "standard_direction" or any(
        item.get("dimension") == "standard_direction"
        for item in path
    )
    current_value_sql = (
        _freebill_category_branch_value_sql(value_sql)
        if has_direction_branch
        else value_sql
    )
    path_conditions, path_params = _build_category_path_filter_conditions(path, prefix=f"tree_{depth}")
    merged_conditions = [f"({where_sql})", *path_conditions]
    query_params = {**params, **path_params}
    current_limit = None if depth == 0 else (level_limit if depth == 1 else child_limit)
    if current_limit is not None and current_limit <= 0:
        return []

    grouped_rows = [
        _row_to_dict(row)
        for row in conn.execute(
            f"""
            SELECT {dimension_sql} AS name,
                   COALESCE(SUM({current_value_sql}), 0) AS value,
                   COUNT(*) AS count
            FROM bill_records
            WHERE {" AND ".join(merged_conditions)}
            GROUP BY {dimension_sql}
            ORDER BY {_build_category_group_order_sql(dimension, dimension_sql)}
            """,
            query_params,
        ).fetchall()
    ]
    rows = _collapse_category_tail(grouped_rows, current_limit)
    next_depth = depth + 1

    def populate_row(row: dict[str, Any]) -> None:
        next_path = [*path, {"dimension": dimension, "value": str(row.get("name") or "")}]
        row["dimension"] = dimension
        row["path"] = next_path
        if next_depth < len(dimensions):
            row["children"] = _query_category_tree_level(
                conn,
                where_sql,
                params,
                value_sql=value_sql,
                dimensions=dimensions,
                path=next_path,
                depth=next_depth,
                level_limit=level_limit,
                child_limit=child_limit,
            )

    for row in rows:
        if row.get("is_remainder"):
            for remainder_item in row.get("remainder_items") or []:
                populate_row(remainder_item)
            continue
        populate_row(row)
    return rows


def _query_category_stats(
    conn: sqlite3.Connection,
    where_sql: str,
    params: dict[str, Any],
    direction: str,
    *,
    value_sql: str,
    limit: int | None,
    child_limit: int | None,
) -> list[dict[str, Any]]:
    query_params = {**params, "category_direction": direction}
    if limit is not None and limit <= 0:
        return []
    display_direction_sql = _freebill_display_direction_sql()
    type_sql = _freebill_effective_text_field_sql("type")
    grouped_categories = [
        _row_to_dict(row)
        for row in conn.execute(
            f"""
            SELECT COALESCE(NULLIF({type_sql}, ''), '未分类') AS name,
                   COALESCE(SUM({value_sql}), 0) AS value,
                   COUNT(*) AS count
            FROM bill_records
            WHERE {where_sql}
              AND {display_direction_sql} = :category_direction
            GROUP BY COALESCE(NULLIF({type_sql}, ''), '未分类')
            ORDER BY ABS(value) DESC, count DESC
            """,
            query_params,
        ).fetchall()
    ]
    categories = _collapse_category_tail(grouped_categories, limit)
    for category in categories:
        if category.get("is_remainder"):
            for remainder_category in category.get("remainder_items") or []:
                remainder_category["children"] = _query_category_counterparty_stats(
                    conn,
                    where_sql,
                    params,
                    direction,
                    str(remainder_category.get("name") or ""),
                    value_sql=value_sql,
                    limit=child_limit,
                )
            continue
        category["children"] = _query_category_counterparty_stats(
            conn,
            where_sql,
            params,
            direction,
            str(category.get("name") or ""),
            value_sql=value_sql,
            limit=child_limit,
        )
    return categories


def _get_direction_categories(category_tree: list[dict[str, Any]], direction: str) -> list[dict[str, Any]]:
    for item in category_tree:
        if item.get("name") == direction and isinstance(item.get("children"), list):
            return item["children"]
    return []


def _query_direction_category_tree(
    conn: sqlite3.Connection,
    where_sql: str,
    params: dict[str, Any],
    *,
    value_sql: str,
    category_limit: int | None,
    category_child_limit: int | None,
) -> list[dict[str, Any]]:
    display_direction_sql = _freebill_display_direction_sql()
    branch_value_sql = _freebill_category_branch_value_sql(value_sql)
    directions = [
        _row_to_dict(row)
        for row in conn.execute(
            f"""
            SELECT {display_direction_sql} AS name,
                   COALESCE(SUM({branch_value_sql}), 0) AS value,
                   COUNT(*) AS count
            FROM bill_records
            WHERE {where_sql}
            GROUP BY {display_direction_sql}
            ORDER BY
                CASE {display_direction_sql}
                    WHEN '支出' THEN 0
                    WHEN '收支' THEN 1
                    WHEN '收入' THEN 2
                    ELSE 5
                END,
                ABS(value) DESC,
                count DESC,
                name
            """,
            params,
        ).fetchall()
    ]
    for direction in directions:
        direction["children"] = _query_category_stats(
            conn,
            where_sql,
            params,
            str(direction.get("name") or ""),
            value_sql=branch_value_sql,
            limit=category_limit,
            child_limit=category_child_limit,
        )
    return directions


def _query_category_counterparty_stats(
    conn: sqlite3.Connection,
    where_sql: str,
    params: dict[str, Any],
    direction: str,
    category_name: str,
    *,
    value_sql: str,
    limit: int | None,
) -> list[dict[str, Any]]:
    if limit is not None and limit <= 0:
        return []
    query_params = {
        **params,
        "category_direction": direction,
        "category_name": category_name,
    }
    display_direction_sql = _freebill_display_direction_sql()
    type_sql = _freebill_effective_text_field_sql("type")
    counterparty_sql = _freebill_effective_text_field_sql("counterparty")
    grouped_rows = [
        _row_to_dict(row)
        for row in conn.execute(
            f"""
            SELECT COALESCE(NULLIF({counterparty_sql}, ''), '未标注交易对方') AS name,
                   COALESCE(SUM({value_sql}), 0) AS value,
                   COUNT(*) AS count
            FROM bill_records
            WHERE {where_sql}
              AND {display_direction_sql} = :category_direction
              AND COALESCE(NULLIF({type_sql}, ''), '未分类') = :category_name
            GROUP BY COALESCE(NULLIF({counterparty_sql}, ''), '未标注交易对方')
            ORDER BY ABS(value) DESC, count DESC, name
            """,
            query_params,
        ).fetchall()
    ]
    return _collapse_category_tail(grouped_rows, limit)


def _query_period_trend(
    conn: sqlite3.Connection,
    where_sql: str,
    params: dict[str, Any],
    *,
    granularity: str,
    limit: int | None,
) -> list[dict[str, Any]]:
    period_expr = _get_trend_period_sql(granularity)
    display_direction_sql = _freebill_display_direction_sql()
    amount_sql = _freebill_effective_number_field_sql("amount")
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
                COALESCE(SUM(CASE WHEN {display_direction_sql} = '收入' THEN {amount_sql} ELSE 0 END), 0) AS income,
                COALESCE(SUM(CASE WHEN {display_direction_sql} = '支出' THEN {amount_sql} ELSE 0 END), 0) AS expense,
                COALESCE(SUM(CASE WHEN {display_direction_sql} NOT IN ('收入', '支出') THEN {amount_sql} ELSE 0 END), 0) AS other,
                COUNT(CASE WHEN {display_direction_sql} = '收入' THEN 1 END) AS income_count,
                COUNT(CASE WHEN {display_direction_sql} = '支出' THEN 1 END) AS expense_count,
                0 AS ignore_count,
                COUNT(CASE WHEN {display_direction_sql} NOT IN ('收入', '支出') THEN 1 END) AS other_count,
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
    create_time_sql = _freebill_effective_text_field_sql("create_time")
    if granularity == "day":
        return f"date({create_time_sql})"
    if granularity == "week":
        return f"date({create_time_sql}, '-' || ((CAST(strftime('%w', {create_time_sql}) AS integer) + 6) % 7) || ' days')"
    if granularity == "year":
        return f"strftime('%Y', {create_time_sql})"
    return f"strftime('%Y-%m', {create_time_sql})"


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
    standard_nature_sql = _freebill_effective_standard_nature_sql()
    standard_direction_sql = _freebill_effective_standard_direction_sql()
    with get_freebill_connection(work_dir) as conn:
        total_row = conn.execute(
            f"SELECT COUNT(*) AS total FROM bill_records WHERE {where_sql}",
            params,
        ).fetchone()
        rows = [
            _normalize_freebill_record_row(_row_to_dict(row))
            for row in conn.execute(
                f"""
                SELECT
                    bill_records.*,
                    {standard_nature_sql} AS effective_standard_nature,
                    {standard_direction_sql} AS effective_standard_direction,
                    EXISTS (
                        SELECT 1
                        FROM freebill_record_overrides
                        WHERE freebill_record_overrides.trade_no = bill_records.trade_no
                    ) AS has_record_override,
                    (
                        SELECT freebill_record_overrides.manual_overrides_json
                        FROM freebill_record_overrides
                        WHERE freebill_record_overrides.trade_no = bill_records.trade_no
                    ) AS manual_overrides_json
                FROM bill_records
                WHERE {where_sql}
                ORDER BY datetime({_freebill_effective_text_field_sql("create_time")}) DESC, {_freebill_effective_text_field_sql("create_time")} DESC, id DESC
                LIMIT :limit OFFSET :offset
                """,
                {**params, "limit": limit, "offset": offset},
            ).fetchall()
        ]
    return {
        "total": int(total_row["total"] or 0),
        "items": rows,
    }


def list_freebill_category_branch_records(
    *,
    program: dict[str, Any] | None = None,
    programs: list[dict[str, Any]] | None = None,
    path: list[dict[str, Any]] | None = None,
    direction: str | None = None,
    category: str | None = None,
    counterparty: str | None = None,
    limit: int = 10,
    offset: int = 0,
    sort_by: str = "amount",
    sort_order: str = "desc",
    work_dir: Path | None = None,
) -> dict[str, Any]:
    branch_where_sql, query_params = _build_category_branch_filter_conditions(
        program=program,
        programs=programs,
        path=path,
        direction=direction,
        category=category,
        counterparty=counterparty,
    )
    order_sql = _build_category_branch_records_order_sql(sort_by, sort_order)
    standard_nature_sql = _freebill_effective_standard_nature_sql()
    standard_direction_sql = _freebill_effective_standard_direction_sql()
    with get_freebill_connection(work_dir) as conn:
        total_row = conn.execute(
            f"SELECT COUNT(*) AS total FROM bill_records WHERE {branch_where_sql}",
            query_params,
        ).fetchone()
        rows = [
            _normalize_freebill_record_row(_row_to_dict(row))
            for row in conn.execute(
                f"""
                SELECT
                       bill_records.*,
                       {standard_nature_sql} AS effective_standard_nature,
                       {standard_direction_sql} AS effective_standard_direction,
                       EXISTS (
                           SELECT 1
                           FROM freebill_record_overrides
                           WHERE freebill_record_overrides.trade_no = bill_records.trade_no
                       ) AS has_record_override,
                       (
                           SELECT freebill_record_overrides.manual_overrides_json
                           FROM freebill_record_overrides
                           WHERE freebill_record_overrides.trade_no = bill_records.trade_no
                       ) AS manual_overrides_json
                FROM bill_records
                WHERE {branch_where_sql}
                ORDER BY {order_sql}
                LIMIT :limit OFFSET :offset
                """,
                {**query_params, "limit": limit, "offset": offset},
            ).fetchall()
        ]
    return {
        "total": int(total_row["total"] or 0),
        "items": rows,
    }


def _build_category_branch_records_order_sql(sort_by: str, sort_order: str) -> str:
    field = sort_by if sort_by in FREEBILL_CATEGORY_BRANCH_RECORD_SORT_FIELDS else "amount"
    direction = "ASC" if str(sort_order).lower() == "asc" else "DESC"
    if field == "amount":
        expressions = (f"COALESCE({_freebill_effective_number_field_sql('amount')}, 0)",)
    elif field == "create_time":
        create_time_sql = _freebill_effective_text_field_sql("create_time")
        expressions = (f"datetime({create_time_sql})", create_time_sql)
    elif field in {"source", "product_name", "remark"}:
        expressions = (f"COALESCE(NULLIF({_freebill_effective_text_field_sql(field)}, ''), '') COLLATE NOCASE",)
    else:
        expressions = FREEBILL_CATEGORY_BRANCH_RECORD_SORT_FIELDS[field]
    clauses = [f"{expression} {direction}" for expression in expressions]
    if field != "amount":
        clauses.append(f"COALESCE({_freebill_effective_number_field_sql('amount')}, 0) DESC")
    if field != "create_time":
        create_time_sql = _freebill_effective_text_field_sql("create_time")
        clauses.extend([f"datetime({create_time_sql}) DESC", f"{create_time_sql} DESC"])
    clauses.append("id DESC")
    return ", ".join(clauses)


def _build_category_branch_filter_conditions(
    *,
    program: dict[str, Any] | None,
    programs: list[dict[str, Any]] | None,
    path: list[dict[str, Any]] | None = None,
    direction: str | None = None,
    category: str | None = None,
    counterparty: str | None = None,
) -> tuple[str, dict[str, Any]]:
    if programs:
        where_sql, params = _build_programs_filter_conditions(programs)
    else:
        where_sql, params = _build_program_filter_conditions(program)
    normalized_path = _normalize_freebill_category_path(path)
    if not normalized_path:
        if not _normalize_text(direction):
            raise ValueError("请选择一个分类分支")
        legacy_direction = str(direction).strip()
        if legacy_direction in STANDARD_DIRECTIONS:
            normalized_path = [{"dimension": "standard_direction", "value": legacy_direction}]
            if category is not None:
                normalized_path.append({"dimension": "type", "value": str(category).strip()})
            if counterparty is not None:
                normalized_path.append({"dimension": "counterparty", "value": str(counterparty).strip()})
        else:
            display_direction_sql = _freebill_display_direction_sql()
            branch_conditions = [f"{display_direction_sql} = :branch_direction"]
            branch_params: dict[str, Any] = {"branch_direction": legacy_direction}
            if category is not None:
                branch_conditions.append(f"COALESCE(NULLIF({_freebill_effective_text_field_sql('type')}, ''), '未分类') = :branch_category")
                branch_params["branch_category"] = category
            if counterparty is not None:
                branch_conditions.append(f"COALESCE(NULLIF({_freebill_effective_text_field_sql('counterparty')}, ''), '未标注交易对方') = :branch_counterparty")
                branch_params["branch_counterparty"] = counterparty
            return f"({where_sql}) AND " + " AND ".join(branch_conditions), {**params, **branch_params}

    branch_conditions, branch_params = _build_category_path_filter_conditions(normalized_path, prefix="branch")
    if not branch_conditions:
        raise ValueError("请选择一个分类分支")
    return f"({where_sql}) AND " + " AND ".join(branch_conditions), {**params, **branch_params}


def list_freebill_filter_options(work_dir: Path | None = None) -> dict[str, list[str]]:
    with get_freebill_connection(work_dir) as conn:
        return {
            "sources": _query_distinct_values(conn, _freebill_effective_text_field_sql("source")),
            "directions": _sort_preferred_values(
                _query_distinct_values(conn, _freebill_display_direction_sql()),
                list(FREEBILL_STANDARD_DIRECTIONS),
            ),
            "types": _sort_preferred_values(
                _query_distinct_values(conn, _freebill_effective_standard_nature_sql()),
                list(FREEBILL_STANDARD_NATURES),
            ),
            "categories": _query_distinct_values(conn, _freebill_effective_text_field_sql("type")),
        }


def _sort_preferred_values(values: list[str], preferred_values: list[str]) -> list[str]:
    priority = {value: index for index, value in enumerate(preferred_values)}
    return sorted(values, key=lambda value: (priority.get(value, len(priority)), value))


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
    return _query_distinct_values(conn, column_name)


def _query_distinct_values(conn: sqlite3.Connection, expression_sql: str) -> list[str]:
    rows = conn.execute(
        f"""
        SELECT DISTINCT {expression_sql} AS value
        FROM bill_records
        WHERE {expression_sql} IS NOT NULL AND TRIM({expression_sql}) != ''
        ORDER BY value
        """
    ).fetchall()
    return [str(row["value"]) for row in rows]


def _normalize_freebill_record_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    raw_values = {
        field: normalized.get(field)
        for field in FREEBILL_MANUAL_OVERRIDE_FIELDS
    }
    raw_direction = normalized.get("direction")
    raw_type = normalized.get("type")
    if "effective_standard_nature" in normalized:
        normalized["standard_nature"] = normalized.pop("effective_standard_nature")
    if "effective_standard_direction" in normalized:
        normalized["standard_direction"] = normalized.pop("effective_standard_direction")
    manual_overrides = _normalize_freebill_manual_overrides(normalized.pop("manual_overrides_json", None))
    for field, value in manual_overrides.items():
        normalized[field] = value
    normalized["raw_direction"] = raw_direction
    normalized["raw_type"] = raw_type
    normalized["raw_values"] = raw_values
    normalized["manual_overrides"] = manual_overrides
    if normalized.get("standard_direction"):
        normalized["direction"] = normalized.get("standard_direction")
    return normalized


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {key: row[key] for key in row.keys()}
