"""使用已有炼丹神珠的一次受限动作；积分与实际材料消耗独立归因。"""
import hashlib
import json

from sqlmodel import Session
from backend.db import engine
from backend.core.fanxiu.catalog.item import load_fanxiu_item_runtime_index
from backend.core.fanxiu.instrumentation import fanxiu_instrumentation_service as instrumentation
from backend.core.fanxiu.storage_bag_prayer_research import classify_prayer_box, record_prayer_material_experiment
from .prayer_task_progress import observe_active_prayer_round, open_prayer_activity_page
from .prayer_sunday_closeout import plan_prayer_closeout_transition
from .prayer_wish_update import use_prayer_pearls
from .prayer_soul_upgrade import upgrade_prayer_souls


def project_prayer_material_inventory(snapshot, *, base_id):
    """从完整当前库存保留精确材料数量、版本、时间和进程证据；不访问游戏。"""
    if (snapshot.get('complete') is not True
            or snapshot.get('source') != 'active_backpack_panel_item_info_list'
            or not snapshot.get('fingerprint')):
        raise ValueError('神珠实验需要完整当前库存')
    rows = [row for row in snapshot['items'] if row.get('base_id') == base_id]
    if any(type(row.get('num')) is not int or row['num'] < 0 for row in rows):
        raise ValueError('神珠库存数量不完整')
    return dict(complete=True, source=snapshot['source'], fingerprint=snapshot['fingerprint'],
        base_id=base_id, quantity=sum(row['num'] for row in rows),
        observed_at=snapshot['observed_at'], evidence=snapshot['evidence'])


def sample_alchemy_prayer_material(context, *, base_id: int):
    """最多一次正式祈愿/补魂，分别最多消耗1000/100材料，不采购、不领奖、不补满。

    操作前后分别取得同轮积分和完整库存；实际材料差值与动作spent相符才
    保存独立材料实验。缺少库存、材料不够或动作费用超过对应限额时正常返回，
    未消费不记样本。活动/轮次变化则丢弃样本；异常保留现场，不补账。
    返回前回世界。两种神珠的单次受限动作、实际消耗、同轮积分和世界退出均已实机验收。
    """
    if type(base_id) is not int or base_id not in (2006001, 2006006):
        raise ValueError('仅支持两种已核实的炼丹神珠')
    cards = load_fanxiu_item_runtime_index(rebuild_missing=False)['cards_by_id']
    rule = classify_prayer_box(cards[str(base_id)], cards)
    if rule['resource_categories'] != ['炼丹'] or not rule['classification_complete']:
        raise ValueError('当前神珠目录分类不符')
    targets = {'炼丹': {'3': 80000, '4': 100000, '5': 100000}}
    before = yield from observe_active_prayer_round(context, round_targets=targets)
    if before['round_score_gap'] < 50000:
        yield from context.go_scene(34)
        return {'outcome': 'preserve_experiment_space'}

    def inventory():
        yield from context.go_scene(34)
        yield from context.wait_click(34, '右侧菜单/储物袋', timeout=10)
        yield from context.wait_scene([525], wait=10)
        return project_prayer_material_inventory(instrumentation.backpack_ui_snapshot(), base_id=base_id)

    inventory_before = yield from inventory()
    yield from context.go_scene(34)
    if inventory_before['quantity'] <= 0:
        return {'outcome': 'waiting_stock'}
    yield from open_prayer_activity_page(context)

    def check_stopped():
        if context.stop_event is not None and context.stop_event.is_set():
            raise InterruptedError('祈愿材料实验已停止')

    action = use_prayer_pearls if base_id == 2006001 else upgrade_prayer_souls
    result = yield from action(context, check_stopped=check_stopped, log=print,
                               maximum_actions=1, maximum_spend=1000 if base_id == 2006001 else 100)
    if result.get('spent', 0) <= 0:
        yield from context.go_scene(34)
        return {'outcome': 'no_affordable_bounded_action', 'action_result': result}
    after = yield from observe_active_prayer_round(context, round_targets=targets)
    changed = plan_prayer_closeout_transition(before, after)
    if changed:
        yield from context.go_scene(34)
        return {**changed, 'experiment_saved': False}
    inventory_after = yield from inventory()
    payload = dict(base_id=base_id, consumed_count=result['spent'], action_result=result,
        catalog_fingerprint=rule['catalog_fingerprint'], prayer_category='炼丹',
        activity_key=before['activity_key'], prayer_before=before, prayer_after=after,
        inventory_before=inventory_before, inventory_after=inventory_after, isolated=True)
    payload['action_key'] = 'prayer-material:' + hashlib.sha256(json.dumps(payload,
        ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    with Session(engine) as session:
        saved = record_prayer_material_experiment(session, payload)
    yield from context.go_scene(34)
    return {'outcome': 'recorded', 'experiment': saved}
