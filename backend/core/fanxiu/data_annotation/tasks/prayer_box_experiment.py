"""当前类别祈愿箱原子研究入口：一次开箱、一组完整前后事实。

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
from backend.core.fanxiu.prayer_research_planning import prayer_research_category_allowed
from backend.core.fanxiu.storage_bag_prayer_research import (
    classify_prayer_box, persist_prayer_box_rule, record_prayer_box_experiment, validate_fixed_box_reward_attribution,
    record_unattributed_fixed_box_experiment,
    load_prayer_box_research, choice_prayer_experiment_from_verified_open,
)
from .prayer_task_progress import observe_active_prayer_round
from .prayer_sunday_closeout import plan_prayer_closeout_transition
from .storage_bag_auto_claim_execution import persist_box_execution_after_verified_open
from .storage_bag_random_box import (
    StorageBagFixedBoxGuiAdapter, StorageBagRandomBoxGuiAdapter, StorageBagRandomBoxRequest,
)
from .storage_bag_choice_box import StorageBagChoiceBoxGuiAdapter, StorageBagChoiceBoxRequest


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
    if 'expected_catalog_fingerprint' in payload and not isinstance(payload['expected_catalog_fingerprint'], str):
        raise ValueError('原子研究目录指纹必须是字符串')
    if ('minimum_score_gap' in payload
            and (type(payload['minimum_score_gap']) is not int or payload['minimum_score_gap'] <= 0)):
        raise ValueError('研究保留积分空间必须是正整数')
    if 'choice_key' in payload:
        key = payload['choice_key']
        if not isinstance(key, str) or not key.isascii() or not key.isdecimal() or int(key) <= 0 or str(int(key)) != key:
            raise ValueError('自选研究需要明确的正整数奖励ID字符串')
        if payload['open_quantity'] != 1:
            raise ValueError('自选选项研究每次只测一箱')


def validate_prayer_box_runtime_graph_alignment(base_id, catalog_cards, runtime_graph):
    """消费前比对完整当前奖励协议；忽略导出目录的展示字段与格式差异。"""
    if runtime_graph.get('complete') is not True or base_id not in runtime_graph.get('requested_ids', []):
        raise ValueError('原子研究需要该箱完整当前原生奖励图')
    native_cards = runtime_graph.get('cards_by_id') or {}
    pending, seen = [base_id], set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        catalog, native = catalog_cards.get(str(current)), native_cards.get(str(current))
        if catalog is None or native is None:
            raise ValueError('当前原生与研究目录缺少相同奖励成员')
        if any(catalog.get(k) != native.get(k) for k in ('id', 'type', 'sub_type', 'effect_value')):
            raise ValueError(f'当前原生物品{current}协议与研究目录不一致，禁止消费')
        def rewards(card):
            return sorted((row['id'], row['count'], row.get('show_condition') or '')
                          for row in card.get('optional_gift_rewards', []))
        if rewards(catalog) != rewards(native):
            raise ValueError(f'当前原生物品{current}奖励或条件与研究目录不一致，禁止消费')
        pending.extend(row['id'] for row in native.get('optional_gift_rewards', []))
    old = classify_prayer_box(catalog_cards[str(base_id)], catalog_cards)
    new = classify_prayer_box(native_cards[str(base_id)], native_cards)
    if (not new['classification_complete'] or old['resource_categories'] != new['resource_categories']
            or old['operation_kind'] != new['operation_kind']
            or old['is_outer_container'] != new['is_outer_container']):
        raise ValueError('当前原生奖励用途与研究目录不一致，禁止消费')


def validate_choice_research_selection(rule, plan, *, choice_key, prayer_category,
                                      selected_reward_rule, count_per_box):
    """消费前核实跨周计划的选项身份；不创建计划、不改日常使用配置。"""
    if (not plan or plan.get('base_id') != rule['base_id']
            or plan.get('catalog_fingerprint') != rule['catalog_fingerprint']):
        raise ValueError('自选研究需要与当前目录一致的已保存跨周计划')
    option = next((row for row in plan.get('options', []) if row['choice_key'] == choice_key), None)
    if (option is None or prayer_category == '炼丹'
            or option.get('prayer_category') != prayer_category
            or option.get('experiment_open_count') != 1):
        raise ValueError('自选研究选项与当前非炼丹祈愿类别不一致')
    # 根卡指纹仅覆盖奖励ID和数量；子卡的资源归属也必须从当前奖励图核实。
    if (not selected_reward_rule.get('classification_complete')
            or selected_reward_rule.get('base_id') != int(choice_key)
            or selected_reward_rule.get('allocation_categories') != [prayer_category]
            or selected_reward_rule.get('resource_categories') == ['炼丹']
            or type(count_per_box) is not int or count_per_box <= 0
            or option.get('count_per_box') != count_per_box):
        raise ValueError('自选奖励子卡或数量与研究计划不一致，停止开箱')
    return option


def execute_prayer_box_experiment(runner, ctx, payload, stop_event, *, context=None):
    """当前祈愿 fixed/random/choice 实验；返回 recorded、excluded 或迁移停止事实。

    数量由研究调用方显式声明，必须在当前完整库存范围内；不能根据历史
    均值推算保证收益。所有导航和开箱沿用公共 Context 与生产适配器。
    前后活动或轮次变化时丢弃本次样本；此结果不能充当原轮收尾完成证据。
    炼丹仅专属箱；其他类别允许复合箱，记录全部到账奖励但只测当前
    祈愿的积分变化。非炼丹实物分支仍须在对应真实活动中验收。
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
    cards = dict(load_fanxiu_item_runtime_index(rebuild_missing=False)['cards_by_id'])
    base = payload['base_id']
    if str(base) not in cards:
        from backend.core.fanxiu.instrumentation.item_reward_graph import read_item_reward_graph_runtime
        graph = read_item_reward_graph_runtime([base])
        for ident, card in graph['cards_by_id'].items():
            cards.setdefault(ident, card)
    rule = classify_prayer_box(cards[str(base)], cards)
    is_choice = rule['operation_kind'] == 'choice'
    supported_card = (cards[str(base)].get('type') == 21 if is_choice else
        cards[str(base)].get('type') == 2 and cards[str(base)].get('sub_type') in (1, 2))
    if (not supported_card
            or not rule['classification_complete']
            or rule['is_outer_container'] and not payload.get('allow_outer_container', False)
            or not prayer_research_category_allowed(before['prayer_category'], rule['resource_categories'])
            or rule['operation_kind'] not in ('fixed', 'random', 'choice')):
        raise ValueError('本入口仅测已分类且符合当前祈愿类别策略的箱子')
    if payload.get('expected_catalog_fingerprint') and rule['catalog_fingerprint'] != payload['expected_catalog_fingerprint']:
        raise ValueError('每日发现与原子研究的目录版本已变化，停止开箱')
    if is_choice:
        if 'choice_key' not in payload:
            raise ValueError('自选研究必须显式指定 choice_key')
        with Session(engine) as session:
            plans = load_prayer_box_research(session)['choice_plans']
        plan = next((row for row in plans if row['base_id'] == base), None)
        reward = next((row for row in cards[str(base)].get('optional_gift_rewards', [])
                       if str(row.get('id')) == payload['choice_key']), None)
        reward_card = cards.get(payload['choice_key'])
        if reward is None or reward_card is None:
            raise ValueError('自选研究缺少当前所选奖励的完整目录')
        validate_choice_research_selection(rule, plan, choice_key=payload['choice_key'],
            prayer_category=before['prayer_category'],
            selected_reward_rule=classify_prayer_box(reward_card, cards), count_per_box=reward.get('count'))
    elif 'choice_key' in payload:
        raise ValueError('固定或随机箱不能声明自选奖励')
    with Session(engine) as session:
        persist_prayer_box_rule(session, rule)
    snapshot = instrumentation.backpack_ui_snapshot()
    if snapshot.get('complete') is not True:
        raise ValueError('原子实验需要完整当前库存')
    item = next((row for row in snapshot['items'] if row.get('base_id') == base
                 and row.get('num', 0) >= payload['open_quantity']), None)
    if item is None:
        raise ValueError('当前单叠库存不足，不合并或采购研究材料')
    identity = dict(base_id=base, instance_id=item['instance_id'], name=rule['name'],
                    quantity=item['num'], open_quantity=payload['open_quantity'])
    if is_choice:
        adapter = StorageBagChoiceBoxGuiAdapter(context=context,
            snapshot_reader=instrumentation.backpack_ui_snapshot, catalog_cards_by_id=cards)
        execution = yield from adapter.execute(StorageBagChoiceBoxRequest(
            **identity, note='选' + cards[payload['choice_key']]['name']))
        with Session(engine) as session:
            experiment = choice_prayer_experiment_from_verified_open(session, execution,
                prayer_before=before, activity_key=before['activity_key'], prayer_category=before['prayer_category'])
        if experiment['catalog_fingerprint'] != rule['catalog_fingerprint']:
            raise ValueError('自选动作期间研究目录改变，丢弃本次实验')
    else:
        adapter_type = StorageBagFixedBoxGuiAdapter if rule['operation_kind'] == 'fixed' else StorageBagRandomBoxGuiAdapter
        adapter = adapter_type(context=context, snapshot_reader=instrumentation.backpack_ui_snapshot,
            catalog_cards_by_id=cards,
            recorder=lambda execution: persist_box_execution_after_verified_open(
                execution, session_factory=lambda: Session(engine)),
            wallet_snapshot_reader=lambda kind: read_wallet_currency_snapshot(
                kind, allow_discovery=True, missing_as_zero=True))
        execution = yield from adapter.execute(StorageBagRandomBoxRequest(**identity))
        experiment = dict(base_id=base, action_key=execution.action_key,
            opened_count=execution.delta.opened_count, rewards=list(execution.delta.rewards),
            catalog_fingerprint=rule['catalog_fingerprint'], operation_kind=rule['operation_kind'],
            prayer_category=before['prayer_category'], activity_key=before['activity_key'],
            prayer_before=before, isolated=True, evidence=execution.evidence())
    after = yield from observe_active_prayer_round(context, round_targets=targets)
    transition = plan_prayer_closeout_transition(before, after)
    if transition:
        yield from context.go_scene(34)
        return {**transition, 'experiment_saved': False, 'action_key': experiment['action_key']}
    experiment['prayer_after'] = after
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
                                 allow_outer_container: bool = False, expected_catalog_fingerprint: str = ''):
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
        'expected_catalog_fingerprint': expected_catalog_fingerprint,
        'prayer_round_targets': {'炼丹': {'3': 80000, '4': 100000, '5': 100000}},
    }, None, context=context))


def sample_exclusive_body_box(context, *, base_id: int, minimum_score_gap: int = 5_000,
                              expected_catalog_fingerprint: str = '',
                              operation_kind: str = 'fixed'):
    """锻体专属单箱测量；随机分布须显式声明，完整面板决定本轮最高档。

    不从第一轮推测后续轮次。已测零收益也直接复用，不连开或补分。
    最少5000分仅为保留实验空间，封顶样本仍由原子记录器隔离。
    """
    from backend.core.fanxiu.prayer_cycle import prayer_cycle_week
    from .prayer_task_progress import inspect_active_prayer_task_panel, native_prayer_round_progress

    if type(minimum_score_gap) is not int or minimum_score_gap < 5_000:
        raise ValueError('锻体单箱研究至少保留5000分实验空间')
    if operation_kind not in ('fixed', 'random'):
        raise ValueError('锻体单箱测量只接受固定或随机奖励分布')
    if prayer_cycle_week().name != '淬体':
        raise ValueError('锻体单箱测量仅在当前淬体祈愿周期运行')
    cards = dict(load_fanxiu_item_runtime_index(rebuild_missing=False)['cards_by_id'])
    card = cards.get(str(base_id))
    if card is None:
        from backend.core.fanxiu.instrumentation.item_reward_graph import read_item_reward_graph_runtime
        graph = read_item_reward_graph_runtime([base_id])
        if not graph['complete']:
            raise ValueError('锻体单箱测量缺少当前完整奖励图')
        for ident, native in graph['cards_by_id'].items():
            cards.setdefault(ident, native)
        card = cards[str(base_id)]
    rule = classify_prayer_box(card, cards)
    if (not rule['classification_complete'] or rule['resource_categories'] != ['淬体']
            or rule['operation_kind'] != operation_kind or rule['is_outer_container']):
        raise ValueError('锻体单箱研究只接受所声明分布的专属叶子箱')
    if expected_catalog_fingerprint and expected_catalog_fingerprint != rule['catalog_fingerprint']:
        raise ValueError('锻体研究请求与当前目录版本不一致')
    from .prayer_research_discovery import discover_prayer_box_research
    plan = yield from discover_prayer_box_research(context, target_base_id=base_id)
    known = next((row for row in plan['known'] if row['base_id'] == base_id), None)
    if known is not None:
        return {'outcome': 'already_measured', 'newly_opened_count': 0, 'known': known}
    candidate = next((row for row in plan['unknown'] if row['base_id'] == base_id), None)
    if candidate is None:
        return {'outcome': 'research_deferred', 'newly_opened_count': 0,
                'reason': '当前库存、版本或未归因事实不允许重新测量'}
    from backend.core.fanxiu.instrumentation.item_reward_graph import read_item_reward_graph_runtime
    graph = read_item_reward_graph_runtime([base_id])
    validate_prayer_box_runtime_graph_alignment(base_id, cards, graph)
    panel = yield from inspect_active_prayer_task_panel(context, expected_activity_id=4502010)
    progress = native_prayer_round_progress(panel, expected_activity_id=4502010, total_rounds=5)
    if progress['round_score_gap'] < minimum_score_gap:
        yield from context.go_scene(34)
        return {'outcome': 'preserve_experiment_space', 'experiment_saved': False,
                'progress': progress, 'minimum_score_gap': minimum_score_gap,
                'newly_opened_count': 0, 'scene': 34}
    return (yield from execute_prayer_box_experiment(None, None, {
        'base_id': base_id, 'open_quantity': 1, 'minimum_score_gap': minimum_score_gap,
        'expected_catalog_fingerprint': expected_catalog_fingerprint,
        'prayer_round_targets': {'淬体': {str(progress['current_round']): progress['round_target_score']}},
    }, None, context=context))
