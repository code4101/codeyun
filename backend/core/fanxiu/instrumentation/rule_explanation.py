"""Read the naturally opened RulerExplainView; never load or execute Lua."""
from .runtime_memory import FanxiuRuntimeMemoryError, table_ref, as_int
from .ui_runtime_context import (
    active_ui_component_objects, has_ui_object_fields,
    read_ui_object_field, read_ui_runtime_snapshot,
)


def read_active_rule_explanation() -> dict:
    """Return the complete text model of one currently active rule window.

    RulerExplainView renders data.mainTitle and data.content, constructed from
    Rule.Rule by RulerExplainData. The window does not retain its requested
    rule ID, so this API reports no invented ID. Callers bind the navigation
    to an activity separately. Reading never authorizes spending or claims.
    """
    keys = {'ViewTitleTF', 'DetailScroll', 'RulerItemCell',
            'HelpCalcContentHeightTF', 'data', 'mainTitle', 'content', 'type',
            'title', 'leader', 'designer', 'programmer', 'artist', 'tester',
            'iconList', 'iconTitle'}

    def read(c):
        panels = {}
        for component in active_ui_component_objects(c):
            if has_ui_object_fields(c, component.address,
                    {'ViewTitleTF', 'DetailScroll', 'RulerItemCell', 'HelpCalcContentHeightTF'}):
                panels[component.address] = component
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError(f'当前规则窗口数量为{len(panels)}',
                code='data_not_loaded' if not panels else 'runtime_incomplete')
        panel = next(iter(panels.values()))
        data = table_ref(read_ui_object_field(c, panel.address, 'data'))
        if data is None:
            raise FanxiuRuntimeMemoryError('规则内容未自然加载', code='data_not_loaded')
        title = read_ui_object_field(c, data.address, 'mainTitle')
        rows, declared = c.reader.list_items(read_ui_object_field(c, data.address, 'content'))
        if not isinstance(title, str) or not title or declared != len(rows) or not 0 < len(rows) <= 64:
            raise FanxiuRuntimeMemoryError('规则标题或完整内容缺失', code='runtime_incomplete')
        sections = []
        for index, row in enumerate(rows):
            ref = table_ref(row)
            body = table_ref(read_ui_object_field(c, ref.address, 'data')) if ref else None
            if body is None:
                raise FanxiuRuntimeMemoryError('规则段落内容缺失', code='runtime_incomplete')
            icons = read_ui_object_field(c, body.address, 'iconList')
            icon_groups = []
            if table_ref(icons) is not None:
                def array(value):
                    ref = table_ref(value)
                    if ref is None:
                        raise FanxiuRuntimeMemoryError('规则图标数组缺失', code='runtime_incomplete')
                    table = c.reader.table(ref.address)
                    fields = {int(key): item for key, item in table['fields'].items()
                              if type(key) in (int, float) and int(key) == key and item is not None}
                    for key, item in enumerate(table['array']):
                        if item is not None:
                            if key in fields and fields[key] != item:
                                raise FanxiuRuntimeMemoryError('规则图标数组索引冲突', code='runtime_incomplete')
                            fields[key] = item
                    if not fields or sorted(fields) != list(range(1, len(fields) + 1)):
                        raise FanxiuRuntimeMemoryError('规则图标数组不连续', code='runtime_incomplete')
                    return [fields[key] for key in sorted(fields)]
                for group in array(icons):
                    group_ref = table_ref(group)
                    if group_ref is None:
                        raise FanxiuRuntimeMemoryError('规则图标分组缺失', code='runtime_incomplete')
                    label = read_ui_object_field(c, group_ref.address, 'iconTitle')
                    items = array(read_ui_object_field(c, group_ref.address, 'iconList'))
                    if not isinstance(label, str) or any(not isinstance(item, str) or not item for item in items):
                        raise FanxiuRuntimeMemoryError('规则图标标签或引用缺失', code='runtime_incomplete')
                    icon_groups.append(dict(title=label, item_references=items))
            values = {key: read_ui_object_field(c, body.address, key)
                      for key in ('title', 'content', 'leader', 'designer', 'programmer', 'artist', 'tester')}
            values = {key: value for key, value in values.items() if isinstance(value, str) and value}
            if not values:
                raise FanxiuRuntimeMemoryError('规则含尚未支持的非文字段落', code='runtime_incomplete')
            sections.append(dict(index=index + 1,
                type=as_int(read_ui_object_field(c, ref.address, 'type')),
                icon_groups=icon_groups, **values))
        return dict(complete=True, read_only=True, title=title, sections=sections,
            rule_id=None, consumption_authorized=False, claim_authorized=False,
            source='active RulerExplainView.data',
            evidence=dict(pid=c.binding.pid, process_start_ticks=c.binding.process_start_ticks))

    return read_ui_runtime_snapshot(keys, read, fast=True)
