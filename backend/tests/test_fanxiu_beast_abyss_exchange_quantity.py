from __future__ import annotations

import pytest

import backend.core.fanxiu.data_annotation.tasks.beast_abyss_exchange as module


def _finish(generator):
    with pytest.raises(StopIteration) as stopped:
        while True:
            next(generator)
    return stopped.value.value


def test_beast_exchange_uses_common_shop_runtime_quantity_proof(monkeypatch):
    calls = []

    def configure(context, quantity, **kwargs):
        calls.append((context, quantity, kwargs))
        if False:
            yield None
        return {
            "quantity": quantity,
            "maximum": 50,
            "owned_currency": 194100,
        }

    monkeypatch.setattr(module, "set_verified_common_shop_quantity", configure)
    context = object()
    proof = _finish(module._configure_beast_exchange_quantity(
        context,
        quantity=8,
        unit_price=400,
        business_maximum=20,
        expected_wallet=194100,
        label="兽渊_兑换/道则碎片",
    ))

    assert calls == [(
        context,
        8,
        {"unit_price": 400, "label": "兽渊_兑换/道则碎片"},
    )]
    assert proof["quantity"] == 8


def test_beast_exchange_rejects_business_cap_before_opening_controller(monkeypatch):
    monkeypatch.setattr(
        module,
        "set_verified_common_shop_quantity",
        lambda *_args, **_kwargs: (_ for _ in ()),
    )
    with pytest.raises(RuntimeError, match="业务可选上限"):
        _finish(module._configure_beast_exchange_quantity(
            object(),
            quantity=9,
            unit_price=400,
            business_maximum=8,
            expected_wallet=194100,
            label="兽渊_兑换/测试",
        ))


def test_beast_exchange_rejects_runtime_wallet_drift(monkeypatch):
    def configure(*_args, **_kwargs):
        if False:
            yield None
        return {"quantity": 8, "maximum": 50, "owned_currency": 194099}

    monkeypatch.setattr(module, "set_verified_common_shop_quantity", configure)
    with pytest.raises(RuntimeError, match="购买框余额与本批账本不一致"):
        _finish(module._configure_beast_exchange_quantity(
            object(),
            quantity=8,
            unit_price=400,
            business_maximum=20,
            expected_wallet=194100,
            label="兽渊_兑换/测试",
        ))
