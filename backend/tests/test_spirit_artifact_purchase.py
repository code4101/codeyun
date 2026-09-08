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


def test_six_explicit_bodies_require_full_total_and_count():
    request = SpiritArtifactPurchaseRequest(40012, 14000906, 2, 3, "带", quantity=6, currency_limit=480)
    current = {**dialog(), "goods_id": 40012, "item_id": 14000906,
               "showNum": 6, "maxNum": 10, "HadPrice": 480}
    validate_spirit_artifact_purchase_dialog(current, request, (12, 34))
    for change in [{"showNum": 1}, {"HadPrice": 479}, {"maxNum": 5}]:
        with pytest.raises(RuntimeError):
            validate_spirit_artifact_purchase_dialog({**current, **change}, request, (12, 34))
    # Initial count may differ while the formal quantity component adjusts it.
    validate_spirit_artifact_purchase_dialog({**current, "showNum": 1}, request,
                                            (12, 34), require_quantity=False)


@pytest.mark.parametrize("quantity,limit", [(0, 480), (-1, 480), (1.5, 480), (True, 480), (6, 479)])
def test_unbounded_or_invalid_quantity_authorization(quantity, limit):
    request = SpiritArtifactPurchaseRequest(40014, 14001506, 3, 3, "环",
                                           quantity=quantity, currency_limit=limit)
    with pytest.raises(ValueError):
        validate_spirit_artifact_purchase_dialog(dialog(), request, (12, 34))
