"""Choose one reward from documented cultivation milestones, independently of UI.

Documents describe benefits; Runtime supplies current progress; the channel
supplies items. These are deliberately separate. No item IDs, names, fixed
five-level ladder or one-copy-one-level assumption belongs to the selector.

Policy: spirit-stone income > recurring resources > panel > ranking > combat.
Within a value class, first attain the documented entry milestone, then unlock another resource
chain, then improve production. This is a stage policy, not a claim that unlike
resources have a common numeric utility. Unknown costs remain unknown; ties
use channel order and never fabricate a cost/benefit ratio.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


VALUE_ORDER = ("spirit_stones", "recurring_resources", "panel", "ranking", "combat")
PHASE_ORDER = ("entry_milestone", "new_chain", "increase", "other")


class CultivationChoiceError(RuntimeError):
    """Missing identity, current progress or documented cultivation rules."""


@dataclass(frozen=True)
class CultivationChoice:
    item_id: int
    column: int
    reason: str
    targets: tuple[dict[str, Any], ...]
    comparisons: tuple[dict[str, Any], ...]


def progress_key(component: Mapping[str, Any]) -> str:
    """Namespace dimensions: pet level is never confused with pet pin."""
    return f"{component['kind']}:{int(component['target_id'])}:{component.get('dimension') or 'rank'}"


def committed_cultivation_choice(
    reward_items: Sequence[Mapping[str, Any]], selected_item_id: int,
) -> int | None:
    """Respect an instance's committed selection before re-planning (idempotency)."""
    if selected_item_id <= 0:
        return None
    matches = [i for i, row in enumerate(reward_items, 1) if int(row["item_id"]) == selected_item_id]
    if len(matches) != 1:
        raise CultivationChoiceError("当期已配置大奖不在当前唯一候选中")
    return matches[0]


def validate_cultivation_rule(
    rule: Mapping[str, Any], *, item_id: int, allowed_keys: set[str], source_refs: set[str]
) -> dict[str, Any]:
    """Validate persisted/AI facts alike. Unknown thresholds must be omitted.

    Empty milestones are an explicit unsupported evidence state, not rank zero.
    Every goal must cite a supplied source and refer to a real target dimension.
    ``copies_needed`` is intentionally not accepted from a language model:
    channel yield and upgrade costs are separate facts, outside this selector.
    """
    if type(rule.get("item_id")) is not int or rule["item_id"] != item_id:
        raise CultivationChoiceError("养成规则道具身份不一致")
    milestones = []
    signatures = set()
    for raw in rule.get("milestones") or []:
        category, phase = raw.get("category"), raw.get("phase")
        if category not in VALUE_ORDER or phase not in PHASE_ORDER:
            raise CultivationChoiceError("养成收益类别或阶段无效")
        if phase != "other" and category not in VALUE_ORDER[:2]:
            raise CultivationChoiceError("非持续资源收益不能标记为产出阶段")
        refs = raw.get("sources") or []
        summary = str(raw.get("summary") or "").strip()
        if not summary or not refs or not set(refs).issubset(source_refs):
            raise CultivationChoiceError("养成节点缺少有效文档证据")
        targets = []
        for goal in raw.get("targets") or []:
            key, level = goal.get("key"), goal.get("level")
            if key not in allowed_keys or type(level) is not int or level <= 0:
                raise CultivationChoiceError("养成节点目标维度或层数无效")
            targets.append({"key": key, "level": level})
        keys = [goal["key"] for goal in targets]
        if not targets or len(set(keys)) != len(keys):
            raise CultivationChoiceError("养成节点目标缺失或重复")
        signature = (category, tuple(sorted((g["key"], g["level"]) for g in targets)))
        if signature in signatures:
            raise CultivationChoiceError("养成节点重复；套装联动收益只能记录一次")
        signatures.add(signature)
        milestones.append({"targets": targets, "category": category, "phase": phase,
                           "summary": summary, "sources": list(refs)})
    return {"item_id": item_id, "milestones": milestones,
            "limitations": str(rule.get("limitations") or "")}


def plan_single_cultivation_choice(
    reward_items: Sequence[Mapping[str, Any]],
    owned_items: Sequence[Mapping[str, Any]],
    rules: Mapping[int, Mapping[str, Any]],
) -> CultivationChoice:
    """Plan a single channel selection from fresh facts; no gameplay or writes.

    A milestone is attained only if *all* its dimension requirements are met.
    Already attained milestones disappear on the next call. A nearer unmet
    milestone in the same category precedes a later one that requires it.
    Exhausted documented ladders are reported, never extended by guessing.
    The target is aspirational: selecting an item does not promise reaching it.
    """
    if not reward_items:
        raise CultivationChoiceError("自选候选为空")
    ids = [int(r["item_id"]) for r in reward_items]
    if len(set(ids)) != len(ids):
        raise CultivationChoiceError("自选候选道具重复")
    states = {}
    for owned in owned_items:
        item_id = int(owned["item_id"])
        if item_id in states:
            raise CultivationChoiceError("道具进度重复")
        components = owned.get("components") or []
        if not components:
            raise CultivationChoiceError(f"道具 {item_id} 缺少完整维度进度")
        values = {}
        for component in components:
            key, rank = progress_key(component), component.get("rank")
            if key in values or type(rank) is not int or rank < 0:
                raise CultivationChoiceError("维度进度缺失、重复或非法")
            values[key] = rank
        states[item_id] = values
    comparisons, options = [], []
    for column, reward in enumerate(reward_items, 1):
        item_id = int(reward["item_id"])
        if item_id not in states or item_id not in rules:
            raise CultivationChoiceError(f"道具 {item_id} 缺少进度或文档规则")
        current = states[item_id]
        pending = []
        for milestone in rules[item_id].get("milestones") or []:
            if any(g["key"] not in current for g in milestone["targets"]):
                raise CultivationChoiceError(f"道具 {item_id} 的规则与 Runtime 维度不一致")
            if not all(current[g["key"]] >= g["level"] for g in milestone["targets"]):
                pending.append(milestone)
        # Pareto prerequisite within each value class, not an assumed 5/10 grid.
        next_nodes = []
        for node in pending:
            requirements = {g["key"]: g["level"] for g in node["targets"]}
            preceded = False
            for other in pending:
                earlier = {g["key"]: g["level"] for g in other["targets"]}
                if (other is not node and other["category"] == node["category"]
                        and earlier.keys() == requirements.keys()
                        and all(earlier[k] <= requirements[k] for k in earlier)
                        and any(earlier[k] < requirements[k] for k in earlier)):
                    preceded = True
                    break
            if not preceded:
                next_nodes.append(node)
        row = {"item_id": item_id, "name": str(reward.get("name") or item_id),
               "current": current, "next_milestones": next_nodes,
               "limitations": rules[item_id].get("limitations", ""),
               "copies_to_target": None}
        comparisons.append(row)
        for node in next_nodes:
            options.append(((VALUE_ORDER.index(node["category"]),
                             PHASE_ORDER.index(node["phase"]), column), column, row, node))
    if not options:
        raise CultivationChoiceError("所有候选均已达到已知节点，需要补充后续文档；未推测下一档")
    _, column, row, node = min(options, key=lambda option: option[0])
    targets = tuple({**g, "current": row["current"][g["key"]]} for g in node["targets"])
    goal_text = "、".join(f"{g['key']} {g['current']}→{g['level']}" for g in targets)
    reason = (f"{row['name']}；目标 {goal_text}；{node['summary']}；"
              f"按持续资源阶段策略选择；升级成本未核实时不承诺本次达到目标")
    return CultivationChoice(row["item_id"], column, reason, targets, tuple(comparisons))
