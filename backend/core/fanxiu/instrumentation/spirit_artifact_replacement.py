"""只读更换部件窗口的有界结构发现；不导航、不点击，不猜可见行或几何。

首次716实测尚待完成。公开输出只作定位研发证据；complete/selection_ready
始终False，直到可见顺序与屏幕位置有真实验证，调用方不得据此盲点。
"""
from __future__ import annotations

import time

from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import read_ui_object_field, read_ui_runtime_snapshot


def read_spirit_artifact_replacement_snapshot(*, ware_id: int, part: int) -> dict:
    """匹配已加载ChangePartView，仅读ItemInfoList/ItemClassDic各最多64项。

    scroll_fields/structure给当前容器与实例行字段名、类型及简单值；
    rows给由v_bagId或ItemVO.id实际读出的实例身份，不把遍历顺序当UI顺序。
    不访问全进程，不执行Lua初始化。缺集合或达到预算保留诊断，不宣称完整。
    再用独立UI上下文检查同窗口根与进程，库存对照验证发现的UID归属。
    """
    if ware_id <= 0 or part not in range(1, 7):
        raise ValueError('更换列表读取需要明确灵器及部位')

    def locate(ctx):
        reader = ctx.reader
        def field(obj, name):
            return read_ui_object_field(ctx, obj.address, name) if obj else None
        registry = reader.table(ctx.binding.component_storage_address)
        found = {}
        for value in [*registry['array'], *registry['fields'].values()]:
            ref = table_ref(value)
            if ref is None:
                continue
            members, count = reader.list_items(ref)
            if not count or len(members) != count:
                continue
            outer = table_ref(field(table_ref(members[-1]), 'm_panel'))
            if (as_int(field(outer, 'v_wareId')) != ware_id
                    or as_int(field(outer, 'v_partId')) != part):
                continue
            scroll = table_ref(field(outer, 'scrollview'))
            if scroll is not None and table_ref(field(outer, 'emptyMask')) is not None:
                found[outer.address] = (outer, scroll)
        if len(found) != 1:
            raise FanxiuRuntimeMemoryError(f'当前目标更换窗口必须唯一，实际{len(found)}')
        return next(iter(found.values()))

    def read(ctx):
        reader = ctx.reader
        outer, scroll = locate(ctx)
        def summary(value):
            if table_ref(value) is not None:
                return {'type': 'table'}
            if value is None or type(value) in (bool, int, float):
                return {'type': type(value).__name__, 'value': value}
            if isinstance(value, str):
                return {'type': 'str', 'value': value[:160]}
            return {'type': type(value).__name__}
        # 716实测：ItemInfoList 是count/_dt_ CList；ItemClassDic是实例池CDictionary。
        # 只走这两条真实成员链，不再遍历Inst、原型或任意子表。
        scroll_fields = reader.fields(scroll)
        values, count = reader.list_items(scroll_fields.get('ItemInfoList'))
        instances = reader.dictionary_fields(scroll_fields.get('ItemClassDic'))
        if count is None or count != len(values) or not 0 <= count <= 64 or len(instances) > 64:
            raise FanxiuRuntimeMemoryError('更换列表数据计数不完整或超过64项读取上限')
        rows, ordered, structure = [], [], []
        for index, value in enumerate(values):
            data = reader.fields(value)
            uid = reader.long(data.get('id'))
            if not uid:
                raise FanxiuRuntimeMemoryError('更换列表有序数据缺少实际UID')
            ordered.append({'index': index, 'item_id': str(uid)})
        if len({row['item_id'] for row in ordered}) != count:
            raise FanxiuRuntimeMemoryError('更换列表有序数据UID重复')
        for pool_key, value in instances.items():
            fields = reader.fields(value)
            uid = reader.long(fields.get('v_bagId'))
            structure.append({'path': f'ItemClassDic[{pool_key}]',
                'fields': {str(k): summary(v) for k, v in fields.items()}})
            if not uid:
                continue
            row_part = as_int(fields.get('v_partId'))
            if row_part != part:
                raise FanxiuRuntimeMemoryError('更换列表实例池行部位与窗口冲突')
            root = table_ref(fields.get('root'))
            root_fields = reader.fields(root) if root else {}
            index = next((r['index'] for r in ordered if r['item_id'] == str(uid)), None)
            rows.append({'pool_key': str(pool_key), 'kind': 'instantiated_row',
                'item_id': str(uid), 'data_index': index, 'part': row_part,
                'is_equipped': fields.get('v_isEquiped'), 'root_present': root is not None,
                'root_fields': {str(k): summary(v) for k, v in root_fields.items()}})
        return {'ware_id': ware_id, 'part': part, 'pid': ctx.memory.pid,
            'process_start_ticks': ctx.memory.process_start_ticks,
            'scroll_fields': {str(k): summary(v) for k, v in scroll_fields.items()},
            'structure': structure, 'ordered_items': ordered, 'data_count': count,
            'row_instance_count': len(instances), 'rows': rows,
            'truncated': False, 'data_complete': True,
            '_generation': (outer.address, scroll.address)}

    result = read_ui_runtime_snapshot([], read)
    from .spirit_artifact import read_spirit_artifact_inventory_runtime
    inventory = read_spirit_artifact_inventory_runtime()
    if (inventory.get('complete') is not True or (inventory['pid'], inventory['process_start_ticks']) !=
            (result['pid'], result['process_start_ticks'])):
        raise FanxiuRuntimeMemoryError('更换列表与完整库存进程身份不一致')
    by_id = {r['item_id']: r for r in inventory['items']}
    for row in result['rows']:
        item = by_id.get(row['item_id'])
        if item is None or (item['ware_id'], item['part']) != (ware_id, part):
            raise FanxiuRuntimeMemoryError('更换列表行UID与库存目标归属不一致')
        row['item'] = item

    def guard(ctx):
        outer, scroll = locate(ctx)
        return (ctx.memory.pid, ctx.memory.process_start_ticks, (outer.address, scroll.address))
    if read_ui_runtime_snapshot([], guard) != (result['pid'], result['process_start_ticks'], result.pop('_generation')):
        raise FanxiuRuntimeMemoryError('读取期间更换窗口或进程代际变化')
    return {**result, 'source': 'active_spiritware_change_part_view_bounded_discovery',
            'captured_at': time.time(), 'read_only': True, 'identity_complete': True,
            'complete': False, 'selection_ready': False,
            'limitation': '行顺序/可见性/几何尚未实测；不得据遍历顺序点击'}
