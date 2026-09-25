"""周二道法兑换：先买碎片和五折神器，再用超过一万的剑玉买原价神器。"""
from __future__ import annotations

STAGE_ID = 'daofa-purchase'
STAGE_VERSION = '1'
SHOP_SCENE = 841
SHOP_BASE_ID = 3001
CURRENCY = 16001
RESERVE = 10000
# Ordered priority: goods_id -> item_id, unit price, discount, weekly limit.
OFFERS = {40000001: (20440001, 5000, None, 1),
          40000003: (20431101, 5000, 50, 1),
          40000004: (20431101, 10000, None, 2)}


from backend.core.fanxiu.data_annotation.tasks.activity_purchase import ActivityPurchasePolicy

POLICY = ActivityPurchasePolicy('道法兑换', SHOP_SCENE, 376, r'道\s*法',
    SHOP_BASE_ID, CURRENCY, OFFERS, frozenset({40000004}), RESERVE)
authorized_rows = POLICY.authorized_rows
plan_daofa_purchase = POLICY.plan
validate_dialog = POLICY.validate_dialog
open_offer = POLICY.open_offer
purchase_daofa_resources = POLICY.run
