from __future__ import annotations

import datetime as dt
import io

import pytest

from backend.core.codex.app_server import CodexAppServerError, read_codex_rate_limits
from backend.core.codex.weekly_quota import (
    CODEX_CHATGPT_AUTH_OVERRIDE,
    CODEX_USAGE_URL,
    CODEX_WEEKLY_QUOTA_SOURCE,
    CODEX_WEEKLY_QUOTA_TASK_KEY,
    collect_codex_weekly_quota_snapshot,
    list_codex_weekly_quota_snapshots,
    load_codex_quota_snapshot,
    parse_codex_rate_limits_snapshot,
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


def test_record_codex_weekly_quota_attributes_midnight_snapshot_to_observed_day(tmp_path):
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
            "date": "2026-08-07",
            "remaining_percent": 39,
            "observed_at": "2026-08-07T00:03:00",
            "bucket": "2026-08-07T00:00:00",
            "reset_at": "",
            "source_url": CODEX_USAGE_URL,
            "source": CODEX_WEEKLY_QUOTA_SOURCE,
        }
    ]


def test_read_codex_weekly_quota_migrates_legacy_previous_day_dates(tmp_path):
    history_path = tmp_path / "weekly_quota_history.json"
    history_path.write_text(
        """{
  "version": 1,
  "snapshots": [
    {
      "date": "2026-09-03",
      "remaining_percent": 37,
      "observed_at": "2026-09-04T00:00:01"
    }
  ]
}""",
        encoding="utf-8",
    )

    assert list_codex_weekly_quota_snapshots(history_path) == [
        {
            "date": "2026-09-04",
            "remaining_percent": 37,
            "observed_at": "2026-09-04T00:00:01",
        }
    ]


def _rate_limits_payload(*, weekly_used_percent=60):
    return {
        "rateLimits": {
            "primary": {"usedPercent": 10, "windowDurationMins": 300},
            "secondary": {
                "usedPercent": weekly_used_percent,
                "windowDurationMins": 10080,
                "resetsAt": 0,
            },
        },
        "rateLimitsByLimitId": {
            "codex": {
                "primary": {"usedPercent": 10, "windowDurationMins": 300},
                "secondary": {
                    "usedPercent": weekly_used_percent,
                    "windowDurationMins": 10080,
                    "resetsAt": 0,
                },
            }
        },
    }


def test_parse_codex_rate_limits_snapshot_chooses_longest_codex_window():
    assert parse_codex_rate_limits_snapshot(_rate_limits_payload(weekly_used_percent=63)) == {
        "remaining_percent": 37,
        "reset_at": "1970-01-01T00:00:00+00:00",
        "window_duration_minutes": 10080,
    }


def test_parse_codex_rate_limits_snapshot_accepts_primary_weekly_window():
    assert parse_codex_rate_limits_snapshot(
        {
            "rateLimitsByLimitId": {
                "codex": {
                    "primary": {
                        "usedPercent": 83,
                        "windowDurationMins": 10080,
                        "resetsAt": 1788749798,
                    },
                    "secondary": None,
                }
            }
        }
    )["remaining_percent"] == 17


def test_collect_codex_weekly_quota_writes_app_server_snapshot(tmp_path):
    calls = []

    def read_rate_limits(**kwargs):
        calls.append(kwargs)
        return _rate_limits_payload()

    result = collect_codex_weekly_quota_snapshot(
        now=dt.datetime(2026, 8, 7, 0, 0, 0),
        history_path=tmp_path / "history.json",
        snapshot_path=tmp_path / "snapshot.json",
        rate_limits_reader=read_rate_limits,
        timeout_seconds=1,
    )

    assert result["date"] == "2026-08-07"
    assert result["remaining_percent"] == 40
    assert result["source"] == CODEX_WEEKLY_QUOTA_SOURCE
    assert calls == [{"timeout_seconds": 1, "config_overrides": CODEX_CHATGPT_AUTH_OVERRIDE}]
    snapshot = load_codex_quota_snapshot(path=tmp_path / "snapshot.json")
    assert snapshot["observed_at"] == "2026-08-07T00:00:00"
    assert snapshot["groups"]


class _InspectableStringIO(io.StringIO):
    def close(self):
        return None


class _FakeAppServerProcess:
    def __init__(self, stdout: str):
        self.stdin = _InspectableStringIO()
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO("")
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def test_read_codex_rate_limits_uses_bounded_public_app_server_protocol():
    process = _FakeAppServerProcess(
        '{"id":1,"result":{"userAgent":"test"}}\n'
        '{"id":2,"result":{"rateLimits":{"secondary":{"usedPercent":63}}}}\n'
    )
    calls = []

    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return process

    result = read_codex_rate_limits(
        executable="codex.exe",
        popen_factory=popen,
        timeout_seconds=1,
    )

    assert result["rateLimits"]["secondary"]["usedPercent"] == 63
    assert calls[0][0] == ["codex.exe", "app-server", "--listen", "stdio://"]
    assert '"method":"account/rateLimits/read"' in process.stdin.getvalue()
    assert process.returncode == 0


def test_read_codex_rate_limits_preserves_upstream_error_message():
    process = _FakeAppServerProcess(
        '{"id":1,"result":{}}\n'
        '{"id":2,"error":{"code":-32603,"message":"failed to fetch https://chatgpt.com/backend-api/wham/usage"}}\n'
    )

    with pytest.raises(CodexAppServerError, match="failed to fetch.*wham/usage"):
        read_codex_rate_limits(
            executable="codex.exe",
            popen_factory=lambda *_args, **_kwargs: process,
            timeout_seconds=1,
        )


def test_codex_weekly_quota_is_optional_standard_daily_midnight_job():
    spec = get_background_task_spec(CODEX_WEEKLY_QUOTA_TASK_KEY)
    policy = _default_background_task_schedule_policy(CODEX_WEEKLY_QUOTA_TASK_KEY)

    assert spec is not None
    assert spec.title == "Codex 每周余额记录"
    assert spec.default_visible is False
    assert policy is not None
    assert policy["trigger"] == {"type": "daily", "time": "00:00"}
    assert policy["outcome"]["on_failure"] == {"type": "retry_after", "minutes": 10}
    assert policy["escalation"] == {
        "type": "codex",
        "after_consecutive_failures": 3,
    }


def test_codex_weekly_quota_api_returns_calendar_snapshots(client, auth_user, monkeypatch):
    monkeypatch.setattr(
        "backend.api.notes.list_codex_weekly_quota_snapshots",
        lambda: [{"date": "2026-08-07", "remaining_percent": 40, "observed_at": "2026-08-07T00:00:00"}],
    )

    response = client.get("/api/notes/codex-weekly-quota")

    assert response.status_code == 200
    assert response.json()["snapshots"][0]["date"] == "2026-08-07"
    assert response.json()["snapshots"][0]["remaining_percent"] == 40
