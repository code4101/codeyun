"""Request-scoped demo sandbox. Fixtures are authored independently of personal bills.

Each user keeps an editable copy; the API serializes demo requests so reset cannot
race with an import or edit. ContextVar also propagates into FastAPI worker threads.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import json

from backend.core.settings import get_settings

_demo_user: ContextVar[int | None] = ContextVar("freebill_demo_user", default=None)


@contextmanager
def freebill_dataset_scope(dataset: str, user_id: int):
    if dataset not in {"personal", "demo"}:
        raise ValueError("未知账单数据集")
    token = _demo_user.set(user_id if dataset == "demo" else None)
    try:
        yield
    finally:
        _demo_user.reset(token)


def current_demo_directory() -> Path | None:
    user_id = _demo_user.get()
    return None if user_id is None else get_settings().data_dir / "freebill-demo" / str(user_id)


def freebill_sheet_owner_key(user_id: int) -> str:
    return f"demo:{user_id}" if _demo_user.get() is not None else str(user_id)


def freebill_workbook_title() -> str:
    return "Freebill 官方示例" if _demo_user.get() is not None else "Freebill 账单"


def default_demo_records() -> list[dict]:
    """Small fixed fixture spans sources, directions, natures, statuses and dates."""
    scenarios = [
        ("支付宝", "支出", "常规", "餐饮", "示例食堂", "午餐", 28.5, "交易成功"),
        ("微信", "支出", "常规", "交通", "示例交通", "公交乘车", 3, "支付成功"),
        ("建行", "收入", "常规", "工资", "示例公司", "工资", 8600, "交易成功"),
        ("支付宝", "支出", "常规", "购物", "示例商店", "日用品", 156, "交易成功"),
        ("微信", "收入", "常规", "退款", "示例商店", "商品退款", 46, "已退款"),
        ("支付宝", "支出", "借贷", "还款", "示例信贷", "贷款还款", 620, "交易成功"),
        ("建行", "收入", "借贷", "借款", "示例信贷", "贷款发放", 2400, "交易成功"),
        ("支付宝", "支出", "理财", "投资", "示例基金", "基金申购", 800, "交易成功"),
        ("建行", "收入", "理财", "投资", "示例基金", "基金赎回", 920, "交易成功"),
        ("微信", "支出", "转账", "转账", "示例联系人甲", "转账支出", 120, "支付成功"),
        ("微信", "收入", "转账", "转账", "示例联系人乙", "转账收入", 180, "支付成功"),
        ("建行", "支出", "流水", "账户流转", "示例账户", "账户资金划出", 500, "交易成功"),
        ("支付宝", "收入", "流水", "账户流转", "示例账户", "账户资金划入", 500, "交易成功"),
        ("支付宝", "不计收支", "常规", "购物", "示例商店", "取消订单", 75, "交易关闭"),
        ("微信", "支出", "常规", "娱乐", "示例影院", "电影票", 68, "支付成功"),
        ("建行", "支出", "常规", "住房", "示例物业", "物业服务", 260, "交易成功"),
        ("支付宝", "收入", "常规", "兼职", "示例工作室", "设计报酬", 350, "交易成功"),
        ("微信", "支出", "常规", "购物", "示例书店", "图书（部分退款）", 90, "部分退款"),
    ]
    records = []
    for i, (source, direction, nature, category, party, product, amount, status) in enumerate(scenarios):
        timestamp = f"{2025 if i < 3 else 2026}-{11 if i < 3 else (i // 3 + 1):02d}-{i % 3 * 7 + 4:02d} {9 + i % 10:02d}:15:00"
        records.append(dict(source=source, trade_no=f"DEMO-{i + 1:04d}",
            merchant_order_no=f"DEMO-ORDER-{i + 1:04d}", create_time=timestamp,
            pay_time=timestamp, modify_time=timestamp, type=category,
            counterparty=party, product_name=product, amount=amount,
            direction=direction, standard_nature=nature, standard_direction=direction,
            status=status, service_fee=0.5 if i == 7 else 0,
            refund_amount=30 if i == 17 else 0, remark="官方虚构示例",
            account_no="DEMO-ACCOUNT", currency="人民币", cash_type="现",
            account_balance=3200 if source == "建行" else None,
            raw_sequence=f"demo-{i + 1}", location="示例城市"))
    return records


def ensure_demo_dataset(*, reset: bool = False) -> dict:
    """Initialize or reset only the currently selected demo copy, including rules."""
    from backend.core.freebill.core import (
        get_freebill_connection, import_bill_records, save_freebill_interpret_rules,
        archive_freebill_raw_bytes,
    )

    if current_demo_directory() is None:
        raise ValueError("只能重置官方示例数据")
    with get_freebill_connection() as conn:
        initialized = conn.execute("SELECT 1 FROM freebill_meta WHERE key = 'demo_initialized'").fetchone()
        if initialized and not reset:
            return {"reset": False}
        if reset:
            for table in ("bill_records", "freebill_record_overrides", "freebill_interpret_rules", "freebill_raw_files", "freebill_meta"):
                conn.execute(f"DELETE FROM {table}")
            conn.commit()
    save_freebill_interpret_rules([
        dict(name=f"示例：{nature}分类", enabled=True, order_index=i,
             matcher=dict(kind="field", field="type", op="eq", value=category),
             set_nature=nature)
        for i, (category, nature) in enumerate([
            ("投资", "理财"), ("转账", "转账"), ("账户流转", "流水"),
        ])
    ])
    records = default_demo_records()
    result = import_bill_records(records, filename="官方虚构示例")
    for source in ("支付宝", "微信", "建行"):
        archive_freebill_raw_bytes(
            f"官方示例-{source}.json",
            json.dumps([row for row in records if row["source"] == source], ensure_ascii=False).encode("utf-8"),
            source=source, import_status="imported", note="完全虚构的官方示例源数据",
        )
    with get_freebill_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO freebill_meta (key, value) VALUES ('demo_initialized', '1')")
        conn.commit()
    return {"reset": reset, **result}
