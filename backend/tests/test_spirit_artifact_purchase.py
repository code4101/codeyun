"""Pure purchase authorization checks; no Runtime or GUI mocks."""
import pytest

from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_purchase import (
    SpiritArtifactPurchaseRequest, validate_spirit_artifact_purchase_dialog,
)


REQUEST = SpiritArtifactPurchaseRequest(40014, 14001506, 3, 3, "弥罗宝光幢·环")


def dialog():
    return dict(complete=True, identity_complete=True, source="active_common_shop_buy_tips",
                pid=12, process_start_ticks=34, goods_id=40014, item_id=14001506,
                cost_item_id=15100001, Price=80, goodsNum=1, ShopModelType=4,
                showNum=1, HadPrice=240, CanBuy=True, isEnough=True)


def test_exact_authorization():
    validate_spirit_artifact_purchase_dialog(dialog(), REQUEST, (12, 34))


@pytest.mark.parametrize("field,value", [
    ("goods_id", 40010), ("item_id", 14000306), ("cost_item_id", 14000002),
    ("showNum", 2), ("Price", 81), ("goodsNum", 2), ("HadPrice", 79),
    ("identity_complete", False), ("process_start_ticks", 35), ("ShopModelType", 3),
    ("CanBuy", False), ("isEnough", False),
])
def test_mismatch_rejected(field, value):
    current = dialog()
    current[field] = value
    with pytest.raises(RuntimeError):
        validate_spirit_artifact_purchase_dialog(current, REQUEST, (12, 34))


def test_insufficient_authorized_budget():
    request = SpiritArtifactPurchaseRequest(40014, 14001506, 3, 3, "环", currency_limit=79)
    with pytest.raises(ValueError):
        validate_spirit_artifact_purchase_dialog(dialog(), request, (12, 34))
