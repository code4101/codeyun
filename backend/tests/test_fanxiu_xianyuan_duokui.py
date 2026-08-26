from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine, select

from backend.core.fanxiu.activity import xianyuan_duokui
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
)
from backend.models import FanxiuExchangeShopItem


def _session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _schedule() -> dict:
    return {
        "available": True,
        "complete": True,
        "created_at": "2026-08-26 15:20:00",
        "items": [
            {
                "id": "846001400015",
                "activityId": 846001,
                "activityType": 129,
                "baseId": 360001,
                "serverCount": 8,
                "avgWorldLevel": 110,
                "startTime": 1787719200000,
                "endTime": 1787762400000,
                "closePanelTime": 1787855939000,
            }
        ],
    }


def test_xianyuan_materialize_and_collect_shop_without_fabricating_wallet(
    monkeypatch,
) -> None:
    schedule_reads = 0

    def read_schedule(**_kwargs):
        nonlocal schedule_reads
        schedule_reads += 1
        return _schedule()

    monkeypatch.setattr(
        "backend.core.fanxiu.activity.runtime_schedule."
        "get_cached_fanxiu_activity_runtime_schedule",
        read_schedule,
    )
    monkeypatch.setattr(
        xianyuan_duokui,
        "_shop_snapshot",
        lambda **_kwargs: {
            "complete": True,
            "active_shop_item_count": 1,
            "items": [
                {
                    "goods_id": 8460001,
                    "item_id": 9023,
                    "source_order": 1,
                    "name": "誓约·黛儿",
                    "goods_num": 1,
                    "token_cost": 10000,
                    "purchase_limit": 1,
                    "purchased_count": 0,
                    "discount": 50,
                    "original_price": 20000,
                    "show_limit": "EqualCrossGroup|360001_8",
                    "disappear_limit": "CL|999",
                    "raw_data": {},
                }
            ],
            "evidence": {"source": "V_ShowList"},
        },
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.wallet."
        "read_wallet_currency_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FanxiuRuntimeMemoryError("23002 尚未同步")
        ),
    )

    with _session() as session:
        activity_id = xianyuan_duokui.ensure_xianyuan_duokui_activity(session)
        assert xianyuan_duokui.ensure_xianyuan_duokui_activity(session) == activity_id
        assert schedule_reads == 1
        detail = xianyuan_duokui.collect_and_store_xianyuan_duokui_activity(
            session, activity_id=activity_id
        )
        item = session.exec(
            select(FanxiuExchangeShopItem).where(
                FanxiuExchangeShopItem.activity_id == activity_id,
                FanxiuExchangeShopItem.goods_id == 8460001,
            )
        ).one()

        assert detail is not None
        assert detail.game_shop_base_id == 360001
        assert detail.currency_type == 23002
        assert detail.current_currency == 0
        assert detail.cumulative_currency == 0
        assert len(detail.shop_items) == 1
        assert detail.shop_items[0].name == "誓约·黛儿"
        assert detail.shop_refresh_status == "updated"
        assert detail.currency_fact_fresh is True
        assert detail.budget_ready is True
        assert item is not None


def test_xianyuan_runtime_occurrence_rejects_ambiguous_schedule(monkeypatch) -> None:
    schedule = _schedule()
    schedule["items"] = [*schedule["items"], dict(schedule["items"][0])]
    monkeypatch.setattr(
        "backend.core.fanxiu.activity.runtime_schedule."
        "get_cached_fanxiu_activity_runtime_schedule",
        lambda **_kwargs: schedule,
    )

    try:
        xianyuan_duokui._runtime_occurrence()
    except ValueError as exc:
        assert "唯一仙缘夺魁实例" in str(exc)
    else:
        raise AssertionError("ambiguous schedule must fail closed")
