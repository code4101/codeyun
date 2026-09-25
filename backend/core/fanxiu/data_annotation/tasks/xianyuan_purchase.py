"""仙缘兑换：只购买灵根和五折誓约匣；尚未配置可选商品。"""
from backend.core.fanxiu.data_annotation.tasks.activity_purchase import ActivityPurchasePolicy

STAGE_ID = 'xianyuan-purchase'
STAGE_VERSION = '1'
# 灵根 Runtime 合并了已领取的 1 次赠送档与 10 次付费档，剩余量由服务端计数决定。
POLICY = ActivityPurchasePolicy(
    label='仙缘兑换',
    shop_scene=842,
    entry_scene=308,
    entry_pattern=r'仙缘.*斗法',
    shop_base_id=4001,
    cost_item_id=19002,
    offers={
    41000033: (390037007,1000,None,11),
    41000001: (19701102,5000,50,1),
})
plan_xianyuan_purchase = POLICY.plan
purchase_xianyuan_resources = POLICY.run
