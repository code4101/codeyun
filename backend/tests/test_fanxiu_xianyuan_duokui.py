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
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.xianyuan_duokui."
        "read_xianyuan_duokui_status_snapshot",
        lambda **_kwargs: (_ for _ in ()).throw(
            FanxiuRuntimeMemoryError("排名缓存尚未预热")
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
        assert detail.rankings_refresh_status == "unavailable"
        assert detail.rankings_refresh_reason == "排名缓存尚未预热"
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


def test_xianyuan_economical_target_is_bounded_by_task_and_rank() -> None:
    tiers = xianyuan_duokui.XIANYUAN_DUOKUI_TRIAL_TIERS
    strategy = xianyuan_duokui.xianyuan_duokui_resource_strategy()

    assert [(tier["currency"], tier["rank_limit"]) for tier in tiers] == [
        (200, None),
        (450, None),
        (800, 512),
        (1000, 256),
        (1600, 64),
    ]
    assert strategy["默认经济档"] == {
        "累计夺魁灵玉": 10_000,
        "兑换目标": "5折誓约·黛儿",
        "goods_id": 8460001,
        "item_id": 9023,
        "判断": "仙缘夺魁资源产能有限，通常取得折扣黛儿即停止",
    }
    assert strategy["任务性价比档"] == {
        "累计夺魁灵玉": 1000,
        "个人榜要求": "前256",
        "判断": "云梦夺分四与云梦试炼四的双任务重叠档",
    }
    assert [tier["currency"] for tier in xianyuan_duokui.XIANYUAN_DUOKUI_CURRENCY_TIERS] == [
        200,
        400,
        700,
        1000,
        1400,
        1800,
        2400,
        3000,
    ]
    assert strategy["保底档"]["累计夺魁灵玉"] == 800
    assert strategy["顺吃高档"][-1]["累计夺魁灵玉"] == 1600
    assert "2008006" in strategy["禁用道具"]
    assert strategy["自动挑战安全设置"]["自动使用夺魁令补充挑战体力"] is False


def test_xianyuan_wallet_uses_canonical_runtime_fields() -> None:
    assert xianyuan_duokui._wallet_amounts(
        {
            "exchange_currency": 720,
            "currency_amount": 725,
            "currency_borrow": 5,
            "cumulative_currency": 760,
        }
    ) == (720, 760)


def test_xianyuan_higher_task_tiers_are_only_eaten_from_rank_already_held() -> None:
    choose = xianyuan_duokui.recommended_xianyuan_duokui_trial_target

    assert choose(None)["currency"] == 800
    assert choose(700)["currency"] == 800
    assert choose(512)["currency"] == 800
    assert choose(256)["currency"] == 1000
    assert choose(64)["currency"] == 1600
