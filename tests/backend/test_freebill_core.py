from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
from sqlmodel import select

from backend.core.freebill import (
    archive_freebill_raw_directory,
    clear_freebill_record_overrides,
    deduplicate_freebill_records,
    get_freebill_connection,
    get_freebill_status,
    get_freebill_dashboard,
    import_bill_records,
    import_alipay_csv_bytes,
    import_alipay_excel_bytes,
    import_wechat_excel_bytes,
    list_freebill_category_branch_records,
    list_freebill_filter_options,
    list_freebill_raw_files,
    list_freebill_records,
    rebuild_freebill_records_from_raw_files,
    upsert_freebill_record_overrides,
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


def _build_wechat_compact_excel_bytes() -> bytes:
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
            [["2026-01-04 10:00:00", "餐饮美食", "午餐店", "盖饭", "支出", "22", "支付成功", "WXCOMPACT001", "WMC001", "/"]],
            columns=columns,
        )
        df.to_excel(writer, index=False)
    return output.getvalue()


def _build_alipay_legacy_csv_bytes() -> bytes:
    lines = [
        "支付宝交易记录明细查询",
        "账号:[test]",
        "起始日期:[2026-01-01 00:00:00]    终止日期:[2026-02-01 00:00:00]",
        "---------------------------------交易记录明细列表------------------------------------",
        "交易号,商家订单号,交易创建时间,付款时间,最近修改时间,交易来源地,类型,交易对方,商品名称,金额（元）,收/支,交易状态,服务费（元）,成功退款（元）,备注,资金状态,",
        "202601010001,M-OLD,2026-01-01 08:00:00,2026-01-01 08:00:00,2026-01-01 08:00:00,支付宝网站,即时到账交易,旧早餐店,旧商品,99.00,支出,交易成功,0.00,0.00,,已支出,",
        "202601030001,M-OTHER,2026-01-03 08:00:00,2026-01-03 08:00:00,2026-01-03 08:00:00,支付宝网站,即时到账交易,余额宝,余额宝-收益发放,1.23,其他,交易成功,0.00,0.00,,已收入,",
    ]
    return "\n".join(lines).encode("gb18030")


def test_freebill_import_and_dashboard(tmp_path: Path) -> None:
    alipay_result = import_alipay_csv_bytes("alipay.csv", _build_alipay_csv_bytes(), work_dir=tmp_path)
    wechat_result = import_wechat_excel_bytes("wechat.xlsx", _build_wechat_excel_bytes(), work_dir=tmp_path)

    assert alipay_result["inserted"] == 2
    assert wechat_result["inserted"] == 1
    assert alipay_result["raw_file"]["sha256"]
    assert wechat_result["raw_file"]["sha256"]

    duplicate_alipay_result = import_alipay_csv_bytes("alipay.csv", _build_alipay_csv_bytes(), work_dir=tmp_path)
    assert duplicate_alipay_result["processed"] == 2
    assert duplicate_alipay_result["inserted"] == 0
    assert duplicate_alipay_result["skipped"] == 2

    dashboard = get_freebill_dashboard(work_dir=tmp_path)
    assert dashboard["summary"]["total_count"] == 3
    assert dashboard["summary"]["total_income"] == 1000
    assert dashboard["summary"]["total_expense"] == 18.5
    assert dashboard["summary"]["balance"] == 981.5
    assert dashboard["expense_categories"][0]["name"] == "餐饮美食"
    assert dashboard["expense_categories"][0]["children"][0] == {
        "name": "早餐店",
        "value": 12.5,
        "count": 1,
    }
    assert dashboard["income_categories"][0]["children"][0]["name"] == "公司"
    assert [item["name"] for item in dashboard["category_tree"]] == ["支出", "收入"]
    assert dashboard["category_tree"][0]["children"][0]["name"] == "餐饮美食"
    assert dashboard["category_tree"][0]["children"][0]["children"][0]["name"] == "早餐店"
    branch_records = list_freebill_category_branch_records(
        direction="支出",
        category="餐饮美食",
        work_dir=tmp_path,
    )
    assert branch_records["total"] == 1
    assert branch_records["items"][0]["product_name"] == "豆浆"
    assert [
        item["amount"]
        for item in list_freebill_category_branch_records(direction="支出", work_dir=tmp_path)["items"]
    ] == [12.5, 6.0]
    assert dashboard["trend_granularity"] == "month"
    assert [item["month"] for item in dashboard["monthly_trend"]] == ["2026-01"]
    assert dashboard["monthly_trend"][0]["income_count"] == 1
    assert dashboard["monthly_trend"][0]["expense_count"] == 2
    assert dashboard["monthly_trend"][0]["count"] == 3

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

    layered_program_dashboard = get_freebill_dashboard(
        work_dir=tmp_path,
        programs=[
            {
                "default": False,
                "rules": [
                    {"action": "include", "matcher": {"kind": "all"}},
                    {
                        "action": "filter",
                        "matcher": {"kind": "field", "field": "source", "op": "eq", "value": "支付宝"},
                    },
                ],
            },
            {
                "default": False,
                "rules": [
                    {"action": "include", "matcher": {"kind": "all"}},
                    {
                        "action": "filter",
                        "matcher": {"kind": "field", "field": "direction", "op": "eq", "value": "支出"},
                    },
                ],
            },
        ],
    )
    assert layered_program_dashboard["summary"]["total_count"] == 1
    assert layered_program_dashboard["summary"]["total_expense"] == 12.5

    date_program_dashboard = get_freebill_dashboard(
        work_dir=tmp_path,
        trend_granularity="day",
        program={
            "default": True,
            "rules": [
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

    year_program_dashboard = get_freebill_dashboard(
        work_dir=tmp_path,
        program={
            "default": True,
            "rules": [
                {
                    "action": "filter",
                    "matcher": {
                        "kind": "field",
                        "field": "create_time",
                        "op": "year",
                        "value": 2026,
                    },
                },
            ],
        },
    )
    assert year_program_dashboard["summary"]["total_count"] == 3
    empty_year_program_dashboard = get_freebill_dashboard(
        work_dir=tmp_path,
        program={
            "default": True,
            "rules": [
                {
                    "action": "filter",
                    "matcher": {
                        "kind": "field",
                        "field": "create_time",
                        "op": "year",
                        "value": 2025,
                    },
                },
            ],
        },
    )
    assert empty_year_program_dashboard["summary"]["total_count"] == 0

    records = list_freebill_records(source="微信", work_dir=tmp_path)
    assert records["total"] == 1
    assert records["items"][0]["counterparty"] == "地铁"

    options = list_freebill_filter_options(work_dir=tmp_path)
    assert options["sources"] == ["微信", "支付宝"]
    assert "支出" in options["directions"]

    status = get_freebill_status(work_dir=tmp_path)
    assert status["raw_file_count"] == 2


def test_freebill_imports_legacy_alipay_csv_and_compact_wechat_excel(tmp_path: Path) -> None:
    alipay_result = import_alipay_csv_bytes("alipay_record_legacy.csv", _build_alipay_legacy_csv_bytes(), work_dir=tmp_path)
    wechat_result = import_wechat_excel_bytes("wechat-compact.xlsx", _build_wechat_compact_excel_bytes(), work_dir=tmp_path)

    assert alipay_result["format"] == "alipay-legacy-csv"
    assert alipay_result["inserted"] == 2
    assert wechat_result["inserted"] == 1

    records = list_freebill_records(work_dir=tmp_path, limit=10)
    legacy_other = next(item for item in records["items"] if item["trade_no"] == "202601030001")
    assert legacy_other["direction"] == "不计收支"
    compact_wechat = next(item for item in records["items"] if item["trade_no"] == "WXCOMPACT001")
    assert compact_wechat["counterparty"] == "午餐店"


def test_freebill_imports_legacy_alipay_excel(tmp_path: Path) -> None:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df = pd.DataFrame(
            [
                [
                    "202601040001",
                    "M-EXCEL",
                    "2026-01-04 09:00:00",
                    "2026-01-04 09:00:00",
                    "2026-01-04 09:00:00",
                    "支付宝网站",
                    "即时到账交易",
                    "余利宝",
                    "余利宝转出到支付宝-基金赎回",
                    "100",
                    "其他",
                    "交易成功",
                    "0",
                    "0",
                    "",
                    "已收入",
                    "支付宝",
                ],
                [
                    None,
                    None,
                    "2026-01-04 00:00:00",
                    None,
                    None,
                    None,
                    "往来款",
                    "银行",
                    None,
                    "100",
                    "往来款",
                    None,
                    None,
                    None,
                    None,
                    "1000",
                    "建行",
                ],
            ],
            columns=[
                "交易号",
                "商家订单号",
                "交易创建时间",
                "付款时间",
                "最近修改时间",
                "交易来源地",
                "类型",
                "交易对方",
                "商品名称",
                "金额（元）",
                "收/支",
                "交易状态",
                "服务费（元）",
                "成功退款（元）",
                "备注",
                "资金状态",
                "类别",
            ],
        )
        df.to_excel(writer, index=False, sheet_name="2026年")

    result = import_alipay_excel_bytes("legacy-alipay.xlsx", output.getvalue(), work_dir=tmp_path)
    assert result["inserted"] == 1
    records = list_freebill_records(work_dir=tmp_path, limit=10)
    assert records["total"] == 1
    assert records["items"][0]["direction"] == "不计收支"


def test_freebill_rebuild_from_raw_files_prefers_detail_sources(tmp_path: Path) -> None:
    import_alipay_csv_bytes("alipay_record_legacy.csv", _build_alipay_legacy_csv_bytes(), work_dir=tmp_path)
    import_alipay_csv_bytes("支付宝交易明细(20260101-20260131).csv", _build_alipay_csv_bytes(), work_dir=tmp_path)

    before = list_freebill_records(work_dir=tmp_path, limit=10)
    duplicate_before = next(item for item in before["items"] if item["trade_no"] == "202601010001")
    assert duplicate_before["amount"] == 99

    result = rebuild_freebill_records_from_raw_files(work_dir=tmp_path)
    assert result["before_records"] == 3
    assert result["after_records"] == 3
    assert result["duplicate_records"] == 1
    assert result["backup_path"]

    after = list_freebill_records(work_dir=tmp_path, limit=10)
    duplicate_after = next(item for item in after["items"] if item["trade_no"] == "202601010001")
    assert duplicate_after["amount"] == 12.5
    assert duplicate_after["type"] == "餐饮美食"
    legacy_other = next(item for item in after["items"] if item["trade_no"] == "202601030001")
    assert legacy_other["direction"] == "不计收支"


def test_freebill_record_overrides_survive_raw_rebuild(tmp_path: Path) -> None:
    import_alipay_csv_bytes("支付宝交易明细(20260101-20260131).csv", _build_alipay_csv_bytes(), work_dir=tmp_path)

    override_result = upsert_freebill_record_overrides(
        ["202601010001"],
        direction="不计收支",
        category="流水",
        note="人工确认不计收支",
        work_dir=tmp_path,
    )
    assert override_result["matched"] == 1
    assert override_result["updated"] == 1
    overridden = next(
        item for item in list_freebill_records(work_dir=tmp_path, limit=10)["items"]
        if item["trade_no"] == "202601010001"
    )
    assert overridden["direction"] == "不计收支"
    assert overridden["type"] == "流水"

    rebuild_result = rebuild_freebill_records_from_raw_files(work_dir=tmp_path, backup=False)
    assert rebuild_result["applied_overrides"] == 1
    rebuilt = next(
        item for item in list_freebill_records(work_dir=tmp_path, limit=10)["items"]
        if item["trade_no"] == "202601010001"
    )
    assert rebuilt["direction"] == "不计收支"
    assert rebuilt["type"] == "流水"

    clear_result = clear_freebill_record_overrides(["202601010001"], work_dir=tmp_path)
    assert clear_result["cleared"] == 1
    restored = next(
        item for item in list_freebill_records(work_dir=tmp_path, limit=10)["items"]
        if item["trade_no"] == "202601010001"
    )
    assert restored["direction"] == "支出"
    assert restored["type"] == "餐饮美食"


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
