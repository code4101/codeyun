from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend.core.fanxiu.instrumentation.wanxiang_baoge import (
    WanxiangRefreshBlocked,
    WanxiangRefundContractError,
    ledger_from_snapshot,
    load_wanxiang_refund_offer_contract,
    require_wanxiang_refresh_allowed,
    select_wanxiang_refund_offer,
    verify_wanxiang_purchase_transition,
)
from backend.core.fanxiu.data_annotation.tasks.wanxiang_baoge import (
    wanxiang_opens_on_date,
)


def _page(target_id=None, purchased=False):
    goods = [{"goods_id": i, "gift_reward": "Item|999_1"} for i in range(1, 6)]
    if target_id:
        goods[4] = {
            "goods_id": target_id,
            "gift_reward": "Item|1201_1",
            "pay_id": 310001,
            "original_price": 120,
            "discount": 0.5,
            "is_prize": 1,
            "purchased": purchased,
            "slot": 5,
        }
    return {
        "complete": True,
        "activity_id": 1240041,
        "goods": goods,
        "refresh_currency_type": 1,
        "refresh_cost": 100,
        "evidence": {"pid": 2631, "process_start_ticks": 5471},
    }


def _snapshot(page, **updates):
    snapshot = {
        "complete": True,
        "activity_id": page["activity_id"],
        "activity_base_id": 2030001,
        "activity_open": True,
        "goods_ids": [row["goods_id"] for row in page["goods"]],
        "purchase_counts": {},
        "buy_times": 0,
        "bought_goods_ids": [],
        "voucher": 6,
        "bound_voucher": 0,
        "spirit_stone": 252278,
        "refund_box_count": 0,
        "evidence": dict(page["evidence"]),
    }
    snapshot.update(updates)
    return snapshot


def test_static_join_proves_six_yuan_refund_and_1140_stones(tmp_path):
    tables = {
        "ChargeGoods": [
            {
                "id": 310001,
                "payId": 310001,
                "priceValue": 600,
                "replaceValue": 6,
                "chargeSource": "WAN_XIANG_SHOP",
            }
        ],
        "Item": [
            {"id": 1201, "effectValue": "96002435_22220"},
            {"id": 1012, "effectValue": "1002_6"},
        ],
        "OptionalGift": [
            {"id": 44553, "groupID": "22220", "giftID": 1012, "number": 1},
            {"id": 44554, "groupID": "22220", "giftID": 1001, "number": 1140},
        ],
    }
    for table, rows in tables.items():
        directory = tmp_path / "parsed_configs" / table
        directory.mkdir(parents=True)
        (directory / "rows.json").write_text(
            json.dumps(rows, ensure_ascii=False), encoding="utf-8"
        )

    contract = load_wanxiang_refund_offer_contract(tmp_path)

    assert "goods_id" not in contract
    assert contract["pay_id"] == 310001
    assert contract["price_cny_fen"] == 600
    assert contract["voucher_cost"] == 6
    assert contract["refund_voucher_amount"] == 6
    assert contract["spirit_stone_reward"] == 1140


@pytest.mark.parametrize("goods_id", [99001, 680001, 999777])
def test_target_identity_does_not_depend_on_season_goods_id(goods_id):
    assert select_wanxiang_refund_offer(_page(goods_id))["goods_id"] == goods_id


@pytest.mark.parametrize("purchased", [False, True])
def test_visible_box_always_blocks_refresh(purchased):
    page = _page(680001, purchased)
    with pytest.raises(WanxiangRefreshBlocked):
        require_wanxiang_refresh_allowed(page, _snapshot(page))


def test_absent_box_without_purchase_allows_refresh():
    page = _page()
    assert require_wanxiang_refresh_allowed(page, _snapshot(page)) is None


@pytest.mark.parametrize(
    "updates",
    [
        {"purchase_counts": {680001: 1}},
        {"purchase_counts": {"680001": 1}},
        {"buy_times": 1},
        {"bought_goods_ids": [680001]},
        {"purchase_counts": {777: 1}},
    ],
)
def test_missing_box_with_purchase_record_never_refreshes(updates):
    page = _page()
    with pytest.raises(WanxiangRefreshBlocked):
        require_wanxiang_refresh_allowed(page, _snapshot(page, **updates))


def test_refresh_confirmation_rechecks_changed_purchase_state():
    page = _page()
    require_wanxiang_refresh_allowed(page, _snapshot(page))
    with pytest.raises(WanxiangRefreshBlocked):
        require_wanxiang_refresh_allowed(
            page, _snapshot(page, purchase_counts={680001: 1})
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"activity_id": 123},
        {"goods_ids": [9] * 5},
        {"evidence": {"pid": 1, "process_start_ticks": 2}},
    ],
)
def test_incoherent_refresh_evidence_is_rejected(updates):
    page = _page()
    with pytest.raises(WanxiangRefreshBlocked):
        require_wanxiang_refresh_allowed(page, _snapshot(page, **updates))


def test_target_payment_must_match():
    page = _page(680001)
    page["goods"][4]["pay_id"] = 999
    with pytest.raises(WanxiangRefundContractError):
        select_wanxiang_refund_offer(page)


def test_purchase_ledger_uses_current_goods_id_and_exact_delta():
    page = _page(680001)
    before = ledger_from_snapshot(_snapshot(page), goods_id=680001)
    after = ledger_from_snapshot(
        _snapshot(page, purchase_counts={"680001": 1}, voucher=0, refund_box_count=1),
        goods_id=680001,
    )
    assert verify_wanxiang_purchase_transition(before, after)["complete"]
    invalid = ledger_from_snapshot(
        _snapshot(page, purchase_counts={99001: 1}, voucher=0, refund_box_count=1),
        goods_id=680001,
    )
    with pytest.raises(WanxiangRefundContractError):
        verify_wanxiang_purchase_transition(before, invalid)


@pytest.mark.parametrize(
    "day,expected",
    [
        ("2026-09-03", False),
        ("2026-09-04", True),
        ("2026-09-05", False),
        ("2026-09-06", False),
    ],
)
def test_only_opening_day_triggers_review(day, expected):
    start = datetime(2026, 9, 4, 0, 0, 5, tzinfo=ZoneInfo("Asia/Shanghai"))
    period = {"complete": True, "start_time_ms": int(start.timestamp() * 1000)}
    assert wanxiang_opens_on_date(period, day) is expected


def test_unknown_opening_time_is_not_treated_as_first_day():
    with pytest.raises(ValueError):
        wanxiang_opens_on_date({"complete": True}, "2026-09-04")


def test_purchased_page_skips_both_refresh_and_purchase_on_repeat(monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import wanxiang_baoge_steps as steps

    page = _page(680001, purchased=True)

    def observe(_context):
        yield from ()
        return {"page": page, "target": select_wanxiang_refund_offer(page)}

    def forbidden(*_args, **_kwargs):
        pytest.fail("已购买分支不得刷新、读取购买账本或再次购买")

    monkeypatch.setattr(steps, "inspect_wanxiang_refund_offer", observe)
    monkeypatch.setattr(steps, "refresh_wanxiang_goods", forbidden)
    monkeypatch.setattr(steps, "read_wanxiang_baoge_runtime", forbidden)
    for _ in range(2):
        # An opaque context has no click methods: these branches need none.
        with pytest.raises(StopIteration) as found:
            next(steps.find_wanxiang_refund_offer(object()))
        assert found.value.value["refreshes"] == 0
        with pytest.raises(StopIteration) as purchased:
            next(steps.purchase_wanxiang_refund_offer(object()))
        assert purchased.value.value["outcome"] == "already_purchased"
