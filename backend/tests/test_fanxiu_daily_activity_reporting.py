"""Real durable outbox contracts; no game, sender or simulated Runtime."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend.core.messaging.wechat_agent_store import AgentStore
from backend.core.fanxiu.activity.daily_activity_reporting import (
    configure_daily_activity_reporting, publish_daily_activity_report,
    read_daily_activity_report_status,
)


def configured_store(tmp_path):
    store = AgentStore(tmp_path / "events.sqlite")
    store.ensure_hook("fanxiu", "account", "group@chatroom")
    store.subscribe_codex_thread("bound-thread", "fanxiu", [], enabled=True)
    configure_daily_activity_reporting(account_id="account", chat_id="group@chatroom",
                                        thread_id="bound-thread", store=store)
    return store


def current_plans():
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    return ({"status": "ready", "timezone": "Asia/Shanghai", "target_date": today, "occurrences": []},
            {"plan_ready": True, "diagnostics": [], "occurrences": []})


def test_concurrent_publications_have_one_fixed_recipient_notification(tmp_path):
    store = configured_store(tmp_path)
    plan, themes = current_plans()
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: publish_daily_activity_report(plan, themes, store=store), range(6)))
    assert all(r["status"] == "recorded" for r in results)
    pending = store.pending_codex_notifications()
    assert len(pending) == 1
    assert pending[0]["hook_key"] == "fanxiu"
    assert pending[0]["text"] == "玩法榜：无\n资源榜：无\n主题集：无"
    assert store.deliveries("fanxiu") == []  # publishing cannot send


def test_uncertain_outbox_is_not_replayed_by_later_publication(tmp_path):
    store = configured_store(tmp_path)
    plan, themes = current_plans()
    publish_daily_activity_report(plan, themes, store=store)
    notification = store.pending_codex_notifications()[0]
    reply = store.reserve_codex_notification(notification["id"])
    store.finish_reply(reply, "uncertain", {"error": "acceptance unknown"})
    publish_daily_activity_report(plan, themes, store=store)
    assert store.pending_codex_notifications() == []
    assert len(store.deliveries("fanxiu")) == 1
    assert read_daily_activity_report_status(store=store)["reports"][0]["receipt"]["status"] == "uncertain"


def test_missing_evidence_and_foreign_subscription_cannot_publish(tmp_path):
    store = configured_store(tmp_path)
    with pytest.raises(ValueError, match="本群"):
        configure_daily_activity_reporting(account_id="account", chat_id="group@chatroom",
                                            thread_id="foreign-thread", store=store)
    plan, themes = current_plans()
    themes["plan_ready"] = False
    assert publish_daily_activity_report(plan, themes, store=store)["status"] == "pending_evidence"
    assert store.pending_codex_notifications() == []
    assert read_daily_activity_report_status(store=store)["reports"][0]["receipt"]["status"] == "pending_evidence"
    themes["plan_ready"] = True
    assert publish_daily_activity_report(plan, themes, store=store)["status"] == "recorded"
    assert len(store.pending_codex_notifications()) == 1


def test_existing_daily_obligation_does_not_retry_on_later_missing_facts(tmp_path):
    store = configured_store(tmp_path)
    plan, themes = current_plans()
    publish_daily_activity_report(plan, themes, store=store)
    before = store.pending_codex_notifications()
    themes["plan_ready"] = False
    result = publish_daily_activity_report(plan, themes, store=store)
    assert result["status"] == "recorded"
    assert result["deliveries"][0]["status"] == "already_recorded"
    assert store.pending_codex_notifications() == before


def test_disabled_reporting_does_not_trigger_evidence_retry(tmp_path):
    store = configured_store(tmp_path)
    from backend.core.fanxiu.activity.daily_activity_reporting import REGISTRY_KEY
    registry = store.get_meta(REGISTRY_KEY)
    registry["fanxiu"]["enabled"] = False
    store.set_meta(REGISTRY_KEY, registry)
    plan, themes = current_plans()
    themes["plan_ready"] = False
    assert publish_daily_activity_report(plan, themes, store=store)["status"] == "disabled"
    assert store.pending_codex_notifications() == []


def test_disabled_subscription_preserves_report_until_reenabled(tmp_path):
    store = configured_store(tmp_path)
    store.subscribe_codex_thread("bound-thread", "fanxiu", [], enabled=False)
    plan, themes = current_plans()
    assert publish_daily_activity_report(plan, themes, store=store)["status"] == "pending_evidence"
    assert store.pending_codex_notifications() == []
    store.subscribe_codex_thread("bound-thread", "fanxiu", [], enabled=True)
    assert publish_daily_activity_report(plan, themes, store=store)["status"] == "recorded"
    assert store.pending_codex_notifications()[0]["hook_key"] == "fanxiu"


def test_subscription_disabled_at_atomic_enqueue_retains_obligation(tmp_path, monkeypatch):
    store = configured_store(tmp_path)
    enqueue = store.enqueue_codex_completion

    def disable_before_enqueue(*args, **kwargs):
        store.subscribe_codex_thread("bound-thread", "fanxiu", [], enabled=False)
        return enqueue(*args, **kwargs)

    monkeypatch.setattr(store, "enqueue_codex_completion", disable_before_enqueue)
    plan, themes = current_plans()
    assert publish_daily_activity_report(plan, themes, store=store)["status"] == "pending_evidence"
    assert store.pending_codex_notifications() == []
    assert read_daily_activity_report_status(store=store)["reports"][0]["receipt"]["status"] == "pending_evidence"

    monkeypatch.setattr(store, "enqueue_codex_completion", enqueue)
    store.subscribe_codex_thread("bound-thread", "fanxiu", [], enabled=True)
    assert publish_daily_activity_report(plan, themes, store=store)["status"] == "recorded"
    assert len(store.pending_codex_notifications()) == 1
    assert store.pending_codex_notifications()[0]["hook_key"] == "fanxiu"
