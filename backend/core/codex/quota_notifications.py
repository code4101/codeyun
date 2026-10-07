"""Persist Codex reset events and deliver them through the public WeChat API."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from filelock import FileLock

from backend.core.settings import get_settings

# Verified against code4102's account-specific archive: 未来社微信分部.
SENDER_ACCOUNT_ID = "wxid_gxgjjgft1oj722"
RECIPIENT_ID = "44812733824@chatroom"
SHANGHAI = ZoneInfo("Asia/Shanghai")
DRIFT = dt.timedelta(seconds=60)


class CodexQuotaNotificationError(RuntimeError):
    """Quota is persisted, but its reset announcement is still pending."""


def _time(value) -> dt.datetime | None:
    try:
        moment = dt.datetime.fromisoformat(str(value))
        return moment.astimezone(dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def _windows(snapshot: dict) -> dict[str, dict]:
    result = {}
    for group in snapshot.get("groups", []):
        for window in group.get("windows", []):
            minutes = int(window.get("window_minutes") or 0)
            if minutes > 0 and _time(window.get("reset_at")):
                result[f"{group['id']}:{minutes}"] = {
                    **window, "name": group["name"], "observed_at": snapshot["observed_at"],
                }
    return result


def notify_codex_quota_resets(
    snapshot: dict, *, previous_snapshot: dict | None = None,
    path: Path | None = None, sender: Callable | None = None,
) -> list[dict]:
    """Notify each observed window reset once, including early resets.

    First collection establishes a baseline; no historical announcements. A
    scheduled deadline crossed between successful samples, or a changed cycle
    starting between samples, is a reset. Timestamp drift within a minute is
    ignored. Missing windows retain their last observation. Pending messages
    survive process restarts and retry on the next collection; failures propagate
    so the existing quota job schedules its five-minute retry. Provider owns
    recipient/account resolution and sending; no GUI or account fallback.
    """
    resolved = path or get_settings().data_dir / "codex" / "quota_reset_notifications.json"
    resolved.parent.mkdir(parents=True, exist_ok=True)

    def persist(state):
        temporary = resolved.with_suffix(f".json.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, resolved)

    with FileLock(str(resolved.with_suffix(".json.lock")), timeout=60):
        # Corrupt state must fail visibly rather than discard delivery history.
        state = json.loads(resolved.read_text(encoding="utf-8")) if resolved.exists() else {
            "version": 1, "windows": _windows(previous_snapshot or {}), "pending": [],
        }
        events = []
        for key, current in _windows(snapshot).items():
            previous = state["windows"].get(key)
            if previous:
                before, now = _time(previous["observed_at"]), _time(current["observed_at"])
                old_reset, new_reset = _time(previous["reset_at"]), _time(current["reset_at"])
                if now <= before:
                    continue
                changed = abs(new_reset - old_reset) > DRIFT
                new_start = new_reset - dt.timedelta(minutes=current["window_minutes"])
                scheduled = before < old_reset <= now
                early = changed and before < new_start <= now and new_start < old_reset
                if (scheduled or early) and not previous.get("notified"):
                    at = new_start if early else old_reset
                    events.append({
                        "window": key, "reset_at": at.isoformat(),
                        "name": current["name"], "label": current["label"],
                        "remaining_percent": current["remaining_percent"],
                    })
                current["notified"] = False if changed else bool(previous.get("notified") or scheduled or early)
            state["windows"][key] = current
        if events:
            lines = ["Codex 账号额度已重置，大家可以继续使用："]
            for event in events:
                clock = _time(event["reset_at"]).astimezone(SHANGHAI).strftime("%m/%d %H:%M")
                lines.append(f"{event['name']} · {event['label']}：当前剩余 {event['remaining_percent']}%（重置于 {clock}）")
            lines.append("https://code4101.com/tools/reset-codex")
            state["pending"].append({"events": events, "text": "\n".join(lines)})
        persist(state)
        if state["pending"] and sender is None:
            from pyxllib.autogui.weixin4_instrumentation import send_text
            sender = send_text
        receipts = []
        while state["pending"]:
            message = state["pending"][0]
            try:
                receipt = sender(RECIPIENT_ID, message["text"], sender_account_id=SENDER_ACCOUNT_ID)
            except Exception as exc:
                message["error"] = str(exc)
                persist(state)
                raise CodexQuotaNotificationError(f"Codex 重置微信提醒发送失败：{exc}") from exc
            state["pending"].pop(0)
            state["last_delivery"] = {**message, "receipt": receipt, "sent_at": dt.datetime.now(dt.timezone.utc).isoformat()}
            persist(state)
            receipts.append(receipt)
        return receipts
