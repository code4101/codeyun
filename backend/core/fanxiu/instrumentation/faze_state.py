"""Current client Faze membership and server-provided expiry facts, read only."""
from datetime import datetime
import time

from .runtime_memory import (
    FanxiuRuntimeMemoryError, LuaJitReader, MumuProcessMemory, as_int,
    manager_index_fields, resolve_manager_root, table_ref,
)

_METHODS = frozenset({'LuaFazeMgr', 'InitSingleton', 'CheckIsTalentFaze'})


def _data(reader, address):
    manager = manager_index_fields(reader, address, _METHODS)
    instance = reader.fields(manager.get('inst'))
    model = reader.fields(instance.get('Model'))
    data = reader.fields(model.get('FazeData'))
    if not data:
        raise FanxiuRuntimeMemoryError('FazeMgr.Model.FazeData尚未加载')
    return data


def read_faze_runtime_snapshot(*, include_expiry_details=True):
    """Read full client membership, retaining exact expiry VO evidence.

    FazeData.UpdateFaZeList includes permanent and unexpired resources from
    fazeInfoVOS. Book ownership or equipped slots cannot replace these facts.
    Membership is the client's current projection, not a server trigger-counter
    or proof of recursive bonus semantics. No GUI, network send or consumption.
    """
    if type(include_expiry_details) is not bool:
        raise ValueError('法则到期明细读取模式须为布尔值')
    started = time.perf_counter()
    memory = MumuProcessMemory.discover_cached()
    reader = LuaJitReader(memory)
    root, hit = resolve_manager_root(
        memory, manager_key='faze-state', marker=b'LuaFazeMgr',
        required_methods=_METHODS, validate=_data)
    data = _data(reader, root)
    active, active_count = reader.list_items(data.get('fazeList'))
    infos, info_count = (reader.list_items(data.get('fazeInfoVOS'))
                         if include_expiry_details else ([], None))
    resource_ids = [as_int(value) for value in active]
    details = []
    for value in infos:
        fields = reader.fields(value)
        details.append({'faze_resource_id': as_int(fields.get('fazeResId')),
                        'end_time': reader.long(fields.get('endTime'))})
    complete = (active_count is not None and len(active) == active_count
                and (not include_expiry_details or (info_count is not None and len(infos) == info_count))
                and all(value is not None and value > 0 for value in resource_ids)
                and all(row['faze_resource_id'] is not None and row['end_time'] is not None
                        for row in details))
    return {'ok': True, 'complete': complete, 'source': 'runtime_memory',
            'protocol': 'FazeMgr.Model.FazeData.fazeList+fazeInfoVOS',
            'client_active_resource_ids': resource_ids,
            'resource_info': details, 'active_count': active_count, 'info_count': info_count,
            'expiry_details_read': include_expiry_details,
            'captured_at': datetime.now().astimezone().isoformat(),
            'elapsed_seconds': time.perf_counter() - started,
            'evidence': {'pid': memory.pid, 'process_start_ticks': memory.process_start_ticks,
                         'root_address': f'0x{root:x}', 'root_cache_hit': hit, 'read_only': True}}


def read_faze_definition_snapshot(resource_ids):
    """Exact naturally loaded Faze definitions, without loading config tables.

    Current active IDs may exceed the offline export. Resolve their resource
    and effect identities through DBMgr's existing ConfigDic; missing rows stay
    explicit. Scalar definitions do not prove server trigger-counter semantics.
    """
    from .ui_runtime_context import read_ui_object_field, read_ui_runtime_snapshot
    from .resource_auto_use import read_runtime_config_rows
    from .item_config import read_loaded_item_text

    identities = frozenset(resource_ids)
    if not identities or any(type(value) is not int or value <= 0 for value in identities):
        raise ValueError('法则配置查询需要明确正整数资源身份')

    def read(context):
        def field(address, name):
            return read_ui_object_field(context, address, name)

        manager = table_ref(field(context.binding.environment_address, 'DBMgr'))
        instance = table_ref(field(manager.address, 'inst')) if manager else None
        wrapper = table_ref(field(instance.address, 'ConfigDic')) if instance else None
        configs = table_ref(field(wrapper.address, '_dt_')) if wrapper else None
        if configs is None:
            raise FanxiuRuntimeMemoryError('DBMgr.ConfigDic尚未自然加载', code='not_loaded')
        tables = context.reader.string_fields(configs.address, frozenset({
            'Gongfa.FazeResource', 'Gongfa.FazeEffectResource'}))
        resources = table_ref(tables.get('Gongfa.FazeResource'))
        effects = table_ref(tables.get('Gongfa.FazeEffectResource'))
        if resources is None or effects is None:
            raise FanxiuRuntimeMemoryError('法则配置表尚未自然加载', code='not_loaded')
        refs = context.reader.numeric_fields(resources.address, identities)
        selected = [table_ref(refs.get(identity)) for identity in sorted(identities)]
        rows = read_runtime_config_rows(
            context.reader, [row for row in selected if row is not None],
            environment_address=context.binding.environment_address,
            state_address=context.binding.state_address, group_name='Gongfa',
            table_name='FazeResource', fields=['id', 'effects', 'tipStr'])
        text_ids = {int(str(row['tipStr'])) for row in rows
                    if str(row.get('tipStr') or '').isdigit() and int(str(row['tipStr'])) > 0}
        localized = read_loaded_item_text(
            text_ids, reader=context.reader,
            environment_address=context.binding.environment_address,
            state_address=context.binding.state_address)
        for row in rows:
            raw_text = str(row.get('tipStr') or '')
            row['tip_text'] = (localized['texts_by_id'].get(int(raw_text))
                               if raw_text.isdigit() else raw_text)
        effect_ids = frozenset(as_int(row.get('effects')) for row in rows
                               if as_int(row.get('effects')) is not None)
        effect_refs = context.reader.numeric_fields(effects.address, effect_ids)
        effect_rows = read_runtime_config_rows(
            context.reader, [ref for value in effect_refs.values()
                             if (ref := table_ref(value)) is not None],
            environment_address=context.binding.environment_address,
            state_address=context.binding.state_address, group_name='Gongfa',
            table_name='FazeEffectResource', fields=['id', 'type', 'params'])
        seen = {as_int(row.get('id')) for row in rows}
        seen_effects = {as_int(row.get('id')) for row in effect_rows}
        if not seen <= identities or not seen_effects <= effect_ids:
            raise FanxiuRuntimeMemoryError('法则配置回读身份不符', code='runtime_incomplete')
        return {'complete': seen == identities and seen_effects == effect_ids,
                'resources': rows, 'effects': effect_rows,
                'missing_resource_ids': sorted(identities - seen),
                'missing_effect_ids': sorted(effect_ids - seen_effects),
                'missing_text_ids': localized['missing_ids'],
                'text_complete': localized['complete'],
                'source': 'loaded_DBMgr.ConfigDic.Gongfa',
                'captured_at': datetime.now().astimezone().isoformat(),
                'evidence': {'pid': context.memory.pid,
                             'process_start_ticks': context.memory.process_start_ticks,
                             'read_only': True, 'lua_methods_invoked': False}}
    return read_ui_runtime_snapshot((), read, fast=True)


__all__ = ['read_faze_runtime_snapshot', 'read_faze_definition_snapshot']
