"""把非祈愿库存均衡计划交给已有自选组件执行；尚未真实游戏验收。

本模块不设置日常开关、不采购、不猜测候选价值。调用方须已取得运行权、
核实非祈愿资格和统一计量规则，再显式调用。失败保留现场，重入按真实
库存重算，不恢复之前批次游标或重放不确定动作。
"""
from dataclasses import replace
from typing import Sequence
from hashlib import sha256
import json
from backend.core.fanxiu.runtime_gui import normalize_ocr_name

from backend.core.fanxiu.data_annotation.tasks.storage_bag_balanced_choice import (
    BalancedBoxChoice,plan_storage_bag_balanced_choice,
)
from backend.core.fanxiu.data_annotation.tasks.storage_bag_choice_box import (
    StorageBagChoiceBoxBlocked,StorageBagChoiceBoxRequest,
    storage_bag_choice_snapshot_facts,read_choice_inventory,
    choice_rewards_from_catalog,
)
from backend.core.fanxiu.storage_bag_prayer_research import classify_prayer_box


def balanced_choice_catalog_fingerprint(card,cards) -> str:
    """配置绑定完整可达奖励图，子卡变化也使旧资格和换算配置失效。"""
    graph = {}
    def visit(item):
        key = str(item["id"])
        if key in graph:
            return
        graph[key] = dict(item)
        for reward in item.get("optional_gift_rewards") or []:
            child = cards.get(str(reward["id"]))
            if child is None:
                raise ValueError("均衡目录奖励图不完整")
            visit(child)
    visit(card)
    return sha256(json.dumps(graph,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()


def storage_bag_balanced_profiles(payload,entries,cards):
    """预检整单均衡配置，返回每箱候选；不读游戏、不修改配置。

    日常入口仍需auto_claim=true且备注精确为“按库存均衡分配”。试验开关
    storage_bag_balanced_choice_enabled缺省关闭。storage_bag_balanced_choices
    用字符串箱ID索引，每项需要：catalog_fingerprint（完整奖励图指纹）、
    non_prayer_verified=true、balance_unit及rewards列表；列表各项为
    reward_id/units_per_item/available_boxes，单箱数量从当前目录取得。
    资格和单位由业务核实，配置不代表实测；已知祈愿类别仍拒绝进入日常
    均衡。preflight与正式执行共用此校验，任何配置失败在整单动作前停止。
    """
    from backend.core.fanxiu.data_annotation.tasks.storage_bag_auto_claim_policy import parse_storage_bag_choice_note
    from backend.core.fanxiu.data_annotation.tasks.storage_bag_balanced_choice import plan_storage_bag_balanced_choice
    selected = {e.base_id for e in entries if e.template=="choice_box"
                and parse_storage_bag_choice_note(e.note)[0]=="balanced_inventory"}
    if not selected:
        return {}
    if payload.get("storage_bag_balanced_choice_enabled") is not True:
        raise ValueError("均衡自选尚未真实验收，必须显式开启验收开关")
    profiles = payload.get("storage_bag_balanced_choices") or {}
    result = {}
    for base in selected:
        card = cards.get(str(base)) or {}
        rule = classify_prayer_box(card,cards)
        if not rule["classification_complete"] or rule["resource_categories"]:
            raise ValueError("祈愿候选或未知奖励不能进入日常均衡自选")
        profile = profiles.get(str(base)) or {}
        if (profile.get("non_prayer_verified") is not True
                or profile.get("catalog_fingerprint") != balanced_choice_catalog_fingerprint(card,cards)):
            raise ValueError("均衡资格或单位配置缺失/目录版本已改变")
        rewards = choice_rewards_from_catalog(card,cards)
        quantities = {r.base_id:r.count_per_box for r in rewards}
        choices = []
        for row in profile.get("rewards") or []:
            reward = row.get("reward_id")
            if type(reward) is not int or reward not in quantities:
                raise ValueError("均衡配置含未知奖励ID")
            if "available_boxes" not in row:
                raise ValueError("均衡配置必须明确可选额度，null表示已核实不限")
            choices.append(BalancedBoxChoice(reward,quantities[reward],
                profile.get("balance_unit"),row.get("units_per_item"),row.get("available_boxes")))
        validate_balanced_box_choices(rewards,choices)
        # 零箱只验证完整单位/额度契约，不把零值当成真实库存。
        plan_storage_bag_balanced_choice(choices,dict.fromkeys(quantities,0),0,
                                         non_prayer_verified=True)
        total = sum(e.quantity for e in entries if e.base_id==base and e.template=="choice_box"
                    and parse_storage_bag_choice_note(e.note)[0]=="balanced_inventory")
        if all(c.available_boxes is not None for c in choices) and sum(c.available_boxes for c in choices)<total:
            raise ValueError("均衡自选可选额度不足以完成全部选中实例")
        result[base] = tuple(choices)
    return result


def validate_balanced_box_choices(rewards,choices: Sequence[BalancedBoxChoice]) -> None:
    """均衡范围必须完整对应真实Catalog，单箱数量与命名选择必须唯一。"""
    by_id = {r.base_id:r for r in rewards}
    if (len(by_id) != len(rewards) or len(choices) != len(rewards)
            or {c.reward_id for c in choices} != set(by_id)):
        raise ValueError("均衡配置必须覆盖全部真实自选奖励")
    if len({normalize_ocr_name(r.name) for r in rewards}) != len(rewards):
        raise ValueError("均衡候选名称重复，不能唯一生成命名选择请求")
    for choice in choices:
        reward = by_id[choice.reward_id]
        if (reward.count_per_box != choice.count_per_box
                or reward.is_partner or reward.is_spirit_artifact):
            raise ValueError("均衡奖励数量与目录不符或需要独立装备/仙侣规划")


def execute_balanced_storage_bag_choice(adapter,request: StorageBagChoiceBoxRequest,
                                      *,choices: Sequence[BalancedBoxChoice],
                                      non_prayer_verified: bool):
    """完整库存→均衡分配→按目标批量开箱→逐批核对到账；不改变配置。

    原始请求须无手工部分数量，按整叠库存规划；各目标批次各自从新快照
    构建请求，不能把预计箱数当作下一批身份。适配器的exact delta必须证实
    箱数与奖励数量，候选全部的实际库存须匹配预计，才继续下一批。
    只接受普通道具/钱包奖励，其他资源由其独立生命周期处理。尚未接入
    每日默认开箱，必须通过真实Task验收再启用。
    """
    if non_prayer_verified is not True or request.open_quantity is not None:
        raise ValueError("均衡执行需要已核实非祈愿资格及完整整叠请求")
    rewards = adapter.rewards(request.base_id)
    validate_balanced_box_choices(rewards,choices)
    before = dict(adapter.snapshot_reader())
    facts = storage_bag_choice_snapshot_facts(before)
    if facts["instances"].get(request.instance_id) != (request.base_id,request.quantity):
        raise StorageBagChoiceBoxBlocked("均衡执行初始库存与请求不一致")
    inventory = read_choice_inventory(rewards,adapter.catalog_cards_by_id,
                                      process_identity=facts["process_identity"])
    plan = plan_storage_bag_balanced_choice(choices,inventory,request.quantity,
                                           non_prayer_verified=True)
    by_id = {r.base_id:r for r in rewards}
    opened = 0
    executions = []
    for batch in plan.batches:
        snapshot = dict(adapter.snapshot_reader())
        current = storage_bag_choice_snapshot_facts(snapshot)
        if current["process_identity"] != facts["process_identity"]:
            raise StorageBagChoiceBoxBlocked("均衡执行中游戏进程已更换")
        target = current["instances"].get(request.instance_id)
        if target != (request.base_id,request.quantity-opened):
            raise StorageBagChoiceBoxBlocked("均衡执行剩余箱数与实测不一致")
        actual = read_choice_inventory(rewards,adapter.catalog_cards_by_id,
                                      process_identity=facts["process_identity"])
        if actual != inventory:
            raise StorageBagChoiceBoxBlocked("均衡执行前候选库存改变，必须重算")
        selected = by_id[batch.reward_id]
        batch_request = replace(request,quantity=target[1],open_quantity=batch.opened_count,
                                note="选"+selected.name)
        result = yield from adapter.execute(batch_request)
        delta = result.delta
        if (delta is None or delta.opened_count != batch.opened_count
                or delta.reward_base_id != batch.reward_id
                or delta.reward_quantity != selected.count_per_box*batch.opened_count):
            raise StorageBagChoiceBoxBlocked("均衡批次未形成完整对应到账证据，停止后续消耗")
        expected = dict(inventory)
        expected[batch.reward_id] += delta.reward_quantity
        actual = read_choice_inventory(rewards,adapter.catalog_cards_by_id,
                                      process_identity=facts["process_identity"])
        if actual != expected:
            raise StorageBagChoiceBoxBlocked("均衡批次后候选库存与实测收益不一致")
        inventory = actual
        opened += delta.opened_count
        executions.append(result)
    return dict(opened_count=opened,remaining_boxes=request.quantity-opened,
                final_inventory=inventory,executions=executions)
