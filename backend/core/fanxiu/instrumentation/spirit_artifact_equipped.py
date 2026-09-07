"""精确装配投影：服务器 putUpSet 引用决定装备，不按背包阶数挑选。

字段依据 SpiritWareClientVO.GetCurPutList / GetServerInfo，以及现有
spirit_artifact_runtime_loader 的装备导出。此模块不调用 Lua 方法、发同步
请求或打开 UI。字段契约仍需真实 Runtime 验收，不以离线测试代替。
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


def read_spirit_artifact_equipped_runtime(ware_ids: Sequence[int]) -> dict[str, Any]:
    """读取指定灵器的真实装备；例如1～8器调用range(1,9)，不写死48件。

    全部指定器必须已自然加载服务器信息；未知不是空装备。先读引用，
    再取完整库存，最后用新context复读引用，避免同reader缓存伪复验。
    """
    from .ui_runtime_context import acquire_ui_runtime_context
    from .runtime_memory import resolve_lua_global_manager_root
    from .spirit_artifact import read_spirit_artifact_inventory_runtime

    requested = tuple(ware_ids)
    if not requested or len(set(requested)) != len(requested) or any(
        type(i) is not int or i <= 0 for i in requested
    ):
        raise ValueError('需要不重复的正灵器编号')

    def observe():
        ctx = acquire_ui_runtime_context([])
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
        dictionary = reader.fields(reader.fields(data['v_wareDic']).get('_valueTable_'))
        references = {}
        for ware_id in requested:
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

    before, identity = observe()
    inventory = read_spirit_artifact_inventory_runtime()
    after, final_identity = observe()
    if (before != after or identity != final_identity or
        identity != (inventory['pid'], inventory['process_start_ticks'])):
        raise FanxiuRuntimeMemoryError('读取期间灵器装备或游戏进程变化，请重新读取')
    return {**project_spirit_artifact_equipped(before, inventory),
            'pid': identity[0], 'process_start_ticks': identity[1],
            'captured_at': time.time(), 'read_only': True,
            'source': 'spiritware_server_put_up_set'}
