"""Read only the currently loaded star-sea purification/charge UI.

Public callers must establish #737 or the charge dialog before reading. Root
binding/recovery belongs to ui_runtime_context; child addresses and values are
never cached here. Missing/ambiguous panels fail closed. UI lifetime after
process restart still needs live validation; investigate active panel membership
before adding address caches or broad discovery.
"""
from __future__ import annotations

from typing import Any, Literal

from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import (
    active_ui_component_objects, has_ui_object_fields, read_ui_object_field,
    read_ui_runtime_snapshot,
)


def read_xinghai_ui(page: Literal['purification', 'charge']) -> dict[str, Any]:
    """Return current selection/energy, with no game writes or hidden loading."""
    if page not in {'purification', 'charge'}:
        raise ValueError(page)
    markers = {'purification': ('PurifyBtn', 'sliderContent', '_breakItemId'),
               'charge': ('ChooseCountTxt', 'CostTxt', 'SureBtn')}[page]

    def read(ctx):
        candidates = [r for r in active_ui_component_objects(ctx)
                      if has_ui_object_fields(ctx, r.address, markers)]
        if len(candidates) != 1:
            raise FanxiuRuntimeMemoryError(f'星海 {page} 活动面板不唯一：{len(candidates)}')
        panel = candidates[0].address
        def number(address, name):
            value = as_int(read_ui_object_field(ctx, address, name))
            if value is None or value < 0:
                raise FanxiuRuntimeMemoryError(f'星海字段无效：{name}')
            return value
        result = {'page': page, 'selected_count': number(panel, '_chooseCount')}
        if page == 'purification':
            slider = table_ref(read_ui_object_field(ctx, panel, 'sliderContent'))
            if slider is None:
                raise FanxiuRuntimeMemoryError('星海真元组件未加载')
            result.update(selected_config_id=number(panel, '_breakItemId'),
                          max_count=number(panel, '_maxCount'),
                          energy=number(slider.address, '_CurrentVal'),
                          energy_limit=number(slider.address, '_MaxVal'))
            if result['selected_config_id']:
                def field(ref, key):
                    if ref is None:
                        raise FanxiuRuntimeMemoryError('星海原料配置尚未加载')
                    return ctx.field(ref.address, key)
                package = ctx.field(ctx.binding.environment_address, 'package')
                configs = field(field(package, 'loaded'), 'Generate.Cfg.BlueStarSea.BreakItem')
                indexes = field(field(ctx.field(ctx.binding.environment_address, 's_globalCfgIdx'), 'BlueStarSea'), 'BreakItem')
                index = as_int(ctx.reader.fields(indexes).get('energyConsume'))
                row = table_ref(ctx.reader.fields(configs).get(result['selected_config_id']))
                values = ctx.reader.table(row.address)['array'] if row else []
                cost = as_int(values[index]) if index is not None and 0 <= index < len(values) else None
                if cost is None or cost <= 0:
                    raise FanxiuRuntimeMemoryError('星海原料真元消耗无效')
                result['unit_energy_cost'] = cost
            else:
                result['unit_energy_cost'] = 0
        return result
    return read_ui_runtime_snapshot(('Generate.Cfg.BlueStarSea.BreakItem', 's_globalCfgIdx', 'BlueStarSea', 'BreakItem') if page == 'purification' else (), read)
