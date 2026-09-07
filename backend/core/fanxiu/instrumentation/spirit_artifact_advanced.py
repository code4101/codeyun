"""高级洗炼只读投影：当前窗口 realList 是已按灵器筛选的有序配置。

读取不会打开窗口、请求洗炼或购买道具。库存大于零不代表满足使用条件；
limitType、品质、稀有词条数量和满值条件仍由具体使用计划校验。
语言表只负责展示，规则及筛选结果取自当前客户端内存。
"""

from __future__ import annotations

from typing import Any
import re
import time

from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import read_ui_object_field, read_ui_runtime_snapshot


def advanced_item_confirmation_names(item: dict[str, Any]) -> tuple[str, ...]:
    """同一配置的展示名与确认文案名可能不同（无瑕石/无暇石），不做近音猜测。"""
    names = [str(item.get('name') or '')]
    match = re.match(r'使用1个<color=[^>]+>(洗灵[^<]+)</color>', str(item.get('useDes') or ''))
    if match:
        names.append(match[1])
    return tuple(dict.fromkeys(name.replace('·', '').replace(' ', '') for name in names if name))


def _read_advanced_panel_identity(ctx, supported_wares: frozenset[int]) -> dict[str, Any]:
    """Fresh narrow membership projection; no config rows or inventory reads."""
    reader = ctx.reader
    field = lambda obj, key: read_ui_object_field(ctx, obj.address, key)
    storage = reader.table(ctx.binding.component_storage_address)
    candidates = {}
    for raw in [*storage['array'], *storage['fields'].values()]:
        window = table_ref(raw)
        if window is None:
            continue
        members, count = reader.list_items(window)
        if not count or len(members) != count:
            continue
        component = table_ref(members[-1])
        panel = table_ref(field(component, 'm_panel')) if component else None
        if panel is None:
            continue
        real = table_ref(field(panel, 'realList'))
        ware = as_int(field(panel, 'V_SpiritWare'))
        if real is None or ware not in supported_wares or table_ref(field(panel, 'ScrollView')) is None:
            continue
        uid = reader.long(field(panel, '_CurSelectSlot'))
        if not uid:
            raise FanxiuRuntimeMemoryError('高级洗炼后置观察选中实例缺失')
        candidates[panel.address] = {
            'window': window.address, 'component': component.address, 'panel': panel.address,
            'real_list': real.address, 'item_id': str(uid), 'ware_id': ware,
            'pid': ctx.memory.pid, 'process_start_ticks': ctx.memory.process_start_ticks,
        }
    if len(candidates) != 1:
        raise FanxiuRuntimeMemoryError(f'高级洗炼后置观察窗口必须唯一，实际{len(candidates)}')
    return next(iter(candidates.values()))


def read_spirit_artifact_advanced_items(*, fast: bool = False) -> dict[str, Any]:
    """读取已打开高级洗炼窗口的目标、全部可见配置及真实库存；歧义报错。

    字段索引沿 s_globalCfgIdx/SpiritWare/SpiritWareCleanseItem 精确读取，
    不展开整个配置索引集合。state_string_field 只复用底层字符串定位，
    当前成员引用每次新读，配置根/索引变化不沿用旧值。不另缓存运行中
    realList、选中实例或库存。结束时另建context窄读当前注册成员/UID，
    不用同reader缓存冒充后置复验，不重复配置或库存。支持器号取正式
    SpiritWareItem配置；其版本是否与运行态完全一致仍需独立核验。
    后置身份复核已随第4器连续消费真实验收；第9器高级窗口仍待真实验收。
    """
    from backend.core.fanxiu.catalog.lua_config import load_default_fanxiu_lang_map
    from .backpack import read_backpack_item_counts
    from .item_config import read_loaded_item_metadata
    from backend.core.fanxiu.catalog.spirit_artifact_wash_rules import load_spirit_artifact_wash_rules

    started = time.monotonic()
    lang = load_default_fanxiu_lang_map()
    timings = {'lang': time.monotonic() - started}
    support_started = time.monotonic()
    supported_wares = frozenset(load_spirit_artifact_wash_rules()['coverage']['ware_ids'])
    timings['supported_wares'] = time.monotonic() - support_started
    read_failures: list[dict[str, Any]] = []
    projection_attempt = 0

    def read(ctx):
        nonlocal projection_attempt
        projection_attempt += 1
        projection_started = time.monotonic()
        reader = ctx.reader
        candidate_context: dict[str, Any] = {}

        def field(obj, key):
            try:
                return read_ui_object_field(ctx, obj.address, key)
            except FanxiuRuntimeMemoryError as exc:
                # Do not inspect the failing object again for diagnostics: that
                # would overwrite the first fault or change recovery behavior.
                # The shared observer still owns exactly its existing retry.
                failure = {**candidate_context, 'attempt': projection_attempt,
                    'field': key, 'object': f'0x{obj.address:x}',
                    'pid': ctx.memory.pid, 'process_start_ticks': ctx.memory.process_start_ticks,
                    'cache_mode': ctx.cache_mode, 'code': exc.code, 'error': str(exc)}
                read_failures.append(failure)
                raise FanxiuRuntimeMemoryError(
                    f'高级洗炼候选字段读取失败：{failure}; first_failure={read_failures[0]}',
                    code=exc.code,
                ) from exc
        index_started = time.monotonic()
        current = table_ref(reader.state_string_field(ctx.binding.environment_address,
            's_globalCfgIdx', state_address=ctx.binding.state_address))
        for key in ('SpiritWare', 'SpiritWareCleanseItem'):
            if current is None:
                raise FanxiuRuntimeMemoryError('高级洗炼配置字段索引成员链未加载')
            current = table_ref(reader.state_string_field(current.address, key,
                state_address=ctx.binding.state_address))
        if current is None:
            raise FanxiuRuntimeMemoryError('高级洗炼配置字段索引未加载')
        wanted = ('id', 'sort', 'item', 'shortDes', 'useDes', 'type', 'limitType', 'limitSpiritType')
        indexes = reader.string_fields(current.address, frozenset(wanted))
        if any(as_int(indexes.get(key)) is None for key in wanted):
            raise FanxiuRuntimeMemoryError('高级洗炼配置字段索引未完整加载')
        timings['config_indexes'] = time.monotonic() - index_started
        timings['config_rows'] = 0.0
        storage = reader.table(ctx.binding.component_storage_address)
        candidates = {}
        for candidate_index, raw in enumerate([*storage['array'], *storage['fields'].values()]):
            window = table_ref(raw)
            if window is None:
                continue
            candidate_context.clear()
            candidate_context.update(candidate_index=candidate_index, window=f'0x{window.address:x}',
                                     component=None, panel=None)
            members, count = reader.list_items(window)
            if not count or len(members) != count:
                continue
            component = table_ref(members[-1])
            if component is not None:
                candidate_context['component'] = f'0x{component.address:x}'
            panel = table_ref(field(component, 'm_panel')) if component else None
            if panel is None:
                continue
            candidate_context['panel'] = f'0x{panel.address:x}'
            real = table_ref(field(panel, 'realList'))
            ware = as_int(field(panel, 'V_SpiritWare'))
            candidate_context['ware_id'] = ware
            if real is None or ware not in supported_wares or table_ref(field(panel, 'ScrollView')) is None:
                continue
            item_id = reader.long(field(panel, '_CurSelectSlot'))
            rows, count = reader.list_items(real)
            if not item_id or count is None or count > 64 or len(rows) != count:
                raise FanxiuRuntimeMemoryError('高级洗炼目标或完整列表无效')
            rows_started = time.monotonic()
            decoded = []
            for raw_row in rows:
                ref = table_ref(raw_row)
                if ref is None:
                    raise FanxiuRuntimeMemoryError('高级洗炼配置行无效')
                table = reader.table(ref.address)
                def value(key):
                    index = int(indexes[key])
                    return table['fields'].get(key, table['fields'].get(index,
                        table['array'][index] if 0 <= index < len(table['array']) else None))
                data = {key: value(key) for key in wanted}
                for key in ('id', 'sort', 'item', 'type'):
                    if as_int(data[key]) is None or int(data[key]) <= 0:
                        raise FanxiuRuntimeMemoryError(f'高级洗炼配置缺少 {key}')
                    data[key] = int(data[key])
                limits = table_ref(data['limitSpiritType'])
                # Lua array capacity includes unused nil slots after ipairs ends.
                limit_values = reader.table(limits.address)['array'][1:] if limits else []
                data['limitSpiritType'] = [as_int(v) for v in limit_values if v is not None]
                if any(v not in supported_wares for v in data['limitSpiritType']):
                    raise FanxiuRuntimeMemoryError('高级洗炼灵器限制包含无效编号')
                if data['limitSpiritType'] and ware not in data['limitSpiritType']:
                    raise FanxiuRuntimeMemoryError('高级洗炼列表与当前灵器限制不一致')
                for key in ('shortDes', 'useDes'):
                    raw_text = data[key]
                    data[key + 'Id'] = as_int(raw_text)
                    data[key] = raw_text if isinstance(raw_text, str) else lang.get(as_int(raw_text), '')
                decoded.append(data)
            if len({row['item'] for row in decoded}) != len(decoded):
                raise FanxiuRuntimeMemoryError('高级洗炼道具身份重复')
            timings['config_rows'] += time.monotonic() - rows_started
            candidates[panel.address] = {'item_id': str(item_id), 'ware_id': ware,
                'part_quality': as_int(field(panel, 'partCfgQuality')), 'items': decoded,
                '_panel_identity': {'window': window.address, 'component': component.address,
                    'panel': panel.address, 'real_list': real.address, 'item_id': str(item_id),
                    'ware_id': ware, 'pid': ctx.memory.pid,
                    'process_start_ticks': ctx.memory.process_start_ticks}}
        if len(candidates) != 1:
            raise FanxiuRuntimeMemoryError(f'当前高级洗炼窗口数量必须为 1，实际 {len(candidates)}')
        result = next(iter(candidates.values()))
        timings['window_config'] = time.monotonic() - projection_started
        timings['window_identity_and_membership'] = (
            timings['window_config'] - timings['config_indexes'] - timings['config_rows'])
        ids = [row['item'] for row in result['items']]
        metadata_started = time.monotonic()
        metadata, status = read_loaded_item_metadata(ids, memory=ctx.memory, reader=reader,
                                                    state_address=ctx.binding.state_address)
        if not status['complete']:
            raise FanxiuRuntimeMemoryError('高级洗炼道具元数据未完整加载')
        timings['metadata'] = time.monotonic() - metadata_started
        inventory_started = time.monotonic()
        counts, inventory = read_backpack_item_counts(ids, manager_key='spirit-artifact-advanced')
        timings['inventory'] = time.monotonic() - inventory_started
        if (inventory['pid'], inventory['process_start_ticks']) != (ctx.memory.pid, ctx.memory.process_start_ticks):
            raise FanxiuRuntimeMemoryError('高级洗炼列表与库存进程不一致')
        for row in result['items']:
            item = metadata[row['item']]
            row['name'] = item['item_name'] or lang.get(item['runtime_name_id'], '')
            row['count'] = counts[row['item']]
        return {**result, 'pid': ctx.memory.pid, 'process_start_ticks': ctx.memory.process_start_ticks,
                'source': 'active_spiritware_advanced_real_list', 'read_only': True,
                'inventory_diagnostics': {key: inventory.get(key) for key in
                    ('discovery', 'backpack_root_cache_hit')}}

    result = read_ui_runtime_snapshot([], read, fast=fast)
    expected_identity = result.pop('_panel_identity')
    guard_started = time.monotonic()
    current_identity = read_ui_runtime_snapshot(
        [], lambda ctx: _read_advanced_panel_identity(ctx, supported_wares), fast=fast,
    )
    timings['identity_guard'] = time.monotonic() - guard_started
    if expected_identity != current_identity:
        raise FanxiuRuntimeMemoryError('读取高级洗炼期间注册窗口、列表、目标或游戏进程发生变化')
    total = time.monotonic() - started
    # 包括公共观察器建 context/恢复及末尾身份复核，不误算成配置表解析。
    timings['observer_and_postcheck'] = max(0.0, total - sum(
        timings.get(key, 0.0) for key in ('lang', 'supported_wares', 'window_config', 'metadata', 'inventory')))
    return {**result, 'timings': {**timings, 'total': total},
            'observation_failures': read_failures}
