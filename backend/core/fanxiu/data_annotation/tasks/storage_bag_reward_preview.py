"""复用正式储物袋定位，检查当前条件奖励预览，不点击打开或使用。"""
from dataclasses import asdict, is_dataclass
from backend.core.fanxiu.catalog.item import load_fanxiu_item_runtime_index
from backend.core.fanxiu.instrumentation import fanxiu_instrumentation_service as instrumentation
from backend.core.fanxiu.instrumentation.item_reward_preview import read_item_reward_preview, read_item_detail_identity
from backend.core.fanxiu.instrumentation.item_config import read_item_metadata_runtime
from backend.core.fanxiu.storage_bag_prayer_research import classify_prayer_box
from .storage_bag_random_box import (
    StorageBagFixedBoxGuiAdapter, StorageBagRandomBoxGuiAdapter, StorageBagRandomBoxRequest,
)


def inspect_storage_bag_item_detail(context, *, base_id: int, return_to_world: bool = True):
    """Observe an owned ordinary box's detail even when its reward graph is unknown.

    Reward classification cannot be a prerequisite for investigating that very
    classification. This separate diagnostic accepts only known box types,
    uses naturally loaded current item metadata (the static catalogue may lag),
    reuses the verified inventory-instance/GUI alignment, disables consumption,
    captures the raw detail and normally returns to world. Set return_to_world
    to False to retain the verified parent page for child-detail research;
    the caller owns safe departure. No reward/probability/score
    conclusion or production permission is derived from opening the detail.
    """
    if type(base_id) is not int or base_id <= 0:
        raise ValueError('详情取证需要正整数道具ID')
    if type(return_to_world) is not bool:
        raise ValueError('return_to_world must be bool')
    metadata = read_item_metadata_runtime([base_id], force=True)
    if metadata.get('complete') is not True:
        raise ValueError('详情取证需要完整当前道具类型身份')
    native = metadata['items_by_id'].get(base_id) or {}
    card = dict(name=native.get('item_name'), type=native.get('item_type_id'),
                sub_type=native.get('item_sub_type_id'))
    if card.get('type') != 2 or card.get('sub_type') not in (1, 2) or not card.get('name'):
        raise ValueError('详情取证仅支持当前Runtime确认的普通固定/随机箱类型')
    cards = {str(base_id): card}
    yield from context.go_scene(525)
    snapshot = instrumentation.backpack_ui_snapshot()
    if snapshot.get('complete') is not True:
        raise ValueError('详情取证需要完整当前储物袋列表')
    items = [row for row in snapshot['items'] if row.get('base_id') == base_id and row.get('num', 0) > 0]
    if not items:
        yield from context.go_scene(34)
        return {'available': False, 'reason': 'absent_from_current_bag_list',
                'base_id': base_id, 'consumption_authorized': False}
    item = items[0]
    adapter_type = StorageBagFixedBoxGuiAdapter if card['sub_type'] == 1 else StorageBagRandomBoxGuiAdapter
    adapter = adapter_type(context=context,
        snapshot_reader=instrumentation.backpack_ui_snapshot, catalog_cards_by_id=cards,
        recorder=lambda _execution: None, detail_only=True)
    detail = yield from adapter.open_detail(StorageBagRandomBoxRequest(base_id=base_id,
        instance_id=item['instance_id'], name=card['name'], quantity=item['num']), snapshot=snapshot)
    identity = read_item_detail_identity(expected_item_id=base_id)
    if identity['instance_id'] != str(item['instance_id']) or identity['quantity'] != item['num']:
        raise ValueError('详情取证与当前库存实例或数量不一致')
    frame = context.cur_frame(update=True)
    if return_to_world:
        yield from context.go_scene(34)
    return {'available': True, 'base_id': base_id, 'instance_id': item['instance_id'],
            'quantity': item['num'],
            'detail_scene_id': adapter.detail_scene_id,
            'native_identity': identity,
            'detail': {key: asdict(value) if is_dataclass(value) else value
                       for key, value in detail.items()}, 'frame_data_url': frame,
            'consumption_authorized': False,
            'final_scene': 34 if return_to_world else adapter.detail_scene_id}


def inspect_storage_bag_reward_preview(context, *, base_id: int):
    """从完整当前库存定位固定/随机箱，核验两次稳定预览后返回世界。

    此接口只导航、读取；不消耗物品，不记录收益，也不授予生产准入。
    返回当前目录指纹与客户端过滤分支，供研究决定下一步取证。
    缺失库存、未知类型、详情身份或完整列表不可靠时保留现场并报错。
    已在19070082详情实机核验连续稳定完整预览并返回世界。
    """
    if type(base_id) is not int or base_id <= 0:
        raise ValueError('奖励预览需要实际箱子正整数 ID')
    cards = load_fanxiu_item_runtime_index(rebuild_missing=False)['cards_by_id']
    if str(base_id) not in cards:
        raise ValueError('奖励预览目标不在当前目录')
    rule = classify_prayer_box(cards[str(base_id)], cards)
    if not rule['classification_complete'] or rule['operation_kind'] not in ('fixed', 'random'):
        raise ValueError('奖励预览仅支持已分类固定或随机箱')
    yield from context.go_scene(34)
    yield from context.wait_click(34, '右侧菜单/储物袋', timeout=10)
    yield from context.wait_scene([525], wait=10)
    snapshot = instrumentation.backpack_ui_snapshot()
    if snapshot.get('complete') is not True:
        raise ValueError('奖励预览需要完整当前储物袋库存')
    item = next((row for row in snapshot['items']
                 if row.get('base_id') == base_id and row.get('num', 0) > 0), None)
    if item is None:
        raise ValueError('奖励预览目标当前无库存')

    def reject_consumption(_execution):
        raise RuntimeError('只读奖励预览禁止执行资源消耗')

    adapter_type = StorageBagFixedBoxGuiAdapter if rule['operation_kind'] == 'fixed' else StorageBagRandomBoxGuiAdapter
    adapter = adapter_type(context=context, snapshot_reader=instrumentation.backpack_ui_snapshot,
        catalog_cards_by_id=cards, recorder=reject_consumption)
    yield from adapter.open_detail(StorageBagRandomBoxRequest(base_id=base_id,
        instance_id=item['instance_id'], name=rule['name'], quantity=item['num'], open_quantity=1),
        snapshot=snapshot)
    previous = None
    for _ in range(8):
        current = read_item_reward_preview(expected_item_id=base_id)
        if current['instance_id'] != str(item['instance_id']):
            raise ValueError('详情预览与当前库存实例身份不符')
        if previous is not None and all(current[key] == previous[key]
                for key in ('item_id', 'instance_id', 'preview_fingerprint', 'rewards')):
            yield from context.go_scene(34)
            return {**current, 'catalog_fingerprint': rule['catalog_fingerprint']}
        previous = current
        yield from context.wait_action_settle(1.0)
    raise ValueError('奖励预览未形成两次稳定完整列表')


def inspect_storage_bag_batch_use_identity(context, *, base_id: int):
    """只打开已实机证明会进入#584的多件箱数量窗，核验后离场。

    详情页“打开”在这三个已验收分支中打开数量窗；只有数量窗的
    第二次“使用”才消费。本取证禁止第二次点击，也拒绝单件库存，
    不把普通箱类型推断为其他道具的安全批量入口。
    """
    if base_id not in (38100035, 39036247, 400008012):
        raise ValueError('该道具的无消费批量弹窗入口尚未实机核实')
    from backend.core.fanxiu.instrumentation import fanxiu_instrumentation_service as ins
    yield from context.go_scene(525)
    bag = ins.backpack_ui_snapshot()
    owned = [r for r in bag.get('items', []) if r.get('base_id') == base_id]
    if bag.get('complete') is not True or len(owned) != 1 or owned[0].get('num', 0) <= 1:
        raise ValueError('无消费批量弹窗取证要求唯一实例且库存大于1')
    detail = yield from inspect_storage_bag_item_detail(context, base_id=base_id, return_to_world=False)
    if not detail.get('available') or detail['quantity'] <= 1:
        raise ValueError('批量弹窗取证详情库存不完整')
    yield from context.wait_click(detail['detail_scene_id'], '打开')
    yield from context.wait_scene_exact([584], timeout=15)
    from .storage_bag_random_box import adjust_bounded_use_quantity
    yield from adjust_bounded_use_quantity(context, 1, maximum=detail['quantity'])
    from backend.core.fanxiu.instrumentation.item_batch_use_dialog import read_item_batch_use_dialog_snapshot
    selected = read_item_batch_use_dialog_snapshot(expected_item_id=base_id)
    if (selected.get('instance_id') != detail['instance_id']
            or selected.get('owned_count') != detail['quantity'] or selected.get('current') != 1):
        raise ValueError('批量弹窗与详情实例不一致，保留现场禁止消费')
    yield from context.wait_click_then_scene(584, '返回', [525], timeout=20)
    after = ins.backpack_ui_snapshot()
    current = [r for r in after.get('items', []) if r.get('instance_id') == detail['instance_id']]
    if after.get('complete') is not True or len(current) != 1 or current[0].get('num') != detail['quantity']:
        raise ValueError('批量弹窗取证离场后库存未保持，不能验收无消费')
    before_counts = {r['instance_id']: r['num'] for r in bag['items'] if not r.get('is_padding')}
    after_counts = {r['instance_id']: r['num'] for r in after['items'] if not r.get('is_padding')}
    if any(after_counts.get(key, 0) < count for key, count in before_counts.items()):
        raise ValueError('批量弹窗取证发生其他库存减少，不能验收无消费')
    yield from context.go_scene(34)
    return {'complete': True, 'base_id': base_id, 'selected': selected,
            'inventory_before': detail['quantity'], 'inventory_after': current[0]['num'],
            'resource_consumption': 0, 'final_scene': 34}


def inspect_storage_bag_reward_item_detail(context, *, base_id: int, reward_item_id: int):
    """Open a bound child reward's tooltip without choosing or using it.

    Currently supports the visually annotated first icon of a two-item ordinary
    box preview. Other layouts fail before clicking. The resulting child page
    is retained for research; the caller owns safe departure/scene annotation.
    Native parent instance, rendered reward order and child identity are checked.
    Neither tooltip identity nor its description proves a full reward graph.
    """
    if type(reward_item_id) is not int or reward_item_id <= 0:
        raise ValueError('子奖励详情需要正整数道具ID')
    parent = yield from inspect_storage_bag_item_detail(
        context, base_id=base_id, return_to_world=False)
    if not parent['available']:
        return parent
    preview = read_item_reward_preview(expected_item_id=base_id)
    if preview['instance_id'] != parent['instance_id']:
        raise ValueError('子奖励详情的父物品实例已变化')
    rewards = preview['rewards']
    if not any(row['item_id'] == reward_item_id for row in rewards):
        raise ValueError(f'当前预览没有目标子奖励 {reward_item_id}；'
                         f'实际奖励ID={[row["item_id"] for row in rewards]}，保留现场')
    if (len(rewards) != 2 or rewards[0]['item_id'] != reward_item_id
            or rewards[0]['ui_index'] != 1):
        raise ValueError('子奖励详情尚未支持该目标位置；保留现场，不猜坐标')
    yield from context.wait_click(parent['final_scene'], '首项奖励详情图标')
    yield from context.wait_action_settle(1.0)
    child = read_item_detail_identity(expected_item_id=reward_item_id)
    if child['quantity'] != rewards[0]['quantity']:
        raise ValueError('子奖励详情数量与已绑定预览不一致')
    return dict(available=True, parent_item_id=base_id,
        parent_instance_id=parent['instance_id'], parent_scene=parent['final_scene'],
        parent_preview_fingerprint=preview['preview_fingerprint'], child_identity=child,
        frame_data_url=context.cur_frame(update=True), consumption_authorized=False,
        child_scene_retained=True)
