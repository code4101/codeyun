from __future__ import annotations

"""Runtime-first 12:00 mail checkpoint for Magic Invasion."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping

from backend.core.fanxiu.mail.runtime_store import (
    current_runtime_mail_sequence_snapshot,
)


MAGIC_INVASION_GIFT_MAIL_TITLE = "美杜莎宝库馈赠"


@dataclass(frozen=True)
class MagicInvasionMailDecision:
    action: str
    reason: str
    mail_id: str = ""


def _mail_business_date(value: Any) -> date | None:
    text = str(value or "").strip()
    for pattern in ("%Y年%m月%d日 %H:%M", "%Y年%m月%d日%H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def select_magic_invasion_gift_mail(
    snapshot: Mapping[str, Any],
    *,
    business_day: date,
) -> MagicInvasionMailDecision:
    """Select the one exact live mail without consulting visual/OCR evidence."""

    if not bool(snapshot.get("complete")):
        raise RuntimeError("魔道邮件：Runtime 邮件快照不完整")
    matches = [
        item
        for item in snapshot.get("items") or []
        if isinstance(item, dict)
        and bool(item.get("present_in_runtime"))
        and str(item.get("title") or "").strip() == MAGIC_INVASION_GIFT_MAIL_TITLE
        and _mail_business_date(item.get("create_time_text")) == business_day
    ]
    if not matches:
        return MagicInvasionMailDecision(
            action="skip",
            reason=f"{business_day.isoformat()} 未发现“{MAGIC_INVASION_GIFT_MAIL_TITLE}”",
        )
    if len(matches) != 1:
        raise RuntimeError(
            f"魔道邮件：{business_day.isoformat()} 发现 {len(matches)} 封同名邮件，"
            "无法证明唯一业务目标"
        )

    target = matches[0]
    execution_status = str(target.get("execution_status") or "").strip()
    if execution_status == "claimed" or target.get("reward_getted") is True:
        return MagicInvasionMailDecision(
            action="skip",
            reason=f"{business_day.isoformat()} 的“{MAGIC_INVASION_GIFT_MAIL_TITLE}”已领取",
        )
    if execution_status != "unclaimed":
        raise RuntimeError(
            f"魔道邮件：目标邮件状态 {execution_status or 'unknown'}，拒绝进入界面"
        )
    if bool(target.get("locked")) or str(target.get("action_policy") or "") != "claim":
        raise RuntimeError("魔道邮件：目标邮件未获得 Runtime 领取授权，拒绝进入界面")
    mail_id = str(target.get("id") or target.get("mail_id") or "").strip()
    if not mail_id:
        raise RuntimeError("魔道邮件：目标邮件缺少精确标识，拒绝进入界面")
    return MagicInvasionMailDecision(
        action="claim",
        reason=f"领取 {business_day.isoformat()} 的“{MAGIC_INVASION_GIFT_MAIL_TITLE}”",
        mail_id=mail_id,
    )


def execute_magic_invasion_mail_checkpoint(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: Any,
    *,
    business_day: date,
):
    """Refresh Runtime first; touch the GUI only for one authorized target."""

    if not runner._refresh_runtime_mail_snapshot("魔道 12:00 邮件", force_refresh=True):
        raise RuntimeError("魔道邮件：动态邮件模型不可用")
    from backend.db import engine

    snapshot = current_runtime_mail_sequence_snapshot(lambda: engine)
    decision = select_magic_invasion_gift_mail(
        snapshot,
        business_day=business_day,
    )
    if decision.action == "skip":
        runner._log("success", f"魔道邮件：{decision.reason}，幂等跳过")
        return {
            "status": "completed",
            "message": f"魔道邮件：{decision.reason}，幂等跳过",
            "action": "skipped",
        }

    claim_payload = {
        **payload,
        "target_mail_ids": [decision.mail_id],
    }
    result = yield from runner._execute_mail_selective_claim_task(
        ctx,
        stop_event,
        claim_payload,
        cleanup_after_claim=False,
    )
    if str(result or "") != "success":
        raise RuntimeError(f"魔道邮件：精确领取未形成成功终态：{result}")
    return {
        "status": "completed",
        "message": f"魔道邮件：{decision.reason}，Runtime 终检通过",
        "action": "claimed",
        "mail_id": decision.mail_id,
    }


__all__ = [
    "MAGIC_INVASION_GIFT_MAIL_TITLE",
    "MagicInvasionMailDecision",
    "execute_magic_invasion_mail_checkpoint",
    "select_magic_invasion_gift_mail",
]
