"""单类祈愿箱原子研究入口：一次开箱、一组完整前后事实。

研发 Cell 调用此任务，不拼装第二套开箱流程。固定箱额外奖励尚未归因时，
保留真实开箱凭证及独立未归因审计事实，返回 excluded，不写收益样本；
中断或观察失败也不补账。
这不是周日收尾作业，不推进任何 Job 时间，也不打开生产验收开关。
"""
from sqlmodel import Session

from backend.db import engine
from backend.core.fanxiu.catalog.item import load_fanxiu_item_runtime_index
from backend.core.fanxiu.instrumentation import fanxiu_instrumentation_service as instrumentation
from backend.core.fanxiu.instrumentation.wallet import read_wallet_currency_snapshot
from backend.core.fanxiu.storage_bag_prayer_research import (
    classify_prayer_box, record_prayer_box_experiment, validate_fixed_box_reward_attribution,
    record_unattributed_fixed_box_experiment,
)
from .prayer_task_progress import observe_active_prayer_round
from .prayer_sunday_closeout import plan_prayer_closeout_transition
from .storage_bag_auto_claim_execution import persist_box_execution_after_verified_open
from .storage_bag_random_box import (
    StorageBagFixedBoxGuiAdapter, StorageBagRandomBoxGuiAdapter, StorageBagRandomBoxRequest,
)


def validate_prayer_box_experiment_request(payload):
    """动作前拒绝不明确的物品、数量及未声明轮次规则；不访问游戏。"""
    for key in ('base_id', 'open_quantity'):
        if type(payload.get(key)) is not int or payload[key] <= 0:
            raise ValueError(f'祈愿原子实验需要正整数 {key}')
    if not isinstance(payload.get('prayer_round_targets'), dict) or not payload['prayer_round_targets']:
        raise ValueError('祈愿原子实验需要已核实的轮次规则')
    if 'inspect_soil_reward_modifier' in payload and type(payload['inspect_soil_reward_modifier']) is not bool:
        raise ValueError('土灵技能取证开关必须是布尔值')
    if 'allow_outer_container' in payload and type(payload['allow_outer_container']) is not bool:
        raise ValueError('外箱转换实验开关必须是布尔值')
    if ('minimum_score_gap' in payload
            and (type(payload['minimum_score_gap']) is not int or payload['minimum_score_gap'] <= 0)):
        raise ValueError('研究保留积分空间必须是正整数')


def execute_prayer_box_experiment(runner, ctx, payload, stop_event, *, context=None):
    """完整单类 fixed/random 实验；返回 recorded、excluded 或迁移停止事实。

    数量由研究调用方显式声明，必须在当前完整库存范围内；不能根据历史
    均值推算保证收益。所有导航和开箱沿用公共 Context 与生产适配器。
    前后活动或轮次变化时丢弃本次样本；此结果不能充当原轮收尾完成证据。
    """
    validate_prayer_box_experiment_request(payload)
    if context is None:
        context = runner._behavior_tree_context(ctx, ctx['asset_tree_path'], stop_event=stop_event)
    targets = payload['prayer_round_targets']
    modifier_snapshot = None
    if payload.get('inspect_soil_reward_modifier', payload['base_id'] == 39032101) is True:
        if payload['base_id'] != 39032101:
            raise ValueError('土灵奖励条件只适用于土灵灵草宝匣')
        from .danling_upgrade import inspect_danling_current_skill
        modifier_snapshot = yield from inspect_danling_current_skill(context, name='坤元土灵')
    before = yield from observe_active_prayer_round(context, round_targets=targets)
    if before['current_score'] >= before['round_target_score']:
        raise ValueError('封顶轮次不能测量开箱积分收益')
    if before['round_score_gap'] < payload.get('minimum_score_gap', 1):
        yield from context.go_scene(34)
        return {'outcome': 'preserve_experiment_space', 'experiment_saved': False}
    yield from context.go_scene(34)
    yield from context.wait_click(34, '右侧菜单/储物袋', timeout=10)
    yield from context.wait_scene([525], wait=10)
    cards = load_fanxiu_item_runtime_index(rebuild_missing=False)['cards_by_id']
    base = payload['base_id']
    rule = classify_prayer_box(cards[str(base)], cards)
    if (cards[str(base)].get('type') != 2 or cards[str(base)].get('sub_type') not in (1, 2)
            or not rule['classification_complete']
            or rule['is_outer_container'] and not payload.get('allow_outer_container', False)
            or rule['resource_categories'] != [before['prayer_category']]
            or rule['operation_kind'] not in ('fixed', 'random')):
        raise ValueError('本入口仅测已分类的当前祈愿专属固定或随机箱')
    snapshot = instrumentation.backpack_ui_snapshot()
    if snapshot.get('complete') is not True:
        raise ValueError('原子实验需要完整当前库存')
    item = next((row for row in snapshot['items'] if row.get('base_id') == base
                 and row.get('num', 0) >= payload['open_quantity']), None)
    if item is None:
        raise ValueError('当前单叠库存不足，不合并或采购研究材料')
    adapter_type = StorageBagFixedBoxGuiAdapter if rule['operation_kind'] == 'fixed' else StorageBagRandomBoxGuiAdapter
    adapter = adapter_type(context=context, snapshot_reader=instrumentation.backpack_ui_snapshot,
        catalog_cards_by_id=cards,
        recorder=lambda execution: persist_box_execution_after_verified_open(
            execution, session_factory=lambda: Session(engine)),
        wallet_snapshot_reader=lambda kind: read_wallet_currency_snapshot(
            kind, allow_discovery=True, missing_as_zero=True))
    execution = yield from adapter.execute(StorageBagRandomBoxRequest(
        base_id=base, instance_id=item['instance_id'], name=rule['name'], quantity=item['num'],
        open_quantity=payload['open_quantity']))
    after = yield from observe_active_prayer_round(context, round_targets=targets)
    transition = plan_prayer_closeout_transition(before, after)
    if transition:
        yield from context.go_scene(34)
        return {**transition, 'experiment_saved': False, 'action_key': execution.action_key}
    experiment = dict(base_id=base, action_key=execution.action_key,
        opened_count=execution.delta.opened_count, rewards=list(execution.delta.rewards),
        catalog_fingerprint=rule['catalog_fingerprint'], operation_kind=rule['operation_kind'],
        prayer_category=before['prayer_category'], activity_key=before['activity_key'],
        prayer_before=before, prayer_after=after, isolated=True, evidence=execution.evidence())
    if modifier_snapshot:
        experiment['reward_modifier_snapshot'] = modifier_snapshot
    excluded = None
    if rule['operation_kind'] == 'fixed':
        try:
            validate_fixed_box_reward_attribution(experiment, rule)
        except ValueError as exc:
            excluded = str(exc)
    if excluded is None:
        with Session(engine) as session:
            record_prayer_box_experiment(session, experiment)
    else:
        with Session(engine) as session:
            record_unattributed_fixed_box_experiment(session, experiment)
    yield from context.go_scene(34)
    return {'outcome': 'excluded' if excluded else 'recorded', 'experiment_saved': excluded is None,
            'excluded_fact_saved': excluded is not None, 'reason': excluded, 'experiment': experiment}


def sample_exclusive_alchemy_box(context, *, base_id: int, minimum_score_gap: int = 50_000,
                                 allow_outer_container: bool = False):
    """炼丹专属单箱研究策略，复用唯一原子实验入口；不补分、不领奖。

    第3/4/5轮目标均已实机核验。神珠、混合箱拒绝；外箱须显式授权转换实验，
    只开外箱，不连开所得内箱，保留每一步的独立积分与奖励归因。
    至少保留5万分空间是保守消耗门槛，并非服务器收益上界；封顶样本仍隔离。
    19070081已实机核验单箱、完整两项库存奖励、同轮前后积分和返回世界。
    """
    if type(minimum_score_gap) is not int or minimum_score_gap < 50_000:
        raise ValueError('单箱研究至少保留50000分实验空间')
    return (yield from execute_prayer_box_experiment(None, None, {
        'base_id': base_id, 'open_quantity': 1, 'minimum_score_gap': minimum_score_gap,
        'allow_outer_container': allow_outer_container,
        'prayer_round_targets': {'炼丹': {'3': 80000, '4': 100000, '5': 100000}},
    }, None, context=context))
