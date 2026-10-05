"""读取热更新客户端已加载的完整箱子奖励图；不执行 Lua 或消费物品。"""
from __future__ import annotations

import time
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
    ctx = acquire_ui_runtime_context(())
    root, _, environment = resolve_lua_global_manager_root(
        ctx.memory, manager_key='mail-item-config', state_address=ctx.binding.state_address,
        global_name='DBMgr', required_methods=_DB_METHODS, validate=_item_config_table)
    # Resolve through the validated manager rather than a second global lookup.
    inst = ctx.reader.fields(manager_index_fields(ctx.reader, root, _DB_METHODS).get('inst'))
    configs = table_ref(ctx.reader.fields(inst.get('ConfigDic')).get('_dt_'))
    table = table_ref(ctx.reader.string_fields(configs.address, frozenset({'Item.OptionalGift'})).get('Item.OptionalGift')) if configs else None
    if table is None:
        raise FanxiuRuntimeMemoryError('Item.OptionalGift 尚未自然加载')
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
    cards, pending, unresolved = {}, set(wanted), set()
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
                parts = str(card['effect_value']).split('_')
                if len(parts)!=2 or not parts[1].isdigit() or int(parts[1]) not in groups:
                    unresolved.add(item_id)
                else:
                    card['optional_gift_rewards'] = groups[int(parts[1])]
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
        read_only=True,source='loaded_runtime_item_reward_graph',
        requested_ids=list(wanted),cards_by_id=cards,config_reward_row_count=len(rows),
        pid=ctx.memory.pid,process_start_ticks=ctx.memory.process_start_ticks,
        captured_at=time.time(),elapsed_seconds=round(time.perf_counter()-started,4))
