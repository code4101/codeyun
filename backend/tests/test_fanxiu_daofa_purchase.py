import pytest
from backend.core.fanxiu.data_annotation.tasks.daofa_purchase import plan_daofa_purchase, validate_dialog


def shop(bought=(0,0,0)):
    specs=[(40000001,20440001,5000,None,1),(40000003,20431101,5000,50,1),
           (40000004,20431101,10000,None,2)]
    return dict(complete=True,shop_base_id=3001,currency_types=[16001],items=[
        dict(goods_id=g,item_id=i,token_cost=p,discount=d,purchase_limit=l,
             purchased_count=b,currency_type=16001)
        for (g,i,p,d,l),b in zip(specs,bought)])


@pytest.mark.parametrize('balance,optional',[(27050,0),(29999,0),(30000,1),(39999,1),(40000,2),(99999,2)])
def test_reserve_after_mandatory(balance,optional):
    actions=plan_daofa_purchase(shop(),balance)
    assert [a['goods_id'] for a in actions[:2]] == [40000001,40000003]
    assert sum(a['quantity'] for a in actions if a['goods_id']==40000004)==optional


def test_reentry_and_insufficient_mandatory_funds():
    assert plan_daofa_purchase(shop((1,1,0)),17050)==[]
    assert plan_daofa_purchase(shop((1,1,1)),20000)[0]['quantity']==1
    assert plan_daofa_purchase(shop((1,1,2)),50000)==[]
    with pytest.raises(ValueError): plan_daofa_purchase(shop(),9999)


def test_confirmation_reserve_and_currency():
    row=shop()['items'][2]
    dialog=dict(complete=True,identity_complete=True,goods_id=40000004,item_id=20431101,
                cost_item_id=16001,Price=10000,maxNum=2,showNum=1,HadPrice=20000,
                CanBuy=True,isEnough=True)
    validate_dialog(dialog,row,1)
    dialog['HadPrice']=19999
    with pytest.raises(ValueError): validate_dialog(dialog,row,1)
    dialog['HadPrice']=20000
    dialog['cost_item_id']=1
    with pytest.raises(ValueError): validate_dialog(dialog,row,1)
