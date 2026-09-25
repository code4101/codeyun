from datetime import datetime

import pytest

from backend.core.fanxiu.data_annotation.tasks.sect_exchange import (
    select_floor_book, sect_exchange_cycle, offer_policy,
)
from backend.core.fanxiu.instrumentation.sect_shop import resolve_sect_item
from backend.core.fanxiu.runtime_gui.exchange_shop import (
    normalize_exchange_product_name, exchange_scroll_direction,
)


def inputs():
    offers = [dict(goods_id=i, item_id=100+i, linked_gongfa_id=i, goods_num=1)
              for i in (1, 2)]
    rules = {i: dict(skill_type_name='心法', progression={'renjie_jie': [
        dict(jie=j, consume_items=[] if j == 1 else [dict(id=100+i, count='1')])
        for j in range(1, 5)]}) for i in (1, 2)}
    return dict(complete=True, items=offers), dict(runtime_complete=True,
        books=[dict(book_id=1, jie=2), dict(book_id=2, jie=3)]), rules


def test_lowest_book_and_owned_copies_prevent_overbuy():
    shop, atlas, rules = inputs()
    result = select_floor_book(shop, atlas, {101: 1}, rules)
    assert (result['goods_id'], result['desired']) == (1, 1)
    assert select_floor_book(shop, atlas, {101: 2, 102: 1}, rules) is None


def test_unlearned_and_missing_rules_are_not_treated_as_full():
    shop, atlas, rules = inputs()
    atlas['books'] = []
    assert select_floor_book(shop, atlas, {}, rules)['desired'] == 4
    with pytest.raises(KeyError):
        select_floor_book(shop, atlas, {}, {})


def test_monday_only_and_profession_alias():
    assert sect_exchange_cycle(datetime(2026, 9, 28)) == '2026-09-28'
    assert sect_exchange_cycle(datetime(2026, 9, 29)) is None
    items = {100: dict(type=999, subType=24, effectValue='1_101,2_102')}
    assert resolve_sect_item(100, 2, items) == 102
    with pytest.raises(ValueError):
        resolve_sect_item(100, 3, items)


def test_learned_badges_preserve_native_order_alignment():
    assert normalize_exchange_product_name('心法寂灭天地已[学习]') == normalize_exchange_product_name('心法·寂灭天地')
    items = [dict(goods_id=1, name='心法·寂灭天地'), dict(goods_id=2, name='心法·五雷正法')]
    assert exchange_scroll_direction([dict(text='心法寂灭天地已[学习]', y=100)],
        items=items, goods_id=2) == 'down'


def test_reserve_applies_only_to_books_and_wallet_enum_is_not_item_id():
    row = dict(goods_id=1, item_id=101, token_cost=7500, discount=None,
               purchase_limit=-1, currency_type=5, goods_num=1)
    policy = offer_policy(row, True)
    dialog = dict(complete=True, identity_complete=True, goods_id=1, item_id=101,
        cost_item_id=9, Price=7500, HadPrice=118650, maxNum=15,
        showNum=15, CanBuy=True, isEnough=True)
    policy.validate_dialog(dialog, row, 15)
    with pytest.raises(ValueError):
        policy.validate_dialog({**dialog, 'HadPrice': 117499}, row, 15)
