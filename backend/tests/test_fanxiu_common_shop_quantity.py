from __future__ import annotations

import pytest

import backend.core.fanxiu.runtime_gui.common_shop_quantity as module


def _finish(generator):
    with pytest.raises(StopIteration) as stopped:
        while True:
            next(generator)
    return stopped.value.value


def _snapshot(**overrides):
    return {
        "complete": True,
        "showNum": 1,
        "maxNum": 1998,
        "Price": 20,
        "HadPrice": 39962,
        "CanBuy": True,
        "isEnough": True,
        **overrides,
    }




def test_common_shop_quantity_rejects_target_outside_runtime_range():
    with pytest.raises(RuntimeError, match="超出 1..99"):
        _finish(module.set_verified_common_shop_quantity(
            object(),
            100,
            unit_price=20,
            label="测试购买",
            snapshot_reader=lambda: _snapshot(maxNum=99),
        ))


@pytest.mark.parametrize("change", [
    {"complete": False}, {"showNum": 99}, {"Price": 21},
    {"HadPrice": 1999}, {"CanBuy": False}, {"isEnough": False},
    {"CanBuy": 1}, {"isEnough": 1},
])
def test_purchase_proof_rejects_inconsistent_or_insufficient_snapshot(change):
    with pytest.raises(RuntimeError):
        module.validate_common_shop_purchase_snapshot(
            _snapshot(showNum=100) | change,
            quantity=100, unit_price=20, label="购买证据",
        )


def test_purchase_proof_accepts_exact_balance_without_mutating_snapshot():
    snapshot = _snapshot(showNum=100, HadPrice=2000)
    original = dict(snapshot)
    proof = module.validate_common_shop_purchase_snapshot(
        snapshot, quantity=100, unit_price=20, label="购买证据",
    )
    assert proof["expected_total"] == proof["owned_currency"] == 2000
    assert proof["quantity"] == 100
    assert snapshot == original


@pytest.mark.parametrize("quantity,price", [(0, 20), (1, 0), (-1, 20)])
def test_purchase_proof_rejects_nonpositive_request(quantity, price):
    with pytest.raises(ValueError):
        module.validate_common_shop_purchase_snapshot(
            _snapshot(), quantity=quantity, unit_price=price, label="购买证据",
        )
