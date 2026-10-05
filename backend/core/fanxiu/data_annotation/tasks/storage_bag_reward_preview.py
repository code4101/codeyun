"""复用正式储物袋定位，检查当前条件奖励预览，不点击打开或使用。"""
from backend.core.fanxiu.catalog.item import load_fanxiu_item_runtime_index
from backend.core.fanxiu.instrumentation import fanxiu_instrumentation_service as instrumentation
from backend.core.fanxiu.instrumentation.item_reward_preview import read_item_reward_preview
from backend.core.fanxiu.storage_bag_prayer_research import classify_prayer_box
from .storage_bag_random_box import (
    StorageBagFixedBoxGuiAdapter, StorageBagRandomBoxGuiAdapter, StorageBagRandomBoxRequest,
)


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
