import pytest

from backend.core.fanxiu.instrumentation.sacred_exchange_shop import decode_sacred_exchange_shop_config
from backend.core.fanxiu.resources.sacred_exchange_planner import plan_sacred_exchange_stock
from backend.core.fanxiu.runtime_gui.sacred_exchange import sacred_shop_product_point
from backend.core.fanxiu.runtime_gui.common_shop_quantity import common_shop_maximum_track_point, validate_common_shop_purchase_snapshot
from backend.core.fanxiu.data_annotation.tasks.sacred_exchange_supply import verify_sacred_buy_identity


def _shop():
    # Verified normal offer: goods5060005 / item100000004 / tree4000005 / 200.
    normal = dict(goods_id=5060005, item_id=100000004, name="弈技·仙弈盒", goods_num=1,
                  cost_item_id=4000005, cost_num=200, limit_times=-1, bought=0)
    return dict(complete=True, rows=[dict(entries=[normal,
        dict(normal, goods_id=5060006, cost_num=20, limit_times=10, bought=10),
        dict(normal, goods_id=5060007, cost_num=0, limit_times=1, bought=1)])])


def _plan():
    return plan_sacred_exchange_stock(_shop(), target_item_id=100000004, current_stock=77, target_stock=4645)


@pytest.mark.parametrize("shop_type", [3, 7])
def test_common_shop_variants_keep_verified_item_cost_and_output(shop_type):
    values = [None] * 19
    for index, value in {1:5060005, 2:3000005, 3:shop_type, 6:100000004, 7:1, 12:-1, 17:7}.items():
        values[index] = value
    row = decode_sacred_exchange_shop_config(values, cost_values=[None, "Item|4000005_200"])
    assert (row["goods_id"], row["item_id"], row["cost_item_id"], row["cost_num"]) == (5060005,100000004,4000005,200)
    values[3] = 99
    with pytest.raises(RuntimeError):
        decode_sacred_exchange_shop_config(values, cost_values=[None, "Item|4000005_200"])


def test_same_item_exhausted_offers_do_not_hide_regular_sku():
    plan = _plan()
    assert (plan.goods_id, plan.exchange_count, plan.total_cost) == (5060005,4568,913600)
    shop = _shop()
    shop["rows"][0]["entries"][1]["bought"] = 0
    with pytest.raises(ValueError, match="命中 2 行"):
        plan_sacred_exchange_stock(shop, target_item_id=100000004,current_stock=77,target_stock=4645)


def _token(text, x, y, w=40):
    return dict(text=text, x=x, y=y, w=w, h=30, score=.99)


def test_split_glyphs_and_omitted_separator_align_name_and_price_to_icon():
    # Geometry from doctor_20261002_224155.png (900x1600).
    tokens = [_token(text, 245+i*36, 855, 34) for i,text in enumerate("弈技仙弈盒")]
    tokens += [_token("200", 462, 916, 56)]
    point = sacred_shop_product_point(tokens, window=dict(x=100,y=250,w=740,h=1080),name="弈技·仙弈盒",unit_price=200)
    assert point == (174.0,900.5)
    assert sacred_shop_product_point(tokens, window=dict(x=100,y=250,w=740,h=1080),name="弈技·仙弈盒",unit_price=20) is None
    duplicate = [dict(t,y=t["y"]+200) for t in tokens]
    with pytest.raises(RuntimeError, match="不唯一"):
        sacred_shop_product_point(tokens+duplicate, window=dict(x=100,y=250,w=740,h=1080),name="弈技·仙弈盒",unit_price=200)


def test_large_quantity_endpoint_is_bounded_and_final_proof_requires_exact_count():
    assert common_shop_maximum_track_point(dict(x=300,y=800,w=360,h=40)) == (660,820)
    raw=dict(complete=True,showNum=4568,maxNum=4568,Price=200,HadPrice=913788,CanBuy=True,isEnough=True)
    assert validate_common_shop_purchase_snapshot(raw,quantity=4568,unit_price=200,label="补给")["expected_total"] == 913600
    with pytest.raises(RuntimeError):
        validate_common_shop_purchase_snapshot(dict(raw,showNum=4567),quantity=4568,unit_price=200,label="补给")
    with pytest.raises(RuntimeError):
        common_shop_maximum_track_point(dict(x=0,y=0,w=float("nan"),h=20))


@pytest.mark.parametrize("change", [{"goods_id":5060006},{"item_id":1300755},{"cost_item_id":1},{"identity_complete":False}])
def test_purchase_identity_rejects_wrong_same_named_offer(change):
    snapshot=dict(identity_complete=True,goods_id=5060005,item_id=100000004,cost_item_id=4000005)
    verify_sacred_buy_identity(snapshot,_plan())
    with pytest.raises(RuntimeError):
        verify_sacred_buy_identity(snapshot|change,_plan())


def test_saved_real_shop_frame_aligns_paid_offer():
    # Native Paddle tokens from doctor_20261002_224155.png, no GUI simulation.
    tokens = [{'text': '弈', 'x': 247.0, 'y': 849.0, 'w': 38.0, 'h': 38.0, 'parent_line_id': 'line-13', 'line_order': 13, 'order': 0}, {'text': '技', 'x': 279.0, 'y': 849.0, 'w': 38.0, 'h': 38.0, 'parent_line_id': 'line-13', 'line_order': 13, 'order': 1}, {'text': '·', 'x': 333.0, 'y': 849.0, 'w': 7.0, 'h': 38.0, 'parent_line_id': 'line-13', 'line_order': 13, 'order': 2}, {'text': '仙', 'x': 356.0, 'y': 849.0, 'w': 38.0, 'h': 38.0, 'parent_line_id': 'line-13', 'line_order': 13, 'order': 3}, {'text': '弈', 'x': 388.0, 'y': 849.0, 'w': 38.0, 'h': 38.0, 'parent_line_id': 'line-13', 'line_order': 13, 'order': 4}, {'text': '盒', 'x': 426.0, 'y': 849.0, 'w': 35.0, 'h': 38.0, 'parent_line_id': 'line-13', 'line_order': 13, 'order': 5}, {'text': '200', 'x': 476.0, 'y': 909.0, 'w': 37.0, 'h': 40.0, 'parent_line_id': 'line-14', 'line_order': 14, 'order': 5}]
    assert sacred_shop_product_point(tokens, window=dict(x=100,y=250,w=740,h=1080),
                                     name="弈技·仙弈盒",unit_price=200) == (174.0,898.5)
