"""洞天兑换：两个页签的业务配置，共用活动兑换执行器。"""
from backend.core.fanxiu.data_annotation.tasks.activity_purchase import ActivityPurchasePolicy
from backend.core.fanxiu.data_annotation.tasks.resource_daily_contract import tuesday_purchase_cycle

STAGE_ID = 'dongtian-purchase'
STAGE_VERSION = '1'
SHOP_SCENE = 840
SHOP_BASE_ID = 620000
TONGBAO_CURRENCY_ID = 37130501
TAIXU_CURRENCY_ID = 37130502
TONGBAO = ActivityPurchasePolicy('洞天通宝兑换',840,279,'洞天',620000,37130501,{
    21000033:(390037007,100,None,25),
    21000014:(37131303,400,20,1),
    21000015:(37131303,800,40,1),
    21000017:(37130505,10,50,10),
},repeated_row_template=True)
TAIXU = ActivityPurchasePolicy('洞天太虚兑换',840,279,'洞天',620000,37130502,{
    21000004:(17003,30,60,10),
    21000005:(37130506,10,50,20),
    21000006:(17011,10,50,20),
    21000007:(17010,10,50,20),
    21000008:(17009,10,50,20),
    21000009:(17012,10,50,20),
},repeated_row_template=True)
plan_dongtian_tongbao_exchange = TONGBAO.plan
plan_dongtian_taixu_exchange = TAIXU.plan
dongtian_purchase_cycle = tuesday_purchase_cycle


def validate_dongtian_purchase_dialog(dialog, action, *, require_quantity=False):
    currency = action.get('currency_type',TONGBAO_CURRENCY_ID)
    policies = {TONGBAO_CURRENCY_ID:TONGBAO,TAIXU_CURRENCY_ID:TAIXU}
    if currency not in policies:
        raise ValueError('未授权币种')
    policies[currency].validate_dialog(dialog,action,action['quantity'] if require_quantity else None)


def purchase_dongtian_resources(context):
    yield from context.go_scene(279)
    if int((yield from context.wait_scene([279],wait=10))) != 279:
        raise RuntimeError('洞天兑换未到达洞天福地')
    yield from context.wait_click(279,'兑换宝阁')
    if int((yield from context.wait_scene([SHOP_SCENE],wait=10))) != SHOP_SCENE:
        raise RuntimeError('洞天兑换未到达兑换宝阁')
    receipts = []
    for tab,policy in (('通宝兑换',TONGBAO),('太虚兑换',TAIXU)):
        yield from context.wait_click(SHOP_SCENE,tab)
        receipts.append({'tab':tab,**(yield from policy.buy_current_shop(context))})
    yield from context.go_scene(34)
    if int((yield from context.wait_scene([34],wait=15))) != 34:
        raise RuntimeError('洞天兑换未返回世界')
    return {'result':'success','outcome':'complete','tabs':receipts,'final_scene':34}
