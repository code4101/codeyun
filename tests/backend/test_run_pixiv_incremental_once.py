from __future__ import annotations

from dataclasses import dataclass

import pytest
from filelock import FileLock

from scripts import run_pixiv_incremental_once as runner


@dataclass
class FakeProfile:
    user_id: int = 2
    pixiv_enabled: bool = True
    last_run_status: str | None = None


@pytest.fixture(autouse=True)
def no_active_pixiv_risk_circuit(monkeypatch):
    monkeypatch.setattr(runner, "_active_risk_circuit", lambda: None)


def test_legacy_pinterest_job_does_not_block_after_pixiv_ownership_is_released(monkeypatch):
    from backend.core.jobs import scheduler
    from backend.plugins.modules.media_sync import runtime

    monkeypatch.setattr(scheduler, "_is_task_enabled", lambda _task_key: True)
    monkeypatch.setattr(runtime, "has_scheduled_pixiv_profiles", lambda: False)

    assert runner._legacy_trigger_enabled() is False


def test_formal_entrypoint_skips_while_legacy_trigger_is_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_legacy_trigger_enabled", lambda: True)
    monkeypatch.setattr(
        runner,
        "_load_profiles",
        lambda _user_id: pytest.fail("profiles must not load while the legacy trigger is enabled"),
    )

    report, report_path = runner.run_once(
        max_remote_operations=20,
        check_only=True,
        optimizer_root=tmp_path,
        run_id="legacy-conflict",
    )

    assert report["status"] == "safety_skipped"
    assert report["stop_reason"] == "legacy_media_sync_home_discovery_enabled"
    assert report["remote_audit"]["remote_operations_total"] == 0
    assert report_path.exists()
    assert report_path.with_suffix(".md").exists()
    assert (tmp_path / "latest.json").exists()


def test_formal_entrypoint_reports_missing_private_media_sync_plugin(tmp_path, monkeypatch):
    def missing_plugin(**_kwargs):
        raise ModuleNotFoundError(
            "No module named 'backend.plugins.modules.media_sync'",
            name="backend.plugins.modules.media_sync",
        )

    monkeypatch.setattr(runner, "_pixiv_source_activity_lease", missing_plugin)

    report, report_path = runner.run_once(
        max_remote_operations=20,
        check_only=True,
        optimizer_root=tmp_path,
        run_id="missing-private-plugin",
    )

    assert report["status"] == "safety_skipped"
    assert report["stop_reason"] == "media_sync_plugin_missing"
    assert report["remote_audit"]["remote_operations_total"] == 0
    assert report_path.exists()
    assert "media_sync_plugin_missing" in report_path.with_suffix(".md").read_text(encoding="utf-8")


def test_formal_entrypoint_check_only_passes_without_network(tmp_path, monkeypatch):
    profile = FakeProfile()
    monkeypatch.setattr(runner, "_legacy_trigger_enabled", lambda: False)
    monkeypatch.setattr(runner, "_load_profiles", lambda _user_id: [profile])
    monkeypatch.setattr(
        runner,
        "_run_profile",
        lambda *_args, **_kwargs: pytest.fail("check-only must not invoke the media sync runtime"),
    )

    report, _report_path = runner.run_once(
        max_remote_operations=20,
        check_only=True,
        optimizer_root=tmp_path,
        run_id="safe-check",
    )

    assert report["status"] == "check_passed"
    assert report["stop_reason"] == "check_only"
    assert report["profile_count"] == 1
    assert report["remote_audit"]["remote_operations_total"] == 0


def test_formal_entrypoint_skips_when_persistent_risk_circuit_is_open(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_legacy_trigger_enabled", lambda: False)
    monkeypatch.setattr(
        runner,
        "_active_risk_circuit",
        lambda: {"blocked_date": "2026-07-21", "reason": "http_rate_limited"},
    )
    monkeypatch.setattr(
        runner,
        "_load_profiles",
        lambda _user_id: pytest.fail("profiles must not load while the risk circuit is open"),
    )

    report, _report_path = runner.run_once(
        max_remote_operations=20,
        check_only=True,
        optimizer_root=tmp_path,
        run_id="risk-open",
    )

    assert report["status"] == "safety_skipped"
    assert report["stop_reason"] == "risk_circuit_open"
    assert report["persistent_risk_circuit"]["reason"] == "http_rate_limited"


def test_formal_entrypoint_runs_business_runtime_inside_budget_audit(tmp_path, monkeypatch):
    profile = FakeProfile()
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(runner, "_legacy_trigger_enabled", lambda: False)
    monkeypatch.setattr(runner, "_load_profiles", lambda _user_id: [profile])

    def fake_run_profile(_profile, **kwargs):
        calls.append(kwargs)
        return {"stage": "completed", "summary": {"pixiv_download": {"new_download_count": 3}}}

    monkeypatch.setattr(runner, "_run_profile", fake_run_profile)

    report, _report_path = runner.run_once(
        max_remote_operations=20,
        sources=["pixiv_collect_ids"],
        target_new_candidates=3,
        optimizer_root=tmp_path,
        run_id="runtime-success",
    )

    assert report["status"] == "completed"
    assert report["stop_reason"] == "completed"
    assert report["remote_audit"]["max_remote_operations"] == 20
    assert report["remote_audit"]["remote_operations_total"] == 0
    assert calls == [
        {
            "sources": ["pixiv_download"],
            "target_new_candidates": 3,
            "run_id": "runtime-success",
        }
    ]


def test_formal_entrypoint_reports_persistent_risk_circuit_stop(tmp_path, monkeypatch):
    from backend.plugins.modules.media_sync import sources

    profile = FakeProfile()
    circuit_path = tmp_path / "pixiv-risk-circuit.json"
    monkeypatch.setattr(runner, "_legacy_trigger_enabled", lambda: False)
    monkeypatch.setattr(runner, "_load_profiles", lambda _user_id: [profile])
    monkeypatch.setattr(sources, "pixiv_risk_circuit_path", lambda: circuit_path)

    def fake_run_profile(_profile, **_kwargs):
        sources.trip_pixiv_risk_circuit(
            reason="verification_challenge",
            signal="captcha",
            operation_kind="page_navigation",
            url="https://www.pixiv.net/",
        )
        return {"stage": "error", "error": "captcha"}

    monkeypatch.setattr(runner, "_run_profile", fake_run_profile)

    report, _report_path = runner.run_once(
        max_remote_operations=20,
        optimizer_root=tmp_path,
        run_id="risk-stop",
    )

    assert report["status"] == "safety_stopped"
    assert report["stop_reason"] == "risk_circuit_tripped"
    assert report["remote_audit"]["risk_circuit"]["reason"] == "verification_challenge"


def test_formal_entrypoint_skips_when_pixiv_source_lease_is_held(tmp_path, monkeypatch):
    monkeypatch.setattr(
        runner,
        "_legacy_trigger_enabled",
        lambda: pytest.fail("lock contention must stop before checking the legacy trigger"),
    )
    held_lock = FileLock(str(tmp_path / "pixiv-incremental.lock"))

    with held_lock.acquire(timeout=0):
        report, _report_path = runner.run_once(
            max_remote_operations=20,
            check_only=True,
            optimizer_root=tmp_path,
            run_id="lock-conflict",
        )

    assert report["status"] == "safety_skipped"
    assert report["stop_reason"] == "pixiv_source_activity_lease_held"
    assert report["remote_audit"]["remote_operations_total"] == 0


def test_pinterest_only_or_stale_profile_running_state_does_not_block_pixiv(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_legacy_trigger_enabled", lambda: False)
    monkeypatch.setattr(runner, "_load_profiles", lambda _user_id: [FakeProfile(last_run_status="running")])

    report, _report_path = runner.run_once(
        max_remote_operations=20,
        check_only=True,
        optimizer_root=tmp_path,
        run_id="busy-profile",
    )

    assert report["status"] == "check_passed"
    assert report["stop_reason"] == "check_only"
    assert report["busy_profile_ids"] == [2]


@pytest.mark.parametrize("budget", [0, 501])
def test_formal_entrypoint_rejects_unsafe_budget(tmp_path, budget):
    with pytest.raises(ValueError):
        runner.run_once(
            max_remote_operations=budget,
            check_only=True,
            optimizer_root=tmp_path,
        )
