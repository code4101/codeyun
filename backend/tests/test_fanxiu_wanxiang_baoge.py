from __future__ import annotations

import json
from dataclasses import replace

import pytest

from backend.core.fanxiu.instrumentation.wanxiang_baoge import (
    WanxiangRefundContractError,
    WanxiangRefundLedger,
    decide_wanxiang_refund_action,
    load_wanxiang_refund_offer_contract,
    verify_wanxiang_purchase_transition,
    verify_wanxiang_refund_box_transition,
)


def _snapshot(**updates):
    snapshot = {
        "complete": True,
        "activity_id": 1240999,
        "activity_base_id": 2030001,
        "activity_open": True,
        "goods_ids": [111, 99001, 222],
        "purchase_counts": {99001: 0},
        "voucher": 6,
        "bound_voucher": 0,
        "spirit_stone": 2000,
        "refund_box_count": 0,
        "evidence": {"pid": 123, "process_start_ticks": 456},
    }
    snapshot.update(updates)
    return snapshot


def _ledger(**updates):
    ledger = WanxiangRefundLedger(
        activity_id=1240999,
        process_start_ticks=456,
        target_purchase_count=0,
        target_visible=True,
        voucher=6,
        bound_voucher=0,
        spirit_stone=2000,
        refund_box_count=0,
    )
    return replace(ledger, **updates)


def test_static_join_proves_six_yuan_refund_and_1140_stones(tmp_path):
    tables = {
        "WanXiangShopPool": [
            {
                "id": 99001,
                "poolId": 99,
                "giftReward": "Item|1201_1",
                "payId": 310001,
                "isPrize": 1,
            }
        ],
        "ShopPoolBase": [{"poolId": 99, "activityLimit": 1}],
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

    assert contract["goods_id"] == 99001
    assert contract["pay_id"] == 310001
    assert contract["price_cny_fen"] == 600
    assert contract["voucher_cost"] == 6
    assert contract["refund_voucher_amount"] == 6
    assert contract["spirit_stone_reward"] == 1140


def test_purchase_is_authorized_only_with_runtime_voucher_balance():
    decision = decide_wanxiang_refund_action(_snapshot())
    assert decision == {
        "action": "purchase_with_voucher",
        "goods_id": 99001,
        "pay_id": 310001,
        "voucher_cost": 6,
        "expected_confirmation": "VoucherUseTipsView",
        "reason": "Runtime证明目标可见、未购买且代币总额不少于6",
    }

    blocked = decide_wanxiang_refund_action(_snapshot(voucher=5))
    assert blocked["outcome"] == "real_money_risk"


def test_target_not_visible_does_not_authorize_paid_refresh():
    result = decide_wanxiang_refund_action(_snapshot(goods_ids=[1, 2, 3, 4, 5]))
    assert result["action"] == "stop"
    assert result["outcome"] == "target_not_visible"


def test_purchased_box_is_resumed_without_rebuying():
    result = decide_wanxiang_refund_action(
        _snapshot(purchase_counts={99001: 1}, refund_box_count=1, voucher=0)
    )
    assert result == {
        "action": "open_refund_box",
        "item_id": 1201,
        "max_count": 1,
        "reason": "6元商品已购买但代币宝匣尚未打开",
    }


def test_purchase_and_box_transitions_are_separate_exact_ledgers():
    before_purchase = _ledger()
    after_purchase = _ledger(
        target_purchase_count=1,
        target_visible=False,
        voucher=0,
        refund_box_count=1,
    )
    assert verify_wanxiang_purchase_transition(before_purchase, after_purchase)[
        "outcome"
    ] == "purchased_box_pending"

    after_box = _ledger(
        target_purchase_count=1,
        target_visible=False,
        voucher=6,
        spirit_stone=3140,
        refund_box_count=0,
    )
    assert verify_wanxiang_refund_box_transition(after_purchase, after_box) == {
        "complete": True,
        "outcome": "refund_complete",
        "net_value": 1140,
    }


def test_transition_rejects_process_replacement_and_non_exact_rewards():
    before = _ledger(refund_box_count=1, voucher=0)
    with pytest.raises(WanxiangRefundContractError, match="同一活动/进程"):
        verify_wanxiang_refund_box_transition(
            before,
            _ledger(
                process_start_ticks=999,
                refund_box_count=0,
                voucher=6,
                spirit_stone=3140,
            ),
        )
    with pytest.raises(WanxiangRefundContractError, match="1140灵石"):
        verify_wanxiang_refund_box_transition(
            before,
            _ledger(refund_box_count=0, voucher=6, spirit_stone=3139),
        )
