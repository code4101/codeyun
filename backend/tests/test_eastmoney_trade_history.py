import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, func, select

from backend.core.stock.eastmoney_sync import (
    TRADE_SOURCE_NORMAL,
    _normalize_trade_row,
    import_trade_history_rows,
)
from backend.models import EastmoneyTradeRecord, EastmoneyTradeSyncRun


def test_normalize_general_history_row_keeps_hk_market_and_all_fees() -> None:
    row = {
        "成交日期": "2026-07-27",
        "成交时间": "11:15:55",
        "证券代码": "01810",
        "证券名称": "小米集团",
        "委托方向": "增强卖出",
        "成交数量": "3000",
        "成交价格": "28.700",
        "成交金额": "86100.006",
        "佣金": "18.600",
        "交易规费": "0.000",
        "印花税": "75.160",
        "过户费": "0.000",
        "成交编号": "1810000057402",
        "币种": "-",
    }

    normalized = _normalize_trade_row(row, TRADE_SOURCE_NORMAL, "测试账户")

    assert normalized["market"] == "HK"
    assert normalized["trade_date"] == "2026-07-27"
    assert normalized["trade_time"] == "11:15:55"
    assert normalized["direction"] == "卖出"
    assert normalized["fee_value"] == pytest.approx(93.76)
    assert normalized["commission_value"] == pytest.approx(18.6)
    assert normalized["stamp_tax_value"] == pytest.approx(75.16)


def test_batch_import_preserves_separate_deal_ids_with_identical_values() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[EastmoneyTradeSyncRun.__table__, EastmoneyTradeRecord.__table__],
    )
    common = {
        "成交日期": "2026-02-24",
        "成交时间": "09:30:00",
        "证券代码": "159278",
        "证券名称": "机器人PH",
        "委托方向": "证券买入",
        "成交数量": "1000",
        "成交价格": "1.158",
        "成交金额": "1158.000",
        "佣金": "0.010",
        "交易规费": "0.050",
    }

    with Session(engine) as session:
        result = import_trade_history_rows(
            session,
            user_id=1,
            account_label="测试账户",
            rows=[
                {**common, "成交编号": "deal-1"},
                {**common, "成交编号": "deal-2"},
                {
                    **common,
                    "成交日期": "2026-02-25",
                    "成交编号": "deal-1",
                },
            ],
        )
        count = session.exec(select(func.count()).select_from(EastmoneyTradeRecord)).one()

    assert result["inserted_count"] == 3
    assert count == 3
