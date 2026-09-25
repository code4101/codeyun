"""Read the loaded Daozang shop's selected floor, never another shop's cache."""
from backend.core.fanxiu.catalog.item import load_fanxiu_item_catalog
from backend.core.fanxiu.instrumentation.exchange_shop import project_exchange_shop_numeric_row
from backend.core.fanxiu.instrumentation.runtime_memory import (
    as_int, table_ref, FanxiuRuntimeMemoryError, resolve_lua_global_manager_root, manager_index_fields,
)
from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
import json
from functools import lru_cache
from backend.core.fanxiu.instrumentation.ui_runtime_context import (
    read_ui_runtime_snapshot, active_ui_component_objects, has_ui_object_fields, read_ui_object_field,
)


def read_sect_shop_snapshot():
    """Caller establishes #848; return complete native display order and goods IDs."""
    return read_ui_runtime_snapshot([], _read, fast=True)


@lru_cache(maxsize=1)
def _items():
    return {r['id']: r for r in json.loads((resolve_fanxiu_export_root() /
        'parsed_configs/Item/rows.json').read_text(encoding='utf-8'))}


def resolve_sect_item(item_id, career, items):
    """Resolve the profession-dependent placeholder before matching a book ID."""
    row = items[item_id]
    if (row.get('type'), row.get('subType')) == (999, 24):
        choices = dict(tuple(map(int, pair.split('_'))) for pair in row['effectValue'].split(','))
        if career not in choices:
            raise ValueError('宗门商品职业映射缺失')
        return choices[career]
    return item_id


def _read(c):
    panels = [p for p in active_ui_component_objects(c) if has_ui_object_fields(
        c, p.address, {'curShopType', 'SecondTabIndex', 'itemScrollview', 'ThirdTabGroup'})]
    if len(panels) != 1:
        raise FanxiuRuntimeMemoryError('道藏阁当前页未唯一加载')
    panel = panels[0]
    floor = as_int(read_ui_object_field(c, panel.address, 'SecondTabIndex'))
    scroll = c.reader.fields(read_ui_object_field(c, panel.address, 'itemScrollview'))
    groups, count = c.reader.list_items(scroll.get('ItemInfoList'))
    if not 0 < count == len(groups) <= 100:
        raise FanxiuRuntimeMemoryError('道藏阁商品清单不完整')
    catalog = {int(row['id']): row for row in load_fanxiu_item_catalog(rebuild_missing=False)['cards']}
    methods = frozenset({'LuaRoleMgr', 'Inst_get'})
    def validate(reader, address):
        fields = manager_index_fields(reader, address, methods)
        if as_int(reader.fields(fields.get('inst')).get('gongfaCareer')) not in (1, 2, 3, 4):
            raise FanxiuRuntimeMemoryError('当前功法职业未加载')
    root, _, _ = resolve_lua_global_manager_root(c.memory, manager_key='sect-shop-role',
        state_address=c.binding.state_address, global_name='RoleMgr',
        required_methods=methods, validate=validate)
    role = c.reader.fields(manager_index_fields(c.reader, root, methods).get('inst'))
    career = as_int(role.get('gongfaCareer'))
    rows = []
    for group in groups:
        entries, n = c.reader.list_items(group)
        if n != 1 or len(entries) != 1:
            raise FanxiuRuntimeMemoryError('道藏阁分组商品暂不支持，不能混用商品 ID')
        vo = c.reader.fields(entries[0])
        cfg = table_ref(vo.get('configData'))
        if cfg is None:
            raise FanxiuRuntimeMemoryError('道藏阁缺少配置')
        v = project_exchange_shop_numeric_row(c.reader.table(cfg.address))
        goods, item, price, currency = (as_int(v[i]) for i in (1, 6, 8, 11))
        if goods != as_int(vo.get('goodsId')) or not item or not price or not currency:
            raise FanxiuRuntimeMemoryError('道藏阁商品身份或单价缺失')
        source_item = item
        item = resolve_sect_item(item, career, _items())
        card = catalog.get(item)
        if card is None:
            raise FanxiuRuntimeMemoryError(f'道藏阁物品 {item} 不在目录')
        rows.append(dict(goods_id=goods, item_id=item, source_item_id=source_item, name=card['name'],
            linked_gongfa_id=int(card.get('linked_gongfa_id') or 0),
            floor=as_int(v[4]), goods_num=as_int(v[7]), token_cost=price,
            currency_type=currency, purchase_limit=as_int(v[13]),
            purchased_count=as_int(vo.get('hasBuyTime')) or 0,
            discount=as_int(v[9]) or None, limit_buy=as_int(v[12])))
    if len({r['goods_id'] for r in rows}) != len(rows) or any(r['floor'] != floor for r in rows):
        raise FanxiuRuntimeMemoryError('道藏阁页签或商品唯一性不符')
    return dict(complete=True, floor=floor, career=career, items=rows,
                currency_types=sorted({r['currency_type'] for r in rows}))
