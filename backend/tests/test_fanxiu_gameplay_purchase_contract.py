"""Pure currency identity and reserve contracts; no simulated GUI flow."""
import pytest
from backend.core.fanxiu.data_annotation.tasks.gameplay_exchange_purchase import GameplayExchangePurchasePolicy


def test_wallet_enum_cannot_substitute_for_currency_item_identity():
    policy = GameplayExchangePurchasePolicy('虚天',739,739,'',80000,15,
        {1010845:(29601,1000,None,20)},reserve=18000)
    row = {'goods_id':1010845,'token_cost':1000}
    dialog = dict(complete=True,identity_complete=True,goods_id=1010845,
        item_id=29601,cost_item_id=15,Price=1000,maxNum=20,showNum=20,
        CanBuy=True,isEnough=True,HadPrice=38000)
    policy.validate_dialog(dialog,row,20)
    with pytest.raises(ValueError,match='商品、币种或价格不符'):
        policy.validate_dialog({**dialog,'cost_item_id':12},row,20)
    with pytest.raises(RuntimeError,match='保留额'):
        policy.validate_dialog({**dialog,'HadPrice':37999},row,20)
