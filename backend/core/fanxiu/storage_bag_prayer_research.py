"""储物袋祈愿资源研究：候选分类、实验事实与跨祈愿收益比较。

资源类别与计分资格分开：开出仙花不证明计分，友好度不是祈愿积分。
同类仙花细分不构成复合资源；炼丹保留分类，但不参与开箱用途竞争。
随机实验零分仅证明该次产出零分，不能据一次抽样把整个奖池判为无关。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
import math
import re
from statistics import stdev
from typing import Any, Mapping

from sqlmodel import Session, select
from backend.models import FanxiuPacketBusinessRecord
from backend.core.fanxiu.business_data import upsert_fanxiu_business_records

BOX_RULE_DOMAIN = "storage_bag_prayer_box_rule"
BOX_EXPERIMENT_DOMAIN = "storage_bag_prayer_box_experiment"
BOX_CONCLUSION_DOMAIN = "storage_bag_prayer_box_conclusion"
BOX_CHOICE_PLAN_DOMAIN = "storage_bag_prayer_choice_research_plan"
BOX_EXPERIMENT_VERSION_DOMAIN = "storage_bag_prayer_experiment_catalog_evidence"
BOX_EXPERIMENT_QUARANTINE_DOMAIN = "storage_bag_prayer_experiment_quarantine"
BOX_EXCLUDED_EXPERIMENT_DOMAIN = "storage_bag_prayer_excluded_experiment"
MATERIAL_EXPERIMENT_DOMAIN = "prayer_material_experiment"


def classify_prayer_box(card: Mapping[str, Any], cards: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """按完整奖励图递归归类，不按箱名/预览截断判定；不授予开箱权限。"""
    categories: set[str] = set()
    unresolved: set[int] = set()
    leaves: list[dict[str, Any]] = []
    visited: set[int] = set()

    def visit(item: Mapping[str, Any], count: float, inherited_category: str = "") -> None:
        # 炼丹灵草箱的叶子可能叫凝血草等，类别不能只靠叶子名称猜。
        name = str(item.get("name") or "")
        description = str(item.get("description") or "")
        declared = (
            "仙花" if "【仙花】" in description else
            "洗灵" if name.startswith("洗灵") else
            "淬体" if "淬体精魄" in name else
            "灵兽" if "饲灵丸" in name else
            "炼丹" if "灵草" in name or "仙草" in name else inherited_category
        )
        if declared:
            categories.add(declared)
        item_id = int(item.get("id") or 0)
        rewards = item.get("optional_gift_rewards") or []
        if rewards:
            if item_id in visited:
                unresolved.add(item_id)
                return
            visited.add(item_id)
            for reward in rewards:
                child_id = int(reward.get("id") or 0)
                child = cards.get(str(child_id))
                if child is None:
                    unresolved.add(child_id)
                else:
                    visit(child, count * float(reward.get("count") or 0), declared)
            visited.remove(item_id)
            return
        category = declared
        if category:
            categories.add(category)
            leaves.append({"item_id": item_id, "name": name, "category": category, "count_path_product": count})
        elif int(item.get("type") or 0) in (2, 21):
            unresolved.add(item_id)

    visit(card, 1)
    eligible = sorted(categories - {"炼丹"})
    direct_boxes = []
    rewards = card.get("optional_gift_rewards") or []
    for reward in rewards:
        child = cards.get(str(reward.get("id"))) or {}
        if int(child.get("type") or 0) in (2,21) and child.get("optional_gift_rewards"):
            direct_boxes.append(dict(base_id=int(child["id"]),name=child.get("name"),count=reward.get("count")))
    outer_container = bool(rewards) and len(direct_boxes) == len(rewards)
    return {
        "base_id": int(card.get("id") or 0), "name": card.get("name"),
        "catalog_fingerprint": sha256(json.dumps(card, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest(),
        "resource_categories": sorted(categories), "allocation_categories": eligible,
        "direct_reward_boxes": direct_boxes, "is_outer_container": outer_container,
        "prayer_planning_role": "convert_to_inner_boxes" if outer_container else "resource_candidate",
        "exclude_from_prayer_top_up": outer_container,
        "compound_resource": len(categories) > 1,
        "flower_only_candidate": categories == {"仙花"} and not unresolved,
        "classification_complete": not unresolved, "unresolved_item_ids": sorted(unresolved),
        "reward_leaves": leaves, "prayer_eligibility": "unverified",
        "operation_kind": "choice" if int(card.get("type") or 0) == 21 else
            "random" if "随机" in str(card.get("effect_description") or "") else "fixed",
    }


def persist_prayer_box_rule(session: Session, rule: Mapping[str, Any]) -> None:
    """显式同步结构分类；实验证据在独立域，不被目录刷新覆盖。"""
    upsert_fanxiu_business_records(session, [{
        "domain": BOX_RULE_DOMAIN, "record_key": str(rule["base_id"]),
        "entity_id": str(rule["base_id"]), "entity_name": rule.get("name"),
        "captured_at": datetime.now().astimezone().isoformat(),
        "source_kind": "catalog_reward_graph", "payload": dict(rule),
    }])


def configure_nested_prayer_box_daily(session: Session, card: Mapping[str, Any], cards: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """显式配置两层箱：外箱日常开启，含祈愿的内箱保留并单独研究。

    必须所有直接奖励都是完整已知箱子，不凭本次零分推断奖池无用。
    不启用未知内箱；此配置不执行游戏，不把内箱收益算作外箱直接积分。
    外箱奖励概率已有储物袋消耗事件累计，内箱祈愿实验仍独立归因。
    """
    from backend.core.fanxiu.storage_bag_settings import set_storage_bag_auto_claim
    rule = classify_prayer_box(card,cards)
    if not rule["is_outer_container"] or not rule["classification_complete"]:
        raise ValueError("外箱日常配置需要完整的纯箱子直接奖励图")
    child_policies = []
    for reward in rule["direct_reward_boxes"]:
        child = cards[str(reward["base_id"])]
        child_rule = classify_prayer_box(child,cards)
        persist_prayer_box_rule(session,child_rule)
        if child_rule["allocation_categories"]:
            set_storage_bag_auto_claim(session,base_id=reward["base_id"],auto_claim=False)
            if child_rule["operation_kind"] == "choice":
                persist_choice_prayer_research_plan(session,child,cards)
            child_policies.append(dict(base_id=reward["base_id"],role="prayer_resource",auto_claim=False))
        else:
            child_policies.append(dict(base_id=reward["base_id"],role="ordinary_resource",auto_claim="existing_setting"))
    persist_prayer_box_rule(session,rule)
    set_storage_bag_auto_claim(session,base_id=rule["base_id"],auto_claim=True)
    result=dict(base_id=rule["base_id"],catalog_fingerprint=rule["catalog_fingerprint"],
        prayer_eligibility="indirect_container",exclude_from_prayer_top_up=True,
        daily_cleanup_eligible=True,child_policies=child_policies)
    upsert_fanxiu_business_records(session,[{"domain":BOX_CONCLUSION_DOMAIN,"record_key":str(rule["base_id"]),
        "entity_id":str(rule["base_id"]),"captured_at":datetime.now().astimezone().isoformat(),
        "source_kind":"explicit_nested_box_daily_policy","payload":result}])
    return result


def validate_prayer_box_experiment(experiment: Mapping[str, Any]) -> dict[str, Any]:
    """核对新实验的完整同活动同轮次证据，返回计分事实；不落盘。

    前后观察必须分别携带已核实的activity_key，不能只在外层填活动名称。
    目录指纹必须在开箱前取得，不把记录时的现行目录猜作实验版本。
    isolated表示调用方已确保仅有该动作；校验不能替代真实取证。
    """
    payload = deepcopy(dict(experiment))
    before, after = payload.get("prayer_before") or {}, payload.get("prayer_after") or {}
    activity = payload.get("activity_key")
    if (not isinstance(activity, str) or not activity.strip()
            or before.get("activity_key") != activity or after.get("activity_key") != activity):
        raise ValueError("祈愿实验前后观察必须属于同一活动")
    from backend.core.fanxiu.prayer_cycle import PRAYER_CYCLE_NAMES
    if payload.get("prayer_category") not in PRAYER_CYCLE_NAMES:
        raise ValueError("祈愿实验缺少明确资源类别")
    if (not isinstance(payload.get("action_key"), str) or not payload["action_key"].strip()
            or type(payload.get("base_id")) is not int or payload["base_id"] <= 0
            or type(payload.get("opened_count")) is not int or payload["opened_count"] <= 0
            or not payload.get("catalog_fingerprint") or payload.get("isolated") is not True):
        raise ValueError("祈愿实验缺少动作凭证、实际数量、目录版本或隔离证据")
    for observation in (before, after):
        fields = ("current_round", "total_rounds", "current_score", "round_target_score")
        if (observation.get("complete") is not True
                or any(type(observation.get(k)) is not int for k in fields)
                or not 0 < observation["current_round"] <= observation["total_rounds"]
                or not 0 <= observation["current_score"] <= observation["round_target_score"]
                or observation["round_target_score"] <= 0):
            raise ValueError("祈愿实验必须有完整有效积分和轮次观察")
    if any(before[k] != after[k] for k in ("current_round", "total_rounds", "round_target_score")):
        raise ValueError("祈愿实验必须属于同轮次及相同积分规则")
    delta = after["current_score"] - before["current_score"]
    if delta < 0:
        raise ValueError("祈愿实验积分不能倒退")
    payload["score_delta"] = delta
    payload["score_per_box"] = delta / payload["opened_count"]
    return payload


def record_prayer_box_experiment(session: Session, experiment: Mapping[str, Any]) -> dict[str, Any]:
    """幂等保存真实实验；要求同活动/轮次前后积分，未归因不计收益均值。

    action_key 复用储物袋已核实开箱凭证；同键不同事实拒绝覆盖。
    开箱后绝不能因持久化失败重开；重试这里只重投实验事实。
    """
    payload = deepcopy(dict(experiment))
    key = str(payload.get("action_key") or "")
    if not key or int(payload.get("opened_count") or 0) <= 0:
        raise ValueError("祈愿实验缺少开箱凭证/数量")
    before, after = payload.get("prayer_before") or {}, payload.get("prayer_after") or {}
    if not before.get("complete") or not after.get("complete") or before.get("current_round") != after.get("current_round"):
        raise ValueError("祈愿实验必须有完整且同轮次积分观察")
    existing = session.exec(select(FanxiuPacketBusinessRecord).where(
        FanxiuPacketBusinessRecord.domain == BOX_EXPERIMENT_DOMAIN,
        FanxiuPacketBusinessRecord.record_key == key,
    )).first()
    delta = int(after["current_score"]) - int(before["current_score"])
    if delta < 0 or not payload.get("isolated") or not payload.get("activity_key"):
        raise ValueError("祈愿实验缺少同活动隔离归因证据")
    excluded = session.exec(select(FanxiuPacketBusinessRecord).where(
        FanxiuPacketBusinessRecord.domain == BOX_EXCLUDED_EXPERIMENT_DOMAIN,
        FanxiuPacketBusinessRecord.record_key == key,
    )).first()
    if excluded:
        raise ValueError("同开箱凭证已有未归因审计事实，不能直接进入收益统计")
    payload["score_delta"] = delta
    payload["score_per_box"] = delta / int(payload["opened_count"])
    if payload.get('reward_modifier_snapshot'):
        payload['reward_modifier_fingerprint'] = soil_box_reward_modifier(payload['reward_modifier_snapshot'])['fingerprint']
    elif payload.get('reward_modifier_fingerprint'):
        raise ValueError('奖励条件指纹必须由本次完整技能证据生成')
    if existing:
        if existing.payload != payload:
            raise ValueError("同开箱凭证祈愿实验事实冲突")
        return deepcopy(existing.payload)
    payload = validate_prayer_box_experiment(payload)
    if payload.get("operation_kind") == "fixed":
        rule_row = session.exec(select(FanxiuPacketBusinessRecord).where(
            FanxiuPacketBusinessRecord.domain == BOX_RULE_DOMAIN,
            FanxiuPacketBusinessRecord.record_key == str(payload["base_id"]),
        )).first()
        validate_fixed_box_reward_attribution(payload, rule_row.payload if rule_row else {})
    upsert_fanxiu_business_records(session, [{"domain": BOX_EXPERIMENT_DOMAIN,
        "record_key": key, "entity_id": str(payload["base_id"]),
        "captured_at": datetime.now().astimezone().isoformat(),
        "source_kind": "real_box_prayer_delta", "payload": payload}])
    return payload


def record_prayer_material_experiment(session: Session, experiment: Mapping[str, Any]) -> dict[str, Any]:
    """保存现有灵草神珠的一次受限使用；材料消费与开箱样本分域。

    要求两次完整储物袋投影证明同进程、同道具实际减少量，业务动作返回的
    spent必须相符；祈愿前后观察仍须完整、同活动、同轮次。纯校验和幂等
    落盘，不加载游戏，不把使用神珠的收益混入箱子均值或授予生产权限。
    """
    payload = deepcopy(dict(experiment))
    base, consumed = payload.get('base_id'), payload.get('consumed_count')
    if base not in (2006001, 2006006) or type(consumed) is not int or consumed <= 0:
        raise ValueError('材料实验仅支持已核实灵草神珠及正整数实际消耗')
    action_result = payload.get('action_result') or {}
    if (payload.get('prayer_category') != '炼丹' or type(action_result.get('spent')) is not int
            or action_result['spent'] != consumed):
        raise ValueError('神珠实验需要对应祈愿及完整动作消耗证据')
    inventories = [payload.get('inventory_before') or {}, payload.get('inventory_after') or {}]
    identities = []
    quantities = []
    for inventory in inventories:
        evidence = inventory.get('evidence') or {}
        if (inventory.get('complete') is not True
                or inventory.get('source') != 'active_backpack_panel_item_info_list'
                or not inventory.get('fingerprint') or inventory.get('base_id') != base
                or type(inventory.get('observed_at')) not in (int, float) or inventory['observed_at'] <= 0
                or type(inventory.get('quantity')) is not int or inventory['quantity'] < 0
                or any(type(evidence.get(k)) is not int or evidence[k] <= 0
                       for k in ('pid', 'process_start_ticks'))):
            raise ValueError('神珠实验需要完整同道具的当前库存投影')
        identities.append((evidence['pid'], evidence['process_start_ticks']))
        quantities.append(inventory['quantity'])
    if (identities[0] != identities[1] or quantities[0] - quantities[1] != consumed
            or inventories[0]['observed_at'] >= inventories[1]['observed_at']):
        raise ValueError('神珠实验库存进程或实际消耗与动作不符')
    # 共用活动/轮次/积分确定性校验；opened_count只用于内部校验，不落盘为箱子。
    validated = validate_prayer_box_experiment({**payload, 'opened_count': consumed})
    payload['score_delta'] = validated['score_delta']
    payload['score_per_material'] = payload['score_delta'] / consumed
    key = payload['action_key']
    existing = session.exec(select(FanxiuPacketBusinessRecord).where(
        FanxiuPacketBusinessRecord.domain == MATERIAL_EXPERIMENT_DOMAIN,
        FanxiuPacketBusinessRecord.record_key == key,
    )).first()
    if existing:
        if existing.payload != payload:
            raise ValueError('同神珠动作凭证存在冲突事实')
        return deepcopy(existing.payload)
    upsert_fanxiu_business_records(session, [{'domain': MATERIAL_EXPERIMENT_DOMAIN,
        'record_key': key, 'entity_id': str(base),
        'captured_at': datetime.now().astimezone().isoformat(),
        'source_kind': 'verified_prayer_material_use', 'payload': payload}])
    return payload


def soil_box_reward_modifier(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """从当前丹灵升级页原生事实解析九转坤元；不套用目录中的其他等级。

    这是奖励归因条件，不能反推旧实验当时拥有此技能。只识别已经实机
    核验的土灵宝匣双倍收益规则，不泛化为所有固定箱的任意数量加成。
    """
    current = snapshot.get('current') or {}
    skill = snapshot.get('current_skill') or {}
    cfg = skill.get('config') or {}
    evidence = snapshot.get('evidence') or {}
    stamp = snapshot.get('observed_at')
    if (snapshot.get('source') != 'active_danling_detail_level_panel'
            or snapshot.get('tab') != 1 or current.get('name') != '坤元土灵'
            or skill.get('name') != '九转坤元'
            or any(type(current.get(k)) is not int or current[k] <= 0 for k in ('id','stage','level'))
            or any(skill.get(k) != current[v] for k,v in (('pet_id','id'),('stage','stage'),('level','level')))
            or cfg.get('petId') != current['id'] or cfg.get('stage') != current['level']
            or any(type(cfg.get(k)) is not int for k in ('petId','stage'))
            or any(type(evidence.get(k)) is not int or evidence[k] <= 0 for k in ('pid','process_start_ticks'))
            or type(stamp) not in (int,float) or not math.isfinite(stamp) or stamp <= 0):
        raise ValueError('土灵双倍归因需要完整当前技能身份、时间及进程证据')
    text = re.sub(r'<[^>]+>|\s+', '', str(skill.get('description') or ''))
    match = re.search(r'每次使用【土灵灵草宝匣】[，,](\d+(?:\.\d+)?)%概率获得双倍收益', text)
    if not match or not 0 < float(match[1]) <= 100:
        raise ValueError('土灵当前技能未声明已核验的双倍收益规则')
    identity = dict(base_id=39032101, pet_id=current['id'], stage=current['stage'],
        level=current['level'], probability=float(match[1])/100, config=cfg, description=text)
    # 阶数用于证明技能已激活；在同一当前等级配置下升阶不改变双倍概率，
    # 不因为与本奖励无关的阶数增长把同一种收益条件拆成多组。
    effective = {key:value for key,value in identity.items() if key != 'stage'}
    return {**identity, 'fingerprint':sha256(json.dumps(effective,ensure_ascii=False,sort_keys=True).encode()).hexdigest()}


def validate_fixed_box_reward_attribution(experiment: Mapping[str, Any], rule: Mapping[str, Any]) -> None:
    """固定箱已到账叶子的数量必须匹配目录乘箱数，否则收益来源待核实。

    条件奖励可以只出现当前可用叶子，不用目录全集冒充当前奖池。
    加成或额外到账尚无独立证据时，保留开箱凭证，但不计入研究均值。
    """
    if rule.get("catalog_fingerprint") != experiment.get("catalog_fingerprint"):
        raise ValueError("固定箱归因缺少同版本奖励规则")
    counts = {}
    for leaf in rule.get("reward_leaves") or []:
        key = int(leaf["item_id"])
        counts[key] = counts.get(key, 0) + float(leaf["count_path_product"])
    rewards = experiment.get("rewards") or []
    snapshot = experiment.get('reward_modifier_snapshot')
    if snapshot:
        modifier = soil_box_reward_modifier(snapshot)
        evidence = experiment.get('evidence') or {}
        stamp = evidence.get('observed_at_before')
        if (experiment.get('base_id') != modifier['base_id']
                or any(evidence.get(k) != snapshot['evidence'][k] for k in ('pid','process_start_ticks'))
                or type(stamp) not in (int,float) or not math.isfinite(stamp)
                or not 0 <= stamp - snapshot['observed_at'] <= 900):
            raise ValueError('土灵双倍技能证据必须先于动作、保持新鲜且属于同进程')
        units = []
        for row in rewards:
            nominal = counts.get(int(row['item_id']), 0)
            if nominal <= 0:
                raise ValueError('土灵双倍奖励包含未知叶子')
            units.append(float(row['quantity'])/nominal)
        n = experiment['opened_count']
        if (not units or len(set(units)) != 1 or not units[0].is_integer()
                or not n <= units[0] <= 2*n):
            raise ValueError('土灵奖励不符合每箱至多一次全奖励双倍规则')
        return
    if not rewards or any(
            int(row["item_id"]) not in counts
            or float(row["quantity"]) != counts[int(row["item_id"])] * experiment["opened_count"]
            for row in rewards):
        raise ValueError("固定箱实际奖励与目录乘箱数不符，额外收益未归因，实验不计入期望")


def record_unattributed_fixed_box_experiment(session: Session, experiment: Mapping[str, Any]) -> dict[str, Any]:
    """保存完整但固定奖励数量不符的研究事实；独立审计域永不进入收益统计。

    必须具有完整同活动同轮积分及同版本固定箱规则，不能保存中断半样本。
    原始事实幂等且不可覆盖；已经进入正常实验域的样本使用隔离接口处理。
    """
    payload = validate_prayer_box_experiment(experiment)
    key = payload['action_key']
    if payload.get('operation_kind') != 'fixed':
        raise ValueError('未归因固定箱审计需要固定箱动作')
    rule = session.exec(select(FanxiuPacketBusinessRecord).where(
        FanxiuPacketBusinessRecord.domain == BOX_RULE_DOMAIN,
        FanxiuPacketBusinessRecord.record_key == str(payload['base_id']),
    )).first()
    if not rule or rule.payload.get('catalog_fingerprint') != payload['catalog_fingerprint']:
        raise ValueError('未归因固定箱审计缺少同版本规则')
    try:
        validate_fixed_box_reward_attribution(payload, rule.payload)
    except ValueError as exc:
        fact = {'experiment': payload, 'reason': str(exc)}
    else:
        raise ValueError('固定奖励数量符合规则，不应作为未归因样本')
    existing = session.exec(select(FanxiuPacketBusinessRecord).where(
        FanxiuPacketBusinessRecord.domain.in_((BOX_EXPERIMENT_DOMAIN, BOX_EXCLUDED_EXPERIMENT_DOMAIN)),
        FanxiuPacketBusinessRecord.record_key == key,
    )).all()
    for row in existing:
        if row.domain != BOX_EXCLUDED_EXPERIMENT_DOMAIN or row.payload != fact:
            raise ValueError('同开箱凭证未归因实验事实冲突')
        return deepcopy(row.payload)
    upsert_fanxiu_business_records(session, [{
        'domain': BOX_EXCLUDED_EXPERIMENT_DOMAIN, 'record_key': key,
        'entity_id': str(payload['base_id']), 'captured_at': datetime.now().astimezone().isoformat(),
        'source_kind': 'unattributed_fixed_box_prayer_delta', 'payload': fact,
    }])
    return fact


def quarantine_prayer_box_experiment(session: Session, *, action_key: str,
                                    reason: str, evidence: Mapping[str, Any]) -> dict[str, Any]:
    """隔离有归因疑点的已保存样本，保留原始事实，不删除或重开箱补账。"""
    row = session.exec(select(FanxiuPacketBusinessRecord).where(
        FanxiuPacketBusinessRecord.domain == BOX_EXPERIMENT_DOMAIN,
        FanxiuPacketBusinessRecord.record_key == action_key,
    )).first()
    if row is None or not reason.strip() or not evidence:
        raise ValueError("样本隔离需要已有动作、明确原因及证据")
    payload = dict(action_key=action_key, base_id=row.payload["base_id"],
                   reason=reason.strip(), evidence=dict(evidence))
    existing = session.exec(select(FanxiuPacketBusinessRecord).where(
        FanxiuPacketBusinessRecord.domain == BOX_EXPERIMENT_QUARANTINE_DOMAIN,
        FanxiuPacketBusinessRecord.record_key == action_key,
    )).first()
    if existing and existing.payload != payload:
        raise ValueError("同动作样本隔离事实冲突")
    upsert_fanxiu_business_records(session, [{"domain": BOX_EXPERIMENT_QUARANTINE_DOMAIN,
        "record_key": action_key, "entity_id": str(row.payload["base_id"]),
        "source_kind": "unresolved_experiment_attribution", "payload": payload}])
    return payload


def record_fixed_box_non_prayer_conclusion(session: Session, *, action_key: str) -> dict[str, Any]:
    """完整固定仙花奖励均已实际获得且积分零变化时，明确排除开箱补分。

    结论绑定目录版本与获取动作；不推断赠送仙花也零分，不套用随机箱。
    目录刷新不会覆盖独立结论，奖励结构改变后旧结论自动失效。
    """
    experiment = session.exec(select(FanxiuPacketBusinessRecord).where(
        FanxiuPacketBusinessRecord.domain == BOX_EXPERIMENT_DOMAIN,
        FanxiuPacketBusinessRecord.record_key == action_key,
    )).first()
    if experiment is None:
        raise ValueError("缺少持久开箱积分实验")
    quarantine = session.exec(select(FanxiuPacketBusinessRecord).where(
        FanxiuPacketBusinessRecord.domain == BOX_EXPERIMENT_QUARANTINE_DOMAIN,
        FanxiuPacketBusinessRecord.record_key == action_key,
    )).first()
    if quarantine:
        raise ValueError("归因待核实的样本不能形成零分结论")
    e = experiment.payload
    rule_row = session.exec(select(FanxiuPacketBusinessRecord).where(
        FanxiuPacketBusinessRecord.domain == BOX_RULE_DOMAIN,
        FanxiuPacketBusinessRecord.record_key == str(e["base_id"]),
    )).first()
    rule = rule_row.payload if rule_row else {}
    fingerprint = e.get("catalog_fingerprint")
    if not fingerprint:
        version = session.exec(select(FanxiuPacketBusinessRecord).where(
            FanxiuPacketBusinessRecord.domain == BOX_EXPERIMENT_VERSION_DOMAIN,
            FanxiuPacketBusinessRecord.record_key == action_key,
        )).first()
        fingerprint = version.payload.get("catalog_fingerprint") if version else None
    if not fingerprint or fingerprint != rule.get("catalog_fingerprint"):
        raise ValueError("零分结论必须绑定实验当时相同目录版本")
    if (rule.get("operation_kind") != "fixed" or e.get("operation_kind") != "fixed"
            or not rule.get("classification_complete") or rule.get("resource_categories") != ["仙花"]
            or e.get("score_delta") != 0):
        raise ValueError("只有完整固定纯仙花零分实验可以证明奖池无开箱祈愿收益")
    expected = {}
    for leaf in rule["reward_leaves"]:
        key = int(leaf["item_id"])
        expected[key] = expected.get(key, 0) + float(leaf["count_path_product"]) * int(e["opened_count"])
    actual = {int(x["item_id"]): int(x["quantity"]) for x in e["rewards"]}
    if not expected or expected != actual:
        raise ValueError("实际奖励未完整覆盖固定奖池")
    result = dict(base_id=e["base_id"], catalog_fingerprint=rule["catalog_fingerprint"],
                  action_key=action_key, activity_key=e["activity_key"], category="仙花",
                  acquisition_method="open_box", prayer_eligibility="verified_non_prayer",
                  exclude_from_prayer_top_up=True, daily_cleanup_eligible=True)
    from backend.core.fanxiu.storage_bag_settings import set_storage_bag_auto_claim
    set_storage_bag_auto_claim(session, base_id=e["base_id"], auto_claim=True)
    upsert_fanxiu_business_records(session, [{"domain": BOX_CONCLUSION_DOMAIN,
        "record_key": str(e["base_id"]), "entity_id": str(e["base_id"]),
        "captured_at": datetime.now().astimezone().isoformat(),
        "source_kind": "fixed_pool_zero_score_verified", "payload": result}])
    return result


def persist_choice_prayer_research_plan(session: Session, card: Mapping[str, Any], cards: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """自选箱跨周研究计划：每个非炼丹祈愿选项只先测1箱，未知不推荐。

    此函数仅配置，不派发游戏动作。对应祈愿活动开启且运行权可用才
    允许实验；证据取得后 load 接口投影进度，不持久步骤或GUI状态。
    """
    rule = classify_prayer_box(card, cards)
    if rule["operation_kind"] != "choice" or not rule["classification_complete"]:
        raise ValueError("跨周选项研究需要完整自选奖励图")
    options = []
    excluded = []
    for reward in card.get("optional_gift_rewards") or []:
        child = cards.get(str(reward["id"]))
        if not child:
            raise ValueError("自选选项目录不完整")
        child_rule = classify_prayer_box(child, cards)
        categories = child_rule["allocation_categories"]
        if child_rule["resource_categories"] == ["炼丹"]:
            excluded.append(dict(choice_key=str(reward["id"]), name=child["name"], reason="炼丹不参与用途竞争"))
            continue
        if len(categories) != 1:
            raise ValueError("选项不是单一非炼丹祈愿类别，先研究结构")
        options.append(dict(choice_key=str(reward["id"]), name=child["name"],
            count_per_box=reward["count"], prayer_category=categories[0], experiment_open_count=1))
    plan = dict(base_id=rule["base_id"], name=rule["name"], catalog_fingerprint=rule["catalog_fingerprint"],
                options=options, excluded_options=excluded, automatic_research_enabled=False,
                reserve_for_prayer=True, comparison="expected_score_divided_by_round_target",
                minimum_reserved_boxes=len(options))
    # 复合研究未完成前不进入无条件日常开箱；保留已有选择备注。
    from backend.core.fanxiu.storage_bag_settings import set_storage_bag_auto_claim
    set_storage_bag_auto_claim(session, base_id=rule["base_id"], auto_claim=False)
    persist_prayer_box_rule(session, rule)
    upsert_fanxiu_business_records(session, [{"domain": BOX_CHOICE_PLAN_DOMAIN,
        "record_key": str(rule["base_id"]), "entity_id": str(rule["base_id"]),
        "captured_at": datetime.now().astimezone().isoformat(), "source_kind": "choice_cross_week_research", "payload": plan}])
    return plan


def choice_prayer_experiment_from_verified_open(session: Session, execution: Any, *, prayer_before: Mapping[str, Any], activity_key: str, prayer_category: str) -> dict[str, Any]:
    """构造本次自选实验事实，不落盘；完整前后积分齐全才记录实验。

    任何失败都丢弃本次研究样本，下次从新积分与库存重新实验。
    不跨 attempt 补账，也不保存待续跑步骤；实际库存由游戏持有。
    """
    delta = execution.delta
    if delta is None or delta.opened_count <= 0:
        raise ValueError("缺少已核实自选箱消耗与奖励差值")
    rule_row = session.exec(select(FanxiuPacketBusinessRecord).where(
        FanxiuPacketBusinessRecord.domain == BOX_RULE_DOMAIN,
        FanxiuPacketBusinessRecord.record_key == str(execution.request.base_id),
    )).first()
    if rule_row is None:
        raise ValueError("自选研究缺少目录版本")
    plan_row = session.exec(select(FanxiuPacketBusinessRecord).where(
        FanxiuPacketBusinessRecord.domain == BOX_CHOICE_PLAN_DOMAIN,
        FanxiuPacketBusinessRecord.record_key == str(execution.request.base_id),
    )).first()
    if plan_row is None or plan_row.payload["catalog_fingerprint"] != rule_row.payload["catalog_fingerprint"]:
        raise ValueError("自选实验目录与研究计划版本不一致")
    option = next((o for o in plan_row.payload["options"] if o["choice_key"] == str(delta.reward_base_id)), None)
    if option is None or option["prayer_category"] != prayer_category:
        raise ValueError("实验选择项与对应祈愿类别不一致")
    key_data = (execution.request.base_id, execution.request.instance_id, delta.before_fingerprint,
                delta.after_fingerprint, delta.reward_base_id)
    key = "prayer-choice:" + sha256(json.dumps(key_data).encode()).hexdigest()
    payload = dict(base_id=execution.request.base_id, action_key=key, opened_count=delta.opened_count,
        rewards=[dict(item_id=delta.reward_base_id, name=execution.selected_reward.name, quantity=delta.reward_quantity)],
        choice_key=str(delta.reward_base_id), prayer_category=prayer_category,
        catalog_fingerprint=rule_row.payload["catalog_fingerprint"], operation_kind="choice",
        prayer_before=dict(prayer_before), activity_key=activity_key, isolated=True,
        evidence=dict(before_fingerprint=delta.before_fingerprint, after_fingerprint=delta.after_fingerprint,
                      instance_id=execution.request.instance_id))
    return payload


def record_prayer_experiment_catalog_evidence(session: Session, *, action_key: str, original_card: Mapping[str, Any]) -> dict[str, Any]:
    """补充旧实验缺失的目录版本凭证，不改原始实验事实。

    original_card 必须是实验前保存的目录证据，不能拿现行版本猜历史。
    同凭证不同版本拒绝覆盖；load 以此版本归组，才能继续累积历史收益。
    """
    experiment = session.exec(select(FanxiuPacketBusinessRecord).where(
        FanxiuPacketBusinessRecord.domain == BOX_EXPERIMENT_DOMAIN,
        FanxiuPacketBusinessRecord.record_key == action_key,
    )).first()
    if experiment is None or int(original_card.get("id") or 0) != int(experiment.payload["base_id"]):
        raise ValueError("原始目录证据与实验箱子不符")
    fingerprint = sha256(json.dumps(original_card,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()
    existing = session.exec(select(FanxiuPacketBusinessRecord).where(
        FanxiuPacketBusinessRecord.domain == BOX_EXPERIMENT_VERSION_DOMAIN,
        FanxiuPacketBusinessRecord.record_key == action_key,
    )).first()
    fact = dict(action_key=action_key,base_id=experiment.payload["base_id"],catalog_fingerprint=fingerprint)
    if existing and existing.payload != fact:
        raise ValueError("旧实验目录证据冲突")
    upsert_fanxiu_business_records(session,[{"domain":BOX_EXPERIMENT_VERSION_DOMAIN,"record_key":action_key,
        "entity_id":str(fact["base_id"]),"captured_at":datetime.now().astimezone().isoformat(),
        "source_kind":"original_pre_experiment_catalog","payload":fact}])
    return fact


def load_prayer_box_research(session: Session) -> dict[str, Any]:
    """只读持久事实；满档截断和未归因实验保留但不授予收益统计资格。"""
    rows = session.exec(select(FanxiuPacketBusinessRecord).where(
        FanxiuPacketBusinessRecord.domain.in_((BOX_RULE_DOMAIN, BOX_EXPERIMENT_DOMAIN, BOX_CONCLUSION_DOMAIN, BOX_CHOICE_PLAN_DOMAIN, BOX_EXPERIMENT_VERSION_DOMAIN, BOX_EXPERIMENT_QUARANTINE_DOMAIN, BOX_EXCLUDED_EXPERIMENT_DOMAIN, MATERIAL_EXPERIMENT_DOMAIN)),
    )).all()
    rules = [deepcopy(r.payload) for r in rows if r.domain == BOX_RULE_DOMAIN]
    experiments = [deepcopy(r.payload) for r in rows if r.domain == BOX_EXPERIMENT_DOMAIN]
    quarantines = {r.record_key: deepcopy(r.payload) for r in rows if r.domain == BOX_EXPERIMENT_QUARANTINE_DOMAIN}
    quarantined = [dict(experiment=e, quarantine=quarantines[e["action_key"]])
                   for e in experiments if e["action_key"] in quarantines]
    experiments = [e for e in experiments if e["action_key"] not in quarantines]
    capped = [e for e in experiments if _prayer_experiment_is_round_capped(e)]
    experiments = [e for e in experiments if not _prayer_experiment_is_round_capped(e)]
    versions = {r.record_key: r.payload for r in rows if r.domain == BOX_EXPERIMENT_VERSION_DOMAIN}
    for experiment in experiments:
        if not experiment.get("catalog_fingerprint") and experiment["action_key"] in versions:
            experiment["catalog_fingerprint"] = versions[experiment["action_key"]]["catalog_fingerprint"]
    conclusions = [deepcopy(r.payload) for r in rows if r.domain == BOX_CONCLUSION_DOMAIN
                   and r.payload.get("action_key") not in quarantines]
    for rule in rules:
        for conclusion in conclusions:
            if (rule["base_id"] == conclusion["base_id"]
                    and rule["catalog_fingerprint"] == conclusion["catalog_fingerprint"]):
                rule.update({k: conclusion[k] for k in ("prayer_eligibility", "exclude_from_prayer_top_up", "daily_cleanup_eligible")})
    choice_plans = [deepcopy(r.payload) for r in rows if r.domain == BOX_CHOICE_PLAN_DOMAIN]
    for plan in choice_plans:
        for option in plan["options"]:
            matches = [e for e in experiments if int(e["base_id"]) == plan["base_id"]
                and e.get("catalog_fingerprint") == plan["catalog_fingerprint"]
                and e.get("choice_key") == option["choice_key"]
                and e.get("prayer_category") == option["prayer_category"]]
            option["status"] = "measured" if matches else "waiting_corresponding_prayer"
            option["opened_count"] = sum(int(e["opened_count"]) for e in matches)
        plan["all_options_measured"] = all(o["status"] == "measured" for o in plan["options"])
    summaries = summarize_prayer_box_experiments(experiments)
    rule_by_id = {int(rule["base_id"]): rule for rule in rules}
    for summary in summaries:
        rule = rule_by_id.get(summary["base_id"], {})
        if (rule.get("exclude_from_prayer_top_up") and summary["total_score_delta"] == 0
                and summary["prayer_category"] == "仙花"
                and summary["catalog_fingerprint"] == rule.get("catalog_fingerprint")):
            summary["eligibility"] = "verified_non_prayer"
    # 候选分类保留炼丹专属箱；allocation_categories只排除复合自选用途竞争。
    candidates = [r for r in rules if not r.get("exclude_from_prayer_top_up")
                  and (r.get("resource_categories") or r.get("allocation_categories"))]
    daily_cleanup = [r for r in rules if r.get("daily_cleanup_eligible")]
    return {"rules": rules, "experiments": experiments, "summaries": summaries,
            "material_experiments": [deepcopy(r.payload) for r in rows if r.domain == MATERIAL_EXPERIMENT_DOMAIN],
            "round_capped_experiments": deepcopy(capped),
            "excluded_experiments": [deepcopy(r.payload) for r in rows if r.domain == BOX_EXCLUDED_EXPERIMENT_DOMAIN],
            "quarantined_experiments": quarantined,
            "conclusions": conclusions, "prayer_candidates": candidates, "daily_cleanup": daily_cleanup, "choice_plans": choice_plans}


def _prayer_experiment_is_round_capped(experiment: Mapping[str, Any]) -> bool:
    after = experiment.get('prayer_after') or {}
    target = after.get('round_target_score')
    return type(target) is int and target > 0 and after.get('current_score') == target


def summarize_prayer_box_experiments(experiments: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """历史每箱积分=总积分/总消耗，按类别、选项和目录版本分别累计。

    不平均大小不同的批次均值，不把一次零分当理论期望零分。
    仅有批量合计时不能恢复单箱方差或置信区间，返回空值而非假精度。
    同动作凭证在持久层幂等；这里再次拒绝重复输入，避免重复计数。
    满档后的显示积分只能证明增量下界，不能区分恰好满档与超过满档；
    这类事实不计收益均值，也不能作为自选选项已测完的证据。
    """
    groups = {}
    keys = set()
    for sample in experiments:
        action_key = str(sample.get("action_key") or "")
        if not action_key or action_key in keys:
            raise ValueError("实验统计需要唯一动作凭证")
        keys.add(action_key)
        if _prayer_experiment_is_round_capped(sample):
            continue
        n, score = int(sample["opened_count"]), int(sample["score_delta"])
        if n <= 0 or score < 0:
            raise ValueError("实验数量/积分增量无效")
        # 既有三个真实实验均为当前仙花活动；新类别须明确填入。
        category = sample.get("prayer_category")
        if not category and str(sample.get("activity_key") or "").startswith("flower-prayer-"):
            category = "仙花"
        if not category:
            raise ValueError("跨祈愿统计缺少资源类别")
        key = (int(sample["base_id"]), str(category), str(sample.get("choice_key") or ""), sample.get("catalog_fingerprint") or "unversioned:" + action_key,
               sample.get('reward_modifier_fingerprint') or '')
        groups.setdefault(key, []).append(sample)
    summaries = []
    for (base_id, category, choice_key, fingerprint, modifier_fingerprint), samples in groups.items():
        n = sum(int(e["opened_count"]) for e in samples)
        total = sum(int(e["score_delta"]) for e in samples)
        values = [int(e["score_delta"]) / int(e["opened_count"]) for e in samples]
        by_count = {}
        for e in samples:
            by_count.setdefault(int(e['opened_count']), []).append(int(e['score_delta']) / int(e['opened_count']))
        batch_statistics = [dict(opened_count=count, experiment_count=len(means),
            mean_score_per_box=sum(means)/len(means),
            batch_mean_stddev=stdev(means) if len(means)>1 else None)
            for count, means in sorted(by_count.items())]
        summaries.append(dict(base_id=base_id, prayer_category=category, choice_key=choice_key,
            reward_modifier_fingerprint=modifier_fingerprint,
            catalog_fingerprint=fingerprint, opened_count=n, experiment_count=len(samples),
            total_score_delta=total, mean_score_per_box=total / n,
            estimate_kind="historical_sample_mean",
            batch_mean_stddev=stdev(values) if len(values) > 1 and len(by_count)==1 else None,
            batch_statistics=batch_statistics,
            individual_score_stddev=None, confidence_interval=None,
            eligibility="verified_positive" if total > 0 else "zero_observed_unproven_pool"))
        if base_id == 39032101 and not modifier_fingerprint:
            summaries[-1].update(estimate_kind='historical_without_reward_modifier_evidence',
                                 eligibility='waiting_verified_reward_context')
    return summaries


def choose_prayer_box_allocation(expected_scores: Mapping[str, float], round_targets: Mapping[str, int]) -> dict[str, Any]:
    """已测完选项按满轮比例比较，保留全部并列最大用途；炼丹排除。

    这是用途判断，不是消耗排序。所有未知目标显式拒绝，调用方应先
    确认该箱所有非炼丹选项都有证据，禁止用缺测子集提前做唯一推荐。
    """
    unresolved = sorted(c for c in expected_scores if c != "炼丹" and
                        (c not in round_targets or int(round_targets[c]) <= 0))
    if unresolved:
        raise ValueError(f"祈愿满轮目标未核实：{unresolved}")
    values = {c: float(v) / int(round_targets[c]) for c, v in expected_scores.items() if c != "炼丹"}
    if any(not math.isfinite(v) or v < 0 for v in values.values()):
        raise ValueError("祈愿期望收益必须为有限非负值")
    best = max(values.values(), default=0)
    chosen = sorted(c for c,v in values.items() if best > 0 and math.isclose(v,best,rel_tol=1e-9,abs_tol=1e-12))
    return {"recommended_category": chosen[0] if len(chosen)==1 else None,
            "usable_categories": chosen, "expected_round_fraction": values,
            "status": "recommended" if chosen else "no_positive_prayer_yield"}


def plan_choice_prayer_allocations(research: Mapping[str, Any], round_targets: Mapping[str, int]) -> list[dict[str, Any]]:
    """跨周研究进度门卫：所有非炼丹选项测齐后才判断最佳用途。

    目标规则缺失继续待核实；不同箱子/目录版本/选择项/祈愿类别独立。
    该计划不操作游戏，也不负责派发，每次从持久事实重新计算。
    """
    results = []
    summaries = research.get("summaries") or []
    for plan in research.get("choice_plans") or []:
        scores = {}
        missing = []
        for option in plan["options"]:
            sample = next((x for x in summaries if x["base_id"] == plan["base_id"]
                and x["catalog_fingerprint"] == plan["catalog_fingerprint"]
                and x["choice_key"] == option["choice_key"]
                and x["prayer_category"] == option["prayer_category"]),None)
            if sample is None:
                missing.append(option["prayer_category"])
            else:
                category = option["prayer_category"]
                # 同类别可能有多个选择项；用途比较取该类最佳选项，不能
                # 让目录最后一项覆盖更高收益。仍要求所有选项各自测齐。
                scores[category] = max(scores.get(category, 0), sample["mean_score_per_box"])
        row = dict(base_id=plan["base_id"], catalog_fingerprint=plan["catalog_fingerprint"],
                   missing_categories=sorted(set(missing)), measured_scores=scores)
        if missing:
            row.update(status="waiting_corresponding_prayer", usable_categories=[])
        elif any(c not in round_targets or int(round_targets[c]) <= 0 for c in scores):
            row.update(status="waiting_verified_round_targets", usable_categories=[])
        else:
            row.update(choose_prayer_box_allocation(scores,round_targets))
        results.append(row)
    return results


def order_prayer_top_up_resources(resources: list[Mapping[str, Any]], *, current_category: str) -> list[dict[str, Any]]:
    """已核实可用资源的消耗顺序：当前周专属先，多种用途的后。

    usable_categories 是最终用途列表（自选并列最优也必须全部保留）；
    未测/未完成规划的资源不进入此队列。用途越广越晚，保留调配余地。
    相同广度按输入顺序稳定排列，不凭未知经济价值强行打破并列。
    """
    eligible = [dict(r) for r in resources if r.get("eligibility_verified") is True
                and current_category in set(r.get("usable_categories") or [])
                and not r.get("exclude_from_prayer_top_up")]
    return sorted(eligible,key=lambda r: len(set(r["usable_categories"]) - {"炼丹"}))
