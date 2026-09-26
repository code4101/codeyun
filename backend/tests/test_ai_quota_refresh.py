import datetime as dt

import pytest

from backend.core import ai_quota_refresh as quota
from backend.api import codex_setup as api
from backend.core.jobs import scheduler
from pyxllib.prog.schedule_policy import compute_next_trigger_at


def test_hourly_schedule_and_retry():
    key = quota.AI_QUOTA_REFRESH_TASK_KEY
    spec = scheduler.get_background_task_spec(key)
    assert spec.default_visible
    assert key in scheduler.DEFAULT_ENABLED_TASK_KEYS
    policy = scheduler._default_background_task_schedule_policy(key)
    assert compute_next_trigger_at(policy, base_time=dt.datetime(2026, 9, 26, 10, 42)) == dt.datetime(2026, 9, 26, 11)
    assert compute_next_trigger_at(policy, base_time=dt.datetime(2026, 9, 26, 11, 7)) == dt.datetime(2026, 9, 26, 12)
    for outcome in ("on_failure", "on_timeout"):
        assert policy["outcome"][outcome] == {"type": "retry_after", "minutes": 5}


@pytest.mark.parametrize("failure", ["exception", "payload", None])
def test_collection_attempts_every_provider_and_reports_failure(monkeypatch, failure):
    calls = []
    def codex():
        calls.append("codex")
        if failure == "exception":
            raise RuntimeError("offline")
        return {"groups": [{}], "observed_at": "now"}
    def opencode():
        calls.append("opencode")
        return {"payload": {"available": failure != "payload", "error": "offline" if failure == "payload" else ""}, "observed_at": "now"}
    def deepseek(key):
        assert key == "test-key"
        calls.append("deepseek")
        return {"payload": {"available": True}, "observed_at": "now"}
    monkeypatch.setattr(quota, "collect_codex_quota_snapshot", codex)
    monkeypatch.setattr(quota, "collect_opencode_usage_snapshot", opencode)
    monkeypatch.setattr(quota, "collect_deepseek_balance_snapshot", deepseek)
    monkeypatch.setattr(quota, "resolve_deepseek_key", lambda session: "test-key")
    if failure:
        with pytest.raises(RuntimeError, match="offline"):
            quota.collect_ai_quota_snapshots()
    else:
        assert len(quota.collect_ai_quota_snapshots()["observed_at"]) == 3
    assert calls == ["codex", "opencode", "deepseek"]


@pytest.mark.parametrize("provider", ["opencode", "deepseek"])
def test_empty_snapshot_get_never_collects(monkeypatch, provider):
    monkeypatch.setattr(api, "_ensure_codex_setup_access", lambda user: None)
    name = "opencode_usage" if provider == "opencode" else "deepseek_balance"
    monkeypatch.setattr(api, f"load_{name}_snapshot", lambda: {"payload": {}, "observed_at": ""})
    monkeypatch.setattr(api, f"collect_{name}_snapshot", lambda *a: pytest.fail("GET must not collect"))
    response = getattr(api, f"get_{name}")(current_user=None)
    assert response.observed_at == ""
