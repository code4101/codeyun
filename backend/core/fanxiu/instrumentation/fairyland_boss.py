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
                'mapId', 'sceneId', 'bossId', 'bossGroupId', 'bossState')}
            result.update({key: reader.long(row.get(key)) for key in ('unitId', 'endTime')})
            # Native packet declares HP as double; casting huge values to int
            # creates spurious precision. Keep the actual decoded number.
            result.update({key: row.get(key) if type(row.get(key)) in (int, float) else None
                           for key in ('currHp', 'maxHp')})
            result['killerName'] = row.get('killerName') if isinstance(row.get('killerName'), str) else None
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


def read_active_fairyland_boss_target() -> dict:
    """Read the unique active BossCommonView's fairyland target, without opening it.

    BossCommonView.data is a packed BossGroup config row. Resolve its declared
    id/map columns rather than treating raw Lua array slots as semantic fields.
    The live group cache is independently read and must share the process
    generation and map/group identity. Closed/ordinary/ambiguous panels remain
    unknown; this observation never authorizes a click or reward-credit claim.
    """
    from .ui_runtime_context import read_ui_runtime_snapshot, read_active_ui_window_panels
    from .resource_auto_use import read_runtime_config_rows

    def observe(ctx):
        panels = read_active_ui_window_panels(ctx, 'BossCommonView')
        evidence = {'pid': ctx.memory.pid, 'process_start_ticks': ctx.memory.process_start_ticks}
        base = {'read_only': True, 'scope': 'active_fairyland_boss_detail_target',
                'reward_credit_verified': False, 'evidence': evidence}
        if len(panels) != 1:
            return dict(base, available=False, reason='detail_panel_closed_or_ambiguous')
        panel = ctx.reader.fields(panels[0])
        if panel.get('isXianJie') is not True or table_ref(panel.get('data')) is None:
            return dict(base, available=False, reason='panel_not_fairyland_or_target_not_loaded')
        cfg = read_runtime_config_rows(ctx.reader, [panel['data']],
            environment_address=ctx.binding.environment_address,
            group_name='BossNeutral', table_name='BossGroup', fields=('id', 'map'),
            state_address=ctx.binding.state_address)[0]
        group, map_id = as_int(cfg.get('id')), as_int(cfg.get('map'))
        snapshot = read_fairyland_boss_snapshot()
        same = all(evidence[key] is not None and evidence[key] == snapshot.get('evidence', {}).get(key)
                   for key in ('pid', 'process_start_ticks'))
        matches = [row for row in snapshot.get('bosses', []) if row['bossGroupId'] == group and row['mapId'] == map_id]
        verified = bool(group and map_id and same and len(matches) == 1 and matches[0]['unitId'])
        return dict(base, available=verified, panel_target_identity_verified=verified,
                    boss_group_id=group, map_id=map_id, target=matches[0] if verified else None,
                    reward_remaining=snapshot.get('reward_remaining'),
                    captured_at=datetime.now().astimezone().isoformat())

    try:
        return read_ui_runtime_snapshot(('Window', 'BossCommonView', 'isXianJie', 'data'), observe, fast=True)
    except FanxiuRuntimeMemoryError as exc:
        return {'available': False, 'read_only': True, 'reason': str(exc), 'code': exc.code,
                'scope': 'active_fairyland_boss_detail_target', 'reward_credit_verified': False}
