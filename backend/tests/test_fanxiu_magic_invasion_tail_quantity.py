from __future__ import annotations


import pytest

from backend.core.fanxiu.runtime_gui import common_shop_quantity as quantity


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            return stop.value


def _dialog(*, show_num: int, maximum: int = 50, price: int = 2500):
    return {
        "complete": True,
        "showNum": show_num,
        "maxNum": maximum,
        "Price": price,
        "HadPrice": 500_000,
        "goodsNum": 1,
        "CanBuy": True,
        "isEnough": True,
    }






def test_common_shop_quantity_rejects_target_outside_runtime_maximum(monkeypatch):
    snapshot_reader = lambda: _dialog(show_num=1, maximum=10)

    def unexpected_controller(*_args, **_kwargs):
        raise AssertionError("越界目标不得进入数量控制器")
        yield

    monkeypatch.setattr(
        quantity,
        "set_verified_integer_slider_count",
        unexpected_controller,
    )
    with pytest.raises(RuntimeError, match=r"购买数量 11 超出 1\.\.10"):
        _drain(
            quantity.set_verified_common_shop_quantity(
                object(),
                11,
                unit_price=2500,
                label="魔道_兑换收尾/测试商品",
                snapshot_reader=snapshot_reader,
            )
        )
