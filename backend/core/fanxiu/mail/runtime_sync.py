from __future__ import annotations

"""Project the read-only MailMgr snapshot into the durable mail catalog."""

import time
from typing import Any

from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from backend.core.fanxiu.mail.normalization import (
    _load_mail_item_name_index,
    _mail_rewards_summary,
    _mail_title_from_vo,
    _normalize_mail_reward_item,
    load_fanxiu_mail_envelope_titles,
)
from backend.core.fanxiu.mail.policy import (
    fanxiu_mail_action_policy_for_rewards,
    fanxiu_mail_desired_status_for_rewards,
    fanxiu_mail_rewards_from_payload,
    fanxiu_mail_title_force_claim_allowed,
)
from backend.core.fanxiu.mail.store import (
    ensure_fanxiu_mail_table,
    format_fanxiu_mail_time_ms,
    upsert_fanxiu_mail_fact,
)
from backend.core.fanxiu.mail.visual_alignment import mail_snapshot_fingerprint
from backend.models import FanxiuMailRecord


def _merge_runtime_mail_payload(
    existing: dict[str, Any] | None,
    runtime_payload: dict[str, Any],
) -> dict[str, Any]:
    """Overlay volatile Runtime state without erasing richer historical evidence."""

    previous = dict(existing or {})
    merged = dict(previous)
    old_vo = previous.get("mailVo") if isinstance(previous.get("mailVo"), dict) else {}
    new_vo = runtime_payload.get("mailVo") if isinstance(runtime_payload.get("mailVo"), dict) else {}
    merged_vo = dict(old_vo)
    for key, value in new_vo.items():
        if value not in (None, "", [], {}):
            merged_vo[key] = value
    # Runtime snapshots may omit i18nParams. Only an explicitly present rich
    # value may replace persisted parameters.
    if "i18nParams" in old_vo and "i18nParams" not in new_vo:
        merged_vo["i18nParams"] = old_vo["i18nParams"]
    merged.update(runtime_payload)
    merged["mailVo"] = merged_vo
    legacy_import = previous.get("packet") if isinstance(previous.get("packet"), dict) else {}
    for key in (
        "mail_content_text",
        "mail_rewards",
        "mail_rewards_summary",
        "mail_rewards_unresolved",
        "mail_rewards_unresolved_reason",
        "has_attachment_hint",
        "seat_eviction_event",
    ):
        if previous.get(key) not in (None, "", [], {}):
            merged[key] = previous[key]
        elif legacy_import.get(key) not in (None, "", [], {}):
            merged[key] = legacy_import[key]
    merged.pop("packet", None)
    if legacy_import:
        merged["source_layers"] = ["historical_import", "runtime_memory"]
    return merged


def _runtime_status(item: dict[str, Any]) -> str:
    if bool(item.get("reward_getted")):
        return "claimed"
    if bool(item.get("has_attachment")):
        return "unclaimed"
    return "no_attachment"


def _display_status(*, runtime_status: str, locked: bool) -> str:
    if locked:
        return "锁定"
    if runtime_status == "claimed":
        return "已领"
    if runtime_status == "no_attachment":
        return "无附件"
    if runtime_status == "absent":
        return "已离开清单"
    return "留存"


def _runtime_rewards(
    raw_rewards: Any,
    existing_rewards: list[dict[str, Any]],
    item_name_index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    existing_by_id = {
        str(item.get("item_id") or ""): item
        for item in existing_rewards
        if isinstance(item, dict) and str(item.get("item_id") or "")
    }
    normalized: list[dict[str, Any]] = []
    for raw in raw_rewards if isinstance(raw_rewards, list) else []:
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get("code") or raw.get("item_id") or "").strip()
        item = dict(existing_by_id.get(item_id) or {})
        catalog_item = _normalize_mail_reward_item(raw, item_name_index=item_name_index)
        if isinstance(catalog_item, dict):
            item.update(catalog_item)
        item.update({"item_id": item_id, "amount": raw.get("amount"), "type": raw.get("type")})
        for key in ("content", "extra_mark", "client_content"):
            if raw.get(key) not in (None, ""):
                item[key] = raw.get(key)
        normalized.append(item)
    return normalized


def _runtime_content(mail_vo: dict[str, Any], envelopes: dict[int, dict[str, Any]]) -> str:
    direct = str(mail_vo.get("content") or "").strip()
    if direct:
        return direct
    try:
        envelope = envelopes.get(int(mail_vo.get("type") or 0)) or {}
    except (TypeError, ValueError):
        envelope = {}
    for key in ("content_plain", "content", "desc_plain", "desc"):
        value = str(envelope.get(key) or "").strip()
        if value:
            return value
    return ""


def _is_sqlite_write_lock(exc: OperationalError) -> bool:
    """Return whether a projection can safely retry after a SQLite writer race."""

    message = str(exc).lower()
    return "database is locked" in message or "database table is locked" in message


def _sync_fanxiu_mail_runtime_snapshot_once(
    session: Session,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Persist one complete runtime snapshot; incomplete reads never age out rows."""

    ensure_fanxiu_mail_table()
    if not isinstance(snapshot, dict) or not snapshot.get("complete"):
        return {
            "ok": False,
            "reason": str((snapshot or {}).get("reason") or "邮件动态快照不完整，拒绝写入"),
            "complete": False,
            "inserted": 0,
            "updated": 0,
            "absent": 0,
        }
    items = snapshot.get("items")
    decoded_count = snapshot.get("decoded_count")
    if not isinstance(items, list) or decoded_count is None or int(decoded_count) != len(items):
        return {
            "ok": False,
            "reason": "邮件动态快照计数不一致，拒绝写入",
            "complete": False,
            "inserted": 0,
            "updated": 0,
            "absent": 0,
        }

    captured_at = str(snapshot.get("captured_at") or "")
    sequence_fingerprint = str(snapshot.get("sequence_fingerprint") or "") or mail_snapshot_fingerprint(items)
    envelopes = load_fanxiu_mail_envelope_titles()
    item_name_index = _load_mail_item_name_index()
    existing_records_by_mail_id = {
        str(row.mail_id or ""): row
        for row in session.exec(select(FanxiuMailRecord)).all()
        if str(row.mail_id or "")
    }
    existing_rewards_by_mail_id = {
        mail_id: fanxiu_mail_rewards_from_payload(row.payload)
        for mail_id, row in existing_records_by_mail_id.items()
    }
    present_ids: set[str] = set()
    inserted = 0
    updated = 0
    now = time.time()

    for fallback_runtime_index, raw_item in enumerate(items):
        if not isinstance(raw_item, dict):
            continue
        mail_id = str(raw_item.get("id") or "").strip()
        if not mail_id:
            continue
        present_ids.add(mail_id)
        mail_vo = {
            "id": mail_id,
            "type": raw_item.get("type"),
            "title": raw_item.get("title"),
            "content": raw_item.get("content"),
            "createTime": raw_item.get("create_time"),
            "expireTime": raw_item.get("expire_time"),
            "read": raw_item.get("read"),
            "rewardGetted": raw_item.get("reward_getted"),
            "senderName": raw_item.get("sender_name"),
            "rewards": raw_item.get("rewards") or [],
        }
        title = _mail_title_from_vo(mail_vo, envelopes) or f"未知邮件类型{raw_item.get('type') or ''}"
        rewards = _runtime_rewards(
            raw_item.get("rewards"),
            existing_rewards_by_mail_id.get(mail_id) or [],
            item_name_index,
        )
        force_claim_title = fanxiu_mail_title_force_claim_allowed(title, rewards)
        desired_status = (
            "可领"
            if force_claim_title
            else (fanxiu_mail_desired_status_for_rewards(rewards) if rewards else "留存")
        )
        action_policy = (
            "claim"
            if force_claim_title
            else (fanxiu_mail_action_policy_for_rewards(rewards) if rewards else "")
        )
        existing_record = existing_records_by_mail_id.get(mail_id)
        # Runtime is the business authority for lock/claim eligibility.  A GUI
        # lock-icon observation is only historical visual evidence and must
        # never remove a Runtime-unlocked mail from the claim target set.
        locked = bool(raw_item.get("locked"))
        runtime_status = _runtime_status(raw_item)
        if locked or runtime_status != "unclaimed":
            action_policy = ""
        create_time_ms = raw_item.get("create_time")
        try:
            create_time_ms = int(create_time_ms) if create_time_ms is not None else None
        except (TypeError, ValueError):
            create_time_ms = None
        content = _runtime_content(mail_vo, envelopes)
        payload = {
            "mailVo": mail_vo,
            "mail_rewards": rewards,
            "mail_rewards_summary": _mail_rewards_summary(rewards),
            "mail_content_text": content,
            "runtime": raw_item,
        }
        payload = _merge_runtime_mail_payload(
            existing_record.payload if existing_record is not None else None,
            payload,
        )
        record, created = upsert_fanxiu_mail_fact(
            session,
            title=title,
            mail_id=mail_id,
            mail_type=raw_item.get("type"),
            create_time_text=format_fanxiu_mail_time_ms(create_time_ms),
            create_time_ms=create_time_ms,
            source="runtime_memory",
            action_policy=action_policy,
            status=_display_status(runtime_status=runtime_status, locked=locked),
            locked=locked,
            payload=payload,
            evidence={"runtime_memory": snapshot.get("evidence") or {}, "captured_at": captured_at},
            seen_capture_at=captured_at,
        )
        record.source = "runtime_memory"
        record.status = _display_status(runtime_status=runtime_status, locked=locked)
        record.runtime_status = runtime_status
        record.desired_status = "锁定" if locked else desired_status
        record.action_policy = action_policy
        record.present_in_runtime = True
        record.reward_getted = raw_item.get("reward_getted") if isinstance(raw_item.get("reward_getted"), bool) else None
        record.has_attachment = bool(raw_item.get("has_attachment"))
        record.attachment_count = int(raw_item.get("attachment_count") or len(rewards))
        raw_runtime_index = raw_item.get("runtime_index")
        record.runtime_index = (
            int(raw_runtime_index)
            if isinstance(raw_runtime_index, int)
            else fallback_runtime_index
        )
        record.runtime_sequence_fingerprint = sequence_fingerprint
        record.last_runtime_sync_at = captured_at
        record.payload = payload
        record.updated_at = now
        session.add(record)
        inserted += int(created)
        updated += int(not created)

    absent = 0
    runtime_rows = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.source == "runtime_memory")).all()
    for record in runtime_rows:
        if str(record.mail_id or "") in present_ids:
            continue
        record.present_in_runtime = False
        directly_claimed = record.runtime_status == "claimed" or record.reward_getted is True
        record.runtime_status = "claimed" if directly_claimed else "claimed_absent"
        record.status = "已领"
        record.action_policy = ""
        record.runtime_index = None
        record.runtime_sequence_fingerprint = sequence_fingerprint
        record.last_runtime_sync_at = captured_at
        evidence = dict(record.evidence or {})
        evidence["runtime_absence_claim"] = {
            "inferred": not directly_claimed,
            "reason": "missing_from_complete_mail_runtime_snapshot",
            "marked_at": captured_at,
        }
        record.evidence = evidence
        record.updated_at = now
        session.add(record)
        absent += 1

    session.commit()
    return {
        "ok": True,
        "complete": True,
        "source": "runtime_memory",
        "record_count": len(present_ids),
        "inserted": inserted,
        "updated": updated,
        "absent": absent,
        "captured_at": captured_at,
        "sequence_fingerprint": sequence_fingerprint,
    }


def sync_fanxiu_mail_runtime_snapshot(
    session: Session,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Persist a verified snapshot, tolerating a short concurrent SQLite writer.

    The Runtime read is already complete before this function starts.  Retrying
    the *same* snapshot after rolling back is therefore idempotent and does not
    weaken the no-stale-mail safety rule.
    """

    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            return _sync_fanxiu_mail_runtime_snapshot_once(session, snapshot)
        except OperationalError as exc:
            if not _is_sqlite_write_lock(exc) or attempt + 1 >= max_attempts:
                raise
            session.rollback()
            # SQLite's connection-level busy timeout already covered the
            # immediate race.  This bounded delay only separates consecutive
            # Scheduler/API writers that happen to finish at the same moment.
            time.sleep(0.2 * (attempt + 1))


def sync_fanxiu_mail_from_runtime(session: Session) -> dict[str, Any]:
    from backend.core.fanxiu.instrumentation.mail import read_mail_snapshot

    started_at = time.perf_counter()
    snapshot = read_mail_snapshot()
    projection_started_at = time.perf_counter()
    result = sync_fanxiu_mail_runtime_snapshot(session, snapshot)
    evidence = snapshot.get("evidence") if isinstance(snapshot, dict) else None
    result.update(
        {
            "runtime_elapsed_seconds": float(snapshot.get("elapsed_seconds") or 0.0),
            "runtime_timings": dict(snapshot.get("timings") or {}),
            "projection_elapsed_seconds": time.perf_counter() - projection_started_at,
            "elapsed_seconds": time.perf_counter() - started_at,
            "root_cache_hit": (
                evidence.get("root_cache_hit") if isinstance(evidence, dict) else None
            ),
        }
    )
    return result


__all__ = ["sync_fanxiu_mail_from_runtime", "sync_fanxiu_mail_runtime_snapshot"]
