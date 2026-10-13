"""Read the selected recipe and quantity from the naturally active alchemy UI."""
from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .resource_auto_use import read_runtime_config_rows
from .ui_runtime_context import (active_ui_component_objects, has_ui_object_fields,
                                 read_ui_object_field, read_ui_runtime_snapshot)

_KEYS = {'itemPanel', 'titleSlider', 'composeNum', 'canComposeNum', 'selectData',
         'startBtn', 'numTxt', 'getNumTxt'}


def read_active_alchemy_selection() -> dict:
    """Observe AlchemyMainView.itemPanel without Lua calls or GUI actions.

    Native AlchemyItemPanel.RefreshProNum displays composeNum and computes
    proficiency * composeNum. These fields avoid losing a small green '1' or
    appending the decorative arrow to yellow points OCR. The caller still
    verifies scene #627 and the selected recipe before any action; this read
    alone never authorizes crafting. Missing/ambiguous panels fail closed.
    """
    def read(c):
        panels = {}
        for component in active_ui_component_objects(c):
            if not has_ui_object_fields(c, component.address, {'itemPanel', 'titleSlider'}):
                continue
            panel = table_ref(read_ui_object_field(c, component.address, 'itemPanel'))
            if panel and has_ui_object_fields(c, panel.address,
                    {'composeNum', 'canComposeNum', 'selectData', 'startBtn', 'numTxt', 'getNumTxt'}):
                panels[panel.address] = panel
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError(f'当前炼丹选择面板数量为{len(panels)}',
                                          code='data_not_loaded' if not panels else 'runtime_incomplete')
        panel = next(iter(panels.values()))
        current = as_int(read_ui_object_field(c, panel.address, 'composeNum'))
        maximum = as_int(read_ui_object_field(c, panel.address, 'canComposeNum'))
        selected = table_ref(read_ui_object_field(c, panel.address, 'selectData'))
        if current is None or maximum is None or not 0 < current <= maximum or selected is None:
            raise FanxiuRuntimeMemoryError('炼丹当前选择或数量不完整', code='runtime_incomplete')
        config = read_runtime_config_rows(c.reader, [selected],
            environment_address=c.binding.environment_address,
            group_name='Medical', table_name='Medical',
            fields=('id', 'item2', 'proficiency', 'formula', 'anyMaterial'))[0]
        recipe_id, item_id, unit = (as_int(config[k]) for k in ('id', 'item2', 'proficiency'))
        if any(v is None or v <= 0 for v in (recipe_id, item_id, unit)):
            raise FanxiuRuntimeMemoryError('炼丹配方身份或单位熟练度不完整', code='runtime_incomplete')
        return {'complete': True, 'read_only': True,
                'source': 'AlchemyMainView.itemPanel.composeNum + Medical.Medical.proficiency',
                'current': current, 'maximum': maximum, 'recipe_id': recipe_id,
                'item_id': item_id, 'base_per_item': unit, 'base_points': unit * current,
                # Raw current-client inputs to ConvertItemToCn/SplitItem2List.
                # AnyItem denotes alternatives, not simultaneous consumption;
                # callers must resolve actual material use before predicting score.
                'material_formula': config.get('formula'),
                'alternative_material_formula': config.get('anyMaterial'),
                'consumption_authorized': False,
                'evidence': {'pid': c.binding.pid, 'process_start_ticks': c.binding.process_start_ticks}}
    return read_ui_runtime_snapshot(_KEYS, read, fast=True)
