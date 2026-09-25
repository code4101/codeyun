"""宗门兑换：每周一先领引魂钟，再集中补足最高未满层的一本功法。"""
from functools import lru_cache
import math
import time

from backend.core.fanxiu.data_annotation.tasks.activity_purchase import ActivityPurchasePolicy
from backend.core.fanxiu.instrumentation.sect_shop import read_sect_shop_snapshot
from backend.core.fanxiu.instrumentation.gongfa_atlas import read_gongfa_atlas_runtime
from backend.core.fanxiu.instrumentation.backpack import read_backpack_item_counts
from backend.core.fanxiu.instrumentation.common_shop_buy_dialog import read_common_shop_buy_dialog_snapshot
from backend.core.fanxiu.data_annotation.tasks.common_shop_quantity import (
    SACRED_SHOP_QUANTITY_ASSETS, set_verified_common_shop_quantity,
)

STAGE_ID = 'sect-exchange'
STAGE_VERSION = '1'
RESERVE = 5000
SHOP = 848
BELL_ITEM = 4070001
FLOORS = {4: '四层', 3: '三层', 2: '二层', 1: '一层'}


def sect_exchange_cycle(moment):
    return moment.date().isoformat() if moment.weekday() == 0 else None


@lru_cache(maxsize=1)
def book_rules():
    from backend.core.fanxiu.catalog.gongfa import load_fanxiu_gongfa_catalog
    return {r['id']: r for r in load_fanxiu_gongfa_catalog(rebuild_missing=False)['cards']}


def select_floor_book(snapshot, atlas, inventory, rules):
    """Only the current floor is considered; None authorizes descending one floor.

    Stored copies count towards completion, so delayed fusion cannot cause
    overbuying. The native progression cost determines the quantity to cap.
    An unlearned book needs one initial copy; unknown rules fail closed.
    """
    if not snapshot.get('complete') or not atlas.get('runtime_complete'):
        raise ValueError('宗门兑换清单或功法进度不完整')
    learned = {r['book_id']: r for r in atlas['books']}
    candidates = []
    for offer in snapshot['items']:
        gid = offer['linked_gongfa_id']
        if not gid:
            continue
        rule = rules[gid]
        if rule['skill_type_name'] not in ('心法', '神通'):
            continue
        stages = [r for kind, rows in rule['progression'].items() if kind.endswith('_jie') for r in rows]
        ranks = {int(r['jie']): r for r in stages}
        if not ranks or set(ranks) != set(range(1, max(ranks) + 1)):
            raise ValueError('功法重数配置不连续')
        current = int(learned.get(gid, {}).get('jie') or 0)
        owned = int(inventory.get(offer['item_id'], 0))
        needed = 1 if current == 0 else 0
        for rank in range(max(2, current + 1), max(ranks) + 1):
            costs = ranks[rank].get('consume_items') or []
            if len(costs) != 1 or int(costs[0]['id']) != offer['item_id']:
                raise ValueError('功法融合消耗不是目标书，拒绝猜测兑换量')
            needed += int(costs[0]['count'])
        missing = max(0, needed - owned)
        if missing:
            candidates.append({**offer, 'current_jie': current, 'max_jie': max(ranks),
                'owned_books': owned, 'missing_books': missing,
                'desired': math.ceil(missing / offer['goods_num'])})
    return min(candidates, key=lambda r: (r['current_jie'], r['goods_id'])) if candidates else None


def offer_policy(row, optional):
    # AllianceShop.currencyType=5 maps to wallet item #9, proven by the live
    # CommonShop dialog. Never confuse this shop enum with an inventory ID.
    if row['currency_type'] != 5 or row['goods_num'] != 1:
        raise ValueError('道藏阁货币或单次产出配置改变')
    gid = row['goods_id']
    return ActivityPurchasePolicy(
        label='宗门兑换',
        shop_scene=SHOP,
        entry_scene=846,
        entry_pattern='宗门',
        shop_base_id=0,
        cost_item_id=9,

        offers={gid: (row['item_id'], row['token_cost'], row['discount'], row['purchase_limit'])},

        optional_goods=frozenset({gid}) if optional else frozenset(),
        reserve=RESERVE,
        repeated_row_template=True, dialog_scene=634, dialog_close='关闭',
        dialog_confirm='兑换（高风险）', current_price_right_ratio=0.65)


def buy_offer(context, snapshot, row, *, optional, initial_dialog=None):
    from backend.core.fanxiu.instrumentation.wallet import read_wallet_currency_snapshot
    wallet = read_wallet_currency_snapshot(5)
    balance = int(wallet['exchange_currency'])
    if optional and balance - RESERVE < row['token_cost']:
        return {'quantity': 0, 'balance': balance, 'reason': 'reserve_or_price_limit'}
    policy = offer_policy(row, optional)
    dialog = initial_dialog or (yield from policy.open_offer(context, row, snapshot))
    policy.validate_dialog(dialog, row)
    if dialog['ShopModelType'] != 5:
        raise ValueError('当前兑换框不是宗门商店')
    available = max(0, dialog['HadPrice'] - (RESERVE if optional else 0)) // row['token_cost']
    desired = row['desired'] if optional else row['purchase_limit'] - row['purchased_count']
    if not optional and available < desired:
        raise ValueError('宗门贡献不足以兑换本周引魂钟')
    quantity = min(desired, available, dialog['maxNum'])
    if quantity <= 0:
        yield from context.wait_click(634, '关闭')
        return {'quantity': 0, 'balance': dialog['HadPrice']}
    if dialog['showNum'] != quantity:
        proof = yield from set_verified_common_shop_quantity(context, quantity,
            unit_price=row['token_cost'], label=row['name'], initial_snapshot=dialog,
            assets=SACRED_SHOP_QUANTITY_ASSETS)
        dialog = proof['snapshot']
    policy.validate_dialog(dialog, row, quantity)
    context.click_shape_center(634, '兑换（高风险）')
    if int((yield from context.wait_scene([SHOP], wait=20))) != SHOP:
        raise RuntimeError('宗门兑换确认后落点不明，禁止重发')
    after = read_sect_shop_snapshot()
    matched = [r for r in after['items'] if r['goods_id'] == row['goods_id']]
    if len(matched) != 1 or matched[0]['purchased_count'] != row['purchased_count'] + quantity:
        raise RuntimeError('宗门兑换计数未确认，禁止重发')
    return dict(goods_id=row['goods_id'], item_id=row['item_id'], name=row['name'],
        quantity=quantity, balance=dialog['HadPrice'] - quantity * row['token_cost'])


def open_sect_shop(context):
    match = yield from context.wait_scene([SHOP, 847, 846, 845, 34], wait=5, required=False)
    current = int(match) if match else None
    if current == SHOP:
        return
    if current not in (847, 846, 845):
        from backend.core.fanxiu.data_annotation.tasks.world_menu_navigation import open_world_menu_function
        match = yield from open_world_menu_function(context, '宗门', expected_scene_ids=[845, 846])
        current = int(match)
    if current == 845:
        yield from context.wait_click(845, '功能')
        current = int((yield from context.wait_scene([846], wait=15)))
    if current == 846:
        yield from open_daozang_card(context)
        current = int((yield from context.wait_scene([847], wait=15)))
    if current != 847:
        raise RuntimeError('宗门道藏阁入口未确认')
    yield from context.wait_click(847, '前往道藏阁')
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        match = yield from context.wait_scene([SHOP], wait=5, required=False)
        if match is not None and int(match) == SHOP:
            return
        yield from context.wait_action_settle(1)
    raise RuntimeError('宗门道藏阁寻路未完成')


def open_daozang_card(context):
    """Locate the unique title inside the annotated scroll viewport afresh.

    Full-frame OCR preserves the low title that parent-envelope recognition
    dropped in the live page. The description contains the same word, so only
    an exact title line may authorize a click.
    """
    from backend.core.fanxiu.data_annotation.ocr_spatial import group_ocr_tokens
    from backend.core.fanxiu.runtime_gui.text import normalize_ocr_name
    box = context.shape(846, '功能列表').box()
    direction = 'down'
    for _ in range(8):
        match = yield from context.wait_scene([846], wait=5)
        if int(match) != 846:
            raise RuntimeError('宗门功能列表不在当前页')
        lines = group_ocr_tokens(context.full_frame_ocr_tokens(match.frame_data_url))
        titles = [r for r in lines if normalize_ocr_name(r['text']) == '道藏阁'
            and box['x'] <= r['x'] and r['x'] + r['w'] <= box['x'] + box['w']
            and box['y'] <= r['y'] and r['y'] + r['h'] <= box['y'] + box['h']]
        if len(titles) > 1:
            raise RuntimeError('道藏阁标题不唯一')
        if titles:
            title = titles[0]
            context.click_frame_point(846, title['x'] + title['w']/2, title['y'] + title['h']/2)
            return
        changed = yield from context.scroll_shape_content(846, '功能列表', direction=direction)
        if not changed:
            if direction == 'up':
                break
            direction = 'up'
    raise RuntimeError('宗门功能列表未找到道藏阁')


def exchange_sect_resources(context):
    yield from open_sect_shop(context)
    yield from context.wait_click(SHOP, '四层')
    yield from context.wait_action_settle(1)
    snapshot = read_sect_shop_snapshot()
    if snapshot['floor'] != 4:
        raise RuntimeError('道藏阁未切至四层')
    bells = [r for r in snapshot['items'] if r['item_id'] == BELL_ITEM]
    if len(bells) != 1 or (bells[0]['token_cost'], bells[0]['purchase_limit'], bells[0]['limit_buy']) != (5000, 1, 2):
        raise ValueError('引魂钟每周限购配置不符')
    purchases = []
    if bells[0]['purchased_count'] < 1:
        purchases.append((yield from buy_offer(context, snapshot, bells[0], optional=False)))
        snapshot = read_sect_shop_snapshot()
    atlas = read_gongfa_atlas_runtime()
    selected = None
    for floor in (4, 3, 2, 1):
        if snapshot['floor'] != floor:
            yield from context.wait_click(SHOP, FLOORS[floor])
            yield from context.wait_action_settle(1)
            snapshot = read_sect_shop_snapshot()
        if snapshot['floor'] != floor:
            raise RuntimeError('道藏阁页签与清单不符')
        ids = [r['item_id'] for r in snapshot['items'] if r['linked_gongfa_id']]
        counts, _ = read_backpack_item_counts(ids, manager_key='sect-exchange-books')
        selected = select_floor_book(snapshot, atlas, counts, book_rules())
        if selected:
            # One book per occurrence, even if this purchase finishes it and
            # leaves enough contribution for another book. Next week reselects.
            purchases.append((yield from buy_offer(context, snapshot, selected, optional=True)))
            break
    yield from context.wait_click(SHOP, '返回')
    yield from context.wait_action_settle(1)
    yield from context.go_scene(34)
    if int((yield from context.wait_scene([34], wait=20))) != 34:
        raise RuntimeError('宗门兑换未回到世界')
    return dict(result='success', outcome='complete', purchases=purchases,
        selected_book=selected, reserve=RESERVE, final_scene=34)
