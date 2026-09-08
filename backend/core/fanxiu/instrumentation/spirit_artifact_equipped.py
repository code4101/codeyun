"""精确装配投影：服务器 putUpSet 引用决定装备，不按背包阶数挑选。

字段依据 SpiritWareClientVO.GetCurPutList / GetServerInfo，以及现有
spirit_artifact_runtime_loader 的装备导出。此模块不调用 Lua 方法、发同步
请求或打开 UI。指定器及省略编号的全馆集合均已真实读取验收；全馆包含
已换本体的当前引用与服务器明确的空槽，不按旧库存阶数回退。
"""

from collections.abc import Mapping, Sequence
import time
from typing import Any

from .runtime_memory import FanxiuRuntimeMemoryError, as_int, manager_index_fields, table_ref


def project_spirit_artifact_equipped(
    references: Mapping[int, Sequence[str]], inventory: Mapping[str, Any],
) -> dict[str, Any]:
    """纯关联：仅接受完整库存和每器真实putUpSet，未装备部位输出空槽。

    所有引用须存在且唯一，背包实例所属灵器须一致；不回退到最高阶。
    空putUpSet仅在上游已证明serverInfo实际存在时才表示六个空槽。
    """
    if inventory.get('complete') is not True:
        raise FanxiuRuntimeMemoryError('精确装备投影需要完整本体库存')
    items = inventory.get('items') or []
    by_id = {row['item_id']: row for row in items}
    if len(by_id) != len(items):
        raise FanxiuRuntimeMemoryError('本体库存实例重复')
    equipped, slots, used = [], [], set()
    for ware_id, ids in sorted(references.items()):
        occupied = {}
        for uid in ids:
            row = by_id.get(uid)
            if uid in used or row is None or row['ware_id'] != ware_id:
                raise FanxiuRuntimeMemoryError('装配引用重复、缺失或跨灵器')
            part = row['part']
            if part not in range(1, 7) or part in occupied:
                raise FanxiuRuntimeMemoryError('装配部位无效或重复')
            used.add(uid)
            occupied[part] = uid
            equipped.append({**row, 'equipped': True})
        slots.extend({'ware_id': ware_id, 'part': part, 'item_id': occupied.get(part)}
                     for part in range(1, 7))
    return {'items': sorted(equipped, key=lambda r: (r['ware_id'], r['part'])),
            'slots': slots, 'equipped_count': len(equipped), 'complete': True}


def read_spirit_artifact_equipped_runtime(ware_ids: Sequence[int] | None = None) -> dict[str, Any]:
    """读取指定灵器的真实装备；例如1～8器调用range(1,9)，不写死48件。

    省略编号时读取服务器当前全部已加载灵器，并前后核验集合未变化。
    全部指定器必须已自然加载服务器信息；未知不是空装备。先读引用，
    再取完整库存，最后用新context复读引用，避免同reader缓存伪复验。
    """
    return read_spirit_artifact_owned_runtime(ware_ids)['equipped']


def read_spirit_artifact_owned_runtime(ware_ids: Sequence[int] | None = None) -> dict[str, Any]:
    """一次只读观察返回完整 inventory 与由它关联的 equipped。

    ware_ids 仅限制装备范围；inventory 始终包含全部本体（包括未装配备件），
    两者字段与原独立接口一致。需要同时核验库存和装备的调用方使用此入口，
    不再先单独读取库存。引用前态→完整库存→独立引用后态仍由提供方核验，
    不接受调用方传入的旧快照，不缓存业务值，不减少进程和引用一致性检查。

    返回 timings_seconds 分解本次观察耗时；它是单次样本，不是性能承诺。
    组合本身不是游戏原子快照；动作后须重新调用，不能跨动作复用返回结果。
    已在4-3重置后的稳定边界完成3轮独立/联合只读对照，全部业务字段一致。
    原装备读取路径与失败恢复策略保持不变；此对照不代替后续动作重入验收。
    """
    from .ui_runtime_context import read_ui_runtime_snapshot
    from .runtime_memory import resolve_lua_global_manager_root
    from .spirit_artifact import read_spirit_artifact_inventory_runtime

    requested = None if ware_ids is None else tuple(ware_ids)
    if requested is not None and (not requested or len(set(requested)) != len(requested) or any(
        type(i) is not int or i <= 0 for i in requested
    )):
        raise ValueError('需要不重复的正灵器编号')

    def observe_context(ctx):
        reader = ctx.reader

        def data_fields(r, root):
            manager = manager_index_fields(r, root, frozenset({'Inst_get'}))
            data = r.fields(r.fields(r.fields(manager.get('inst')).get('Model')).get('data'))
            if table_ref(data.get('v_wareDic')) is None:
                raise FanxiuRuntimeMemoryError('灵器服务器模型尚未加载')
            return data

        root, _, _ = resolve_lua_global_manager_root(
            ctx.memory, manager_key='spirit-artifact-equipped',
            state_address=ctx.binding.state_address, global_name='SpiritwareMgr',
            required_methods=frozenset({'Inst_get'}), validate=data_fields)
        data = data_fields(reader, root)
        # 当前客户端 Dictionary 使用 _dt_；数字键可处于 array/hash 两区。
        dictionary = reader.dictionary_fields(data['v_wareDic'])
        current_ids = requested if requested is not None else tuple(sorted(
            int(key) for key in dictionary if as_int(key) is not None and as_int(key) > 0
        ))
        if not current_ids:
            raise FanxiuRuntimeMemoryError('服务器灵器集合未加载')
        if requested is None and (len(current_ids) != len(dictionary) or len(set(current_ids)) != len(current_ids)):
            raise FanxiuRuntimeMemoryError('服务器灵器集合键不完整或重复')
        references = {}
        for ware_id in current_ids:
            ware = reader.fields(dictionary.get(ware_id))
            server = table_ref(ware.get('v_serverInfo'))
            if as_int(ware.get('v_wareId')) != ware_id or server is None:
                raise FanxiuRuntimeMemoryError(f'灵器{ware_id}服务器装配信息未加载')
            info = reader.fields(server)
            if as_int(info.get('type')) != ware_id or table_ref(info.get('putUpSet')) is None:
                raise FanxiuRuntimeMemoryError(f'灵器{ware_id}装配列表身份不完整')
            values, count = reader.list_items(info['putUpSet'])
            if count is None or not 0 <= count <= 6 or count != len(values):
                raise FanxiuRuntimeMemoryError('灵器装配列表数量异常')
            ids = [str(reader.long(value) or '') for value in values]
            if any(not uid or uid == '0' for uid in ids):
                raise FanxiuRuntimeMemoryError('灵器装配引用无效')
            references[ware_id] = tuple(ids)
        return references, (ctx.memory.pid, ctx.memory.process_start_ticks)

    def observe():
        # Equip actions replace putUpSet allocations. Let the shared observer
        # refresh stale memory mappings; never repeat the equip action here.
        return read_ui_runtime_snapshot([], observe_context)

    started = time.perf_counter()
    before, identity = observe()
    references_at = time.perf_counter()
    inventory = read_spirit_artifact_inventory_runtime()
    inventory_at = time.perf_counter()
    after, final_identity = observe()
    verified_at = time.perf_counter()
    if (before != after or identity != final_identity or
        identity != (inventory['pid'], inventory['process_start_ticks'])):
        raise FanxiuRuntimeMemoryError('读取期间灵器装备或游戏进程变化，请重新读取')
    equipped = {**project_spirit_artifact_equipped(before, inventory),
            'pid': identity[0], 'process_start_ticks': identity[1],
            'captured_at': time.time(), 'read_only': True,
            'source': 'spiritware_server_put_up_set'}
    return {'inventory': inventory, 'equipped': equipped,
            'timings_seconds': {
                'references_before': references_at - started,
                'inventory': inventory_at - references_at,
                'references_after': verified_at - inventory_at,
                'total': time.perf_counter() - started,
            }}
