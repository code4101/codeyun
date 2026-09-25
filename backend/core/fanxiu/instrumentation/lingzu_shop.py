"""Read the currently selected Lingzu shop tab; no game writes or cached UI pointers."""
from functools import lru_cache
import json
from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.instrumentation.ui_runtime_context import (
    read_ui_runtime_snapshot, active_ui_component_objects, has_ui_object_fields, read_ui_object_field)
from backend.core.fanxiu.instrumentation.runtime_memory import table_ref, as_int, FanxiuRuntimeMemoryError
from backend.core.fanxiu.instrumentation.exchange_shop import project_exchange_shop_numeric_row, decode_exchange_shop_config

@lru_cache(maxsize=1)
def item_names():
    rows=json.loads((resolve_fanxiu_export_root()/'parsed_configs/Item/rows.json').read_text(encoding='utf-8'))
    return {int(r['id']):r.get('name_plain') or r.get('name') for r in rows}

def read_lingzu_shop_snapshot():
    """Caller must establish #844. Resolve selected tab anew; reject incomplete membership."""
    return read_ui_runtime_snapshot([], _read, fast=True)

def _read(c):
    panels=[]
    for host in active_ui_component_objects(c):
        if not has_ui_object_fields(c,host.address,{'tabPanelGroup','tabContent'}):
            continue
        tabs=c.reader.fields(read_ui_object_field(c,host.address,'tabPanelGroup'))
        index=as_int(tabs.get('curTabIndex'))
        mapping=c.reader.fields(tabs.get('panelShowComps'))
        storage=table_ref(mapping.get('_dt_'))
        if index is None or storage is None: continue
        table=c.reader.table(storage.address)
        slot=index+1
        value=table['array'][slot] if slot<len(table['array']) else None
        value=value if value is not None else table['fields'].get(slot)
        comp=table_ref(value)
        panel=table_ref(read_ui_object_field(c,comp.address,'m_panel')) if comp else None
        if not panel: continue
        fields=c.reader.fields(panel)
        if fields.get('isShowing') is True and fields.get('hasClose') is False and 'shopScrollview' in fields:
            panels.append(fields)
    if len(panels)!=1:
        raise FanxiuRuntimeMemoryError('灵祖兑换页未唯一加载')
    panel=panels[0]
    scroll=c.reader.fields(panel['shopScrollview'])
    groups,count=c.reader.list_items(scroll.get('ItemInfoList'))
    if count!=len(groups) or count!=as_int(panel.get('listCount')) or not 1<=count<=64:
        raise FanxiuRuntimeMemoryError('灵祖兑换商品列表不完整')
    rows=[]
    for group in groups:
        entries,n=c.reader.list_items(group)
        if n!=1 or len(entries)!=1:
            raise FanxiuRuntimeMemoryError('灵祖商品行结构变化')
        vo=c.reader.fields(entries[0])
        config=table_ref(vo.get('configData'))
        if not config: raise FanxiuRuntimeMemoryError('灵祖商品配置缺失')
        values=project_exchange_shop_numeric_row(c.reader.table(config.address))
        cost=table_ref(values[9])
        if not cost: raise FanxiuRuntimeMemoryError('灵祖商品消耗缺失')
        row=decode_exchange_shop_config(values,cost_values=project_exchange_shop_numeric_row(c.reader.table(cost.address)))
        bought=as_int(vo.get('hasBuyTime'))
        if row['second_tag']!=9 or row['cost_item_id']!=147 or row['goods_id']!=as_int(vo.get('goodsId')) or bought is None or bought<0:
            raise FanxiuRuntimeMemoryError('灵祖商品身份或计数不符')
        rows.append({**row,'name':item_names()[row['item_id']], 'currency_type':147,
                     'token_cost':row['cost_num'],'purchase_limit':row['limit_times'],
                     'purchased_count':bought,'discount':as_int(values[19])})
    if len({r['goods_id'] for r in rows})!=len(rows):
        raise FanxiuRuntimeMemoryError('灵祖商品重复')
    return {'complete':True,'shop_base_id':9,'currency_types':[147],'items':rows}
