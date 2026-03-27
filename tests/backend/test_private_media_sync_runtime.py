import pytest

from backend.private_modules.media_sync import runtime as media_sync_runtime


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
