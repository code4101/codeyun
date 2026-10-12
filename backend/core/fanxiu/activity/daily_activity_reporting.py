"""Daily activity reports through the existing engineering-owned outbox.

The daily synchronization Job publishes one completed business stage. Its
fixed, already-subscribed recipient uses the normal durable stage queue and
archive verifier; neither this module nor the game Kernel sends WeChat.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from filelock import FileLock

from backend.core.fanxiu.activity.daily_activity_report import build_daily_activity_report

REGISTRY_KEY = "fanxiu_daily_activity_reporting"
TIMEZONE = ZoneInfo("Asia/Shanghai")


def _store(store=None):
    if store is None:
        from backend.core.messaging.codex_results import result_store
        return result_store()
    return store


def _lock(store):
    return FileLock(str(store.path.with_name(store.path.name + ".daily-activity-report.lock")), timeout=15)


def _marker(hook_key: str, business_date: str) -> str:
    return f"fanxiu_daily_activity_report:{hook_key}:{business_date}"


def configure_daily_activity_reporting(*, account_id: str, chat_id: str,
                                       thread_id: str, store=None) -> dict[str, Any]:
    """Enable daily reporting to an existing subscribed, fixed group.

    Owner-authorized local configuration only. A reporting subscription is a
    durable recipient binding, not an AI execution lease or a game operator.
    No turn is started and no message is sent.
    """
    store = _store(store)
    hook = next((h for h in store.status()["hooks"]
                 if h["account_id"] == account_id and h["chat_id"] == chat_id), None)
    if hook is None:
        raise ValueError("每日活动播报目的群尚未接入")
    subscription = next((s for s in store.codex_notifications_status()["subscriptions"]
                         if s["thread_id"] == thread_id and s["hook_key"] == hook["key"] and s["enabled"]), None)
    if subscription is None:
        raise ValueError("每日活动播报须复用本群已启用的工程结果订阅")
    record = {"account_id": account_id, "chat_id": chat_id, "hook_key": hook["key"],
              "thread_id": thread_id, "enabled": True}
    with _lock(store):
        registry = store.get_meta(REGISTRY_KEY, {})
        registry[hook["key"]] = record
        store.set_meta(REGISTRY_KEY, registry)
    return record


def acknowledge_existing_daily_activity_report(*, hook_key: str, business_date: date,
                                                source_seq: int, store=None) -> dict[str, Any]:
    """Baseline an already-sent answer only after matching its real archive echo.

    This prevents enabling daily reports from replaying today's answered query.
    A sender receipt alone is insufficient; the original text and date must
    match an outgoing archived message for this fixed account and group.
    """
    store = _store(store)
    config = store.get_meta(REGISTRY_KEY, {}).get(hook_key)
    if not config:
        raise ValueError("未配置活动播报")
    receipt = next((r for r in store.deliveries(hook_key, 100)
                    if r["status"] == "sent" and r["through_seq"] == source_seq), None)
    if not receipt or datetime.fromtimestamp(receipt["created_at"], TIMEZONE).date() != business_date:
        raise ValueError("缺少当日活动查询发送凭证")
    text = receipt["text"]
    if not all(label in text for label in ("玩法榜：", "资源榜：", "主题集：")):
        raise ValueError("原回答不是完整活动列表")
    from pyxllib.autogui.wechat_accounts import get_account_storage
    page = get_account_storage(config["account_id"]).list_messages(
        config["chat_id"], limit=100, include_resources=False)
    echo = next((r for r in page["items"] if r.get("sender_username") == config["account_id"]
                 and r.get("message_text") == text
                 and r.get("create_time", 0) >= receipt["created_at"] - 3
                 and datetime.fromtimestamp(r["create_time"], TIMEZONE).date() == business_date), None)
    if echo is None:
        raise ValueError("活动列表尚无匹配的群归档回读")
    marker = {"status": "verified", "source_seq": source_seq,
              "archive_local_id": echo["local_id"], "archive_server_id": str(echo.get("server_id", "")),
              "business_date": business_date.isoformat(), "origin": "existing_query"}
    with _lock(store):
        key = _marker(hook_key, business_date.isoformat())
        existing = store.get_meta(key)
        if existing and existing.get("origin") != "existing_query":
            raise ValueError("当日工程报告已入队，不覆盖其投递状态")
        store.set_meta(key, marker)
    return marker


def publish_daily_activity_report(plan: Mapping[str, Any], theme_plan: Mapping[str, Any],
                                  *, store=None) -> dict[str, Any]:
    """Queue the synchronized day's report exactly once; never send or play.

    A stable stage ID plus a destination/day marker survives retries and
    subscription changes. Sending/uncertain notifications are never replayed.
    Missing activity facts remain explicit, rather than being rendered as 无.
    """
    store = _store(store)
    registry = {key: config for key, config in store.get_meta(REGISTRY_KEY, {}).items()
                if config.get("enabled")}
    if not registry:
        return {"status": "disabled"}
    # A completed daily obligation must not become pending again merely because
    # a later sync cannot load theme facts. In particular, uncertain deliveries
    # remain owned by the sender's original receipt, never by a new game retry.
    today = datetime.now(TIMEZONE).date().isoformat()
    with _lock(store):
        recorded = []
        for hook_key in registry:
            previous = store.get_meta(_marker(hook_key, today))
            if previous and previous.get("status") != "pending_evidence":
                recorded.append({"hook_key": hook_key, "status": "already_recorded", "receipt": previous})
        if len(recorded) == len(registry):
            return {"status": "recorded", "business_date": today, "deliveries": recorded}
    report = build_daily_activity_report(plan, theme_plan)
    if not report["ready"]:
        day = datetime.now(TIMEZONE).date().isoformat()
        with _lock(store):
            for hook_key, config in registry.items():
                key = _marker(hook_key, day)
                previous = store.get_meta(key)
                if config.get("enabled") and (not previous or previous.get("status") == "pending_evidence"):
                    store.set_meta(key, {"status": "pending_evidence", "reason": report["reason"],
                                         "business_date": day, "origin": "daily_sync",
                                         "diagnostics": report.get("diagnostics", [])})
        return {"status": "pending_evidence", "reason": report["reason"],
                "diagnostics": report.get("diagnostics", [])}
    day = report["business_date"].isoformat()
    if day != datetime.now(TIMEZONE).date().isoformat():
        return {"status": "pending_evidence", "reason": "活动清单不属于实际当日，禁止迟到播报"}
    deliveries = []
    subscriptions = store.codex_notifications_status()["subscriptions"]
    with _lock(store):
        for hook_key, config in registry.items():
            if not config.get("enabled"):
                continue
            key = _marker(hook_key, day)
            previous = store.get_meta(key)
            if previous and previous.get("status") != "pending_evidence":
                deliveries.append({"hook_key": hook_key, "status": "already_recorded", "receipt": previous})
                continue
            # The provider fixes the recipient permanently, but the subscription
            # can be disabled after setup. Preserve an explicit retry condition
            # rather than interpreting a rejected enqueue as a queue failure.
            if not any(s["thread_id"] == config["thread_id"] and s["hook_key"] == hook_key
                       and s["enabled"] for s in subscriptions):
                reason = "每日活动播报的固定群订阅未启用，未入队"
                store.set_meta(key, {"status": "pending_evidence", "reason": reason,
                                     "business_date": day, "origin": "daily_sync"})
                deliveries.append({"hook_key": hook_key, "status": "pending_evidence", "reason": reason})
                continue
            turn_id = f"stage:daily-activity-list:{day}"
            accepted = store.enqueue_codex_completion(config["thread_id"], turn_id, report["text"])
            notification = next((n for n in store.codex_notifications_status()["notifications"]
                                 if n["thread_id"] == config["thread_id"] and n["turn_id"] == turn_id), None)
            if notification is None:
                if not accepted:
                    # The subscription can change after the advisory check.
                    # The provider's atomic rejection owns that race; retain
                    # the obligation without turning a disabled destination
                    # into a failed game synchronization or selecting another.
                    reason = "每日活动播报未获工程队列接纳，保留固定群待重试"
                    store.set_meta(key, {"status": "pending_evidence", "reason": reason,
                                         "business_date": day, "origin": "daily_sync"})
                    deliveries.append({"hook_key": hook_key, "status": "pending_evidence", "reason": reason})
                    continue
                raise RuntimeError("当日活动报告未进入固定群的工程投递队列")
            receipt = {"status": notification["status"], "notification_id": notification["id"],
                       "business_date": day, "text": report["text"], "origin": "daily_sync",
                       "thread_id": config["thread_id"], "turn_id": turn_id}
            store.set_meta(key, receipt)
            deliveries.append({"hook_key": hook_key, "accepted": accepted, "receipt": receipt})
    pending = any(d.get("status") == "pending_evidence" for d in deliveries)
    return {"status": "pending_evidence" if pending else "recorded",
            "business_date": day, "deliveries": deliveries}


def read_daily_activity_report_status(*, business_date: date | None = None, store=None) -> dict[str, Any]:
    """Read queue and engineering archive verification without game access."""
    store = _store(store)
    day = (business_date or datetime.now(TIMEZONE).date()).isoformat()
    notifications = {n["id"]: n for n in store.codex_notifications_status()["notifications"]}
    records = []
    for key, config in store.get_meta(REGISTRY_KEY, {}).items():
        record = dict(store.get_meta(_marker(key, day), {"status": "not_recorded"}))
        notification_id = record.get("notification_id")
        if notification_id is not None:
            verification = store.get_meta(f"codex_result_verification:{notification_id}", {})
            record["archive_verification"] = verification
            if verification.get("status") == "verified":
                record["status"] = "verified"
            elif notification_id in notifications:
                record["status"] = notifications[notification_id]["status"]
        records.append({"config": config, "receipt": record})
    return {"business_date": day, "reports": records}
