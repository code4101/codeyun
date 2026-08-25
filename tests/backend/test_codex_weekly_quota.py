from __future__ import annotations

import datetime as dt

from backend.core.codex.weekly_quota import (
    CODEX_USAGE_URL,
    CODEX_WEEKLY_QUOTA_TASK_KEY,
    collect_codex_weekly_quota_snapshot,
    list_codex_weekly_quota_snapshots,
    parse_codex_weekly_quota_text,
    record_codex_weekly_quota_snapshot,
)
from backend.core.jobs.scheduler import (
    _default_background_task_schedule_policy,
    get_background_task_spec,
)


def test_parse_codex_weekly_quota_uses_main_weekly_limit_instead_of_spark():
    parsed = parse_codex_weekly_quota_text(
        """
        每周使用限额
        46% 剩余
        重置时间：2026年8月11日 14:44
        GPT-5.3-Codex-Spark
        57% 剩余
        重置时间：2026年8月9日 0:06
        """
    )

    assert parsed == {
        "remaining_percent": 46,
        "reset_at": "2026年8月11日 14:44",
    }


def test_record_codex_weekly_quota_attributes_midnight_snapshot_to_previous_day(tmp_path):
    history_path = tmp_path / "weekly_quota_history.json"
    record_codex_weekly_quota_snapshot(
        remaining_percent=40,
        observed_at=dt.datetime(2026, 8, 7, 0, 0, 0),
        path=history_path,
    )
    record_codex_weekly_quota_snapshot(
        remaining_percent=39,
        observed_at=dt.datetime(2026, 8, 7, 0, 3, 0),
        path=history_path,
    )

    assert list_codex_weekly_quota_snapshots(history_path) == [
        {
            "date": "2026-08-06",
            "remaining_percent": 39,
            "observed_at": "2026-08-07T00:03:00",
            "reset_at": "",
            "source_url": CODEX_USAGE_URL,
        }
    ]


def test_collect_codex_weekly_quota_writes_snapshot_and_closes_its_success_tab(tmp_path):
    class FakeTimeouts:
        def __call__(self, **_kwargs):
            return None

    class FakeSet:
        timeouts = FakeTimeouts()

    class FakeBody:
        text = "每周使用限额 40% 剩余 重置时间：2026年8月11日 14:44 GPT-5.3-Codex-Spark 57% 剩余"

    class FakeTab:
        url = CODEX_USAGE_URL
        set = FakeSet()
        closed = False

        def get(self, url, **_kwargs):
            self.url = url

        def ele(self, *_args, **_kwargs):
            return FakeBody()

        def wait(self, _seconds):
            return None

        def close(self):
            self.closed = True

    class FakeBrowser:
        def __init__(self):
            self.tab = FakeTab()
            self.tab_ids = ["existing", "quota"]

        def get_tabs(self):
            return []

        def new_tab(self):
            return self.tab

    browser = FakeBrowser()
    result = collect_codex_weekly_quota_snapshot(
        now=dt.datetime(2026, 8, 7, 0, 0, 0),
        history_path=tmp_path / "history.json",
        browser_factory=lambda: browser,
        timeout_seconds=1,
    )

    assert result["date"] == "2026-08-06"
    assert result["remaining_percent"] == 40
    assert browser.tab.closed is True


def test_codex_weekly_quota_is_optional_standard_daily_midnight_job():
    spec = get_background_task_spec(CODEX_WEEKLY_QUOTA_TASK_KEY)
    policy = _default_background_task_schedule_policy(CODEX_WEEKLY_QUOTA_TASK_KEY)

    assert spec is not None
    assert spec.title == "Codex 每周余额记录"
    assert spec.default_visible is False
    assert policy is not None
    assert policy["trigger"] == {"type": "daily", "time": "00:00"}
    assert policy["outcome"]["on_failure"] == {"type": "retry_after", "minutes": 10}


def test_codex_weekly_quota_api_returns_calendar_snapshots(client, auth_user, monkeypatch):
    monkeypatch.setattr(
        "backend.api.notes.list_codex_weekly_quota_snapshots",
        lambda: [{"date": "2026-08-06", "remaining_percent": 40, "observed_at": "2026-08-07T00:00:00"}],
    )

    response = client.get("/api/notes/codex-weekly-quota")

    assert response.status_code == 200
    assert response.json()["snapshots"][0]["remaining_percent"] == 40
