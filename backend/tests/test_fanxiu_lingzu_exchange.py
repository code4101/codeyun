from datetime import datetime, timedelta
from backend.core.fanxiu.data_annotation.tasks.lingzu_exchange import exchange_policy, OFFERS, PRAYER_GOODS, lingzu_exchange_cycle

def snapshot():
    return {'complete':True,'shop_base_id':9,'currency_types':[147],'items':[
        dict(goods_id=g,item_id=v[0],token_cost=v[1],discount=v[2],purchase_limit=v[3],
             currency_type=147,purchased_count=0) for g,v in OFFERS.items()]}

def test_dynamic_current_mandatory_next_locked_and_reserve():
    for week in range(5):
        moment=datetime(2026,9,22)+timedelta(weeks=week)
        current,next_name,locked,p=exchange_policy(moment)
        assert locked==PRAYER_GOODS[next_name] and locked not in p.offers
        assert p.plan(snapshot(),2000)[0]['goods_id']==PRAYER_GOODS[current]
        assert sum(a['quantity']*a['token_cost'] for a in p.plan(snapshot(),2000))==2000
        actions=p.plan(snapshot(),253322)
        assert actions[0]['goods_id']==PRAYER_GOODS[current]
        assert locked not in [a['goods_id'] for a in actions]
        assert 253322-sum(a['quantity']*a['token_cost'] for a in actions)>=4000
        assert all(a['goods_id'] not in (30014,30015,30016,30017) for a in actions)

def test_monday_releases_expiring_quota_and_occurrences_are_distinct():
    mon=datetime(2026,9,21)
    assert exchange_policy(mon)[2] is None
    assert lingzu_exchange_cycle(mon)!=lingzu_exchange_cycle(mon+timedelta(days=1))
    assert lingzu_exchange_cycle(mon+timedelta(days=2)) is None


def test_scroll_uses_visible_order_without_rewinding_known_top():
    from backend.core.fanxiu.runtime_gui.exchange_shop import exchange_scroll_direction
    items=[dict(goods_id=1,name='灵祖蜕玉自选匣'),dict(goods_id=2,name='万灵自选宝匣'),dict(goods_id=3,name='洗灵奇石')]
    assert exchange_scroll_direction([dict(text='万灵自选宝匣',y=200)],items=items,goods_id=3)=='down'
    assert exchange_scroll_direction([dict(text='洗灵奇石',y=200)],items=items,goods_id=1)=='up'
    assert exchange_scroll_direction([dict(text='洗灵奇石',y=200)],items=items,goods_id=3)=='visible'


def test_lingzu_current_price_is_right_of_row_midpoint():
    from backend.core.fanxiu.runtime_gui.exchange_shop import resolve_exchange_shop_item
    lines=[dict(text='灵祖蜕玉自选匣',x=232,y=257,w=244,h=35),
           dict(text='5000',x=449,y=323,w=60,h=24),
           dict(text='10000',x=555,y=323,w=95,h=24)]
    args=dict(product_list_box=dict(x=50,y=226,w=803,h=1033),
              product_row_boxes=[dict(x=62,y=242,w=783,h=143)],
              expected_name='灵祖蜕玉自选匣',current_price_right_ratio=0.6)
    assert resolve_exchange_shop_item(lines,expected_unit_price=5000,**args).current_unit_price==5000
    import pytest
    with pytest.raises(RuntimeError):
        resolve_exchange_shop_item(lines,expected_unit_price=10000,**args)
