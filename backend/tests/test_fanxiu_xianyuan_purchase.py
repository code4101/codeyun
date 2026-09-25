from backend.core.fanxiu.data_annotation.tasks.xianyuan_purchase import POLICY


def test_only_remaining_linggen_and_half_price_oath():
    rows = [dict(goods_id=g,item_id=i,token_cost=p,discount=d,purchase_limit=l,
                 purchased_count=1 if g==41000033 else 0,currency_type=19002)
            for g,(i,p,d,l) in POLICY.offers.items()]
    rows.append(dict(goods_id=41000002,item_id=19701102,token_cost=10000,discount=None,
                     purchase_limit=1,purchased_count=0,currency_type=19002))
    snapshot=dict(complete=True,shop_base_id=4001,currency_types=[19002],items=rows)
    actions=POLICY.plan(snapshot,18901)
    assert [a['quantity'] for a in actions]==[10,1]
    assert sum(a['quantity']*a['token_cost'] for a in actions)==15000
    for row in rows[:2]: row['purchased_count']=row['purchase_limit']
    assert POLICY.plan(snapshot,3901)==[]
