"""Current dedicated 仙纹 experience resources; no loading or consumption."""
from datetime import datetime

from .runtime_memory import (
    FanxiuRuntimeMemoryError, as_int, manager_index_fields,
    resolve_lua_global_manager_root, table_ref,
)
from .ui_runtime_context import (
    acquire_ui_runtime_context, active_ui_component_objects, has_ui_object_fields,
    read_ui_object_field, read_ui_runtime_snapshot,
)
from .item_config import _DB_METHODS, _item_config_table
from .resource_auto_use import read_runtime_config_rows
from .backpack import read_backpack_item_counts


def read_xianqiao_upgrade_resources_snapshot() -> dict:
    """Read naturally loaded CoreWareItem rates and current dedicated inventory.

    The client's GetExpItemSupplyExpNum reads Core.CoreWareItem.param. These
    items feed equipped 仙纹 sumExp; they do not represent role cultivation or
    the expenditure needed to open a new hole. Only positive integral rates
    with type=1 are supported. A complete snapshot proves available resources,
    not which equipment to upgrade or permission to feed/discard old equipment.
    """
    context = acquire_ui_runtime_context(())
    root, _, environment = resolve_lua_global_manager_root(
        context.memory, manager_key='xianqiao-upgrade-config',
        state_address=context.binding.state_address, global_name='DBMgr',
        required_methods=_DB_METHODS, validate=_item_config_table,
    )
    manager = manager_index_fields(context.reader, root, _DB_METHODS)
    instance = context.reader.fields(manager.get('inst'))
    config_dict = context.reader.fields(instance.get('ConfigDic'))
    configs = table_ref(config_dict.get('_dt_'))
    if configs is None:
        raise FanxiuRuntimeMemoryError('仙纹经验配置字典未自然加载')
    table = table_ref(context.reader.string_fields(
        configs.address, frozenset({'Core.CoreWareItem'}),
    ).get('Core.CoreWareItem'))
    if table is None:
        raise FanxiuRuntimeMemoryError('Core.CoreWareItem 未自然加载')
    packed = context.reader.table(table.address)
    values = list(packed['fields'].values()) + list(packed['array'])
    refs = {ref.address: ref for value in values if (ref := table_ref(value))}
    if not 1 <= len(refs) <= 128:
        raise FanxiuRuntimeMemoryError('仙纹经验完整配置列表缺失或越界')
    rows = read_runtime_config_rows(
        context.reader, list(refs.values()), environment_address=environment,
        group_name='Core', table_name='CoreWareItem',
        fields=('itemId', 'type', 'param'),
        state_address=context.binding.state_address,
    )
    rates = {}
    for row in rows:
        item_id = as_int(row['itemId'])
        rate_text = str(row['param'])
        if (not item_id or item_id <= 0 or item_id in rates
                or as_int(row['type']) != 1 or not rate_text.isdigit()
                or int(rate_text) <= 0):
            raise FanxiuRuntimeMemoryError(f'仙纹经验物品协议未核实：{row}')
        rates[item_id] = int(rate_text)
    counts, evidence = read_backpack_item_counts(rates, manager_key='xianqiao-upgrade-backpack')
    identity = (context.memory.pid, context.memory.process_start_ticks)
    if identity != (evidence.get('pid'), evidence.get('process_start_ticks')):
        raise FanxiuRuntimeMemoryError('仙纹经验配置与库存进程身份不一致')
    items = [dict(item_id=item_id, quantity=counts[item_id], exp_per_item=rate)
             for item_id, rate in sorted(rates.items())]
    return dict(
        complete=True, read_only=True,
        source='Core.CoreWareItem + BackpackMgr.Model.BackpackData.ItemVoDic',
        items=items, available_exp=sum(x['quantity'] * x['exp_per_item'] for x in items),
        captured_at=datetime.now().astimezone().isoformat(), evidence=evidence,
    )


def read_xianqiao_strengthening_limits_snapshot(base_ids, *, include_costs=False) -> dict:
    """Read GetEquipMaxLevel's loaded map without opening disabled UI buttons.

    The native method takes the maximum level-map key. Verify contiguous
    positive keys and the last row's item/level identity before using that
    limit to skip a worn part. Missing configuration cannot prove max level.
    """
    ids = sorted(set(base_ids))
    if type(include_costs) is not bool:
        raise ValueError('include_costs必须为布尔值')
    if not ids or any(type(v) is not int or v <= 0 for v in ids):
        raise ValueError('仙纹等级上限需要正整数 base_ids')
    context = acquire_ui_runtime_context(())
    from .xianqiao import _XIANQIAO_METHODS, _xianqiao_data_fields, _table_entries
    root, _, environment = resolve_lua_global_manager_root(
        context.memory, manager_key='xianqiao-upgrade-levels',
        state_address=context.binding.state_address, global_name='ImmHoleMgr',
        required_methods=_XIANQIAO_METHODS, validate=_xianqiao_data_fields,
    )
    data = _xianqiao_data_fields(context.reader, root)
    if table_ref(data.get('coreWareLevelMap')) is None:
        raise FanxiuRuntimeMemoryError(
            '仙纹等级配置 coreWareLevelMap 未自然加载', code='data_not_loaded',
        )
    maps = _table_entries(context.reader, data.get('coreWareLevelMap'))
    limits = {}
    costs = {}
    for ident in ids:
        levels = _table_entries(context.reader, maps.get(ident))
        keys = sorted(as_int(k) for k in levels if as_int(k) is not None and as_int(k) > 0)
        if not keys or keys != list(range(1, keys[-1] + 1)):
            raise FanxiuRuntimeMemoryError(
                f'仙纹{ident}等级上限未完整加载：'
                f'item_count={len(maps)}, levels={keys}'
            )
        last = table_ref(levels[keys[-1]])
        row = read_runtime_config_rows(
            context.reader, [last], environment_address=environment,
            group_name='Core', table_name='CoreWareLevel', fields=('itemId', 'level'),
            state_address=context.binding.state_address,
        )[0]
        if as_int(row['itemId']) != ident or as_int(row['level']) != keys[-1]:
            raise FanxiuRuntimeMemoryError('仙纹等级上限原生行身份不一致')
        limits[ident] = keys[-1]
        if include_costs:
            rows = read_runtime_config_rows(
                context.reader, [table_ref(levels[k]) for k in keys],
                environment_address=environment, state_address=context.binding.state_address,
                group_name='Core', table_name='CoreWareLevel',
                fields=('itemId', 'level', 'consumeExp', 'addMainAttr'),
            )
            if any(as_int(r['itemId']) != ident or as_int(r['level']) != k
                   or as_int(r['consumeExp']) is None or as_int(r['consumeExp']) <= 0
                   for k, r in zip(keys, rows, strict=True)):
                raise FanxiuRuntimeMemoryError('仙纹完整强化费用行身份不一致')
            costs[ident] = rows
    return dict(complete=True, read_only=True, limits=limits, costs=costs,
                pid=context.binding.pid, process_start_ticks=context.binding.process_start_ticks)


def read_xianqiao_upgrade_panel_snapshot() -> dict:
    """Read the actual target and material slots before any strengthening action.

    ``slotList`` can contain equipment as well as experience items. Executors
    must reject non-dedicated entries; the quick-insert button is not admission.
    """
    def observe(context):
        panels = [obj for obj in active_ui_component_objects(context)
                  if has_ui_object_fields(context, obj.address,
                                          {'coreExpList', 'slotList', 'upgradeBtn', 'selectView'})]
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError('仙纹强化页未唯一打开')
        panel = panels[0]
        data = table_ref(read_ui_object_field(context, panel.address, 'data'))
        if data is None:
            raise FanxiuRuntimeMemoryError('仙纹强化目标未自然加载')
        target = {}
        for key in ('baseId', 'level', 'wear'):
            target[key] = read_ui_object_field(context, data.address, key)
        for key in ('id', 'totalExp', 'sumExp'):
            value = read_ui_object_field(context, data.address, key)
            target[key] = context.reader.long(value) if table_ref(value) else as_int(value)
        lists = {}
        for name in ('coreExpList', 'slotList'):
            rows, count = context.reader.list_items(read_ui_object_field(context, panel.address, name))
            if count is None or count != len(rows):
                raise FanxiuRuntimeMemoryError(f'强化材料列表{name}不完整')
            decoded = []
            for row in rows:
                ref = table_ref(row)
                if ref is None:
                    raise FanxiuRuntimeMemoryError('强化材料成员不完整')
                item_vo = table_ref(read_ui_object_field(context, ref.address, 'itemVo'))
                address = item_vo.address if item_vo else ref.address
                decoded.append({'item_id': as_int(read_ui_object_field(context, address, 'baseId')),
                                'quantity': as_int(read_ui_object_field(context, address, 'num')),
                                'equipment_material': read_ui_object_field(context, ref.address, 'equipId') is not None,
                                'selected': read_ui_object_field(context, ref.address, 'isSelect')})
            lists[name] = decoded
        if not as_int(target['baseId']) or as_int(target['level']) is None:
            raise FanxiuRuntimeMemoryError('仙纹强化目标身份不完整')
        from .xianqiao import _XIANQIAO_METHODS, _xianqiao_data_fields, _table_entries
        root, _, environment = resolve_lua_global_manager_root(
            context.memory, manager_key='xianqiao-upgrade-levels',
            state_address=context.binding.state_address, global_name='ImmHoleMgr',
            required_methods=_XIANQIAO_METHODS, validate=_xianqiao_data_fields,
        )
        data_fields = _xianqiao_data_fields(context.reader, root)
        level_map = _table_entries(context.reader, data_fields.get('coreWareLevelMap'))
        levels = _table_entries(context.reader, level_map.get(as_int(target['baseId'])))
        refs = [ref for key, value in levels.items()
                if as_int(key) is not None and as_int(key) > 0 and (ref := table_ref(value))]
        if not refs:
            raise FanxiuRuntimeMemoryError('当前仙纹等级费用未自然加载')
        costs = read_runtime_config_rows(
            context.reader, refs, environment_address=environment,
            group_name='Core', table_name='CoreWareLevel',
            fields=('itemId', 'level', 'consumeExp'),
            state_address=context.binding.state_address,
        )
        costs.sort(key=lambda row: row['level'])
        if (any(as_int(row['itemId']) != as_int(target['baseId']) for row in costs)
                or [as_int(row['level']) for row in costs] != list(range(1, len(costs)+1))
                or any(as_int(row['consumeExp']) is None or as_int(row['consumeExp']) <= 0 for row in costs)):
            raise FanxiuRuntimeMemoryError('仙纹等级费用身份或完整性不一致')
        return dict(complete=True, read_only=True, target=target, **lists,
                    levels=costs, max_level=len(costs),
                    pid=context.binding.pid, process_start_ticks=context.binding.process_start_ticks)
    return read_ui_runtime_snapshot((), observe)


def read_xianqiao_exp_selection_snapshot() -> dict:
    """Read the dedicated item quantity dialog; opening/selecting spends nothing.

    Only the subsequent upgrade-page 强化 action commits resource consumption.
    The dialog's maximum is capped by the target equipment's missing experience.
    """
    def observe(context):
        panels = [obj for obj in active_ui_component_objects(context)
                  if has_ui_object_fields(context, obj.address,
                                          {'itemId', 'useNum', 'canUseMax', 'expNum',
                                           'itemMaxNum', 'SynthesisBtn', 'Slider'})]
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError('仙纹专用经验数量页未唯一打开')
        panel = panels[0]
        values = {key: as_int(read_ui_object_field(context, panel.address, key))
                  for key in ('itemId', 'useNum', 'canUseMax', 'expNum', 'itemMaxNum')}
        if (any(value is None for value in values.values())
                or values['itemId'] <= 0 or values['expNum'] <= 0
                or not 0 <= values['useNum'] <= values['canUseMax'] <= values['itemMaxNum']):
            raise FanxiuRuntimeMemoryError('仙纹专用经验数量或上限不完整')
        return dict(complete=True, read_only=True, **values,
                    selected_exp=values['useNum'] * values['expNum'],
                    pid=context.binding.pid, process_start_ticks=context.binding.process_start_ticks)
    return read_ui_runtime_snapshot((), observe)
