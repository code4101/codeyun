"""读取热更新客户端已加载的完整箱子奖励图；不执行 Lua 或消费物品。"""
from __future__ import annotations

import time
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping

from .item_config import read_loaded_item_metadata, read_loaded_item_text, _item_config_table, _DB_METHODS
from .resource_auto_use import read_runtime_config_rows
from .runtime_memory import FanxiuRuntimeMemoryError, as_int, manager_index_fields, resolve_lua_global_manager_root, table_ref
from .ui_runtime_context import acquire_ui_runtime_context


def index_loaded_optional_gift_rows(rows: Iterable[Mapping]) -> dict:
    """规范化已解码完整配置；保留条件分支，拒绝重复身份和无效数量。

    实机热更新 groupID 使用字符串，未分配行采用空组/零奖励占位。
    占位行不进入奖池，不把其他缺失值当作零。
    """
    groups = defaultdict(list)
    seen = set()
    for row in rows:
        if row['groupID'] == '' and row['giftID'] == 0 and row['number'] == 0:
            continue
        identity, group, child, count = (
            int(row[k]) if isinstance(row[k], str) and row[k].isdigit() else as_int(row[k])
            for k in ('id', 'groupID', 'giftID', 'number'))
        if None in (identity, group, child, count) or min(identity, group, child, count) <= 0 or identity in seen:
            raise FanxiuRuntimeMemoryError(f'OptionalGift配置身份、数量缺失或重复：{row}')
        if not isinstance(row['showCondition'], str):
            raise FanxiuRuntimeMemoryError('OptionalGift条件缺失')
        seen.add(identity)
        groups[group].append(dict(id=child, count=count, config_id=identity, show_condition=row['showCondition']))
    return {group: sorted(rewards, key=lambda x: x['config_id']) for group, rewards in groups.items()}


def resolve_optional_gift_reward_source(effect_value: object, groups: Mapping) -> dict:
    """Distinguish unsupported protocols from a missing OptionalGift group.

    A bare number is not an OptionalGift group ID. The exported client's
    GetStoreBagItemList has a numeric DropTeam path, but that alone does not
    bind a particular item to that path. Keep such items unresolved until the
    relationship and current source rows are proved; never infer probabilities.
    """
    parts = str(effect_value).split('_')
    if len(parts) != 2 or not parts[1].isdigit():
        return dict(resolved=False, reason='unsupported_reward_protocol',
                    effect_value=effect_value)
    group_id = int(parts[1])
    if group_id not in groups:
        return dict(resolved=False, reason='optional_gift_group_missing',
                    group_id=group_id, effect_value=effect_value)
    return dict(resolved=True, source='Item.OptionalGift', group_id=group_id,
                rewards=groups[group_id])


def _loaded_reward_config_context():
    """Resolve the naturally loaded config dictionary through validated DBMgr."""
    ctx = acquire_ui_runtime_context(())
    root, _, environment = resolve_lua_global_manager_root(
        ctx.memory, manager_key='mail-item-config', state_address=ctx.binding.state_address,
        global_name='DBMgr', required_methods=_DB_METHODS, validate=_item_config_table)
    inst = ctx.reader.fields(manager_index_fields(ctx.reader, root, _DB_METHODS).get('inst'))
    configs = table_ref(ctx.reader.fields(inst.get('ConfigDic')).get('_dt_'))
    if configs is None:
        raise FanxiuRuntimeMemoryError('自然加载的配置字典缺失')
    return ctx, environment, configs


def inspect_item_reward_graph_sources_runtime(item_ids: Iterable[int]) -> dict:
    """Inventory available runtime sources without loading configs or using items.

    Static exports cannot establish whether a newer client naturally loaded a
    different reward table. This diagnostic returns current item protocols and
    loaded reward-related table names from one validated process. It proves
    source availability only: neither a matching numeric ID nor a table name
    proves a reward relationship, pool completeness, probability or permission.
    No Lua calls, server requests or GUI actions are submitted.
    """
    wanted = tuple(item_ids)
    if not wanted or len(wanted) > 128 or any(type(x) is not int or x <= 0 for x in wanted):
        raise ValueError('奖励图来源诊断需要1至128个正整数物品ID')
    started = time.perf_counter()
    ctx, _environment, configs = _loaded_reward_config_context()
    packed = ctx.reader.table(configs.address)
    names = sorted(k for k, v in packed['fields'].items()
                   if isinstance(k, str) and table_ref(v) is not None)
    if not names or len(names) > 10000:
        raise FanxiuRuntimeMemoryError('自然加载的配置名称列表缺失或超过边界')
    metadata, diagnostics = read_loaded_item_metadata(wanted, memory=ctx.memory,
        reader=ctx.reader, state_address=ctx.binding.state_address)
    if not diagnostics['complete']:
        raise FanxiuRuntimeMemoryError(f"奖励图来源诊断道具缺失：{diagnostics['missing_ids']}")
    return dict(source='loaded_runtime_reward_source_inventory', read_only=True,
        source_inventory_complete=True, reward_graph_complete=False,
        requested_ids=list(wanted),
        items={str(i): dict(name=r['item_name'], type=r['item_type_id'],
            sub_type=r['item_sub_type_id'], effect_value=r['effect_value'])
            for i, r in metadata.items()},
        loaded_config_count=len(names),
        loaded_config_names=names,
        loaded_reward_config_names=[name for name in names
            if re.search(r'reward|gift|drop|pandora|box', name, re.IGNORECASE)],
        pid=ctx.memory.pid, process_start_ticks=ctx.memory.process_start_ticks,
        captured_at=time.time(), elapsed_seconds=round(time.perf_counter()-started, 4))


def read_item_reward_graph_runtime(item_ids: Iterable[int]) -> dict:
    """返回可交给 classify_prayer_box 的当前配置 cards_by_id 和完整性诊断。

    静态目录可能落后于服务器热更新。本接口在同一进程上下文中读取
    DBMgr 已自然加载的 Item/OptionalGift，保留全部 showCondition 分支，
    递归解析最多 512 个道具；未支持的奖励协议保留 unresolved_item_ids，
    complete=False；配置身份错误或循环报错。这里证明配置
    奖励图，不证明当前条件过滤、服务器概率或实际计分，不能授予开箱权限。
    """
    wanted = tuple(item_ids)
    if not wanted or len(wanted) > 128 or any(type(x) is not int or x <= 0 for x in wanted):
        raise ValueError('奖励图需要1至128个正整数物品ID')
    started = time.perf_counter()
    ctx, environment, configs = _loaded_reward_config_context()
    table = table_ref(ctx.reader.string_fields(configs.address, frozenset({'Item.OptionalGift'})).get('Item.OptionalGift'))
    rows = []
    groups = None
    reward_source = 'Item.OptionalGift'
    if table is not None:
        packed = ctx.reader.table(table.address)
        raw = dict(packed['fields'])
        for key, value in enumerate(packed['array']):
            if value is not None:
                if key in raw and raw[key] != value:
                    raise FanxiuRuntimeMemoryError('OptionalGift array/hash身份冲突')
                raw[key] = value
        refs = [v for k,v in raw.items() if (as_int(k) or 0) > 0 and table_ref(v)]
        if not refs or len(refs) > 100000:
            raise FanxiuRuntimeMemoryError('OptionalGift 完整配置列表缺失或超过边界')
        rows = read_runtime_config_rows(ctx.reader, refs, environment_address=environment,
            group_name='Item', table_name='OptionalGift',
            fields=('id','groupID','giftID','number','showCondition'))
        groups = index_loaded_optional_gift_rows(rows)
    # A missing lazy reward table says nothing about its pool. Item metadata
    # and unrelated leaf resources remain readable; box edges stay unresolved.
    cards, pending, unresolved, unresolved_sources = {}, set(wanted), set(), {}
    for _depth in range(16):
        if not pending:
            break
        if len(cards)+len(pending)>512:
            raise FanxiuRuntimeMemoryError('奖励图超过512个道具')
        metadata, diagnostics = read_loaded_item_metadata(pending, memory=ctx.memory,
            reader=ctx.reader, state_address=ctx.binding.state_address)
        if not diagnostics['complete']:
            raise FanxiuRuntimeMemoryError(f"奖励图道具缺失：{diagnostics['missing_ids']}")
        texts = {v for r in metadata.values() for k,v in r.items()
                 if k in ('runtime_description_id','runtime_effect_description_id') and type(v) is int and v>0}
        localized = read_loaded_item_text(texts, reader=ctx.reader, state_address=ctx.binding.state_address,
            environment_address=environment) if texts else {'texts_by_id':{},'complete':True}
        if not localized['complete']:
            raise FanxiuRuntimeMemoryError('奖励图本地化文本缺失')
        next_ids = set()
        for item_id, meta in metadata.items():
            if not meta['item_name']:
                raise FanxiuRuntimeMemoryError('奖励图物品名称缺失')
            card = dict(id=item_id,name=meta['item_name'],type=meta['item_type_id'],
                sub_type=meta['item_sub_type_id'],effect_value=meta['effect_value'],
                description=localized['texts_by_id'].get(meta['runtime_description_id'],''),
                effect_description=localized['texts_by_id'].get(meta['runtime_effect_description_id'],''))
            if card['type'] in (2,21):
                resolution = (resolve_optional_gift_reward_source(card['effect_value'], groups)
                              if groups is not None else
                              dict(resolved=False, reason='optional_gift_config_not_loaded',
                                   effect_value=card['effect_value']))
                if not resolution['resolved']:
                    unresolved.add(item_id)
                    unresolved_sources[str(item_id)] = resolution
                else:
                    card['optional_gift_rewards'] = resolution['rewards']
                    next_ids.update(r['id'] for r in card['optional_gift_rewards'])
            cards[str(item_id)] = card
        pending = next_ids-{int(x) for x in cards}
    if pending:
        raise FanxiuRuntimeMemoryError('奖励图超过16层')
    checked = set()
    def check_cycle(item_id, path):
        if item_id in path:
            raise FanxiuRuntimeMemoryError(f'奖励图循环：{item_id}')
        if item_id in checked:
            return
        for reward in cards[str(item_id)].get('optional_gift_rewards',[]):
            check_cycle(reward['id'], path | {item_id})
        checked.add(item_id)
    for item_id in wanted:
        check_cycle(item_id, set())
    return dict(complete=not unresolved,unresolved_item_ids=sorted(unresolved),
        unresolved_sources=unresolved_sources,
        read_only=True,source='loaded_runtime_item_reward_graph',
        requested_ids=list(wanted),cards_by_id=cards,config_reward_row_count=len(rows),
        optional_gift_config_loaded=groups is not None,
        optional_gift_source=reward_source if groups is not None else None,
        pid=ctx.memory.pid,process_start_ticks=ctx.memory.process_start_ticks,
        captured_at=time.time(),elapsed_seconds=round(time.perf_counter()-started,4))
