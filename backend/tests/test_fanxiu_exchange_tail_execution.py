from types import SimpleNamespace

import pytest

from backend.core.fanxiu.data_annotation.tasks.exchange_tail_planning import (
    authorize_exchange_purchase,
    verify_exchange_purchase_counts,
    verify_exchange_wallet,
)


def _item(goods_id: int, *, purchased: int, limit: int = 10):
    return SimpleNamespace(
        goods_id=goods_id,
        name=f"item-{goods_id}",
        purchased_count=purchased,
        purchase_limit=limit,
    )


def _purchase(goods_id: int, *, quantity: int):
    return SimpleNamespace(
        goods_id=goods_id,
        name=f"item-{goods_id}",
        quantity=quantity,
    )


def test_purchase_authorization_returns_cost_and_remaining_wallet() -> None:
    assert authorize_exchange_purchase(
        current_wallet=10_000,
        quantity=3,
        unit_price=2_000,
        reserved_tokens=4_000,
        name="target",
    ) == (6_000, 4_000)


def test_purchase_authorization_fails_before_crossing_reserve() -> None:
    with pytest.raises(RuntimeError, match="突破锁定资源预留额"):
        authorize_exchange_purchase(
            current_wallet=10_000,
            quantity=4,
            unit_price=2_000,
            reserved_tokens=4_000,
            name="target",
        )


def test_wallet_verification_requires_all_authoritative_sources() -> None:
    assert verify_exchange_wallet(
        4_000,
        {"商店": 4_000, "Runtime钱包": 4_000, "计划": 4_000},
    ) == 4_000
    with pytest.raises(RuntimeError, match="Runtime钱包=3999"):
        verify_exchange_wallet(
            4_000,
            {"商店": 4_000, "Runtime钱包": 3_999},
        )


def test_finite_purchase_counts_close_and_unlimited_rows_are_skipped() -> None:
    expected = verify_exchange_purchase_counts(
        [_item(1, purchased=2), _item(2, purchased=0, limit=-1)],
        [_item(1, purchased=5), _item(2, purchased=0, limit=-1)],
        [_purchase(1, quantity=3), _purchase(2, quantity=99)],
    )

    assert expected == {1: 5}


def test_purchase_count_verification_fails_closed_on_missing_final_row() -> None:
    with pytest.raises(RuntimeError, match="最终购买数 -1 != 5"):
        verify_exchange_purchase_counts(
            [_item(1, purchased=2)],
            [],
            [_purchase(1, quantity=3)],
        )
