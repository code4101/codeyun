"""Observe the client's cached ChaosBoss tab routes; identity remains a candidate."""
from datetime import datetime
import json

from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import acquire_lua_global_runtime_context
from .resource_auto_use import read_runtime_config_rows


def read_wanxian_task_binding_candidates() -> dict:
    """Read FunctionMgr's existing tabViewDic[101], without populating it.

    Exported FunctionType names 101 ChaosBoss; daily ActiveTask 1044 connects
    the localized Wanxian title to ChaosBossChallenge and activity type192.
    This establishes a route candidate, not the live #749 panel identity or
    the current reward cycle. TaskData/RevenueMgr ownership must follow the
    observed tab luaPath/openValue; shared QuestMgr rows cannot replace it.
    """
    ctx = None
    try:
        ctx = acquire_lua_global_runtime_context({'FunctionMgr'})
        manager = ctx.loaded_manager_fields('FunctionMgr', frozenset({'LuaFunctionMgr', 'Inst_get'}))
        reader = ctx.reader
        instance = reader.fields(manager.get('inst'))
        data = reader.fields(reader.fields(instance.get('Model')).get('FunctionData'))
        cache = reader.fields(data.get('tabViewDic'))
        ref = table_ref(cache.get(101))
        source = 'tabViewDic[101]'
        source_diagnostics = {}
        if ref is not None:
            items, count = reader.list_items(ref)
            if count is None or count != len(items) or count > 64:
                raise FanxiuRuntimeMemoryError('ChaosBoss页签缓存不完整', code='runtime_incomplete')
        else:
            # FunctionData retains the already-loaded source config independently
            # of its lazy tab cache. Decode it, never call GetViewTabByType.
            config = table_ref(data.get('openFuncCfg'))
            if config is None:
                raise FanxiuRuntimeMemoryError('页签缓存及FunctionData已加载配置均缺失', code='data_not_loaded')
            raw = reader.fields(config)
            if table_ref(raw.get('_dt_')):
                values = reader.dictionary_fields(config)
            else:
                decoded = reader.table(config.address)
                values = dict(decoded['fields'])
                for key, value in enumerate(decoded['array']):
                    if value is not None:
                        if key in values and values[key] != value:
                            raise FanxiuRuntimeMemoryError('OpenFunction配置array/hash冲突', code='runtime_incomplete')
                        values.setdefault(key, value)
            if len(values) > 8192:
                raise FanxiuRuntimeMemoryError('OpenFunction配置超过有界范围', code='runtime_incomplete')
            config_items = [v for v in values.values() if table_ref(v)]
            selectors = read_runtime_config_rows(reader, config_items,
                environment_address=ctx.binding.environment_address,
                group_name='Open_function', table_name='OpenFunction', fields=('type', 'subType'),
                state_address=ctx.binding.state_address)
            source_diagnostics = {'source_row_count': len(config_items),
                                  'cached_field_names': sorted(str(k) for k in raw)[:16],
                                  'loaded_view_types': sorted({as_int(row.get('subType')) for row in selectors
                                      if as_int(row.get('type')) == 9 and as_int(row.get('subType')) is not None})}
            items = [v for v, row in zip(config_items, selectors) if row['type'] == 9 and row['subType'] == 101]
            count = len(items)
            source = 'FunctionData.openFuncCfg(type9/subType101)'
        rows = read_runtime_config_rows(reader, items,
            environment_address=ctx.binding.environment_address,
            group_name='Open_function', table_name='OpenFunction',
            fields=('id', 'type', 'subType', 'name', 'tittleName', 'luaPath', 'openValue', 'sort'),
            state_address=ctx.binding.state_address) if items else []
        for row in rows:
            for key, value in row.items():
                if value is not None and not isinstance(value, (str, int, float, bool)):
                    raise FanxiuRuntimeMemoryError(f'OpenFunction.{key}不是标量', code='runtime_incomplete')
        return {'available': True, 'scope': 'currently_loaded_function_tab_cache',
                'view_type': 101, 'tabs': rows, 'declared_count': count,
                'candidate_route_available': bool(rows),
                'route_source': source,
                'source_diagnostics': source_diagnostics,
                'panel_identity_verified': False, 'reward_cycle_verified': False,
                'read_only': True, 'captured_at': datetime.now().astimezone().isoformat(),
                'evidence': {'pid': ctx.memory.pid, 'process_start_ticks': ctx.memory.process_start_ticks}}
    except FanxiuRuntimeMemoryError as exc:
        return {'available': False, 'reason': str(exc), 'code': exc.code, 'read_only': True,
                'panel_identity_verified': False, 'reward_cycle_verified': False,
                'evidence': {'pid': ctx.memory.pid if ctx else None,
                            'process_start_ticks': ctx.memory.process_start_ticks if ctx else None}}


def read_wanxian_revenue_task_candidates() -> dict:
    """Observe the loaded type192 instance's Revenue/Quest native task IDs.

    ChaosBossModel.CheckCanDrawTaskReward2 passes its activityId to RevenueMgr,
    whose GetRevenueTaskRedDot reads QuestData. This is a native family candidate
    with an explicit instance period, still not proof #749 selected this branch
    or that today's formal attempt claimed any specific tier. Never authorizes
    claims and never reconstructs the missing original before observation.
    """
    from .activity_runtime import read_activity_instances_runtime_snapshot
    from .daily_task_rewards import read_activity_task_reward_snapshots
    from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
    activities = read_activity_instances_runtime_snapshot()
    instances = [row for row in activities['items'] if row['activityType'] == 192]
    if len(instances) != 1:
        return {'available': False, 'reason': 'loaded_activity192_identity_not_unique',
                'instance_candidates': instances, 'panel_identity_verified': False}
    instance = instances[0]
    shared = read_activity_task_reward_snapshots((), include_activity_tasks=True)
    a, b = activities['evidence'], shared.get('evidence') or {}
    same_process = all(a.get(key) is not None and a.get(key) == b.get(key)
                       for key in ('pid', 'process_start_ticks'))
    rows = json.loads((resolve_fanxiu_export_root() / 'parsed_configs/ActiveTask/rows.json').read_text(encoding='utf-8'))
    specs = {int(row['id']): row for row in rows if row.get('activityId') == instance['activityId'] and row.get('type') == 3}
    entries = [row for row in shared.get('task_entries', []) if as_int(row.get('taskId')) in specs]
    finished = sorted({as_int(value) for value in shared.get('finished_task_ids', [])
                       if as_int(value) in specs})
    present = {as_int(row.get('taskId')) for row in entries} | set(finished)
    return {'available': bool(shared.get('ok') and same_process and specs),
            'scope': 'loaded_activity192_revenue_quest_family_candidate',
            'activity_instance': instance, 'same_process_verified': same_process,
            'static_task_ids': sorted(specs), 'task_entries': entries,
            'finished_task_ids': finished, 'missing_task_ids': sorted(set(specs) - present),
            'task_names': {str(key): row.get('name') for key, row in specs.items()},
            'panel_identity_verified': False, 'reward_branch_verified': False,
            'original_claim_delta_verified': False, 'native_claimed_tier_count': None,
            'evidence': {'activity': a, 'quest': b}, 'read_only': True,
            'captured_at': datetime.now().astimezone().isoformat()}
