"""Read the full active experience-book list, including off-screen rows."""
from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import (
    active_ui_component_objects, has_ui_object_fields,
    read_ui_object_field, read_ui_runtime_snapshot,
)


def read_experience_book_panel_snapshot() -> dict:
    """Observe CommonItemUseTipsView's complete ordered _ItemList, without input.

    A bottom '日常' row does not prove preceding book rows were consumed.
    This read exposes owned counts and acquisition-row identities before an
    executor chooses a visible action. It does not authorize a shop purchase.
    """
    def observe(context):
        panels = [obj for obj in active_ui_component_objects(context)
                  if has_ui_object_fields(context, obj.address,
                                          {'itemScrollView', '_ItemList', 'allCount'})]
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError('经验书完整列表未唯一打开', code='data_not_loaded')
        panel = panels[0]
        declared = as_int(read_ui_object_field(context, panel.address, 'allCount'))
        rows, count = context.reader.list_items(
            read_ui_object_field(context, panel.address, '_ItemList'))
        if declared is None or declared != count or count != len(rows) or not 1 <= count <= 128:
            raise FanxiuRuntimeMemoryError('经验书原生完整列表数量不一致')
        items = []
        for index, row in enumerate(rows):
            ref = table_ref(row)
            if ref is None:
                raise FanxiuRuntimeMemoryError('经验书列表成员不是完整对象')
            item = {'index': index}
            for key in ('title', 'desc', 'num', 'type', 'id', 'shopId', 'getWayItem',
                        'isGetWay', 'keepView'):
                value = read_ui_object_field(context, ref.address, key)
                if value is not None and not isinstance(value, (str, int, float, bool)):
                    raise FanxiuRuntimeMemoryError(f'经验书成员字段{key}尚未支持')
                item[key] = value
            if not isinstance(item['title'], str) or as_int(item['type']) is None:
                raise FanxiuRuntimeMemoryError('经验书成员标题或类型不完整')
            items.append(item)
        return dict(complete=True, read_only=True,
                    source='CommonItemUseTipsView._ItemList', items=items,
                    declared_count=declared, pid=context.binding.pid,
                    process_start_ticks=context.binding.process_start_ticks)
    return read_ui_runtime_snapshot((), observe)
