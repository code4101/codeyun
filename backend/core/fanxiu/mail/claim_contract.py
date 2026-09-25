"""邮件领取与清理的领域规则，独立于 Task、Kernel 和 GUI。

仅解释调用方提供的 Runtime 快照，不读取游戏或数据库，不提交未知物品协助。
策略校验、目标筛选、删除保护和终态计数共用同一套规则；实际动作由 Task 编排。
protected_claim_authorizer 是调用方提供的显式授权判定，锁定邮件始终排除。
"""
from __future__ import annotations

from typing import Any, Callable

from .policy import (
    fanxiu_mail_reward_is_always_claim,
    fanxiu_mail_reward_name_known,
    fanxiu_mail_rewards_from_payload,
    fanxiu_mail_rewards_unresolved,
)


class MailPolicyClassificationError(RuntimeError):
    def __init__(self, message: str, *, unknown_items: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.unknown_items = list(unknown_items or [])



def claimable_mail_targets(
    snapshot: dict[str, Any],
    *,
    protected_claim_authorizer: Callable[[dict[str, Any]], bool] | None = None,
) -> list[dict[str, Any]]:
    items = snapshot.get("items")
    if not isinstance(items, list):
        return []
    return [
        item
        for item in items
        if isinstance(item, dict)
        and str(item.get("execution_status") or "") == "unclaimed"
        and bool(item.get("present_in_runtime"))
        and not bool(item.get("locked"))
        and (
            str(item.get("action_policy") or "") == "claim"
            or (
                protected_claim_authorizer is not None
                and bool(protected_claim_authorizer(item))
            )
        )
    ]


def runtime_mail_identity(item: dict[str, Any]) -> str:
    return str(item.get("id") or item.get("mail_id") or "").strip()


def deletable_mail_garbage(
    snapshot: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return the exact unlocked Runtime mails covered by one-key delete."""

    items = snapshot.get("items")
    if not isinstance(items, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        mail_id = runtime_mail_identity(item)
        if (
            mail_id
            and bool(item.get("present_in_runtime"))
            and not bool(item.get("locked"))
            and (
                str(item.get("execution_status") or "") == "claimed"
                or (
                    str(item.get("execution_status") or "") == "no_attachment"
                    and runtime_mail_read_state(item) is True
                )
            )
        ):
            result[mail_id] = item
    return result


def runtime_mail_read_state(item: dict[str, Any]) -> bool | None:
    """Return Runtime's authoritative read flag when projection preserved it."""

    direct = item.get("read")
    if isinstance(direct, bool):
        return direct
    payload = item.get("payload")
    runtime_payload = payload.get("runtime") if isinstance(payload, dict) else None
    nested = runtime_payload.get("read") if isinstance(runtime_payload, dict) else None
    return nested if isinstance(nested, bool) else None


def protected_mail_ids(snapshot: dict[str, Any]) -> set[str]:
    """Return locked or policy-retained mails that deletion must preserve."""

    return {
        runtime_mail_identity(item)
        for item in snapshot.get("items") or []
        if isinstance(item, dict)
        and bool(item.get("present_in_runtime"))
        and runtime_mail_identity(item)
        and (
            bool(item.get("locked"))
            or (
                bool(item.get("has_attachment"))
                and str(item.get("execution_status") or "") == "unclaimed"
            )
        )
    }


def validate_mail_policy_snapshot(
    snapshot: dict[str, Any],
    *,
    reason: str,
    require_all_classified: bool = False,
) -> None:
    """Fail closed unless every live attachment has an explicit safe policy."""

    failures: list[str] = []
    unknown_items: list[dict[str, Any]] = []
    for item in snapshot.get("items") or []:
        if not isinstance(item, dict) or not bool(item.get("present_in_runtime")):
            continue
        if str(item.get("execution_status") or "") != "unclaimed" or not bool(
            item.get("has_attachment")
        ):
            continue
        mail_id = str(item.get("id") or item.get("mail_id") or "?")
        desired = str(item.get("desired_status") or "").strip()
        policy = str(item.get("action_policy") or "").strip()
        locked = bool(item.get("locked"))
        # Reward-name completeness is an authority requirement for a
        # positive claim action, not for an explicit no-op.  A retained
        # or locked mail is already fail-closed: this task will not click
        # it, so a newly introduced item id must not block unrelated,
        # fully classified claim targets forever.
        if locked:
            if desired != "锁定" or policy:
                failures.append(f"{mail_id}:锁定邮件策略不一致")
            continue
        payload = item.get("payload")
        rewards = fanxiu_mail_rewards_from_payload(payload)
        if fanxiu_mail_rewards_unresolved(payload) or not rewards:
            failures.append(f"{mail_id}:奖励未解析")
            continue
        has_always_claim_reward = any(
            fanxiu_mail_reward_is_always_claim(reward)
            for reward in rewards
        )
        unresolved = [
            reward
            for reward in rewards
            if not fanxiu_mail_reward_name_known(reward)
        ]
        if unresolved and not has_always_claim_reward:
            item_ids = [str(reward.get("item_id") or "?") for reward in unresolved]
            unknown_items.extend(
                {
                    "mail_id": mail_id,
                    "item_id": str(reward.get("item_id") or ""),
                    "reward_type": reward.get("type"),
                    "item_type": str(reward.get("item_type") or ""),
                    "item_type_id": reward.get("item_type_id"),
                    "item_sub_type_id": reward.get("item_sub_type_id"),
                    "runtime_name_id": reward.get("runtime_name_id"),
                    "icon": str(reward.get("icon") or ""),
                    "use_condition": str(reward.get("use_condition") or ""),
                    "name_source": str(reward.get("name_source") or ""),
                    "policy_resolution": str(reward.get("policy_resolution") or ""),
                }
                for reward in unresolved
            )
            # At task start an unknown retained mail must not prevent
            # unrelated, fully classified claim targets from running.  At
            # terminal verification it is not a completed business state:
            # keep the job due and escalate the exact evidence.
            if require_all_classified or desired not in {"锁定", "留存"} or policy:
                failures.append(f"{mail_id}:存在未知道具 {item_ids}")
                continue
        if desired in {"锁定", "留存"} and not policy:
            continue
        if desired == "可领" and policy == "claim":
            continue
        failures.append(f"{mail_id}:desired={desired or '-'} policy={policy or '-'}")
    if failures:
        raise MailPolicyClassificationError(
            f"邮件_选择性领取：{reason}存在 {len(failures)} 封未完成安全分类的附件邮件，"
            f"拒绝领取并拒绝顺延到次日；details={failures[:8]}",
            unknown_items=unknown_items,
        )


def validate_mail_terminal_result(
    result: dict[str, Any],
    *,
    target_count: int,
    require_garbage_cleanup: bool = True,
) -> None:
    """Validate the count contract before exposing a successful summary."""

    if str(result.get("result") or "") != "success":
        raise RuntimeError("邮件_选择性领取：批次没有形成 success 业务终态")
    claimed_count = int(result.get("claimed_count") or 0)
    garbage_before = int(result.get("garbage_before") or 0)
    garbage_after = int(result.get("garbage_after") or 0)
    deleted_count = int(result.get("deleted_count") or 0)
    protected_count = int(result.get("protected_count") or 0)
    if claimed_count != int(target_count):
        raise RuntimeError(
            "邮件_选择性领取：批次仍有待领取目标或领取计数不一致，"
            f"target={target_count} claimed={claimed_count}"
        )
    if require_garbage_cleanup and (
        garbage_after != 0 or deleted_count != garbage_before
    ):
        raise RuntimeError(
            "邮件_选择性领取：可删除垃圾未形成归零闭环，"
            f"before={garbage_before} deleted={deleted_count} after={garbage_after}"
        )
    if min(claimed_count, garbage_before, garbage_after, deleted_count, protected_count) < 0:
        raise RuntimeError("邮件_选择性领取：业务终态计数非法，拒绝报告成功")


def select_mail_claim_targets(
    snapshot: dict[str, Any],
    target_mail_ids: set[str] | None,
    *,
    protected_claim_authorizer: Callable[[dict[str, Any]], bool] | None = None,
) -> list[dict[str, Any]]:
    targets = claimable_mail_targets(
        snapshot,
        protected_claim_authorizer=protected_claim_authorizer,
    )
    wanted = {str(value) for value in target_mail_ids or set() if str(value)}
    if not wanted:
        return targets
    return [
        item
        for item in targets
        if str(item.get("id") or item.get("mail_id") or "") in wanted
    ]


def mail_target_requires_claim(
    snapshot: dict[str, Any],
    mail_id: str,
    *,
    protected_claim_authorizer: Callable[[dict[str, Any]], bool] | None = None,
) -> bool:
    """Return whether one exact Runtime identity still needs a claim.

    A claim request is irreversible and the detail sheet may already have
    switched from #122 (claim) to #123 (delete) before the batch's stable
    snapshot is refreshed.  Only a new complete MailMgr read may classify
    that case as already completed; title similarity is insufficient when
    several adjacent mails look identical.
    """

    target_id = str(mail_id or "")
    return any(
        str(item.get("id") or item.get("mail_id") or "") == target_id
        for item in claimable_mail_targets(
            snapshot,
            protected_claim_authorizer=protected_claim_authorizer,
        )
    )
