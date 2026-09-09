"""只读已打开的自动洗炼设置/运行窗口，不调用 Lua 或改变洗炼状态。

只缓存公共 UI 根；每次重新校验活动成员和实例，关闭/重开均重新定位。
设置页与运行页已通过真实验收；运行结束窗口关闭时 available=False，
该值本身不说明暂停原因，须结合完成弹窗及背包/候选状态判断。
"""
from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import read_ui_object_field, read_ui_runtime_snapshot


def read_spirit_artifact_auto_snapshot() -> dict:
    """返回设置额度与条件，或运行中累计消耗；未打开返回 available=False。"""
    def read(ctx):
        found = []
        # 两个目标都是顶层窗口；不展开世界页、灵器列表等无关子组件。
        storage = ctx.reader.table(ctx.binding.component_storage_address)
        panels = {}
        for raw in [*storage['array'], *storage['fields'].values()]:
            group = table_ref(raw)
            if group is None:
                continue
            members, count = ctx.reader.list_items(group)
            if not count:
                continue
            if len(members) != count:
                raise FanxiuRuntimeMemoryError('活动窗口列表不完整')
            component = table_ref(members[-1])
            panel = table_ref(read_ui_object_field(ctx, component.address, 'm_panel')) if component else None
            if panel:
                panels[panel.address] = panel
        for obj in panels.values():
            field = lambda key: read_ui_object_field(ctx, obj.address, key)
            is_settings = table_ref(field('SliderNum')) and table_ref(field('KeepBtn'))
            is_running = table_ref(field('txtAlearyCount')) and table_ref(field('btnCommon'))
            if not is_settings and not is_running:
                continue
            item = ctx.reader.long(field('itemUId' if is_settings else 'itemUid'))
            if not item:
                raise FanxiuRuntimeMemoryError('自动洗炼窗口缺少实例')
            row = {'available': True, 'mode': 'settings' if is_settings else 'running',
                   'item_id': str(item), 'material_id': as_int(field('costItemId'))}
            keys = {'useNum': 'budget', 'useMax': 'maximum', 'totalCostNum': 'per_cost'} if is_settings else {
                'costMaxNum': 'budget', 'perCost': 'per_cost', 'totalCost': 'consumed', 'initHadNum': 'initial_inventory'}
            for key, name in keys.items():
                row[name] = as_int(field(key))
                if row[name] is None:
                    raise FanxiuRuntimeMemoryError(f'自动洗炼缺少 {key}')
            if is_settings:
                for key, name in [('qualityDataList', 'quality_options'), ('attrDataList', 'attribute_options')]:
                    values, count = ctx.reader.list_items(field(key))
                    if count is None or len(values) != count:
                        raise FanxiuRuntimeMemoryError(f'自动洗炼选项不完整 {key}')
                    row[name] = [{k: ctx.reader.fields(v).get(k) for k in
                                  ('keepKey', 'isSelect', 'isLock', 'isSpecial', 'desc')} for v in values]
            found.append(row)
        if len(found) > 1:
            raise FanxiuRuntimeMemoryError('自动洗炼窗口不唯一')
        return {**(found[0] if found else {'available': False}),
                'pid': ctx.memory.pid, 'process_start_ticks': ctx.memory.process_start_ticks}
    return read_ui_runtime_snapshot([], read, fast=True)
