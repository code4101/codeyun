"""Read already-loaded fairyland boss facts; never request or initialize them."""
from datetime import datetime
from time import perf_counter

from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import acquire_lua_global_runtime_context


def read_fairyland_boss_snapshot() -> dict:
    """Observe XianJieBossMgr.Model.XianJieBossData in one process generation.

    The client updates bossMapInfoVoDic on both list and individual updates;
    bossInfoVOS alone can retain replaced VOs. Read the current group dictionary
    separately from that initial list. Unloaded lists/dictionaries are unknown,
    never proof of zero targets. Drop records are a cached shared-server feed,
    not an account reward receipt or proof of fatigue consumption. No Lua call,
    singleton initialization, protocol send, GUI action or persistent write.
    """
    started = perf_counter()
    ctx = None
    try:
        ctx = acquire_lua_global_runtime_context({'XianJieBossMgr'})
        reader = ctx.reader
        manager = ctx.loaded_manager_fields('XianJieBossMgr', frozenset({
            'LuaXianJieBossMgr', 'Inst_get', 'GetXianJieBossDropRecord'}))
        instance = reader.fields(manager.get('inst'))
        model = reader.fields(instance.get('Model'))
        data = reader.fields(model.get('XianJieBossData'))
        if not data:
            raise FanxiuRuntimeMemoryError('仙界首领实例尚未加载', code='data_not_loaded')

        def boss(value):
            row = reader.fields(value)
            result = {key: as_int(row.get(key)) for key in (
                'mapId', 'unitId', 'bossId', 'bossGroupId', 'currHp', 'maxHp')}
            result.update({key: row.get(key) if isinstance(row.get(key), bool) else None
                           for key in ('isDead', 'isBigBoss', 'isFocus')})
            return result

        list_ref = table_ref(data.get('bossInfoVOS'))
        list_count = None
        if list_ref is not None:
            maps, list_count = reader.list_items(list_ref)
            if list_count is None or list_count != len(maps) or list_count > 4096:
                raise FanxiuRuntimeMemoryError('仙界首领初始列表不完整', code='runtime_incomplete')
        dictionary = data.get('bossMapInfoVoDic')
        dictionary_loaded = table_ref(reader.fields(dictionary).get('_dt_')) is not None
        groups = reader.dictionary_fields(dictionary) if dictionary_loaded else {}
        if len(groups) > 4096:
            raise FanxiuRuntimeMemoryError('仙界首领字典超过有界范围', code='runtime_incomplete')
        bosses = [dict(dictionary_group_id=as_int(key), **boss(value))
                  for key, value in groups.items()]
        validated = bool(bosses) and all(
            row['bossGroupId'] == row['dictionary_group_id']
            and row['isDead'] is not None and row['isBigBoss'] is not None
            for row in bosses)
        drops_ref = table_ref(data.get('xianJieBossDropRecord'))
        drops = None
        if drops_ref is not None:
            items, count = reader.list_items(drops_ref)
            if count is None or count != len(items) or count > 4096:
                raise FanxiuRuntimeMemoryError('仙界掉落记录不完整', code='runtime_incomplete')
            drops = []
            for item in items:
                row = reader.fields(item)
                drops.append({**{key: as_int(row.get(key)) for key in ('mapId', 'bossId', 'itemId')},
                              'dropTime': reader.long(row.get('dropTime')),
                              **{key: row.get(key) if isinstance(row.get(key), str) else None
                                 for key in ('roleName', 'serverName')}})
        fatigue = as_int(data.get('fatigue'))
        return {'available': True, 'complete': validated and list_count is not None and fatigue is not None,
                'scope': 'currently_loaded_fairyland_boss_group_cache',
                'protocol': 'XianJieBossMgr.Model.XianJieBossData',
                'read_only': True, 'reward_remaining': fatigue,
                'initial_list_count': list_count, 'group_dictionary_loaded': dictionary_loaded,
                'bosses': bosses, 'normal_boss_alive_count': sum(
                    row['isBigBoss'] is False and row['isDead'] is False for row in bosses
                ) if validated else None,
                'attention_target': boss(data['attentionDown']) if table_ref(data.get('attentionDown')) else None,
                'drop_records': drops, 'drop_records_scope': 'cached_shared_server_feed',
                'account_reward_receipt_verified': False,
                'captured_at': datetime.now().astimezone().isoformat(),
                'evidence': {'pid': ctx.memory.pid, 'process_start_ticks': ctx.memory.process_start_ticks},
                'elapsed_seconds': perf_counter() - started}
    except FanxiuRuntimeMemoryError as exc:
        return {'available': False, 'complete': False, 'read_only': True,
                'scope': 'currently_loaded_fairyland_boss_group_cache',
                'reason': str(exc), 'code': exc.code,
                'evidence': {'pid': ctx.memory.pid if ctx else None,
                            'process_start_ticks': ctx.memory.process_start_ticks if ctx else None},
                'elapsed_seconds': perf_counter() - started}
