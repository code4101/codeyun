"""邮件记录的查询与展示投影。

保留 Runtime 与历史观测记录的筛选、排序和奖励补全语义；
调用方只负责鉴权及传输格式。游戏收取操作不经过此模块。
"""

from datetime import datetime
from typing import Any
from sqlmodel import Session, select
from backend.models import FanxiuMailRecord
from backend.core.fanxiu.mail.store import ensure_fanxiu_mail_table, normalize_fanxiu_mail_time_text
from backend.core.fanxiu.mail.normalization import _mail_rewards_summary, _normalize_mail_rewards

def _fanxiu_mail_create_time_sort_value(row: FanxiuMailRecord) -> float:
    if row.create_time_ms is not None:
        try:
            return float(row.create_time_ms)
        except (TypeError, ValueError):
            pass
    normalized = normalize_fanxiu_mail_time_text(row.create_time_text)
    if not normalized:
        return 0.0
    try:
        return datetime.strptime(normalized, "%Y年%m月%d日%H:%M").timestamp() * 1000
    except ValueError:
        return 0.0

def _fanxiu_mail_record_sort_key(row: FanxiuMailRecord) -> tuple[float, float, float]:
    return (
        _fanxiu_mail_create_time_sort_value(row),
        float(row.last_seen_at or 0),
        float(row.updated_at or 0),
    )

def _fanxiu_mail_record_has_display_payload(row: FanxiuMailRecord) -> bool:
    if row.source == "runtime_memory":
        return True
    payload = row.payload or {}
    content = payload.get("mail_content_text")
    if isinstance(content, str) and content.strip():
        return True
    rewards = payload.get("mail_rewards")
    if isinstance(rewards, list) and rewards:
        return True
    packet = payload.get("packet")
    if isinstance(packet, dict):
        packet_content = packet.get("mail_content_text")
        if isinstance(packet_content, str) and packet_content.strip():
            return True
        packet_rewards = packet.get("mail_rewards")
        if isinstance(packet_rewards, list) and packet_rewards:
            return True
    return False

def _fanxiu_mail_reward_existing_index(rewards: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(rewards, list):
        return {}
    indexed: dict[str, dict[str, Any]] = {}
    for reward in rewards:
        if not isinstance(reward, dict):
            continue
        item_id = str(reward.get("item_id") or reward.get("id") or "").strip()
        if item_id and item_id not in indexed:
            indexed[item_id] = reward
    return indexed

def _fanxiu_mail_enrich_recomputed_rewards(
    recomputed: list[dict[str, Any]],
    existing_rewards: Any,
) -> list[dict[str, Any]]:
    existing_by_id = _fanxiu_mail_reward_existing_index(existing_rewards)
    if not existing_by_id:
        return recomputed
    enriched: list[dict[str, Any]] = []
    for reward in recomputed:
        item_id = str(reward.get("item_id") or "").strip()
        existing = existing_by_id.get(item_id) or {}
        merged = dict(reward)
        for key in ("item_name", "item_type", "quality", "icon", "small_icon", "description", "name_source"):
            if not merged.get(key) and existing.get(key):
                merged[key] = existing[key]
        enriched.append(merged)
    return enriched

def mail_record_view(row: FanxiuMailRecord) -> dict[str, Any]:
    payload = row.payload or {}
    if not isinstance(payload, dict):
        payload = {}
    packet = payload.get("packet") if isinstance(payload.get("packet"), dict) else {}
    mail_vo = payload.get("mailVo") if isinstance(payload.get("mailVo"), dict) else packet.get("mailVo")
    existing_rewards = payload.get("mail_rewards")
    if not isinstance(existing_rewards, list):
        existing_rewards = packet.get("mail_rewards")
    rewards = existing_rewards if isinstance(existing_rewards, list) else []
    if not rewards and isinstance(mail_vo, dict):
        recomputed_rewards = _normalize_mail_rewards(mail_vo)
        if recomputed_rewards:
            rewards = _fanxiu_mail_enrich_recomputed_rewards(recomputed_rewards, rewards)
    direct_content = payload.get("mail_content_text")
    packet_content = packet.get("mail_content_text") if isinstance(packet, dict) else ""
    content_text = direct_content if isinstance(direct_content, str) else packet_content
    response_payload: dict[str, Any] = {}
    if isinstance(content_text, str) and content_text.strip():
        response_payload["mail_content_text"] = content_text
    if rewards:
        response_payload["mail_rewards"] = rewards
        response_payload["mail_rewards_summary"] = _mail_rewards_summary(rewards)
    for key in (
        "mail_rewards_unresolved",
        "mail_rewards_unresolved_reason",
        "has_attachment_hint",
        "orphan_action_status",
    ):
        if key in payload:
            response_payload[key] = payload.get(key)
    evidence = row.evidence or {}
    if not isinstance(evidence, dict):
        evidence = {}
    response_evidence = {
        key: evidence.get(key)
        for key in (
            "orphan_action",
            "visible_orphan_backfill",
            "has_attachment_hint",
            "orphan_action_reason",
        )
        if key in evidence
    }
    return {
        "id": row.id,
        "mail_key": row.mail_key,
        "mail_id": row.mail_id,
        "title": row.title,
        "normalized_title": row.normalized_title,
        "mail_type": row.mail_type,
        "create_time_text": row.create_time_text,
        "create_time_ms": row.create_time_ms,
        "source": row.source,
        "status": row.status,
        "execution_status": row.execution_status,
        "desired_status": row.desired_status,
        "present_in_runtime": row.present_in_runtime,
        "reward_getted": row.reward_getted,
        "has_attachment": row.has_attachment,
        "attachment_count": row.attachment_count,
        "last_runtime_sync_at": row.last_runtime_sync_at,
        "locked": row.locked,
        "action_policy": row.action_policy,
        "last_action_error": row.last_action_error,
        "seen_count": row.seen_count,
        "first_seen_at": row.first_seen_at,
        "last_seen_at": row.last_seen_at,
        "payload": response_payload,
        "evidence": response_evidence,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def query_mail_records(
    session: Session,
    *,
    limit: int = 2000,
    offset: int = 0,
    status: str = "",
    action_policy: str = "",
    source: str = "runtime_memory",
    include_absent: bool = True,
    include_empty_actions: bool = False,
) -> dict[str, Any]:
    """按来源和状态查询邮件，完成展示过滤与时间排序后分页；不更新记录。"""
    ensure_fanxiu_mail_table()
    stmt = select(FanxiuMailRecord)
    status_text = status.strip() if isinstance(status, str) else ""
    action_policy_text = action_policy.strip() if isinstance(action_policy, str) else ""
    source_text = source.strip().lower() if isinstance(source, str) else "runtime_memory"
    if status_text:
        stmt = stmt.where(FanxiuMailRecord.status == status_text)
    if action_policy_text:
        stmt = stmt.where(FanxiuMailRecord.action_policy == action_policy_text)
    if source_text in {"packet_evidence", "packet+orphan", "packet_orphan"}:
        stmt = stmt.where(FanxiuMailRecord.source.in_(("packet", "packet_orphan_action")))
    elif source_text and source_text != "all":
        stmt = stmt.where(FanxiuMailRecord.source == source_text)
    if include_absent is not True and source_text == "runtime_memory":
        stmt = stmt.where(FanxiuMailRecord.present_in_runtime == True)  # noqa: E712
    stmt = stmt.order_by(FanxiuMailRecord.last_seen_at.desc(), FanxiuMailRecord.updated_at.desc())
    rows = session.exec(stmt).all()
    if include_empty_actions is not True:
        rows = [row for row in rows if _fanxiu_mail_record_has_display_payload(row)]
    rows = sorted(rows, key=_fanxiu_mail_record_sort_key, reverse=True)
    total_count = len(rows)
    rows = rows[offset:offset + limit]
    records = [mail_record_view(row) for row in rows]
    payload = {
        "ok": True,
        "count": len(records),
        "total": total_count,
        "offset": offset,
        "limit": limit,
        "records": records,
    }
    return payload
