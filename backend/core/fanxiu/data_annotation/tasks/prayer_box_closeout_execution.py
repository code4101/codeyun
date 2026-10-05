"""祈愿箱收尾正式执行链：观察→单箱→观察→完整实验→本轮领奖。

尚未真实游戏验收；周日Job仅在显式prayer_box_closeout_enabled开关下调用。
不采购、不修改auto_claim；不恢复旧步骤或补跨次积分，异常保留现场。
"""
from collections.abc import Mapping
from typing import Any

from sqlmodel import Session

from backend.db import engine
from backend.core.fanxiu.catalog.item import load_fanxiu_item_runtime_index
from backend.core.fanxiu.instrumentation import fanxiu_instrumentation_service
from backend.core.fanxiu.storage_bag_prayer_research import (
    classify_prayer_box,load_prayer_box_research,record_prayer_box_experiment,
    choice_prayer_experiment_from_verified_open,
)
from backend.core.fanxiu.data_annotation.tasks.prayer_task_progress import observe_active_prayer_round
from backend.core.fanxiu.data_annotation.tasks.prayer_sunday_closeout import (
    plan_prayer_closeout,plan_prayer_closeout_boxes,
    select_prayer_closeout_choice,plan_prayer_closeout_transition,
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
            result.append(dict(base_id=base,quantity=quantities[base],
                               catalog_fingerprint=current["catalog_fingerprint"]))
    return result


def execute_prayer_box_closeout(runner,ctx,payload,stop_event):
    """正式任务中的有界收尾；未补足返回错误，不把每日领奖当补分完成。

    每次重新读取完整研究、当前目录及库存，从现有Runtime实例创建1箱请求。
    开箱前先取积分，动作后先核对完整积分再记录研究。异常样本丢弃，已消耗
    库存保留为真实游戏事实；重入从新观察重新规划，不再次投递旧动作。
    达到原轮最高档后只领奖；进入新轮即停止，绝不追着补下一轮。
    """
    targets = dict(payload.get("prayer_round_targets") or {})
    maximum = payload.get("prayer_closeout_max_boxes",20)
    if type(maximum) is not int or maximum <= 0:
        raise ValueError("祈愿收尾单次箱数上限无效")
    context = runner._behavior_tree_context(ctx,ctx["asset_tree_path"],stop_event=stop_event)
    observed = yield from observe_active_prayer_round(context,round_targets=targets)
    anchor = (observed["activity_key"],observed["current_round"])
    opened = 0
    while True:
        runner._raise_if_stopped(stop_event)
        if (observed["activity_key"],observed["current_round"]) != anchor:
            raise RuntimeError("祈愿收尾原轮已变化，停止当前消耗")
        decision = plan_prayer_closeout(observed)
        if decision["action"] == "complete":
            if decision["outcome"] == "already_full":
                yield from runner.claim_current_prayer_task_rewards(context,stop_event)
            yield from context.go_scene(34)
            return {**decision,"opened_count":opened,"message":decision.get("reason") or "本轮已满并完成领奖"}
        if opened >= maximum:
            raise RuntimeError("祈愿收尾达到本次箱数上限，当前轮尚未补足")
        before = observed
        yield from context.go_scene(34)
        yield from context.wait_click(34,"右侧菜单/储物袋",timeout=10.0)
        yield from context.wait_scene([525],wait=10.0)
        snapshot = fanxiu_instrumentation_service.backpack_ui_snapshot()
        cards = load_fanxiu_item_runtime_index(rebuild_missing=False)["cards_by_id"]
        with Session(engine) as session:
            research = load_prayer_box_research(session)
        plan = plan_prayer_closeout_boxes(before,prayer_category=before["prayer_category"],
            research=research,inventory=prayer_closeout_inventory(snapshot,cards,research),round_targets=targets)
        action = plan["next_open"]
        if action is None:
            raise RuntimeError("本轮缺分，但没有已核实的可用祈愿箱")
        instance = next(row for row in snapshot["items"] if row.get("base_id") == action["base_id"]
                        and not row.get("is_padding") and row.get("num",0)>0)
        identity = dict(base_id=action["base_id"],instance_id=instance["instance_id"],
                        name=action["name"],quantity=instance["num"],open_quantity=1)
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
        # 没有完整后观察则此次样本不保存，也不进入下一次消耗。
        observed = yield from observe_active_prayer_round(context,round_targets=targets)
        transition = plan_prayer_closeout_transition(before,observed)
        if transition is not None:
            # 不把新轮当旧轮成功，不把这次不完整原子实验存入研究；Job
            # 外层只有success才能推进next_time，因此这里保持未完成终态。
            return {**transition,"opened_count":opened+experiment["opened_count"]}
        experiment["prayer_after"] = observed
        with Session(engine) as session:
            record_prayer_box_experiment(session,experiment)
        opened += experiment["opened_count"]
