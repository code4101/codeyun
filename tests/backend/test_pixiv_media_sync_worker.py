from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from backend.core import media_sync_worker as worker
from backend.plugins.modules.media_sync import models, runtime


def test_worker_check_loads_without_web_server(capsys) -> None:
    assert worker.main(["--check"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["worker_kind"] == "media-sync"
    assert payload["manager"] == "SyncJobManager"


def test_launch_persists_state_and_uses_detached_module_process(tmp_path, monkeypatch) -> None:
    state_root = tmp_path / "state"
    log_root = tmp_path / "logs"
    popen_calls = []

    monkeypatch.setattr(worker, "_state_root", lambda: state_root)
    monkeypatch.setattr(worker, "codeyun_temp_root", lambda *parts: log_root)
    monkeypatch.setattr(worker, "_pid_is_alive", lambda pid: int(pid) == 4321)
    monkeypatch.setattr(
        worker,
        "popen_python_module_service",
        lambda *args, **kwargs: popen_calls.append((args, kwargs)) or SimpleNamespace(pid=4321),
    )

    payload = worker.launch_media_sync_legacy_worker(
        {"user_id": 7, "scope_key": "candidate:pixiv", "requested_sources": ["pixiv_download"]},
        {"running": True, "stage": "queued", "logs": []},
    )

    assert payload["pid"] == 4321
    assert payload["running"] is True
    assert payload["config"]["requested_sources"] == ["pixiv_download"]
    assert popen_calls[0][0][0] == "backend.core.media_sync_worker"
    with pytest.raises(RuntimeError, match="已有同通道媒体任务"):
        worker.launch_media_sync_legacy_worker(
            {"user_id": 8, "scope_key": "candidate:pixiv", "requested_sources": ["pixiv_download"]},
            {"running": True, "stage": "queued", "logs": []},
        )


def test_local_job_launch_keeps_media_snapshot_and_submits_state_reference(tmp_path, monkeypatch) -> None:
    state_root = tmp_path / "state"
    submitted = []
    monkeypatch.setattr(worker, "_state_root", lambda: state_root)
    monkeypatch.setattr(
        "backend.core.jobs.local_runtime.submit_local_job",
        lambda **kwargs: submitted.append(kwargs) or SimpleNamespace(id="local-media-1"),
    )

    payload = worker.launch_media_sync_local_job(
        {"user_id": 7, "scope_key": "candidate:video", "requested_sources": ["video_download"]},
        {"running": True, "stage": "queued", "logs": []},
    )

    assert payload["running"] is True
    assert payload["local_job_run_id"] == "local-media-1"
    assert submitted[0]["job_type"] == "media.sync"
    assert submitted[0]["user_id"] == 7
    assert submitted[0]["resource_key"] == "resource:media-sync:exclusive"
    assert set(submitted[0]["payload"]) == {"state_path"}
    assert worker.read_worker_snapshot(7, "candidate:video")["stage"] == "queued"


def test_discovery_and_curation_workers_can_run_in_parallel(tmp_path, monkeypatch) -> None:
    state_root = tmp_path / "state"
    submitted = []
    monkeypatch.setattr(worker, "_state_root", lambda: state_root)
    monkeypatch.setattr(worker, "_pid_is_alive", lambda _pid: True)
    monkeypatch.setattr(
        "backend.core.jobs.local_runtime.submit_local_job",
        lambda **kwargs: submitted.append(kwargs) or SimpleNamespace(id=f"job-{len(submitted)}"),
    )

    worker.launch_media_sync_local_job(
        {"user_id": 7, "scope_key": "candidate:pixiv", "requested_sources": ["pixiv_download"]},
        {"running": True, "stage": "queued", "logs": []},
    )
    worker.launch_media_sync_local_job(
        {"user_id": 7, "scope_key": "candidate:pixiv", "requested_sources": ["pixiv_curate"]},
        {"running": True, "stage": "queued", "logs": []},
    )

    assert [call["resource_key"] for call in submitted] == [
        "resource:media-sync:discovery:pixiv",
        "resource:media-sync:curation:pixiv",
    ]
    assert len(list(state_root.glob("user-*.json"))) == 2
    curation_path = next(
        path
        for path in state_root.glob("user-*.json")
        if (worker._read_json(path) or {}).get("worker_lane") == "curation:pixiv"
    )
    worker._locked_update(
        curation_path,
        {"running": False, "stage": "finished", "updated_at": time.time() + 1},
    )
    assert worker.read_worker_snapshot(7, "candidate:pixiv")["worker_lane"] == "curation:pixiv"


def test_successful_curation_enqueues_independent_membership_followup(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "worker.json"
    worker._write_json(path, {"logs": []})
    calls = []
    monkeypatch.setattr(
        "backend.core.media_membership_reconcile.enqueue_media_membership_reconcile",
        lambda **kwargs: calls.append(kwargs)
        or {"platform": "pixiv", "local_job_run_id": "membership-1", "queued": True},
    )

    result = worker._enqueue_membership_followups(
        path,
        {
            "user_id": 7,
            "root_dir": r"E:\data\m2510mn",
            "requested_sources": ["pixiv_curate"],
        },
        {"stage": "finished", "error": None, "summary": {"pixiv_curate": {"promoted": 3}}},
    )

    assert calls == [{"user_id": 7, "platform": "pixiv", "root_dir": r"E:\data\m2510mn"}]
    assert result["pixiv"]["local_job_run_id"] == "membership-1"
    persisted = worker._read_json(path)
    assert persisted["membership_followups"]["pixiv"]["queued"] is True
    assert "membership-1" in persisted["logs"][-1]


@pytest.mark.parametrize(
    "snapshot",
    [
        {"stage": "partial_error", "error": "curation failed", "summary": {"pixiv_curate": {}}},
        {"stage": "finished", "error": None, "summary": {}},
    ],
)
def test_failed_or_incomplete_curation_does_not_enqueue_membership(
    tmp_path, monkeypatch, snapshot
) -> None:
    path = tmp_path / "worker.json"
    worker._write_json(path, {"logs": []})
    monkeypatch.setattr(
        "backend.core.media_membership_reconcile.enqueue_media_membership_reconcile",
        lambda **_kwargs: pytest.fail("unexpected membership handoff"),
    )

    assert (
        worker._enqueue_membership_followups(
            path,
            {"user_id": 7, "requested_sources": ["pixiv_curate"]},
            snapshot,
        )
        == {}
    )


def test_worker_status_and_cancel_survive_manager_recreation(tmp_path, monkeypatch) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir(parents=True)
    monkeypatch.setattr(worker, "_state_root", lambda: state_root)
    monkeypatch.setattr(worker, "_pid_is_alive", lambda pid: int(pid) == 88)
    path = worker.worker_state_path(9, "candidate:pixiv")
    worker._write_json(
        path,
        {
            "running": True,
            "cancel_requested": False,
            "stage": "pixiv-download",
            "message": "downloading",
            "started_at": 10.0,
            "updated_at": 11.0,
            "logs": [],
            "summary": {},
            "error": None,
            "needs_login": False,
            "action_hint": None,
            "pid": 88,
        },
    )

    recreated_manager = runtime.SyncJobManager()
    snapshot = recreated_manager.snapshot(9, scope_key="candidate:pixiv")
    assert snapshot["running"] is True
    assert snapshot["stage"] == "pixiv-download"

    recreated_manager.request_cancel(9, scope_key="candidate:pixiv")
    cancelled = worker.read_worker_snapshot(9, "candidate:pixiv")
    assert cancelled["cancel_requested"] is True
    assert "等待当前步骤安全退出" in cancelled["message"]


def test_synchronous_scheduler_waits_on_external_worker(monkeypatch) -> None:
    manager = runtime.SyncJobManager()
    starts = []
    snapshots = iter([{"running": True}, {"running": False, "stage": "success"}])
    monkeypatch.setattr(manager, "start", lambda *args, **kwargs: starts.append((args, kwargs)))
    monkeypatch.setattr(manager, "snapshot", lambda *_args, **_kwargs: next(snapshots))
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)

    result = manager.run_external_and_wait(
        models.MediaSyncProfile(user_id=12),
        sources=["pinterest_download"],
        scope_key="candidate:pinterest",
    )

    assert result["stage"] == "success"
    assert starts[0][1]["sources"] == ["pinterest_download"]
    assert starts[0][1]["scope_key"] == "candidate:pinterest"


@pytest.mark.parametrize(
    ("source", "scope_key"),
    [
        ("pixiv_download", "candidate:pixiv"),
        ("pinterest_download", "candidate:pinterest"),
    ],
)
def test_platform_start_delegates_to_external_worker(
    monkeypatch, source: str, scope_key: str
) -> None:
    calls = []
    monkeypatch.setattr(worker, "launch_media_sync_worker", lambda config, state: calls.append((config, state)))
    manager = runtime.SyncJobManager()

    manager.start(
        models.MediaSyncProfile(user_id=12),
        sources=[source],
        overrides={"platform_download_target_count": 25},
        scope_key=scope_key,
    )

    assert len(calls) == 1
    config, state = calls[0]
    assert config["platform_download_target_count"] == 25
    assert config["scope_key"] == scope_key
    assert state["running"] is True
    assert state["stage"] == "queued"
    assert manager._states == {}
