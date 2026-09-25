"""洞天购买：通宝、太虚白名单兑换与闭环。

商品事实来自活动商店 Runtime，GUI 只负责定位；同名折扣档按 goods_id 区分。
由资源_每日处理在周二调用。每次重入依据服务端 purchased_count 重新规划。
"""
from __future__ import annotations

from typing import Any, Mapping

STAGE_ID = "dongtian-purchase"
STAGE_VERSION = "1"


class DongtianOfferNotVisible(RuntimeError):
    """No safe visible candidate; scrolling is allowed before another observation."""


SHOP_BASE_ID = 620000
TONGBAO_CURRENCY_ID = 37130501
SHOP_SCENE = 840
# goods_id: (item_id, unit_price, discount)
AUTHORIZED_GOODS = {
    21000033: (390037007, 100, None),
    21000014: (37131303, 400, 20),
    21000015: (37131303, 800, 40),
    21000017: (37130505, 10, 50),
}

TAIXU_CURRENCY_ID = 37130502
TAIXU_AUTHORIZED_GOODS = {
    21000004: (17003, 30, 60),
    21000005: (37130506, 10, 50),
    21000006: (17011, 10, 50),
    21000007: (17010, 10, 50),
    21000008: (17009, 10, 50),
    21000009: (17012, 10, 50),
}


def plan_dongtian_taixu_exchange(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Plan only the six discounted material offers, excluding unlimited rows."""
    return _plan_exchange(snapshot, TAIXU_CURRENCY_ID, TAIXU_AUTHORIZED_GOODS)


def plan_dongtian_tongbao_exchange(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return only authorized, still-unpurchased finite offers, or fail closed."""
    return _plan_exchange(snapshot, TONGBAO_CURRENCY_ID, AUTHORIZED_GOODS)


def _plan_exchange(snapshot, currency, authorized):
    if snapshot.get('complete') is not True or snapshot.get('shop_base_id') != SHOP_BASE_ID:
        raise ValueError('洞天商店身份或完整性不符')
    if snapshot.get('currency_types') != [currency]:
        raise ValueError('兑换货币不符')
    actions = []
    seen = set()
    for row in snapshot['items']:
        goods_id = row['goods_id']
        if goods_id not in authorized:
            continue
        if goods_id in seen:
            raise ValueError('授权商品 ID 重复')
        seen.add(goods_id)
        signature = (row['item_id'], row['token_cost'], row.get('discount'))
        if signature != authorized[goods_id] or row['currency_type'] != currency:
            raise ValueError(f'商品 {goods_id} 配置已变化')
        limit, bought = row['purchase_limit'], row['purchased_count']
        if not isinstance(limit, int) or not isinstance(bought, int) or not 0 <= bought <= limit:
            raise ValueError(f'商品 {goods_id} 限购状态无效')
        if bought < limit:
            actions.append({**row, 'quantity': limit - bought})
    if seen != set(authorized):
        raise ValueError('白名单商品缺失，需核对售罄隐藏或版本变化')
    return actions


def validate_dongtian_purchase_dialog(dialog: Mapping[str, Any], action: Mapping[str, Any],
                                     *, require_quantity: bool = False) -> None:
    """Validate exact product/currency/price before any irreversible click."""
    goods_id = action['goods_id']
    currency = action.get('currency_type', TONGBAO_CURRENCY_ID)
    authorized = {TONGBAO_CURRENCY_ID: AUTHORIZED_GOODS, TAIXU_CURRENCY_ID: TAIXU_AUTHORIZED_GOODS}.get(currency, {})
    if goods_id not in authorized:
        raise ValueError('未授权商品')
    item_id, price, _ = authorized[goods_id]
    if dialog.get('complete') is not True or dialog.get('identity_complete') is not True:
        raise ValueError('购买框 Runtime 身份不完整')
    if (dialog.get('goods_id'), dialog.get('item_id'), dialog.get('cost_item_id'),
            dialog.get('Price')) != (goods_id, item_id, currency, price):
        raise ValueError('购买框与授权商品不一致')
    quantity = action['quantity']
    if not 0 < quantity <= dialog['maxNum'] or dialog['HadPrice'] < quantity * price:
        raise ValueError('限购或兑换货币不足')
    if dialog.get('CanBuy') is not True or dialog.get('isEnough') is not True:
        raise ValueError('当前商品不可购买')
    if require_quantity and dialog['showNum'] != quantity:
        raise ValueError('购买数量尚未对齐')


def open_visible_dongtian_offer(context: Any, action: Mapping[str, Any], *, shop_snapshot=None):
    """Open a visible candidate; Runtime, not OCR, authorizes the resulting dialog.

    Row geometry comes from the annotated repeated card. OCR only locates a
    candidate inside the viewport. Pool indices do not prove visibility.
    Missing/ambiguous candidates stop without a purchase; callers may scroll
    using the standard list gesture and obtain a new observation.
    """
    from backend.core.fanxiu.data_annotation.ocr_spatial import group_ocr_tokens
    from backend.core.fanxiu.runtime_gui.exchange_shop import resolve_exchange_shop_item
    from backend.core.fanxiu.instrumentation.common_shop_buy_dialog import read_common_shop_buy_dialog_snapshot

    scene = yield from context.wait_scene([SHOP_SCENE], wait=5)
    if int(scene) != SHOP_SCENE:
        raise RuntimeError('不在洞天兑换宝阁')
    tokens = tuple(context.ocr_tokens_in_shapes(SHOP_SCENE, ('商品列表',), crop=True))
    lines = tuple(group_ocr_tokens(tokens))
    container = context.shape(SHOP_SCENE, '商品列表').box()
    template = context.shape(SHOP_SCENE, '商品列表/商品模板').box()
    name_box = context.shape(SHOP_SCENE, '商品列表/商品模板/名称').box()
    name_offset = name_box['y'] - template['y']
    rows = [dict(x=template['x'], y=line['y']-name_offset,
                 w=template['w'], h=template['h'])
            for line in lines if str(line.get('text','')).replace(' ','') == action['name']]
    try:
        try:
            target = resolve_exchange_shop_item(
                (*lines, *(t for t in tokens if str(t.get('text','')).isdigit())),
                product_list_box=container, product_row_boxes=rows,
                expected_name=action['name'], expected_unit_price=action['token_cost'])
        except RuntimeError:
            if shop_snapshot is None:
                raise
            planner = (plan_dongtian_taixu_exchange if action['currency_type'] == TAIXU_CURRENCY_ID
                       else plan_dongtian_tongbao_exchange)
            planner(shop_snapshot)
            from backend.core.fanxiu.runtime_gui.exchange_shop import resolve_ordered_exchange_candidate
            target = resolve_ordered_exchange_candidate(
                lines, items=shop_snapshot['items'], goods_id=action['goods_id'],
                product_list_box=container, row_height=template['h'])
    except RuntimeError as exc:
        raise DongtianOfferNotVisible(str(exc)) from exc
    context.click_frame_point(SHOP_SCENE, target.x, target.y)
    landed = yield from context.wait_scene([566], wait=12)
    if int(landed) != 566:
        raise RuntimeError('未进入购买框，停止')
    snapshot = read_common_shop_buy_dialog_snapshot()
    validate_dongtian_purchase_dialog(snapshot, action)
    return snapshot


def purchase_open_dongtian_offer(context: Any, action: Mapping[str, Any]):
    """Purchase an already opened authorized offer once, then verify server counts.

    No retry surrounds confirmation. An uncertain result requires fresh shop
    planning, never reusing the previous action's quantity.
    """
    from backend.core.fanxiu.instrumentation.common_shop_buy_dialog import read_common_shop_buy_dialog_snapshot
    from backend.core.fanxiu.instrumentation.activity_shop import collect_activity_shop_runtime
    from backend.core.fanxiu.data_annotation.tasks.common_shop_quantity import set_verified_common_shop_quantity

    before = read_common_shop_buy_dialog_snapshot()
    validate_dongtian_purchase_dialog(before, action)
    if before['showNum'] != action['quantity']:
        proof = yield from set_verified_common_shop_quantity(
            context, action['quantity'], unit_price=action['token_cost'],
            label='洞天兑换', initial_snapshot=before)
        before = proof['snapshot']
    validate_dongtian_purchase_dialog(before, action, require_quantity=True)
    context.click_shape_center(566, '购买')
    landed = yield from context.wait_scene([SHOP_SCENE], wait=15)
    if int(landed) != SHOP_SCENE:
        raise RuntimeError('购买已发送但落点未确认，禁止重发')
    after = collect_activity_shop_runtime(shop_base_id=SHOP_BASE_ID,
                                         expected_currency_type=action['currency_type'])
    matches = [r for r in after['items'] if r['goods_id'] == action['goods_id']]
    expected = action['purchased_count'] + action['quantity']
    if len(matches) != 1 or matches[0]['purchased_count'] != expected:
        raise RuntimeError('购买已发送但服务端已购次数未闭环，禁止重发')
    return after



def dongtian_purchase_cycle(moment):
    """Tuesday business occurrence only; Monday 10:00 opening is already past.

    Use the parent's frozen job_now (including planned-time attempts), not the
    wall clock. Weekly receipts suppress repeated successful Tuesday attempts.
    """
    from datetime import timedelta
    if moment.weekday() != 1:
        return None
    monday = moment.date() - timedelta(days=1)
    return f"week:{monday.isoformat()}"


def purchase_dongtian_resources(context):
    """Visit both tabs, exhaust authorized remaining limits, return to world #34.

    GUI candidates only open dialogs; exact Runtime identity gates confirmation.
    Scroll retries never include a purchase or uncertain confirmation. A failed
    run leaves no completion receipt; reentry plans from current server counts.
    """
    from backend.core.fanxiu.instrumentation.activity_shop import collect_activity_shop_runtime

    yield from context.go_scene(SHOP_SCENE)
    if int((yield from context.wait_scene([SHOP_SCENE], wait=10))) != SHOP_SCENE:
        raise RuntimeError('洞天购买未到达兑换宝阁')
    receipts = []
    for tab, currency, planner in (
        ('通宝兑换', TONGBAO_CURRENCY_ID, plan_dongtian_tongbao_exchange),
        ('太虚兑换', TAIXU_CURRENCY_ID, plan_dongtian_taixu_exchange),
    ):
        yield from context.wait_click(SHOP_SCENE, tab)
        snapshot = collect_activity_shop_runtime(shop_base_id=SHOP_BASE_ID,
                                                expected_currency_type=currency)
        while actions := planner(snapshot):
            action = actions[0]
            # A tab may retain its scroll offset. First inspect the current
            # viewport, then reset to top and scan down with bounded gestures.
            try:
                yield from open_visible_dongtian_offer(context, action, shop_snapshot=snapshot)
            except DongtianOfferNotVisible:
                for _ in range(8):
                    yield from context.scroll_shape_content(SHOP_SCENE, '商品列表', direction='up')
                for scan in range(9):
                    try:
                        yield from open_visible_dongtian_offer(context, action, shop_snapshot=snapshot)
                        break
                    except DongtianOfferNotVisible:
                        if scan == 8:
                            raise
                        yield from context.scroll_shape_content(SHOP_SCENE, '商品列表', direction='down')
            snapshot = yield from purchase_open_dongtian_offer(context, action)
        receipts.append({'tab': tab, 'currency': currency, 'remaining': [],
                         'purchased_counts': {r['goods_id']: r['purchased_count']
                                              for r in snapshot['items']}})
    yield from context.go_scene(34)
    if int((yield from context.wait_scene([34], wait=10))) != 34:
        raise RuntimeError('洞天购买完成，但未返回世界 #34')
    return {'result': 'success', 'outcome': 'complete', 'tabs': receipts, 'final_scene': 34}
