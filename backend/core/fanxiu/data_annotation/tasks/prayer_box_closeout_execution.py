"""祈愿箱收尾正式执行链：观察→有界开箱→观察→完整实验→本轮领奖。

已实机通过有界批次与第4轮补满；领奖弹窗修复后恢复已验证。
周日Job仅在显式prayer_box_closeout_enabled开关下调用。
2026-10-11正式Job实机通过第5轮已领奖重入：五轮100项全部已领、
当前轮20项已领、零开箱、返回世界。该验收不替代未完成轮次的跨轮消耗验收。
不采购、不修改auto_claim；不恢复旧步骤或补跨次积分，异常保留现场。
"""
from collections.abc import Mapping
from typing import Any
import time

from sqlmodel import Session

from backend.db import engine
from backend.core.fanxiu.catalog.item import load_fanxiu_item_runtime_index
from backend.core.fanxiu.instrumentation import fanxiu_instrumentation_service
from backend.core.fanxiu.storage_bag_prayer_research import (
    classify_prayer_box,load_prayer_box_research,record_prayer_box_experiment,
    choice_prayer_experiment_from_verified_open,
)
from backend.core.fanxiu.data_annotation.tasks.prayer_task_progress import (
    observe_active_prayer_round, project_final_prayer_round_claim,
)
from backend.core.fanxiu.data_annotation.tasks.prayer_sunday_closeout import (
    plan_prayer_closeout,plan_prayer_closeout_boxes,
    select_prayer_closeout_choice,plan_prayer_closeout_transition,
    plan_prayer_closeout_batch,
)
from backend.core.fanxiu.data_annotation.tasks.storage_bag_choice_box import (
    StorageBagChoiceBoxGuiAdapter,StorageBagChoiceBoxRequest,
)
from backend.core.fanxiu.data_annotation.tasks.storage_bag_random_box import (
    StorageBagRandomBoxGuiAdapter,StorageBagFixedBoxGuiAdapter,StorageBagRandomBoxRequest,
)
from backend.core.fanxiu.data_annotation.tasks.storage_bag_auto_claim_execution import (
    persist_box_execution_after_verified_open,
)
from backend.core.fanxiu.instrumentation.wallet import read_wallet_currency_snapshot


def prayer_closeout_inventory(snapshot: Mapping[str, Any], cards: Mapping[str, Mapping[str, Any]],
                             research: Mapping[str, Any]) -> list[dict[str, Any]]:
    """将完整当前库存与当前目录绑定，汇总研究候选数量；不读取或操作游戏。"""
    evidence = snapshot.get("evidence") or {}
    if (snapshot.get("complete") is not True
            or snapshot.get("source") != "active_backpack_panel_item_info_list"
            or not snapshot.get("fingerprint")
            or any(type(evidence.get(k)) is not int or evidence[k] <= 0
                   for k in ("pid","process_start_ticks"))):
        raise ValueError("补分规划需要完整同进程储物袋事实")
    instances, quantities = set(), {}
    for row in snapshot.get("items") or []:
        if row.get("is_padding"):
            continue
        instance, base, count = row.get("instance_id"),row.get("base_id"),row.get("num")
        if (not instance or instance in instances or type(base) is not int or base <= 0
                or type(count) is not int or count < 0):
            raise ValueError("补分库存包含重复或未核实实例")
        instances.add(instance)
        quantities[base] = quantities.get(base,0) + count
    result = []
    for rule in research.get("rules") or []:
        base = rule["base_id"]
        card = cards.get(str(base))
        if card is not None and quantities.get(base,0):
            current = classify_prayer_box(card,cards)
            # 根卡指纹不包含外部叶子卡全部字段；同步核对奖励图，避免子卡
            # 变更而根卡未变时继续套旧收益。此处只排除，不猜测迁移历史。
            if any(current.get(k) != rule.get(k) for k in (
                    "resource_categories","reward_leaves","operation_kind","is_outer_container")):
                continue
            # 当前测量版本含递归子协议；与规则不同即由规划拒绝，不沿用旧收益。
            result.append(dict(base_id=base,quantity=quantities[base],
                               catalog_fingerprint=current["catalog_fingerprint"]))
    return result


def execute_prayer_box_closeout(runner,ctx,payload,stop_event):
    """正式任务中的有界收尾；未补足返回错误，不把每日领奖当补分完成。

    每次重新读取完整研究、当前目录及库存；默认单箱，可显式限制批量。
    开箱前先取积分，动作后先核对完整积分再记录研究。异常样本丢弃，已消耗
    库存保留为真实游戏事实；重入从新观察重新规划，不再次投递旧动作。
    炼丹领完原轮后验证同活动的下一轮并继续；第5轮领奖后重新读取完整原生
    列表，核对同活动、仍在第5轮且全部任务已领奖才成功，其他类别完成当前
    轮即返回。每次重入从当前事实开始，不恢复旧游标，不新增持久逐轮台账。
    最终轮已领奖重入及完整周期事实判据已通过正式Job实机验收。
    """
    from backend.core.fanxiu.prayer_cycle import current_prayer_cycle
    enabled_categories = payload.get('prayer_box_closeout_categories')
    if enabled_categories is not None:
        if (not isinstance(enabled_categories,list) or not enabled_categories
                or any(not isinstance(c,str) for c in enabled_categories)):
            raise ValueError('祈愿补分类别范围必须是非空字符串列表')
        if current_prayer_cycle() not in enabled_categories:
            return dict(result='success',outcome='category_not_enabled',message='本周类别未启用箱子补分')
    targets = dict(payload.get("prayer_round_targets") or {})
    maximum = payload.get("prayer_closeout_max_boxes",20)
    if type(maximum) is not int or maximum <= 0:
        raise ValueError("祈愿收尾单次箱数上限无效")
    maximum_batch = payload.get('prayer_closeout_maximum_batch', 1)
    maximum_actions = payload.get('prayer_closeout_max_actions')
    if (type(maximum_batch) is not int or maximum_batch <= 0
            or maximum_actions is not None and (type(maximum_actions) is not int or maximum_actions <= 0)):
        raise ValueError('祈愿收尾批量或动作数上限无效')
    context = runner._behavior_tree_context(ctx,ctx["asset_tree_path"],stop_event=stop_event)
    def observe():
        progress = yield from observe_active_prayer_round(context,round_targets=targets,native_alchemy=True)
        if progress['prayer_category'] == '炼丹':
            # 每轮从实际完整列表取得目标，包括尚无历史样本的第1/2轮。
            if not isinstance(targets.get('炼丹'),dict):
                targets['炼丹'] = {}
            targets['炼丹'][str(progress['current_round'])] = progress['round_target_score']
        return progress
    modifier_snapshot = None
    if payload.get('prayer_closeout_base_id') == 39032101:
        # 显式土灵箱验收先取技能、再取开箱前积分，避免初次观察后为取证
        # 离开祈愿又重复进入。这里只导航取证，物品准入仍由当前规划核验。
        from .danling_upgrade import inspect_danling_current_skill
        modifier_snapshot = yield from inspect_danling_current_skill(context, name='坤元土灵')
    observed = yield from observe()
    anchor = (observed["activity_key"],observed["current_round"])
    opened = 0
    actions = 0
    while True:
        runner._raise_if_stopped(stop_event)
        if (observed["activity_key"],observed["current_round"]) != anchor:
            raise RuntimeError("祈愿收尾原轮已变化，停止当前消耗")
        decision = plan_prayer_closeout(observed,prayer_category=observed['prayer_category'])
        if decision["action"] == "complete":
            claim_outcome = None
            if decision["outcome"] == "already_full":
                claim_outcome = yield from runner.claim_current_prayer_task_rewards(context,stop_event)
                if claim_outcome not in {'claimed', 'already_settled'}:
                    raise RuntimeError('本轮积分已满，但未取得稳定领奖终态')
                if not decision['stop_after_current_round']:
                    if observed['current_round'] < observed['total_rounds']:
                        following = yield from observe()
                        if (following['activity_key'] != observed['activity_key']
                                or following['current_round'] != observed['current_round'] + 1):
                            raise RuntimeError('炼丹领奖后未确认同活动下一轮，停止消耗')
                        observed = following
                        anchor = (observed['activity_key'],observed['current_round'])
                        continue
                    # 炼丹最后一轮：claim 只证明 OCR 可见范围 settled，必须重新读取
                    # 完整原生列表核对同活动、仍在最后一轮且全部任务已领奖。仍有
                    # 未领奖任务时失败保留现场，不假成功、不无界循环。
                    final_fact = yield from observe()
                    if (final_fact['activity_key'] != observed['activity_key']
                            or final_fact['current_round'] != observed['current_round']):
                        raise RuntimeError('炼丹最终轮领奖后未确认同活动同一轮，停止消耗')
                    final_panel = final_fact.get('native_panel')
                    if not isinstance(final_panel, Mapping):
                        raise RuntimeError('炼丹最终轮领奖缺少完整原生任务列表，停止消耗')
                    claim_fact = project_final_prayer_round_claim(final_panel,
                        total_rounds=final_fact['total_rounds'])
                    yield from context.go_scene(34)
                    return {**decision, 'outcome':'final_round_claimed',
                        'opened_count':opened, 'action_count':actions, 'claim_outcome':claim_outcome,
                        'final_round_claimed':claim_fact['final_round_claimed'],
                        'claimed_task_count':claim_fact['claimed_task_count'],
                        'all_rounds_claimed':claim_fact['all_rounds_claimed'],
                        'claimed_rounds':claim_fact['claimed_rounds'],
                        'cycle_claimed_task_count':claim_fact['cycle_claimed_task_count'],
                        'activity_identity':dict(activity_key=final_fact['activity_key'],
                            activity_id=claim_fact['activity_id'],
                            current_round=claim_fact['current_round'],
                            total_rounds=claim_fact['total_rounds']),
                        'message':'炼丹最终轮全部任务已确认领奖，收尾完成'}
            yield from context.go_scene(34)
            return {**decision, 'outcome':'topped_up' if opened and claim_outcome else decision['outcome'],
                "opened_count":opened, 'action_count':actions, 'claim_outcome':claim_outcome,
                "message":decision.get("reason") or "本轮已满并完成领奖"}
        if maximum_actions is not None and actions >= maximum_actions:
            yield from context.go_scene(34)
            return {**decision, 'action':'stop', 'outcome':'bounded_actions_complete',
                'opened_count':opened, 'action_count':actions,
                'message':'本次完整批次已结束，本轮仍需从新观察重新规划'}
        if opened >= maximum:
            raise RuntimeError("祈愿收尾达到本次箱数上限，当前轮尚未补足")
        with Session(engine) as session:
            research = load_prayer_box_research(session)
        soil_measured = observed['prayer_category'] == '炼丹' and any(
            s['base_id'] == 39032101 and s.get('eligibility') == 'verified_positive'
            for s in research.get('summaries') or [])
        if soil_measured and (modifier_snapshot is None
                or time.time() - modifier_snapshot['observed_at'] >= 600):
            from .danling_upgrade import inspect_danling_current_skill
            modifier_snapshot = yield from inspect_danling_current_skill(context, name='坤元土灵')
            # 取证导航后重新取完整积分，不能沿用取证前的开箱前观察。
            observed = yield from observe()
            continue
        before = observed
        yield from context.go_scene(34)
        yield from context.wait_click(34,"右侧菜单/储物袋",timeout=10.0)
        yield from context.wait_scene([525],wait=10.0)
        snapshot = fanxiu_instrumentation_service.backpack_ui_snapshot()
        cards = load_fanxiu_item_runtime_index(rebuild_missing=False)["cards_by_id"]
        with Session(engine) as session:
            research = load_prayer_box_research(session)
        plan = plan_prayer_closeout_boxes(before,prayer_category=before["prayer_category"],
            research=research,inventory=prayer_closeout_inventory(snapshot,cards,research),round_targets=targets,
            reward_modifier_snapshots={39032101:modifier_snapshot} if modifier_snapshot else None,
            explicit_base_id=payload.get('prayer_closeout_base_id'))
        action = plan["next_open"]
        if action is None:
            raise RuntimeError("本轮缺分，但没有已核实的可用祈愿箱")
        instance = next(row for row in snapshot["items"] if row.get("base_id") == action["base_id"]
                        and not row.get("is_padding") and row.get("num",0)>0)
        batch = plan_prayer_closeout_batch(additional_score=before['round_score_gap'],
            expected_score_per_box=action['expected_score_per_box'],
            available_count=instance['num'], remaining_budget=maximum-opened,
            maximum_batch=1 if action['operation_kind'] == 'choice' else maximum_batch)
        identity = dict(base_id=action["base_id"],instance_id=instance["instance_id"],
                        name=action["name"],quantity=instance["num"],open_quantity=batch)
        category = before["prayer_category"]
        if action["operation_kind"] == "choice":
            selected = (payload.get("prayer_choice_selection") or {}).get(str(action["base_id"]))
            reward_id = int(select_prayer_closeout_choice(action,
                policy=payload.get("prayer_choice_tie_policy","stable_id"),explicit_choice=selected))
            request = StorageBagChoiceBoxRequest(**identity,note="选"+cards[str(reward_id)]["name"])
            adapter = StorageBagChoiceBoxGuiAdapter(context=context,
                snapshot_reader=fanxiu_instrumentation_service.backpack_ui_snapshot,catalog_cards_by_id=cards)
            execution = yield from adapter.execute(request)
            with Session(engine) as session:
                experiment = choice_prayer_experiment_from_verified_open(session,execution,
                    prayer_before=before,activity_key=before["activity_key"],prayer_category=category)
            if experiment["catalog_fingerprint"] != action["catalog_fingerprint"]:
                raise RuntimeError("自选动作期间研究目录改变，丢弃本次实验")
        else:
            adapter_type = (StorageBagFixedBoxGuiAdapter if action["operation_kind"]=="fixed"
                            else StorageBagRandomBoxGuiAdapter)
            adapter = adapter_type(context=context,
                snapshot_reader=fanxiu_instrumentation_service.backpack_ui_snapshot,catalog_cards_by_id=cards,
                recorder=lambda execution:persist_box_execution_after_verified_open(
                    execution,session_factory=lambda:Session(engine)),
                wallet_snapshot_reader=lambda kind:read_wallet_currency_snapshot(
                    kind,allow_discovery=True,missing_as_zero=True))
            execution = yield from adapter.execute(StorageBagRandomBoxRequest(**identity))
            experiment = dict(base_id=action["base_id"],action_key=execution.action_key,
                opened_count=execution.delta.opened_count,rewards=list(execution.delta.rewards),
                catalog_fingerprint=action["catalog_fingerprint"],operation_kind=action["operation_kind"],
                prayer_before=before,activity_key=before["activity_key"],prayer_category=category,
                isolated=True,evidence=execution.evidence())
            if action['base_id'] == 39032101 and modifier_snapshot:
                experiment['reward_modifier_snapshot'] = modifier_snapshot
        # 没有完整后观察则此次样本不保存，也不进入下一次消耗。
        observed = yield from observe()
        transition = plan_prayer_closeout_transition(before,observed)
        if transition is not None:
            # 不把新轮当旧轮成功，不把这次不完整原子实验存入研究；Job
            # 外层只有success才能推进next_time，因此这里保持未完成终态。
            return {**transition,"opened_count":opened+experiment["opened_count"]}
        experiment["prayer_after"] = observed
        with Session(engine) as session:
            record_prayer_box_experiment(session,experiment)
        opened += experiment["opened_count"]
        actions += 1
