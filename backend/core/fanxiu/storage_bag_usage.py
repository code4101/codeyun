"""Persistent policy classification and verified yield accounting for storage-bag items."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

from sqlmodel import Session, delete, select

from backend.models import (
    FanxiuStorageBagItemSetting,
    FanxiuStorageBagOpenEvent,
    FanxiuStorageBagYieldAggregate,
)
from backend.core.fanxiu.instrumentation.backpack_ui import backpack_ui_snapshot_fingerprint


ANALYSIS_VERSION = 2
STORAGE_BAG_OPERATION_TEMPLATES = frozenset({
    "random_box",
    "fixed_box",
    "choice_box",
    "direct_use",
    "special_use",
    "npc_gift",
    "unsupported",
})
STORAGE_BAG_YIELD_MODES = frozenset({"random", "fixed", "none"})


@dataclass(frozen=True)
class StorageBagItemAnalysis:
    operation_template: str
    yield_mode: str
    reason: str
    fingerprint: str


@dataclass(frozen=True)
class StorageBagVerifiedOpenDelta:
    opened_count: int
    rewards: tuple[dict[str, Any], ...]
    before_fingerprint: str
    after_fingerprint: str


def _analysis_text(row: Mapping[str, Any]) -> tuple[str, str, str, bool]:
    item = row.get("item") if isinstance(row.get("item"), Mapping) else {}
    name = str(item.get("name") or row.get("name") or "").strip()
    type_name = str(item.get("type_name") or row.get("type_name") or "").strip()
    details = json.dumps(
        {
            "description": item.get("description"),
            "effect_description": item.get("effect_description"),
            "effect_detail_preview": item.get("effect_detail_preview"),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return name, type_name, details, bool(item.get("can_use"))


def storage_bag_analysis_fingerprint(row: Mapping[str, Any]) -> str:
    """Hash only immutable catalog semantics; user notes remain live policy input."""

    name, type_name, details, can_use = _analysis_text(row)
    payload = {
        "version": ANALYSIS_VERSION,
        "base_id": int(row.get("base_id") or 0),
        "name": name,
        "type_name": type_name,
        "details": details,
        "can_use": can_use,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def analyze_storage_bag_item(row: Mapping[str, Any]) -> StorageBagItemAnalysis:
    """Classify reward semantics once, without granting any GUI click permission."""

    fingerprint = storage_bag_analysis_fingerprint(row)
    name, type_name, details, can_use = _analysis_text(row)
    item = row.get("item") if isinstance(row.get("item"), Mapping) else {}
    # Candidate reward names may themselves contain words such as “随机匣”.
    # They describe a child reward, not the owning box's selection semantics.
    # Random/fixed classification therefore uses only the owning item's text.
    compact = "".join((
        name,
        type_name,
        str(item.get("description") or ""),
        str(item.get("effect_description") or ""),
    )).replace(" ", "")
    if type_name == "NPC礼物":
        return StorageBagItemAnalysis("npc_gift", "none", "Catalog 明确标识 NPC礼物", fingerprint)
    if type_name == "自选匣" or any(token in compact for token in ("任一种", "任选", "自选")):
        return StorageBagItemAnalysis("choice_box", "none", "Catalog 明确包含自选奖励语义", fingerprint)
    if "拜师函" in name:
        return StorageBagItemAnalysis("special_use", "random", "拜师函属于独立使用流程", fingerprint)

    box_like = type_name == "礼包宝匣" or any(
        token in name for token in ("匣", "箱", "宝袋", "馈赠", "赠宝", "甄选礼")
    )
    random_like = any(token in compact for token in ("随机", "概率", "有概率", "随机掉落"))
    if box_like and random_like:
        return StorageBagItemAnalysis("random_box", "random", "Catalog 奖励文案包含随机/概率语义", fingerprint)
    if box_like:
        return StorageBagItemAnalysis("fixed_box", "fixed", "Catalog 为非自选、非随机礼包", fingerprint)
    if can_use or name in {"灵石", "VIP经验"}:
        return StorageBagItemAnalysis("direct_use", "fixed", "Catalog 标识为可直接使用物品", fingerprint)
    return StorageBagItemAnalysis("unsupported", "none", "Catalog 无法归入已知可复用模板", fingerprint)


def ensure_storage_bag_item_analysis(
    session: Session,
    row: Mapping[str, Any],
) -> FanxiuStorageBagItemSetting:
    """Persist analysis once per catalog fingerprint and reuse it thereafter."""

    base_id = int(row.get("base_id") or 0)
    if base_id <= 0:
        raise ValueError("储物袋分类需要有效 base_id")
    analysis = analyze_storage_bag_item(row)
    record = session.get(FanxiuStorageBagItemSetting, base_id)
    now = time.time()
    if record is None:
        record = FanxiuStorageBagItemSetting(base_id=base_id, created_at=now, updated_at=now)
    if (
        record.analysis_status != "classified"
        or record.analysis_fingerprint != analysis.fingerprint
        or record.operation_template not in STORAGE_BAG_OPERATION_TEMPLATES
        or record.yield_mode not in STORAGE_BAG_YIELD_MODES
    ):
        record.operation_template = analysis.operation_template
        record.yield_mode = analysis.yield_mode
        record.analysis_status = "classified"
        record.analysis_fingerprint = analysis.fingerprint
        record.analysis_reason = analysis.reason
        record.analyzed_at = now
        record.updated_at = now
        session.add(record)
        session.flush()
    return record


def ensure_storage_bag_atlas_analysis(
    session: Session,
    atlas: Mapping[str, Any],
) -> int:
    """Classify every cumulative atlas row and refresh changed catalog fingerprints."""

    count = 0
    for row in atlas.get("items") or []:
        if not isinstance(row, Mapping):
            continue
        ensure_storage_bag_item_analysis(session, row)
        count += 1
    return count


def _reward_identity(row: Mapping[str, Any]) -> tuple[str, int | None, str]:
    reward_key = str(row.get("reward_key") or "").strip()
    raw_item_id = row.get("item_id")
    item_id = int(raw_item_id) if str(raw_item_id or "").isdigit() and int(raw_item_id) > 0 else None
    name = str(row.get("name") or "").strip()
    if not reward_key and item_id is None and not name:
        raise ValueError("收益行必须有 item_id 或名称")
    return reward_key or (f"id:{item_id}" if item_id is not None else f"name:{name}"), item_id, name


def derive_storage_bag_open_delta(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    target_base_id: int,
    target_instance_id: str,
    catalog_cards_by_id: Mapping[str, Mapping[str, Any]],
    additional_rewards: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]] = (),
) -> StorageBagVerifiedOpenDelta:
    """Attribute one globally-serialized use/open action from two complete Runtime lists."""

    for label, snapshot in (("动作前", before), ("动作后", after)):
        if snapshot.get("complete") is not True:
            raise ValueError(f"{label}储物袋 Runtime 快照不完整")
        if snapshot.get("source") != "active_backpack_panel_item_info_list":
            raise ValueError(f"{label}储物袋 Runtime 不是活动面板 ItemInfoList")
    before_fingerprint = str(before.get("fingerprint") or backpack_ui_snapshot_fingerprint(before))
    after_fingerprint = str(after.get("fingerprint") or backpack_ui_snapshot_fingerprint(after))
    if not before_fingerprint or not after_fingerprint:
        raise ValueError("动作前后储物袋 Runtime 缺少规范指纹")
    if before_fingerprint == after_fingerprint:
        raise ValueError("动作前后储物袋 Runtime 指纹没有变化")

    def instance_quantities(snapshot: Mapping[str, Any]) -> dict[str, tuple[int, int]]:
        result: dict[str, tuple[int, int]] = {}
        for raw in snapshot.get("items") or []:
            if not isinstance(raw, Mapping) or raw.get("is_padding"):
                continue
            instance_id = str(raw.get("instance_id") or "").strip()
            base_id = int(raw.get("base_id") or 0)
            quantity = int(raw.get("num") or 0)
            if not instance_id or base_id <= 0 or quantity < 0 or instance_id in result:
                raise ValueError("储物袋 Runtime 实例身份无效或重复")
            result[instance_id] = (base_id, quantity)
        return result

    before_instances = instance_quantities(before)
    after_instances = instance_quantities(after)
    target_id = str(target_instance_id or "").strip()
    target_before = before_instances.get(target_id)
    if target_before is None or target_before[0] != int(target_base_id):
        raise ValueError("动作前 Runtime 没有唯一目标实例")
    target_after = after_instances.get(target_id)
    if target_after is not None and target_after[0] != int(target_base_id):
        raise ValueError("动作后目标实例身份发生冲突")
    opened_count = target_before[1] - (target_after[1] if target_after else 0)
    if opened_count <= 0:
        raise ValueError("目标实例数量没有减少，不能证明实际开启数量")

    before_by_base: dict[int, int] = {}
    after_by_base: dict[int, int] = {}
    for base_id, quantity in before_instances.values():
        before_by_base[base_id] = before_by_base.get(base_id, 0) + quantity
    for base_id, quantity in after_instances.values():
        after_by_base[base_id] = after_by_base.get(base_id, 0) + quantity
    rewards: list[dict[str, Any]] = []
    for base_id in sorted(set(before_by_base) | set(after_by_base)):
        delta = after_by_base.get(base_id, 0) - before_by_base.get(base_id, 0)
        if base_id == int(target_base_id):
            if delta != -opened_count:
                raise ValueError("目标物品总量变化与目标实例开启数量不一致")
            continue
        if delta < 0:
            raise ValueError(f"非目标物品 {base_id} 数量减少，无法唯一归因本次收益")
        if delta <= 0:
            continue
        card = catalog_cards_by_id.get(str(base_id)) or {}
        rewards.append({
            "item_id": base_id,
            "name": str(card.get("name") or "").strip(),
            "quantity": delta,
        })
    rewards = _normalized_rewards([*rewards, *additional_rewards]) if (rewards or additional_rewards) else []
    if not rewards:
        raise ValueError("动作后没有可归因的正向奖励增量")
    return StorageBagVerifiedOpenDelta(
        opened_count=opened_count,
        rewards=tuple(rewards),
        before_fingerprint=before_fingerprint,
        after_fingerprint=after_fingerprint,
    )


def _normalized_rewards(rewards: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for raw in rewards:
        key, item_id, name = _reward_identity(raw)
        reward_key = str(raw.get("reward_key") or "").strip()
        quantity = int(raw.get("quantity") or 0)
        if quantity <= 0:
            raise ValueError("收益数量必须为正整数")
        if key not in merged:
            order.append(key)
            merged[key] = {"item_id": item_id, "name": name, "quantity": 0}
            if reward_key:
                merged[key]["reward_key"] = reward_key
        elif name and not merged[key]["name"]:
            merged[key]["name"] = name
        merged[key]["quantity"] += quantity
    if not merged:
        raise ValueError("已执行开箱不能记录空收益")
    return [merged[key] for key in order]


def format_storage_bag_average_yield(
    opened_count: int,
    total_rewards: list[Mapping[str, Any]],
) -> str:
    if opened_count <= 0:
        return ""
    parts: list[str] = []
    for row in total_rewards:
        quantity = int(row.get("quantity") or 0)
        if quantity < 0:
            raise ValueError("累计收益不能为负")
        value = Decimal(quantity) / Decimal(opened_count)
        text = format(value.quantize(Decimal("0.01")), "f").rstrip("0").rstrip(".")
        label = str(row.get("name") or "").strip() or str(row.get("item_id") or "未知")
        parts.append(f"{label}{text}")
    return "，".join(parts)


def record_storage_bag_open_event(
    session: Session,
    *,
    action_key: str,
    base_id: int,
    operation_template: str,
    opened_count: int,
    rewards: list[Mapping[str, Any]],
    runtime_before_fingerprint: str,
    runtime_after_fingerprint: str,
    evidence: Mapping[str, Any] | None = None,
) -> FanxiuStorageBagYieldAggregate:
    """Atomically append one verified event and update its cumulative projection."""

    normalized_action_key = str(action_key or "").strip()
    if not normalized_action_key:
        raise ValueError("收益事件缺少幂等 action_key")
    if operation_template not in {"random_box", "fixed_box", "direct_use", "special_use"}:
        raise ValueError("该操作模板不产生可统计的自动收益")
    if int(base_id) <= 0 or int(opened_count) <= 0:
        raise ValueError("收益事件需要有效物品 ID 和实际使用数量")
    if not runtime_before_fingerprint or not runtime_after_fingerprint:
        raise ValueError("收益事件必须包含动作前后 Runtime 指纹")
    setting = session.get(FanxiuStorageBagItemSetting, int(base_id))
    if (
        setting is None
        or setting.analysis_status != "classified"
        or setting.operation_template != operation_template
        or setting.yield_mode not in {"random", "fixed"}
    ):
        raise ValueError("收益事件与持久化物品分类或收益模式不一致")
    existing = session.exec(
        select(FanxiuStorageBagOpenEvent).where(
            FanxiuStorageBagOpenEvent.action_key == normalized_action_key
        )
    ).first()
    if existing is not None:
        aggregate = session.get(FanxiuStorageBagYieldAggregate, int(base_id))
        if aggregate is None or existing.base_id != int(base_id):
            raise ValueError("收益事件幂等键与聚合身份冲突")
        return aggregate

    normalized_rewards = _normalized_rewards(list(rewards))
    event = FanxiuStorageBagOpenEvent(
        action_key=normalized_action_key,
        base_id=int(base_id),
        operation_template=operation_template,
        opened_count=int(opened_count),
        rewards=normalized_rewards,
        runtime_before_fingerprint=str(runtime_before_fingerprint),
        runtime_after_fingerprint=str(runtime_after_fingerprint),
        evidence=dict(evidence or {}),
    )
    aggregate = session.get(FanxiuStorageBagYieldAggregate, int(base_id))
    if aggregate is None:
        aggregate = FanxiuStorageBagYieldAggregate(base_id=int(base_id))
    totals = _normalized_rewards([
        *(aggregate.total_rewards or []),
        *normalized_rewards,
    ])
    aggregate.opened_count = int(aggregate.opened_count or 0) + int(opened_count)
    aggregate.total_rewards = totals
    aggregate.average_yield = format_storage_bag_average_yield(aggregate.opened_count, totals)
    aggregate.updated_at = time.time()
    session.add(event)
    session.add(aggregate)
    session.flush()
    return aggregate


def delete_storage_bag_usage_history(session: Session, *, base_id: int) -> None:
    session.exec(delete(FanxiuStorageBagOpenEvent).where(FanxiuStorageBagOpenEvent.base_id == base_id))
    aggregate = session.get(FanxiuStorageBagYieldAggregate, base_id)
    if aggregate is not None:
        session.delete(aggregate)
    session.flush()


__all__ = [
    "ANALYSIS_VERSION",
    "StorageBagItemAnalysis",
    "StorageBagVerifiedOpenDelta",
    "analyze_storage_bag_item",
    "delete_storage_bag_usage_history",
    "derive_storage_bag_open_delta",
    "ensure_storage_bag_atlas_analysis",
    "ensure_storage_bag_item_analysis",
    "format_storage_bag_average_yield",
    "record_storage_bag_open_event",
    "storage_bag_analysis_fingerprint",
]
