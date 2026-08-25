from __future__ import annotations

from typing import Any

from backend.core.fanxiu.prayer_cycle import current_prayer_cycle


FINAL_MAIL_STATUSES = {"claimed", "deleted", "missing_from_list"}
REQUESTED_MAIL_STATUSES = {"claim_requested", "delete_requested"}
MAIL_DESIRED_STATUSES = {"锁定", "留存", "可领"}
MAIL_PRAYER_AUTO_CLAIM_MAX_VALUE = 200
MAIL_PRAYER_TIE_BREAK_PRIORITY: tuple[str, ...] = ("仙花", "洗灵", "淬体", "灵兽", "炼丹")

MAIL_PRAYER_VALUES_BY_CATEGORY: dict[str, dict[str, float]] = {
    "炼丹": {"炼丹灵草匣": 50, "神品灵草匣": 500, "炼丹灵草宝匣": 100},
    "淬体": {"淬体精魄": 10},
    "灵兽": {"珍品饲灵丸": 100},
    "洗灵": {"洗灵奇石": 10},
    "仙花": {"瑶池玉莲": 100, "造化青莲": 0},
}

MAIL_PROTECTED_RESOURCE_NAMES_BY_CATEGORY: dict[str, set[str]] = {
    category: set(values)
    for category, values in MAIL_PRAYER_VALUES_BY_CATEGORY.items()
}

MAIL_PROTECTED_RESOURCE_NAMES = {
    name
    for names in MAIL_PROTECTED_RESOURCE_NAMES_BY_CATEGORY.values()
    for name in names
}

MAIL_ALWAYS_CLAIM_RESOURCE_KEYWORDS = {"潜修心得"}
MAIL_ALWAYS_CLAIM_TITLE_KEYWORDS = {
    "宗门灵泉活动收益",
    "魔狱封阵奖励",
}


def fanxiu_mail_title_is_always_claim(title: Any) -> bool:
    text = str(title or "").strip()
    return bool(text and any(keyword in text for keyword in MAIL_ALWAYS_CLAIM_TITLE_KEYWORDS))


def fanxiu_mail_title_force_claim_allowed(title: Any, rewards: list[dict[str, Any]]) -> bool:
    if not fanxiu_mail_title_is_always_claim(title) or not rewards:
        return False
    for reward in rewards:
        if not fanxiu_mail_reward_name_known(reward) or fanxiu_mail_reward_is_faze(reward):
            return False
    return True


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


def fanxiu_mail_rewards_unresolved(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("mail_rewards_unresolved") or payload.get("has_attachment_hint"):
        return True
    if payload.get("orphan_action_status") is not None:
        return True
    return isinstance(payload.get("packet_orphan_action"), dict)


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


def fanxiu_mail_reward_prayer_value(reward: dict[str, Any]) -> float | None:
    item_name = _mail_reward_text(reward, "item_name")
    if not item_name:
        return None
    for values in MAIL_PRAYER_VALUES_BY_CATEGORY.values():
        if item_name in values:
            return values[item_name]
    return None


def _mail_reward_amount(reward: dict[str, Any]) -> float:
    raw_amount = reward.get("amount", 1)
    try:
        amount = float(raw_amount)
    except (TypeError, ValueError):
        return 0
    return amount if amount > 0 else 0


def fanxiu_mail_prayer_values_by_category(rewards: list[dict[str, Any]]) -> dict[str, float]:
    totals = {category: 0.0 for category in MAIL_PRAYER_VALUES_BY_CATEGORY}
    for reward in rewards:
        category = fanxiu_mail_reward_protected_resource_category(reward)
        unit_value = fanxiu_mail_reward_prayer_value(reward)
        if category and unit_value is not None:
            totals[category] += unit_value * _mail_reward_amount(reward)
    return totals


def fanxiu_mail_prayer_target(rewards: list[dict[str, Any]]) -> tuple[str, float]:
    totals = fanxiu_mail_prayer_values_by_category(rewards)
    max_value = max(totals.values(), default=0)
    if max_value <= 0:
        return "", 0
    for category in MAIL_PRAYER_TIE_BREAK_PRIORITY:
        if totals.get(category) == max_value:
            return category, max_value
    return "", 0


def fanxiu_mail_reward_is_always_claim(reward: dict[str, Any]) -> bool:
    item_name = _mail_reward_text(reward, "item_name")
    return bool(item_name and any(keyword in item_name for keyword in MAIL_ALWAYS_CLAIM_RESOURCE_KEYWORDS))


def fanxiu_mail_reward_name_known(reward: dict[str, Any]) -> bool:
    item_name = _mail_reward_text(reward, "item_name")
    return bool(item_name and not item_name.startswith("未知道具"))


def fanxiu_mail_desired_status_for_rewards(
    rewards: list[dict[str, Any]],
    *,
    prayer_category: str | None = None,
) -> str:
    if not rewards:
        return "可领"
    for reward in rewards:
        if not fanxiu_mail_reward_name_known(reward):
            return "留存"
        if fanxiu_mail_reward_is_faze(reward):
            return "锁定"
    if any(fanxiu_mail_reward_is_always_claim(reward) for reward in rewards):
        return "可领"
    target_category, max_value = fanxiu_mail_prayer_target(rewards)
    if max_value <= MAIL_PRAYER_AUTO_CLAIM_MAX_VALUE:
        return "可领"
    current_category = prayer_category or current_prayer_cycle()
    return "可领" if target_category == current_category else "留存"


def fanxiu_mail_action_policy_for_rewards(
    rewards: list[dict[str, Any]],
    *,
    prayer_category: str | None = None,
) -> str:
    status = fanxiu_mail_desired_status_for_rewards(rewards, prayer_category=prayer_category)
    return "claim" if status == "可领" else ""


def fanxiu_mail_desired_status_for_record(record: Any | None) -> str:
    if record is None:
        return ""
    status = str(record.status or "").strip()
    if status in MAIL_DESIRED_STATUSES:
        return status
    if bool(record.locked):
        return "锁定"
    legacy_status = status.lower()
    if legacy_status == "seen":
        return "留存"
    return ""


def fanxiu_mail_action_policy_for_record(record: Any | None) -> str:
    if record is None:
        return ""
    runtime_status = str(getattr(record, "runtime_status", "") or "").strip()
    if runtime_status:
        if (
            runtime_status != "unclaimed"
            or not bool(getattr(record, "present_in_runtime", False))
            or bool(getattr(record, "locked", False))
        ):
            return ""
        return "claim" if str(getattr(record, "action_policy", "") or "").strip() == "claim" else ""
    status = str(record.status or "").strip().lower()
    if status in FINAL_MAIL_STATUSES or status in REQUESTED_MAIL_STATUSES:
        return ""
    return "claim" if str(record.status or "").strip() == "可领" else ""


def fanxiu_mail_visible_group_action_policy(records: list[Any]) -> str:
    if not records:
        return ""
    for record in records:
        if fanxiu_mail_action_policy_for_record(record) != "claim":
            return ""
    return "claim"
