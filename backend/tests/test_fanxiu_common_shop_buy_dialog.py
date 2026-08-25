from backend.core.fanxiu.instrumentation.common_shop_buy_dialog import plan_common_shop_quantity


def test_quantity_plan_reaches_tianyan_inventory_floor_with_minimum_cost() -> None:
    plan = plan_common_shop_quantity(
        inventory=193,
        target_inventory=3000,
        goods_num=1,
        unit_price=20,
        max_num=5845,
        currency=116906,
    )

    assert plan == {
        "quantity": 2807,
        "cost": 56140,
        "result_inventory": 3000,
        "within_dialog_max": True,
        "currency_sufficient": True,
        "ready": True,
    }
