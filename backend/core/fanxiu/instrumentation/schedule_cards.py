"""Read #66's actual ordered carousel, not the world-line schedule superset.

WorldLineViewNew.UpdatePageAct passes pageActList to pageScrollView. Resolve
the live tab from WorldLineMainView on every observation; never cache pooled
panel addresses. Caller must establish #66 readiness first. No Lua is executed.
"""
from __future__ import annotations

import hashlib
import json
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError, LuaRef, as_int, table_ref,
)
from backend.core.fanxiu.instrumentation.ui_runtime_context import (
    active_ui_component_objects, has_ui_object_fields, read_ui_object_field,
    read_ui_runtime_snapshot,
)


@lru_cache(maxsize=2)
def activity_definitions(path: str, modified_ns: int) -> dict[int, dict]:
    """Cache static names only; export replacement invalidates by mtime."""
    return {int(row['id']): row for row in json.loads(Path(path).read_text(encoding='utf-8'))}


def read_schedule_card_runtime_snapshot() -> dict[str, Any]:
    """Return ordered card tasks, exact identities and completeness evidence.

    Includes expired and future cards that the client actually put in its
    carousel. Dates and scheduleId never filter membership. Missing/ambiguous
    UI or unsupported identities return complete=False. A loaded empty list is
    complete with count=0; it must not be confused with an unreadable page.
    """
    def read(ctx):
        hosts = [obj for obj in active_ui_component_objects(ctx)
                 if has_ui_object_fields(ctx, obj.address, {'WorldLineMainView', 'tabPanelGroup'})]
        if len(hosts) != 1:
            raise FanxiuRuntimeMemoryError('#66 日程主窗口未唯一加载', code='data_not_loaded')
        tabs = ctx.reader.fields(read_ui_object_field(ctx, hosts[0].address, 'tabPanelGroup'))
        current_index = as_int(tabs.get('curTabIndex'))
        panel_map = ctx.reader.fields(tabs.get('panelShowComps'))
        count = as_int(panel_map.get('count'))
        storage = table_ref(panel_map.get('_dt_'))
        if current_index is None or count is None or not 0 <= current_index < count <= 16 or storage is None:
            raise FanxiuRuntimeMemoryError('日程页签列表不完整', code='runtime_incomplete')
        # panelShowComps is a lazily populated tab map, not a dense card CList.
        table = ctx.reader.table(storage.address)
        slot = current_index + 1
        array_value = table['array'][slot] if slot < len(table['array']) else None
        hash_value = table['fields'].get(slot)
        if array_value is not None and hash_value is not None and array_value != hash_value:
            raise FanxiuRuntimeMemoryError('当前页签槽冲突', code='runtime_incomplete')
        comp = table_ref(array_value if array_value is not None else hash_value)
        panel_ref = table_ref(read_ui_object_field(ctx, comp.address, 'm_panel')) if comp else None
        if not panel_ref or not has_ui_object_fields(ctx, panel_ref.address, {'pageActList', 'pageScrollView', 'dateList'}):
            raise FanxiuRuntimeMemoryError('#66 当前页签不是卡片日程页', code='data_not_loaded')
        panel = ctx.reader.fields(panel_ref)
        if panel.get('isShowing') is not True or panel.get('hasClose') is True:
            raise FanxiuRuntimeMemoryError('#66 卡片页未显示', code='data_not_loaded')
        values, count = ctx.reader.list_items(panel['pageActList'])
        scroll = ctx.reader.fields(panel['pageScrollView'])
        shown, shown_count = ctx.reader.list_items(scroll.get('ItemInfoList'))
        if count is None or count > 64 or shown_count != count or shown != values:
            raise FanxiuRuntimeMemoryError('卡片模型与轮播数据不一致', code='runtime_incomplete')
        path = resolve_fanxiu_export_root() / 'parsed_configs' / 'Activity' / 'rows.json'
        definitions = activity_definitions(str(path), path.stat().st_mtime_ns)
        items = []
        def number(value):
            return ctx.reader.long(value) if isinstance(value, LuaRef) else as_int(value)
        for index, value in enumerate(values):
            data = ctx.reader.fields(value)
            activity_id = as_int(data.get('activityId'))
            definition = definitions.get(activity_id, {})
            name = str(definition.get('name_plain') or definition.get('name') or '').strip()
            subtitle = str(definition.get('littleName_plain') or definition.get('littleName') or '').strip()
            runtime_id = number(data.get('id'))
            start, end = number(data.get('startTime')), number(data.get('endTime'))
            if not name or not runtime_id or not start or not end or end < start:
                raise FanxiuRuntimeMemoryError(f'第 {index+1} 张卡片身份不完整', code='runtime_incomplete')
            items.append({'index': index, 'key': f'{activity_id}:{runtime_id}',
                          'name': name, 'title': name + subtitle,
                          'activity_id': activity_id, 'runtime_id': runtime_id,
                          'activity_type': as_int(data.get('activityType')),
                          'state': as_int(data.get('state')),
                          'start_time': start, 'end_time': end})
        if len({item['key'] for item in items}) != count:
            raise FanxiuRuntimeMemoryError('卡片身份重复', code='runtime_incomplete')
        fingerprint = hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()
        return {'ok': True, 'complete': True, 'items': items, 'count': count,
                'fingerprint': fingerprint, 'captured_at_epoch': time.time(),
                'evidence': {'pid': ctx.memory.pid, 'process_start_ticks': ctx.memory.process_start_ticks,
                             'source': 'WorldLineViewNew.pageActList / pageScrollView.ItemInfoList'}}
    try:
        return read_ui_runtime_snapshot((), read, fast=True)
    except FanxiuRuntimeMemoryError as exc:
        return {'ok': False, 'complete': False, 'items': [], 'error_code': exc.code, 'reason': str(exc)}
