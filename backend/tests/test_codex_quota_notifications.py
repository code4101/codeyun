import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.core.codex import quota_notifications as notices, weekly_quota


def snapshot(at, reset, remaining=20, minutes=300):
    return {"observed_at": at, "groups": [{"id": "codex", "name": "通用使用限额", "windows": [{
        "window_minutes": minutes, "label": "5 小时", "reset_at": reset,
        "remaining_percent": remaining,
    }]}]}


BEFORE = snapshot("2026-10-07T00:00:00+00:00", "2026-10-07T01:00:00+00:00")
AFTER = snapshot("2026-10-07T02:00:00+00:00", "2026-10-07T06:30:00+00:00", 90)


def test_first_read_is_baseline_and_reset_sent_once(tmp_path):
    path = tmp_path / "notices.json"
    calls = []
    def sender(*args, **kwargs):
        calls.append((args, kwargs))
        return {"result": 1}
    notices.notify_codex_quota_resets(BEFORE, path=path, sender=sender)
    assert not calls
    notices.notify_codex_quota_resets(AFTER, path=path, sender=sender)
    notices.notify_codex_quota_resets(AFTER, path=path, sender=sender)
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == "44812733824@chatroom"
    assert kwargs == {"sender_account_id": "wxid_gxgjjgft1oj722"}
    assert "剩余 90%" in args[1] and "10/07 09:00" in args[1]


def test_pending_survives_failure_and_process_restart(tmp_path):
    path = tmp_path / "notices.json"
    def failed(*args, **kwargs):
        raise RuntimeError("offline")
    with pytest.raises(RuntimeError, match="微信提醒发送失败"):
        notices.notify_codex_quota_resets(AFTER, previous_snapshot=BEFORE, path=path, sender=failed)
    assert len(json.loads(path.read_text(encoding="utf-8"))["pending"]) == 1
    calls = []
    notices.notify_codex_quota_resets(AFTER, path=path, sender=lambda *a, **k: calls.append(a))
    notices.notify_codex_quota_resets(AFTER, path=path, sender=lambda *a, **k: calls.append(a))
    assert len(calls) == 1
    assert json.loads(path.read_text(encoding="utf-8"))["pending"] == []


def test_expired_marker_notifies_once_even_before_first_use(tmp_path):
    path = tmp_path / "notices.json"
    calls = []
    sender = lambda *a, **k: calls.append(a)
    idle = snapshot("2026-10-07T02:00:00+00:00", BEFORE["groups"][0]["windows"][0]["reset_at"], 100)
    notices.notify_codex_quota_resets(idle, previous_snapshot=BEFORE, path=path, sender=sender)
    notices.notify_codex_quota_resets(AFTER, path=path, sender=sender)
    assert len(calls) == 1


@pytest.mark.parametrize("at,reset,expected", [
    ("2026-10-07T00:30:00+00:00", "2026-10-07T01:00:30+00:00", 0),
    # Early new cycle began at 00:15, before the old 01:00 deadline.
    ("2026-10-07T00:30:00+00:00", "2026-10-07T05:15:00+00:00", 1),
    # A marker correction whose inferred cycle starts before the last sample.
    ("2026-10-07T00:30:00+00:00", "2026-10-07T04:00:00+00:00", 0),
])
def test_drift_and_early_reset(tmp_path, at, reset, expected):
    calls = []
    notices.notify_codex_quota_resets(snapshot(at, reset), previous_snapshot=BEFORE,
        path=tmp_path / "notices.json", sender=lambda *a, **k: calls.append(a))
    assert len(calls) == expected


def test_concurrent_collections_do_not_duplicate(tmp_path):
    path = tmp_path / "notices.json"
    calls = []
    def collect(_):
        return notices.notify_codex_quota_resets(AFTER, previous_snapshot=BEFORE,
            path=path, sender=lambda *a, **k: calls.append(a))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(collect, range(2)))
    assert len(calls) == 1


def test_missing_window_preserves_baseline(tmp_path):
    path = tmp_path / "notices.json"
    notices.notify_codex_quota_resets({"groups": [], "observed_at": AFTER["observed_at"]},
        previous_snapshot=BEFORE, path=path)
    calls = []
    notices.notify_codex_quota_resets(AFTER, path=path, sender=lambda *a, **k: calls.append(a))
    assert len(calls) == 1


def test_all_buckets_combined_and_next_reset_is_announced(tmp_path):
    path = tmp_path / "notices.json"
    previous = snapshot(BEFORE["observed_at"], BEFORE["groups"][0]["windows"][0]["reset_at"])
    current = snapshot(AFTER["observed_at"], AFTER["groups"][0]["windows"][0]["reset_at"])
    for value in (previous, current):
        value["groups"].append({**value["groups"][0], "id": "model", "name": "模型额度"})
    calls = []
    sender = lambda *a, **k: calls.append(a)
    notices.notify_codex_quota_resets(current, previous_snapshot=previous, path=path, sender=sender)
    assert len(calls) == 1 and "模型额度" in calls[0][1]
    notices.notify_codex_quota_resets(snapshot("2026-10-07T08:00:00+00:00", "2026-10-07T12:00:00+00:00"), path=path, sender=sender)
    assert len(calls) == 2


def test_manual_collector_persists_before_notification_failure(monkeypatch):
    saved = []
    monkeypatch.setattr(weekly_quota, "read_codex_quota_groups", lambda: AFTER["groups"])
    monkeypatch.setattr(weekly_quota, "load_codex_quota_snapshot", lambda: BEFORE)
    monkeypatch.setattr(weekly_quota, "save_codex_quota_snapshot", lambda groups, at: saved.append(groups) or AFTER)
    monkeypatch.setattr(weekly_quota, "record_codex_general_quota", lambda *a, **k: True)
    def notify(snapshot, **kwargs):
        assert saved and kwargs["previous_snapshot"] == BEFORE
        raise RuntimeError("notification failed")
    monkeypatch.setattr(notices, "notify_codex_quota_resets", notify)
    with pytest.raises(RuntimeError, match="notification failed"):
        weekly_quota.collect_codex_quota_snapshot(now=dt.datetime.fromisoformat(AFTER["observed_at"]))


def test_page_keeps_balance_when_notification_fails(monkeypatch):
    from backend.api import codex_setup
    monkeypatch.setattr(codex_setup, "_ensure_codex_setup_access", lambda user: None)
    monkeypatch.setattr(codex_setup, "load_codex_quota_snapshot", lambda: AFTER)
    monkeypatch.setattr(codex_setup, "list_codex_weekly_quota_snapshots", lambda: [])
    def failed():
        raise notices.CodexQuotaNotificationError("微信提醒发送失败")
    monkeypatch.setattr(codex_setup, "collect_codex_quota_snapshot", failed)
    response = codex_setup.refresh_codex_quota(current_user=None)
    assert response.groups[0].windows[0].remaining_percent == 90
    assert response.error == "微信提醒发送失败"
