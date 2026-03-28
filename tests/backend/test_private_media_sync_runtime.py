import pytest

from backend.private_modules.media_sync import runtime as media_sync_runtime
from backend.private_modules.media_sync.models import MediaSyncProfile


@pytest.mark.parametrize(
    ("source", "runner_name", "extra_config"),
    [
        (
            "pixiv",
            "run_pixiv_sync",
            {
                "pixiv_enabled": False,
                "pixiv_bookmarks_url": "https://www.pixiv.net/users/1/bookmarks/artworks",
            },
        ),
        (
            "pixiv_related",
            "run_pixiv_related_sync",
            {
                "pixiv_related_enabled": False,
            },
        ),
        (
            "pinterest",
            "run_pinterest_sync",
            {
                "pinterest_enabled": False,
                "pinterest_board_url": "https://www.pinterest.com/example/board/",
            },
        ),
        (
            "pinterest_related",
            "run_pinterest_related_sync",
            {
                "pinterest_related_enabled": False,
            },
        ),
    ],
)
def test_run_job_executes_explicit_source_even_if_legacy_hidden_toggle_is_false(
    monkeypatch,
    source,
    runner_name,
    extra_config,
):
    manager = media_sync_runtime.SyncJobManager()
    monkeypatch.setattr(manager, "_persist_started", lambda *args, **kwargs: None)
    monkeypatch.setattr(manager, "_persist_finished", lambda *args, **kwargs: None)

    calls: list[dict] = []

    def fake_runner(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "source": source}

    monkeypatch.setattr(media_sync_runtime, runner_name, fake_runner)

    config = {
        "user_id": 1,
        "root_dir": r"D:\sync-root",
        "pixiv_enabled": True,
        "pixiv_bookmarks_url": "",
        "pixiv_download_limit": 0,
        "pixiv_related_enabled": True,
        "pixiv_related_seed_min_weight": 1,
        "pixiv_related_seed_limit": 12,
        "pixiv_related_download_limit": 24,
        "pinterest_enabled": True,
        "pinterest_board_url": "",
        "pinterest_download_limit": 0,
        "pinterest_related_enabled": True,
        "pinterest_related_seed_min_weight": 1,
        "pinterest_related_seed_limit": 12,
        "pinterest_related_download_limit": 24,
        "requested_sources": [source],
    }
    config.update(extra_config)

    manager._run_job(config)

    snapshot = manager.snapshot(1)

    assert len(calls) == 1
    assert snapshot["running"] is False
    assert snapshot["summary"][source]["ok"] is True


@pytest.mark.parametrize(
    ("source", "runner_name", "config_key"),
    [
        ("pixiv", "run_pixiv_sync", "pixiv_download_limit"),
        ("pinterest", "run_pinterest_sync", "pinterest_download_limit"),
    ],
)
def test_run_job_for_bookmark_sync_ignores_legacy_download_limit(
    monkeypatch,
    source,
    runner_name,
    config_key,
):
    manager = media_sync_runtime.SyncJobManager()
    monkeypatch.setattr(manager, "_persist_started", lambda *args, **kwargs: None)
    monkeypatch.setattr(manager, "_persist_finished", lambda *args, **kwargs: None)

    calls: list[dict] = []

    def fake_runner(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "source": source}

    monkeypatch.setattr(media_sync_runtime, runner_name, fake_runner)

    config = {
        "user_id": 1,
        "root_dir": r"D:\sync-root",
        "pixiv_enabled": True,
        "pixiv_bookmarks_url": "https://www.pixiv.net/users/1/bookmarks/artworks",
        "pixiv_download_limit": 99,
        "pixiv_related_enabled": True,
        "pixiv_related_seed_min_weight": 1,
        "pixiv_related_seed_limit": 12,
        "pixiv_related_download_limit": 24,
        "pinterest_enabled": True,
        "pinterest_board_url": "https://www.pinterest.com/example/board/",
        "pinterest_download_limit": 99,
        "pinterest_related_enabled": True,
        "pinterest_related_seed_min_weight": 1,
        "pinterest_related_seed_limit": 12,
        "pinterest_related_download_limit": 24,
        "requested_sources": [source],
    }
    config[config_key] = 321

    manager._run_job(config)

    assert len(calls) == 1
    assert calls[0]["limit"] == 0


@pytest.mark.parametrize(
    ("source", "runner_name", "config_key"),
    [
        ("pixiv_related", "run_pixiv_related_sync", "pixiv_related_seed_min_weight"),
        ("pinterest_related", "run_pinterest_related_sync", "pinterest_related_seed_min_weight"),
    ],
)
def test_run_job_for_related_sync_uses_fixed_seed_min_weight_one(
    monkeypatch,
    source,
    runner_name,
    config_key,
):
    manager = media_sync_runtime.SyncJobManager()
    monkeypatch.setattr(manager, "_persist_started", lambda *args, **kwargs: None)
    monkeypatch.setattr(manager, "_persist_finished", lambda *args, **kwargs: None)

    calls: list[dict] = []

    def fake_runner(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "source": source}

    monkeypatch.setattr(media_sync_runtime, runner_name, fake_runner)

    config = {
        "user_id": 1,
        "root_dir": r"D:\sync-root",
        "pixiv_enabled": True,
        "pixiv_bookmarks_url": "https://www.pixiv.net/users/1/bookmarks/artworks",
        "pixiv_download_limit": 0,
        "pixiv_related_enabled": True,
        "pixiv_related_seed_min_weight": 9,
        "pixiv_related_seed_limit": 12,
        "pixiv_related_download_limit": 24,
        "pinterest_enabled": True,
        "pinterest_board_url": "https://www.pinterest.com/example/board/",
        "pinterest_download_limit": 0,
        "pinterest_related_enabled": True,
        "pinterest_related_seed_min_weight": 9,
        "pinterest_related_seed_limit": 12,
        "pinterest_related_download_limit": 24,
        "requested_sources": [source],
    }
    config[config_key] = 0

    manager._run_job(config)

    assert len(calls) == 1
    assert calls[0]["seed_min_weight"] == 1


def test_run_job_marks_unexpected_top_level_exception_as_error(monkeypatch):
    manager = media_sync_runtime.SyncJobManager()
    persisted: list[tuple[float, str, dict]] = []
    monkeypatch.setattr(manager, "_persist_started", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        manager,
        "_persist_finished",
        lambda _user_id, finished_at, status, summary: persisted.append((finished_at, status, summary)),
    )

    manager._run_job(
        {
            "user_id": 1,
            "root_dir": r"D:\sync-root",
            "requested_sources": None,
        }
    )

    snapshot = manager.snapshot(1)
    assert snapshot["running"] is False
    assert snapshot["stage"] == "error"
    assert snapshot["message"] == "同步异常中断。"
    assert "NoneType" in (snapshot["error"] or "")
    assert persisted
    assert persisted[-1][1] == "error"


def test_build_status_response_marks_stale_running_profile_as_interrupted(monkeypatch):
    manager = media_sync_runtime.SyncJobManager()

    monkeypatch.setattr(media_sync_runtime, "build_pixiv_snapshot", lambda **kwargs: {"name": "pixiv"})
    monkeypatch.setattr(media_sync_runtime, "build_pixiv_related_snapshot", lambda **kwargs: {"name": "pixiv_related"})
    monkeypatch.setattr(media_sync_runtime, "build_pinterest_snapshot", lambda **kwargs: {"name": "pinterest"})
    monkeypatch.setattr(media_sync_runtime, "build_pinterest_membership_snapshot", lambda **kwargs: {"name": "pinterest_membership"})
    monkeypatch.setattr(media_sync_runtime, "build_pinterest_related_snapshot", lambda **kwargs: {"name": "pinterest_related"})

    profile = MediaSyncProfile(
        user_id=2,
        root_dir=r"D:\sync-root",
        last_run_status="running",
        last_run_started_at=100.0,
        last_run_finished_at=None,
        updated_at=130.0,
        last_run_summary={},
    )

    snapshot = manager.build_status_response(profile)

    assert snapshot["running"] is False
    assert snapshot["stage"] == "error"
    assert snapshot["message"] == "上次任务异常中断，状态未正常回收。"
    assert snapshot["finished_at"] == 130.0
    assert snapshot["summary"]["error"] == "任务可能因后端重启、进程退出或未捕获异常而中断。"


def test_request_cancel_marks_running_job(monkeypatch):
    manager = media_sync_runtime.SyncJobManager()
    monkeypatch.setattr(manager, "_persist_started", lambda *args, **kwargs: None)
    monkeypatch.setattr(manager, "_persist_finished", lambda *args, **kwargs: None)

    manager._states[1] = media_sync_runtime.JobState(running=True, stage="pinterest-membership", message="正在执行")

    manager.request_cancel(1)

    snapshot = manager.snapshot(1)
    assert snapshot["running"] is True
    assert snapshot["cancel_requested"] is True
    assert snapshot["message"] == "已请求停止，等待当前步骤安全退出。"


def test_run_job_marks_cancelled_when_stop_requested(monkeypatch):
    manager = media_sync_runtime.SyncJobManager()
    persisted: list[tuple[float, str, dict]] = []
    monkeypatch.setattr(manager, "_persist_started", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        manager,
        "_persist_finished",
        lambda _user_id, finished_at, status, summary: persisted.append((finished_at, status, summary)),
    )
    manager._states[1] = media_sync_runtime.JobState(running=True, cancel_requested=True)

    manager._run_job(
        {
            "user_id": 1,
            "root_dir": r"D:\sync-root",
            "pixiv_enabled": True,
            "pixiv_bookmarks_url": "",
            "pixiv_download_limit": 0,
            "pixiv_related_enabled": True,
            "pixiv_related_seed_min_weight": 1,
            "pixiv_related_seed_limit": 12,
            "pixiv_related_download_limit": 24,
            "pinterest_enabled": True,
            "pinterest_board_url": "https://www.pinterest.com/example/board/",
            "pinterest_download_limit": 0,
            "pinterest_related_enabled": True,
            "pinterest_related_seed_min_weight": 1,
            "pinterest_related_seed_limit": 12,
            "pinterest_related_download_limit": 24,
            "requested_sources": ["pinterest_membership"],
        }
    )

    snapshot = manager.snapshot(1)
    assert snapshot["running"] is False
    assert snapshot["cancel_requested"] is False
    assert snapshot["stage"] == "cancelled"
    assert snapshot["message"] == "同步已停止。"
    assert persisted
    assert persisted[-1][1] == "cancelled"
