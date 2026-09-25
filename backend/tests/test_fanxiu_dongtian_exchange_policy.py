import pytest

from backend.core.fanxiu.data_annotation.tasks.dongtian_exchange import (
    plan_dongtian_tongbao_exchange, validate_dongtian_purchase_dialog,
)


def snapshot():
    offers = [(21000033, 390037007, 100, None, 25),
              (21000014, 37131303, 400, 20, 1),
              (21000015, 37131303, 800, 40, 1),
              (21000017, 37130505, 10, 50, 10),
              (21000016, 37131303, 2000, None, 3)]
    return dict(complete=True, shop_base_id=620000, currency_types=[37130501],
                items=[dict(goods_id=g, item_id=i, token_cost=p, discount=d,
                            purchase_limit=n, purchased_count=0, currency_type=37130501)
                       for g,i,p,d,n in offers])


def test_only_authorized_discount_offers_and_remaining_quantities():
    data = snapshot()
    data['items'][0]['purchased_count'] = 24
    actions = plan_dongtian_tongbao_exchange(data)
    assert [a['goods_id'] for a in actions] == [21000033, 21000014, 21000015, 21000017]
    assert [a['quantity'] for a in actions] == [1, 1, 1, 10]
    for row in data['items']:
        row['purchased_count'] = row['purchase_limit']
    assert plan_dongtian_tongbao_exchange(data) == []


@pytest.mark.parametrize('key,value', [('complete', False), ('shop_base_id', 1),
                                      ('currency_types', [1])])
def test_wrong_shop_fails_closed(key, value):
    data = snapshot()
    data[key] = value
    with pytest.raises(ValueError):
        plan_dongtian_tongbao_exchange(data)


@pytest.mark.parametrize('field,value', [('goods_id',21000016), ('cost_item_id',1),
                                        ('Price',2000), ('showNum',2)])
def test_confirmation_rejects_wrong_offer_currency_price_or_count(field, value):
    action = plan_dongtian_tongbao_exchange(snapshot())[1]
    dialog = dict(complete=True, identity_complete=True, goods_id=21000014,
                  item_id=37131303, cost_item_id=37130501, Price=400,
                  showNum=1, maxNum=1, HadPrice=22754, CanBuy=True, isEnough=True)
    dialog[field] = value
    with pytest.raises(ValueError):
        validate_dongtian_purchase_dialog(dialog, action, require_quantity=True)


def test_order_alignment_locates_obscured_target_and_rejects_reordered_anchors():
    from backend.core.fanxiu.runtime_gui.exchange_shop import resolve_ordered_exchange_candidate
    items = [dict(goods_id=i, name=name) for i,name in enumerate(('甲','乙','丙','目标'))]
    lines = [dict(text=name,x=100,y=200+i*163,w=80,h=30)
             for i,name in enumerate(('甲','乙','丙'))]
    kwargs = dict(items=items, goods_id=3, product_list_box=dict(x=0,y=100,w=800,h=1000),
                  row_height=151)
    target = resolve_ordered_exchange_candidate(lines, **kwargs)
    assert target.y == pytest.approx(704)
    lines[1]['y'] += 50
    with pytest.raises(RuntimeError):
        resolve_ordered_exchange_candidate(lines, **kwargs)


def test_taixu_discount_whitelist_currency_and_reentry():
    from backend.core.fanxiu.data_annotation.tasks.dongtian_exchange import plan_dongtian_taixu_exchange
    offers = [(21000004,17003,30,60,10), (21000005,37130506,10,50,20),
              (21000006,17011,10,50,20), (21000007,17010,10,50,20),
              (21000008,17009,10,50,20), (21000009,17012,10,50,20),
              (21000010,17011,20,None,-1)]
    data = dict(complete=True, shop_base_id=620000, currency_types=[37130502],
                items=[dict(goods_id=g,item_id=i,token_cost=p,discount=d,
                            purchase_limit=n,purchased_count=0,currency_type=37130502)
                       for g,i,p,d,n in offers])
    actions = plan_dongtian_taixu_exchange(data)
    assert len(actions) == 6
    assert sum(a['quantity']*a['token_cost'] for a in actions) == 1300
    action = actions[0]
    dialog = dict(complete=True,identity_complete=True,goods_id=21000004,item_id=17003,
                  cost_item_id=37130502,Price=30,showNum=10,maxNum=10,HadPrice=26077,
                  CanBuy=True,isEnough=True)
    validate_dongtian_purchase_dialog(dialog,action,require_quantity=True)
    dialog['cost_item_id'] = 37130501
    with pytest.raises(ValueError):
        validate_dongtian_purchase_dialog(dialog,action)
    for row in data['items'][:6]:
        row['purchased_count'] = row['purchase_limit']
    assert plan_dongtian_taixu_exchange(data) == []


@pytest.mark.parametrize('day,expected', [(21,None),(22,'week:2026-09-21'),(23,None),(27,None),(29,'week:2026-09-28')])
def test_dongtian_tuesday_business_cycle(day, expected):
    from datetime import datetime
    from backend.core.fanxiu.data_annotation.tasks.dongtian_exchange import dongtian_purchase_cycle
    assert dongtian_purchase_cycle(datetime(2026,9,day)) == expected
