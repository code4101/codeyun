from __future__ import annotations

from typing import Any

from backend.models import FanxiuMailRecord


FINAL_MAIL_STATUSES = {"claimed", "deleted", "missing_from_list"}
REQUESTED_MAIL_STATUSES = {"claim_requested", "delete_requested"}

MAIL_PROTECTED_RESOURCE_NAMES_BY_CATEGORY: dict[str, set[str]] = {
    "炼丹": {"炼丹灵草匣", "神品灵草匣", "炼丹灵草宝匣"},
    "淬体": {"淬体精魄"},
    "灵兽": {"珍品饲灵丸"},
    "洗灵": {"洗灵奇石"},
    "仙花": {"瑶池玉莲", "造化青莲"},
}

MAIL_PROTECTED_RESOURCE_NAMES = {
    name
    for names in MAIL_PROTECTED_RESOURCE_NAMES_BY_CATEGORY.values()
    for name in names
}

MAIL_ALWAYS_CLAIM_RESOURCE_NAMES = {"潜修心得·四刻"}


def fanxiu_mail_rewards_from_payload(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rewards = payload.get("mail_rewards")
    if isinstance(rewards, list):
        return [item for item in rewards if isinstance(item, dict)]
    packet_payload = payload.get("packet")
    if isinstance(packet_payload, dict):
        rewards = packet_payload.get("mail_rewards")
        if isinstance(rewards, list):
            return [item for item in rewards if isinstance(item, dict)]
    return []


def _mail_reward_text(reward: dict[str, Any], key: str) -> str:
    return str(reward.get(key) or "").strip()


def fanxiu_mail_reward_is_faze(reward: dict[str, Any]) -> bool:
    item_type = _mail_reward_text(reward, "item_type")
    item_name = _mail_reward_text(reward, "item_name")
    return item_type == "法则" or ("法则" in item_name if item_name else False)


def fanxiu_mail_reward_protected_resource_category(reward: dict[str, Any]) -> str:
    item_name = _mail_reward_text(reward, "item_name")
    if not item_name:
        return ""
    for category, names in MAIL_PROTECTED_RESOURCE_NAMES_BY_CATEGORY.items():
        if item_name in names:
            return category
    return ""


def fanxiu_mail_reward_is_always_claim(reward: dict[str, Any]) -> bool:
    return _mail_reward_text(reward, "item_name") in MAIL_ALWAYS_CLAIM_RESOURCE_NAMES


def fanxiu_mail_reward_name_known(reward: dict[str, Any]) -> bool:
    item_name = _mail_reward_text(reward, "item_name")
    return bool(item_name and not item_name.startswith("未知道具"))


def fanxiu_mail_action_policy_for_rewards(rewards: list[dict[str, Any]]) -> str:
    if not rewards:
        return "delete"
    for reward in rewards:
        if not fanxiu_mail_reward_name_known(reward):
            return ""
        if fanxiu_mail_reward_is_faze(reward):
            return ""
    if any(fanxiu_mail_reward_is_always_claim(reward) for reward in rewards):
        return "claim"
    for reward in rewards:
        if fanxiu_mail_reward_protected_resource_category(reward):
            return ""
    return "claim"


def fanxiu_mail_action_policy_for_record(record: FanxiuMailRecord | None) -> str:
    if record is None:
        return ""
    if bool(record.locked):
        return ""
    if str(record.status or "").strip().lower() in FINAL_MAIL_STATUSES | REQUESTED_MAIL_STATUSES:
        return ""
    return fanxiu_mail_action_policy_for_rewards(fanxiu_mail_rewards_from_payload(record.payload))
