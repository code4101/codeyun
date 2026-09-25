"""Pure trial policy and text contracts; real interactions require game acceptance."""
from __future__ import annotations

import pytest
from backend.core.fanxiu.data_annotation.trial_purchase import normalize_xianqiao_trial_purchase_target, purchases_completed_before_price


def test_purchase_model_maps_live_price_to_completed_count():
    assert [purchases_completed_before_price(price) for price in (100, 150, 200)] == [0, 1, 2]
    assert normalize_xianqiao_trial_purchase_target(3) == 3
    with pytest.raises(ValueError):
        normalize_xianqiao_trial_purchase_target(4)
    with pytest.raises(ValueError):
        purchases_completed_before_price(250)
