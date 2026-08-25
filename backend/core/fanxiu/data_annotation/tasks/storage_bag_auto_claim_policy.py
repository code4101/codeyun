"""Pure planner for user-selected storage-bag item templates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping


PlanAction = Literal["execute", "route", "defer", "fail"]

# A routed item is owned by exactly one independent business lifecycle.  Keep
# the route identity equal to that lifecycle's task_type instead of inventing
# a second action-shaped name such as ``route_npc_gift``.
NPC_GIFT_EXTERNAL_ROUTE = "xianyuan_auto_gift"


@dataclass(frozen=True)
class StorageBagAutoClaimDecision:
    action: PlanAction
    base_id: int
    quantity: int
    operation_template: str
    yield_mode: str
    note: str
    reason: str
    external_route: str = ""
    choice_kind: str = ""
    choice_value: str = ""
    condition_key: str = ""


def parse_storage_bag_choice_note(note: str) -> tuple[str, str]:
    """Parse only the two currently authorized, deterministic choice forms."""

    text = re.sub(r"\s+", "", str(note or ""))
    if not text:
        raise ValueError("自选匣备注为空")
    if text == "选第1个可以选的仙侣":
        return "first_available_partner", ""
    if re.search(r"第(?:1|一)个(?:可以|可)选", text):
        return "first_available", ""
    match = re.fullmatch(r"选(?:择)?(.+)", text)
    if match and match.group(1):
        return "named", match.group(1)
    raise ValueError("自选匣备注只支持“选目标名”或“选第1个可以选的…”")


def storage_bag_note_condition(note: str) -> str:
    compact = re.sub(r"\s+", "", str(note or ""))
    if "洗灵祈愿周" in compact:
        return "xiling_prayer_week"
    if "周使用" in compact or "活动时使用" in compact:
        return "unresolved_activity_window"
    return ""


def decide_storage_bag_auto_claim_item(
    row: Mapping[str, Any],
) -> StorageBagAutoClaimDecision | None:
    if row.get("auto_claim") is not True:
        return None
    base_id = int(row.get("base_id") or 0)
    quantity = int(row.get("num") or 0)
    template = str(row.get("operation_template") or "")
    yield_mode = str(row.get("yield_mode") or "")
    note = str(row.get("note") or "").strip()
    if base_id <= 0:
        return StorageBagAutoClaimDecision("fail", base_id, quantity, template, yield_mode, note, "缺少有效 base_id")
    if quantity <= 0:
        return None
    if str(row.get("analysis_status") or "") != "classified" or not template:
        return StorageBagAutoClaimDecision("fail", base_id, quantity, template, yield_mode, note, "物品尚未完成一次性分类")
    if template == "npc_gift":
        return StorageBagAutoClaimDecision(
            "route", base_id, quantity, template, yield_mode, note,
            "NPC 礼物由仙缘送礼业务生命周期消费",
            external_route=NPC_GIFT_EXTERNAL_ROUTE,
        )
    if template == "unsupported":
        return StorageBagAutoClaimDecision("fail", base_id, quantity, template, yield_mode, note, "没有已知可复用操作模板")
    condition_key = storage_bag_note_condition(note)
    if condition_key:
        return StorageBagAutoClaimDecision(
            "defer", base_id, quantity, template, yield_mode, note,
            "备注包含活动时机；必须由权威活动 Runtime 证明当前窗口",
            condition_key=condition_key,
        )
    if template == "choice_box":
        try:
            choice_kind, choice_value = parse_storage_bag_choice_note(note)
        except ValueError as exc:
            return StorageBagAutoClaimDecision("fail", base_id, quantity, template, yield_mode, note, str(exc))
        return StorageBagAutoClaimDecision(
            "execute", base_id, quantity, template, yield_mode, note,
            "自选规则已解析；仍需 GUI 候选唯一映射与选中态复验",
            choice_kind=choice_kind,
            choice_value=choice_value,
        )
    if template in {"random_box", "fixed_box", "direct_use", "special_use"}:
        return StorageBagAutoClaimDecision(
            "execute", base_id, quantity, template, yield_mode, note,
            "已进入共享模板；动作前仍需 Runtime/GUI 双重门卫",
        )
    return StorageBagAutoClaimDecision("fail", base_id, quantity, template, yield_mode, note, "分类值不属于支持集合")


__all__ = [
    "NPC_GIFT_EXTERNAL_ROUTE",
    "StorageBagAutoClaimDecision",
    "decide_storage_bag_auto_claim_item",
    "parse_storage_bag_choice_note",
    "storage_bag_note_condition",
]
