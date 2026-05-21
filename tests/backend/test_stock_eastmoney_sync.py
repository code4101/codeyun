import json

from sqlmodel import select

from backend.core.stock.eastmoney_ocr import parse_mobile_trade_detail_from_ocr_document
from backend.core.stock.eastmoney_statement import (
    EastmoneyStatement,
    EastmoneyStatementFundFlow,
    EastmoneyStatementPosition,
    fund_flow_to_trade_row,
    read_eastmoney_statement_pdf,
)
from backend.core.stock.eastmoney_sync import (
    TRADE_SOURCE_MOBILE_DETAIL,
    TRADE_SOURCE_NORMAL,
    _normalize_trade_row,
    import_pdf_statement,
    import_mobile_trade_detail_record,
    list_fund_flow_filter_options,
    list_fund_flow_records,
    list_latest_position_snapshots,
)
from backend.core.stock.eastmoney_sheet import refresh_eastmoney_sheet_workbook
from backend.models import (
    EastmoneyAssetSnapshot,
    EastmoneyFundFlowRecord,
    EastmoneyPositionSnapshot,
    EastmoneyStatementImport,
    EastmoneyTradeRecord,
    EastmoneyTradeSyncRun,
    SheetDocument,
    User,
    WorkbookDocument,
    WorkbookSheetLink,
)


def _mobile_detail_row() -> dict[str, str]:
    return {
        "发生日期": "2026-05-08",
        "发生时间": "09:53:48",
        "名称": "机器人PH",
        "代码": "159278",
        "币种": "人民币",
        "买卖类别": "证券卖出",
        "发生金额": "2241.890",
        "成交金额": "2242.000",
        "成交数量": "2000",
        "成交价格": "1.121",
        "印花税": "0.000",
        "过户费": "0.000",
        "佣金": "0.020",
        "其他费用": "0.090",
        "流水号": "0700573505",
        "股东账号": "0307349901",
        "股份余额": "28000",
        "资金余额": "2241.890",
        "扩位简称": "机器人ETF鹏华",
    }


def test_normalize_trade_row_accepts_mobile_trade_detail_fields():
    row = _mobile_detail_row()

    normalized = _normalize_trade_row(row, TRADE_SOURCE_MOBILE_DETAIL, "陈坤泽")

    assert normalized["trade_date"] == "2026-05-08"
    assert normalized["trade_time"] == "09:53:48"
    assert normalized["occurrence_date"] == "2026-05-08"
    assert normalized["occurrence_time"] == "09:53:48"
    assert normalized["security_code"] == "159278"
    assert normalized["security_name"] == "机器人PH"
    assert normalized["direction"] == "卖出"
    assert normalized["occurrence_amount"] == "2241.890"
    assert normalized["amount"] == "2242.000"
    assert normalized["fee"] == "0.11"
    assert normalized["commission"] == "0.020"
    assert normalized["stamp_tax"] == "0.000"
    assert normalized["transfer_fee"] == "0.000"
    assert normalized["other_fee"] == "0.090"
    assert normalized["deal_id"] == "0700573505"
    assert normalized["shareholder_account"] == "0307349901"
    assert normalized["share_balance"] == "28000"
    assert normalized["fund_balance"] == "2241.890"
    assert normalized["extended_name"] == "机器人ETF鹏华"


def test_normalize_trade_row_computes_occurrence_amount_for_web_rows():
    row = {
        "成交日期": "2026-05-08",
        "成交时间": "09:54:23",
        "证券名称": "机器人PH",
        "证券代码": "159278",
        "委托方向": "证券卖出",
        "成交数量": "2000",
        "成交价格": "1.121",
        "成交金额": "2242.000",
        "佣金": "0.020",
        "其他费用": "0.090",
        "成交编号": "0101000022222110",
    }

    normalized = _normalize_trade_row(row, "normal_history_deal", "陈坤泽")

    assert normalized["direction"] == "卖出"
    assert normalized["fee"] == "0.11"
    assert normalized["occurrence_amount"] == "2241.89"
    assert normalized["amount"] == "2242.000"


def test_parse_mobile_trade_detail_from_ocr_document():
    def shape(text: str, x1: int, y1: int, x2: int) -> dict:
        return {
            "label": json.dumps({"text": text}, ensure_ascii=False),
            "points": [[x1, y1], [x2, y1 + 16]],
        }

    document = {
        "shapes": [
            shape("交易明细", 140, 12, 210),
            shape("发生日期", 12, 58, 74),
            shape("2026-05-08", 130, 58, 218),
            shape("发生时间", 254, 58, 318),
            shape("09:53:48", 392, 58, 472),
            shape("名称", 12, 92, 46),
            shape("机器人PH", 150, 92, 220),
            shape("代码", 254, 92, 288),
            shape("159278", 402, 92, 466),
            shape("币种", 12, 126, 46),
            shape("人民币", 176, 126, 232),
            shape("买卖类别", 254, 126, 332),
            shape("证券卖出", 394, 126, 472),
            shape("发生金额", 12, 160, 74),
            shape("2241.890", 172, 160, 246),
            shape("成交金额", 254, 160, 318),
            shape("2242.000", 392, 160, 472),
            shape("成交数量", 12, 194, 74),
            shape("2000", 176, 194, 222),
            shape("成交价格", 254, 194, 318),
            shape("1.121", 424, 194, 472),
            shape("印花税", 12, 228, 62),
            shape("0.000", 176, 228, 224),
            shape("过户费", 254, 228, 304),
            shape("0.000", 424, 228, 472),
            shape("佣金", 12, 262, 46),
            shape("0.020", 176, 262, 224),
            shape("其他费用", 254, 262, 318),
            shape("0.090", 424, 262, 472),
            shape("流水号", 12, 296, 62),
            shape("0700573505", 128, 296, 224),
            shape("股东账号", 254, 296, 318),
            shape("0307349901", 374, 296, 472),
            shape("股份余额", 12, 330, 74),
            shape("28000", 176, 330, 224),
            shape("资金余额", 254, 330, 318),
            shape("2241.890", 392, 330, 472),
            shape("扩位简称", 12, 364, 74),
            shape("机器人ETF鹏华", 134, 364, 252),
        ]
    }

    row, lines = parse_mobile_trade_detail_from_ocr_document(document)

    assert row == _mobile_detail_row()
    assert "发生日期2026-05-08发生时间09:53:48" in lines


def test_import_mobile_trade_detail_updates_existing_web_record(session):
    user = User(username="eastmoney-user", email="eastmoney@example.com", hashed_password="pw")
    session.add(user)
    session.commit()
    session.refresh(user)

    base_run = EastmoneyTradeSyncRun(
        user_id=int(user.id),
        account_label="陈坤泽",
        start_date="2026-01-30",
        end_date="2026-05-10",
        status="success",
    )
    session.add(base_run)
    session.commit()
    session.refresh(base_run)

    session.add(
        EastmoneyAssetSnapshot(
            user_id=int(user.id),
            sync_run_id=base_run.id,
            account_label="陈坤泽",
            captured_at=100,
            raw_json={"证券市值": "261497.86"},
        )
    )
    web_normalized = _normalize_trade_row(
        {
            "成交日期": "2026-05-08",
            "成交时间": "09:54:23",
            "证券名称": "机器人PH",
            "证券代码": "159278",
            "委托方向": "证券卖出",
            "成交数量": "2000",
            "成交价格": "1.121",
            "成交金额": "2242.000",
            "佣金": "0.020",
            "其他费用": "0.090",
            "成交编号": "0101000022222110",
        },
        TRADE_SOURCE_NORMAL,
        "陈坤泽",
    )
    session.add(
        EastmoneyTradeRecord(
            user_id=int(user.id),
            sync_run_id=base_run.id,
            account_label="陈坤泽",
            first_seen_at=1,
            last_seen_at=1,
            created_at=1,
            updated_at=1,
            **web_normalized,
        )
    )
    session.commit()

    result = import_mobile_trade_detail_record(
        session,
        user_id=int(user.id),
        row=_mobile_detail_row(),
        ocr_lines=["发生日期2026-05-08发生时间09:53:48"],
    )
    records = session.exec(select(EastmoneyTradeRecord)).all()

    assert result["created"] is False
    assert result["run"]["updated_count"] == 1
    assert len(records) == 1
    record = records[0]
    assert record.source == TRADE_SOURCE_MOBILE_DETAIL
    assert record.occurrence_amount == "2241.890"
    assert record.commission == "0.020"
    assert record.other_fee == "0.090"
    assert record.shareholder_account == "0307349901"
    assert record.extended_name == "机器人ETF鹏华"
    assert record.raw_json["_ocr_lines"] == ["发生日期2026-05-08发生时间09:53:48"]


def test_fund_flow_to_trade_row_computes_trade_amount_from_net_occurrence():
    flow = EastmoneyStatementFundFlow(
        flow_date="2026-02-24",
        flow_category="证券买入",
        security_code="159278",
        security_name="机器人PH",
        quantity="25900",
        price="1.1580",
        occurrence_amount="-29993.70",
        fee="1.50",
        stamp_tax="0.00",
        transfer_fee="0.00",
        fund_balance="47.00",
        raw_text="20260224 证券买入 159278 机器人PH 25900 1.1580 -29993.70 1.50 0.00 0.00 47.00",
    )

    row = fund_flow_to_trade_row(flow)

    assert row is not None
    assert row["发生金额"] == "29993.7"
    assert row["成交金额"] == "29992.2"
    assert row["手续费"] == "1.50"


def test_import_pdf_statement_stores_flows_positions_and_trades(session, monkeypatch):
    user = User(username="eastmoney-pdf-user", email="eastmoney-pdf@example.com", hashed_password="pw")
    session.add(user)
    session.commit()
    session.refresh(user)

    base_run = EastmoneyTradeSyncRun(
        user_id=int(user.id),
        account_label="陈坤泽(540*****0427)",
        start_date="2026-05-01",
        end_date="2026-05-10",
        status="success",
    )
    session.add(base_run)
    session.commit()
    session.refresh(base_run)
    existing_trade = _normalize_trade_row(
        {
            "成交日期": "2026-05-08",
            "成交时间": "09:54:23",
            "证券名称": "机器人PH",
            "证券代码": "159278",
            "委托方向": "证券卖出",
            "成交数量": "2000",
            "成交价格": "1.1210",
            "成交金额": "2242",
            "手续费": "0.11",
        },
        TRADE_SOURCE_NORMAL,
        "陈坤泽(540*****0427)",
    )
    session.add(
        EastmoneyTradeRecord(
            user_id=int(user.id),
            sync_run_id=base_run.id,
            account_label="陈坤泽(540*****0427)",
            first_seen_at=1,
            last_seen_at=1,
            created_at=1,
            updated_at=1,
            **existing_trade,
        )
    )
    session.commit()

    statement = EastmoneyStatement(
        file_path=r"C:\tmp\statement.pdf",
        file_name="statement.pdf",
        file_size=100,
        file_mtime=1000,
        file_sha256="abc123",
        raw_text="raw",
        lines=[],
        print_time="2026-05-10 23:54:50",
        printed_at=1000,
        query_start_date="2025-01-01",
        query_end_date="2026-05-10",
        customer_name="陈坤泽",
        fund_account="540*****0427",
        asset_summary={"总资产": "263739.34", "证券市值": "261497.86", "资金余额": "2241.48", "资金可用": "2241.48"},
        positions=[
            EastmoneyStatementPosition(
                market="深市A股",
                security_code="159278",
                security_name="机器人PH",
                quantity="28000",
                market_price="1.145",
                cost_price="1.181",
                market_value="32060.00",
            )
        ],
        fund_flows=[
            EastmoneyStatementFundFlow(
                flow_date="2026-05-08",
                flow_category="证券卖出",
                security_code="159278",
                security_name="机器人PH",
                quantity="2000",
                price="1.1210",
                occurrence_amount="2241.89",
                fee="0.11",
                stamp_tax="0.00",
                transfer_fee="0.00",
                fund_balance="2241.89",
                raw_text="20260508 证券卖出 159278 机器人PH 2000 1.1210 2241.89 0.11 0.00 0.00 2241.89",
            ),
            EastmoneyStatementFundFlow(
                flow_date="2026-05-06",
                flow_category="证券转银行",
                security_code="",
                security_name="",
                quantity="0",
                price="0.0000",
                occurrence_amount="-2641.24",
                fee="0.00",
                stamp_tax="0.00",
                transfer_fee="0.00",
                fund_balance="0.00",
                raw_text="20260506 证券转银行 0 0.0000 -2641.24 0.00 0.00 0.00 0.00",
            ),
        ],
    )
    monkeypatch.setattr("backend.core.stock.eastmoney_sync.read_eastmoney_statement_pdf", lambda _path: statement)

    result = import_pdf_statement(session, user_id=int(user.id), path=statement.file_path)

    assert result["flow_count"] == 2
    assert result["inserted_flow_count"] == 2
    assert result["inserted_trade_count"] == 0
    assert result["updated_trade_count"] == 1
    assert len(session.exec(select(EastmoneyStatementImport)).all()) == 1
    assert len(session.exec(select(EastmoneyFundFlowRecord)).all()) == 2
    assert len(session.exec(select(EastmoneyPositionSnapshot)).all()) == 1
    latest_positions = list_latest_position_snapshots(session, user_id=int(user.id))
    assert latest_positions["total"] == 1
    assert latest_positions["items"][0]["security_code"] == "159278"
    assert latest_positions["items"][0]["market_value"] == "32060.00"
    trades = session.exec(select(EastmoneyTradeRecord)).all()
    assert len(trades) == 1
    trade = trades[0]
    assert trade.trade_date == "2026-05-08"
    assert trade.amount == "2242"
    assert trade.source == TRADE_SOURCE_NORMAL
    assert "pdf_statement_flow" in trade.raw_json

    filter_options = list_fund_flow_filter_options(session, user_id=int(user.id))
    assert set(filter_options["categories"]) == {"证券卖出", "证券转银行"}
    assert filter_options["security_codes"] == ["159278"]
    assert filter_options["security_names"] == ["机器人PH"]

    filtered_by_name = list_fund_flow_records(session, user_id=int(user.id), security_name="机器人PH")
    assert filtered_by_name["total"] == 1
    assert filtered_by_name["items"][0]["flow_category"] == "证券卖出"

    filtered_by_code_and_category = list_fund_flow_records(
        session,
        user_id=int(user.id),
        flow_category="证券卖出",
        security_code="159278",
    )
    assert filtered_by_code_and_category["total"] == 1

    workbook_payload = refresh_eastmoney_sheet_workbook(
        session,
        user_id=int(user.id),
        actor_user_id=int(user.id),
    )

    assert workbook_payload["workbook"]["title"] == "东方财富"
    assert {sheet["key"] for sheet in workbook_payload["sheets"]} == {
        "operation-history",
        "local-history",
        "positions",
        "sync-runs",
    }
    assert next(sheet for sheet in workbook_payload["sheets"] if sheet["key"] == "operation-history")["row_count"] == 2

    workbook = session.exec(select(WorkbookDocument).where(WorkbookDocument.title == "东方财富")).first()
    assert workbook is not None
    links = session.exec(select(WorkbookSheetLink).where(WorkbookSheetLink.workbook_id == str(workbook.numeric_id))).all()
    assert len(links) == 4

    operation_sheet = session.exec(
        select(SheetDocument).where(
            SheetDocument.owner_type == "eastmoney",
            SheetDocument.owner_key == str(user.id),
            SheetDocument.sheet_key == "operation-history",
        )
    ).first()
    assert operation_sheet is not None
    assert operation_sheet.document_json["view_settings"]["pagination"] == {"enabled": True, "page_size": 50}
    assert operation_sheet.document_json["rows"][0][:4] == ["2026-05-08", "证券卖出", "159278", "机器人PH"]

    trade_sheet = session.exec(
        select(SheetDocument).where(
            SheetDocument.owner_type == "eastmoney",
            SheetDocument.owner_key == str(user.id),
            SheetDocument.sheet_key == "local-history",
        )
    ).first()
    assert trade_sheet is not None
    assert trade_sheet.document_json["rows"][0][5] == "-2000"
    trade_headers = trade_sheet.document_json["columns"]
    assert trade_sheet.document_json["rows"][0][trade_headers.index("费用")] == "-0.11"


def test_eastmoney_trade_sheet_adds_batch_profit_attribution_columns(session):
    user = User(username="eastmoney-attribution-user", email="eastmoney-attr@example.com", hashed_password="pw")
    session.add(user)
    session.commit()
    session.refresh(user)

    run = EastmoneyTradeSyncRun(
        user_id=int(user.id),
        account_label="陈坤泽",
        start_date="2026-01-01",
        end_date="2026-01-05",
        status="success",
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    rows = [
        ("2026-01-01", "09:30:00", "证券买入", "100", "10.000", "1000", "B001"),
        ("2026-01-02", "09:30:00", "证券买入", "50", "15.000", "750", "B002"),
        ("2026-01-03", "09:30:00", "证券买入", "50", "10.000", "500", "B003"),
        ("2026-01-04", "09:30:00", "证券卖出", "20", "20.000", "400", "S001"),
        ("2026-01-05", "09:30:00", "证券卖出", "60", "15.000", "900", "S002"),
    ]
    for index, (date, trade_time, direction, quantity, price, amount, deal_id) in enumerate(rows, start=1):
        normalized = _normalize_trade_row(
            {
                "成交日期": date,
                "成交时间": trade_time,
                "证券名称": "机器人PH",
                "证券代码": "159278",
                "委托方向": direction,
                "成交数量": quantity,
                "成交价格": price,
                "成交金额": amount,
                "成交编号": deal_id,
            },
            TRADE_SOURCE_NORMAL,
            "陈坤泽",
        )
        session.add(
            EastmoneyTradeRecord(
                user_id=int(user.id),
                sync_run_id=run.id,
                account_label="陈坤泽",
                first_seen_at=index,
                last_seen_at=index,
                created_at=index,
                updated_at=index,
                **normalized,
            )
        )
    session.commit()

    refresh_eastmoney_sheet_workbook(
        session,
        user_id=int(user.id),
        actor_user_id=int(user.id),
    )

    trade_sheet = session.exec(
        select(SheetDocument).where(
            SheetDocument.owner_type == "eastmoney",
            SheetDocument.owner_key == str(user.id),
            SheetDocument.sheet_key == "local-history",
        )
    ).first()
    assert trade_sheet is not None

    headers = trade_sheet.document_json["columns"]
    rows_by_date = {row[headers.index("日期")]: row for row in trade_sheet.document_json["rows"]}
    first_sell = rows_by_date["2026-01-04"]
    second_sell = rows_by_date["2026-01-05"]

    assert trade_sheet.document_json["header_groups"] == []
    assert trade_sheet.document_json["data_start_row"] == 1
    assert trade_sheet.document_json["column_configs"]["归因动作"]["header_background_color"] == "#dbeafe"
    assert first_sell[headers.index("归因动作")] == "出池匹配"
    assert first_sell[headers.index("匹配成本均价")] == "15.000"
    assert first_sell[headers.index("匹配成本金额")] == "300"
    assert first_sell[headers.index("本次归因盈亏")] == "+100"
    assert first_sell[headers.index("本次归因收益率")] == "+33.33%"
    assert first_sell[headers.index("匹配批次")] == "20@15.000"

    assert second_sell[headers.index("匹配成本均价")] == "12.500"
    assert second_sell[headers.index("匹配成本金额")] == "750"
    assert second_sell[headers.index("本次归因盈亏")] == "+150"
    assert second_sell[headers.index("本次归因收益率")] == "+20.00%"
    assert second_sell[headers.index("匹配批次")] == "30@15.000 + 30@10.000"
