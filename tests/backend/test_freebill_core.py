from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
from sqlmodel import select

from backend.core.freebill import (
    archive_freebill_raw_directory,
    deduplicate_freebill_records,
    get_freebill_connection,
    get_freebill_status,
    get_freebill_dashboard,
    import_bill_records,
    import_alipay_csv_bytes,
    import_wechat_excel_bytes,
    list_freebill_filter_options,
    list_freebill_raw_files,
    list_freebill_records,
)
from backend.core.freebill_sheet import get_freebill_sheet_workbook, refresh_freebill_sheet_workbook
from backend.models import SheetDocument, User, WorkbookDocument, WorkbookSheetLink


def _build_alipay_csv_bytes() -> bytes:
    header = [
        "交易订单号",
        "商家订单号",
        "交易时间",
        "交易分类",
        "交易对方",
        "商品说明",
        "金额",
        "收/支",
        "交易状态",
        "备注",
    ]
    rows = [
        ["202601010001", "M001", "2026-01-01 08:00:00", "餐饮美食", "早餐店", "豆浆", "12.5", "支出", "交易成功", ""],
        ["202601020001", "M002", "2026-01-02 09:00:00", "工资", "公司", "工资", "1000", "收入", "交易成功", ""],
    ]
    lines = [""] * 24
    lines.append(",".join(header))
    lines.extend(",".join(row) for row in rows)
    return "\n".join(lines).encode("gbk")


def _build_wechat_excel_bytes() -> bytes:
    output = io.BytesIO()
    columns = [
        "交易时间",
        "交易类型",
        "交易对方",
        "商品",
        "收/支",
        "金额(元)",
        "当前状态",
        "交易单号",
        "商户单号",
        "备注",
    ]
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df = pd.DataFrame(
            [["2026-01-03 10:00:00", "交通出行", "地铁", "地铁票", "支出", "¥6.00", "支付成功", "WX001", "WM001", ""]],
            columns=columns,
        )
        df.to_excel(writer, index=False, startrow=16)
    return output.getvalue()


def test_freebill_import_and_dashboard(tmp_path: Path) -> None:
    alipay_result = import_alipay_csv_bytes("alipay.csv", _build_alipay_csv_bytes(), work_dir=tmp_path)
    wechat_result = import_wechat_excel_bytes("wechat.xlsx", _build_wechat_excel_bytes(), work_dir=tmp_path)

    assert alipay_result["inserted"] == 2
    assert wechat_result["inserted"] == 1
    assert alipay_result["raw_file"]["sha256"]
    assert wechat_result["raw_file"]["sha256"]

    dashboard = get_freebill_dashboard(work_dir=tmp_path)
    assert dashboard["summary"]["total_count"] == 3
    assert dashboard["summary"]["total_income"] == 1000
    assert dashboard["summary"]["total_expense"] == 18.5
    assert dashboard["summary"]["balance"] == 981.5
    assert dashboard["expense_categories"][0]["name"] == "餐饮美食"
    assert dashboard["trend_granularity"] == "month"
    assert [item["month"] for item in dashboard["monthly_trend"]] == ["2026-01"]

    daily_dashboard = get_freebill_dashboard(work_dir=tmp_path, trend_granularity="day")
    assert daily_dashboard["trend_granularity"] == "day"
    assert [item["month"] for item in daily_dashboard["monthly_trend"]] == [
        "2026-01-01",
        "2026-01-02",
        "2026-01-03",
    ]

    weekly_dashboard = get_freebill_dashboard(work_dir=tmp_path, trend_granularity="week")
    assert weekly_dashboard["trend_granularity"] == "week"
    assert [item["month"] for item in weekly_dashboard["monthly_trend"]] == ["2025-12-29"]

    yearly_dashboard = get_freebill_dashboard(work_dir=tmp_path, trend_granularity="year")
    assert yearly_dashboard["trend_granularity"] == "year"
    assert [item["month"] for item in yearly_dashboard["monthly_trend"]] == ["2026"]

    expense_program_dashboard = get_freebill_dashboard(
        work_dir=tmp_path,
        program={
            "default": False,
            "rules": [
                {"action": "include", "matcher": {"kind": "all"}},
                {
                    "action": "filter",
                    "matcher": {"kind": "field", "field": "direction", "op": "eq", "value": "支出"},
                },
            ],
        },
    )
    assert expense_program_dashboard["summary"]["total_count"] == 2
    assert expense_program_dashboard["summary"]["total_income"] == 0
    assert expense_program_dashboard["summary"]["total_expense"] == 18.5

    date_program_dashboard = get_freebill_dashboard(
        work_dir=tmp_path,
        trend_granularity="day",
        program={
            "default": False,
            "rules": [
                {"action": "include", "matcher": {"kind": "all"}},
                {
                    "action": "filter",
                    "matcher": {
                        "kind": "field",
                        "field": "create_time",
                        "op": "between",
                        "values": ["2026-01-02", "2026-01-03"],
                    },
                },
            ],
        },
    )
    assert date_program_dashboard["summary"]["total_count"] == 2
    assert [item["month"] for item in date_program_dashboard["monthly_trend"]] == ["2026-01-02", "2026-01-03"]

    records = list_freebill_records(source="微信", work_dir=tmp_path)
    assert records["total"] == 1
    assert records["items"][0]["counterparty"] == "地铁"

    options = list_freebill_filter_options(work_dir=tmp_path)
    assert options["sources"] == ["微信", "支付宝"]
    assert "支出" in options["directions"]

    status = get_freebill_status(work_dir=tmp_path)
    assert status["raw_file_count"] == 2


def test_freebill_raw_directory_excludes_derived_files(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    work_dir = tmp_path / "work"
    (source_dir / "支付宝").mkdir(parents=True)
    (source_dir / "支付宝" / "账单.csv").write_bytes(b"raw bill")
    (source_dir / "bill.db").write_bytes(b"legacy database snapshot")
    (source_dir / "settings.json").write_text('{"amount_separator":"ten_thousands"}', encoding="utf-8")

    archive_result = archive_freebill_raw_directory(
        source_dir,
        work_dir=work_dir,
        include_database_snapshot=True,
    )
    assert archive_result["archived_count"] == 2
    assert archive_result["skipped_count"] == 1

    raw_files = list_freebill_raw_files(work_dir=work_dir)
    assert raw_files["total"] == 1
    assert raw_files["items"][0]["relative_path"] == "支付宝/账单.csv"
    assert raw_files["items"][0]["source"] == "支付宝"

    status = get_freebill_status(work_dir=work_dir)
    assert status["raw_file_count"] == 1


def test_freebill_deduplicates_trade_no_across_sources(tmp_path: Path) -> None:
    base_record = {
        "source": "支付宝",
        "trade_no": "DUP001",
        "merchant_order_no": "/",
        "create_time": "2026-01-01 08:00:00",
        "pay_time": None,
        "modify_time": None,
        "location": None,
        "type": "微信红包",
        "counterparty": "对方",
        "product_name": "红包",
        "amount": 8.0,
        "direction": "收入",
        "status": "已存入零钱",
        "service_fee": None,
        "refund_amount": None,
        "remark": "/",
        "fund_status": None,
        "imported_at": 1,
    }
    import_result = import_bill_records(
        [
            base_record,
            {**base_record, "source": "微信", "fund_status": "已存入零钱", "imported_at": 2},
        ],
        filename="duplicate",
        work_dir=tmp_path,
    )
    assert import_result["inserted"] == 1
    assert import_result["skipped"] == 1

    with get_freebill_connection(tmp_path) as conn:
        conn.execute(
            """
            INSERT INTO bill_records (
                source, trade_no, merchant_order_no, create_time, pay_time, modify_time,
                location, type, counterparty, product_name, amount, direction, status,
                service_fee, refund_amount, remark, fund_status, imported_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "微信",
                "LEGACYDUP",
                "/",
                "2026-01-02 09:00:00",
                None,
                None,
                None,
                "微信红包",
                "对方",
                "红包",
                9.0,
                "收入",
                "已存入零钱",
                None,
                None,
                "/",
                "已存入零钱",
                1,
            ),
        )
        conn.execute(
            """
            INSERT INTO bill_records (
                source, trade_no, merchant_order_no, create_time, pay_time, modify_time,
                location, type, counterparty, product_name, amount, direction, status,
                service_fee, refund_amount, remark, fund_status, imported_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "支付宝",
                "LEGACYDUP",
                "/",
                "2026-01-02 09:00:00",
                None,
                None,
                None,
                "微信红包",
                "对方",
                "红包",
                9.0,
                "收入",
                "已存入零钱",
                None,
                None,
                "/",
                None,
                1,
            ),
        )
        conn.commit()

    cleanup = deduplicate_freebill_records(work_dir=tmp_path)
    assert cleanup["deleted_records"] == 1
    records = list_freebill_records(work_dir=tmp_path, limit=20)
    legacy_rows = [item for item in records["items"] if item["trade_no"] == "LEGACYDUP"]
    assert len(legacy_rows) == 1
    assert legacy_rows[0]["source"] == "微信"


def test_freebill_refreshes_note_sheet_workbook(tmp_path: Path, monkeypatch, session) -> None:
    monkeypatch.setenv("CODEYUN_FREEBILL_WORK_DIR", str(tmp_path))
    import_alipay_csv_bytes("alipay.csv", _build_alipay_csv_bytes())
    import_wechat_excel_bytes("wechat.xlsx", _build_wechat_excel_bytes())
    with get_freebill_connection(tmp_path) as conn:
        for month in range(1, 14):
            conn.execute(
                """
                INSERT INTO bill_records (
                    source, trade_no, merchant_order_no, create_time, pay_time, modify_time,
                    location, type, counterparty, product_name, amount, direction, status,
                    service_fee, refund_amount, remark, fund_status, imported_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "测试",
                    f"MANUAL{month:02d}",
                    None,
                    f"{2024 + (month - 1) // 12}-{((month - 1) % 12) + 1:02d}-01 12:00:00",
                    None,
                    None,
                    None,
                    f"测试类目{month:02d}",
                    "测试对方",
                    "测试商品",
                    float(month),
                    "支出",
                    "交易成功",
                    None,
                    None,
                    None,
                    None,
                    0,
                ),
            )
        conn.commit()

    user = User(
        username="freebill-sheet-user",
        email="freebill-sheet-user@example.com",
        hashed_password="pw",
        is_active=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    workbook_payload = refresh_freebill_sheet_workbook(
        session,
        user_id=int(user.id),
        actor_user_id=int(user.id),
    )

    assert workbook_payload["workbook"]["title"] == "Freebill 账单"
    assert [item["key"] for item in workbook_payload["sheets"]] == [
        "records",
        "monthly",
        "categories",
        "raw-files",
    ]
    assert workbook_payload["sheets"][0]["row_count"] == 16
    assert workbook_payload["sheets"][1]["row_count"] == 14
    assert workbook_payload["sheets"][2]["row_count"] == 16
    assert workbook_payload["sheets"][3]["row_count"] == 2
    assert get_freebill_sheet_workbook(session, user_id=int(user.id))["workbook"]["id"] == workbook_payload["workbook"]["id"]

    workbook = session.exec(select(WorkbookDocument)).one()
    links = session.exec(select(WorkbookSheetLink).order_by(WorkbookSheetLink.order_index)).all()
    sheets = session.exec(select(SheetDocument).order_by(SheetDocument.sheet_key)).all()
    assert workbook.owner_user_id == user.id
    assert len(links) == 4
    assert {sheet.owner_type for sheet in sheets} == {"freebill"}
    records_sheet = next(sheet for sheet in sheets if sheet.sheet_key == "records")
    assert records_sheet.document_json["columns"][:4] == ["交易时间", "来源", "收支", "分类"]
    assert len(records_sheet.document_json["rows"]) == 16
    assert "height_mode" not in records_sheet.document_json["view_settings"]
    assert records_sheet.document_json["view_settings"]["pagination"]["page_size"] == 50
    records_configs = records_sheet.document_json["column_configs"]
    assert records_configs["交易时间"]["value_type"] == "date"
    assert records_configs["金额"]["value_type"] == "number"
    assert records_configs["来源"]["value_mode"] == "fixed_options"
    assert records_configs["收支"]["value_mode"] == "fixed_options"
    assert records_configs["分类"]["value_mode"] == "fixed_options"
    assert records_configs["状态"]["value_mode"] == "fixed_options"
    assert records_configs["资金状态"]["value_mode"] == "fixed_options"
    assert "value_mode" not in records_configs["交易对方"]

    categories_sheet = next(sheet for sheet in sheets if sheet.sheet_key == "categories")
    categories_configs = categories_sheet.document_json["column_configs"]
    assert categories_configs["收支"]["value_mode"] == "fixed_options"
    assert categories_configs["分类"]["value_mode"] == "fixed_options"
    assert categories_configs["金额"]["value_type"] == "number"

    raw_files_sheet = next(sheet for sheet in sheets if sheet.sheet_key == "raw-files")
    raw_files_configs = raw_files_sheet.document_json["column_configs"]
    assert raw_files_configs["来源"]["value_mode"] == "fixed_options"
    assert raw_files_configs["类型"]["value_mode"] == "fixed_options"
    assert raw_files_configs["状态"]["value_mode"] == "fixed_options"
