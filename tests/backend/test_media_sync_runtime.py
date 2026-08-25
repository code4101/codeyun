from __future__ import annotations

from contextlib import contextmanager
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from backend.plugins.modules.media_sync import runtime, sources
from backend.models import DeviceFile


def test_reconcile_local_media_directory_index_repairs_missing_and_stale_rows(
    tmp_path,
    engine,
    monkeypatch,
) -> None:
    media_root = tmp_path / "2、pinterest"
    media_root.mkdir()
    current_path = media_root / "current.jpg"
    missing_path = media_root / "missing.jpg"
    current_path.write_bytes(b"current")
    monkeypatch.setattr(sources, "engine", engine)
    monkeypatch.setattr(sources, "get_device_id", lambda: "test-device")
    with Session(engine) as session:
        session.add(
            DeviceFile(
                device_id="test-device",
                absolute_path=str(missing_path),
                last_known_path=str(missing_path),
                media_kind="image",
                match_status="matched",
                weight=7,
            )
        )
        session.commit()

    result = sources.reconcile_local_media_directory_index(media_root)

    assert result["created_count"] == 1
    assert result["dangling_count"] == 1
    with Session(engine) as session:
        current = session.exec(
            select(DeviceFile).where(DeviceFile.absolute_path == str(current_path))
        ).one()
        stale = session.exec(
            select(DeviceFile).where(DeviceFile.last_known_path == str(missing_path))
        ).one()
    assert current.match_status == "matched"
    assert current.media_kind == "image"
    assert stale.absolute_path is None
    assert stale.match_status == "dangling"
    assert stale.weight == 7


def test_pinterest_backfill_without_state_database_is_empty(tmp_path) -> None:
    result = sources.backfill_pinterest_source_items(2, root_dir=str(tmp_path))

    assert result == {"added": 0, "updated": 0}


def test_pixiv_artwork_media_store_keeps_one_storage_family(tmp_path) -> None:
    store = sources.PixivStateStore(
        tmp_path / "3、pixiv" / "recommend",
        state_root=tmp_path / "1、pixiv",
        db_name="state.sqlite3",
    )

    safe_store = sources.pixiv_artwork_media_store(store, {"x_restrict": 0})
    pixi_store = sources.pixiv_artwork_media_store(store, {"x_restrict": 1})

    assert safe_store.target_root == (tmp_path / "3、pixiv" / "recommend").resolve()
    assert pixi_store.target_root == (tmp_path / "3、pixiv" / "recommend").resolve()
    assert safe_store.state_root == store.state_root
    assert pixi_store.state_root == store.state_root
    assert sources.pixiv_content_allowed(0) is True
    assert sources.pixiv_content_allowed(1) is False


def test_pixiv_tags_do_not_split_the_storage_family() -> None:
    assert sources.pixiv_rating_family(0, ["制服", "風景"]) == "pixiv"
    assert sources.pixiv_rating_family(0, ["監禁"]) == "pixiv"
    assert sources.pixiv_rating_family(0, '["DID", "拘束"]') == "pixiv"
    assert sources.pixiv_rating_family(0, ["水着"]) == "pixiv"
    assert sources.pixiv_rating_family(0, [], title="栗花落カナヲ① 捕縛") == "pixiv"
    assert sources.pixiv_rating_family(0, [], collection_url="discover://pixiv/home-following-r18") == "pixiv"


def test_pixiv_device_file_key_supports_the_legacy_general_root() -> None:
    current_root = Path(r"E:\data\m2510mn\1、pixiv")
    legacy_path = r"D:\home\chenkunze\data\m2510mn\pixiv\019_作者\作品_129436284\12.jpg"

    assert sources.canonicalize_pixiv_device_file_key(current_root, legacy_path) == str(
        Path("作者", "作品_129436284", "12.jpg")
    )


def test_pixiv_alias_reconcile_preserves_weight_above_new_scan_default(tmp_path, engine, monkeypatch) -> None:
    current_path = tmp_path / "1、pixiv" / "Noi" / "144420952_sample.png"
    current_path.parent.mkdir(parents=True)
    current_path.write_bytes(b"image")
    legacy_path = tmp_path / "pixiv" / "Noi" / current_path.name

    monkeypatch.setattr(sources, "engine", engine)
    with Session(engine) as session:
        current = DeviceFile(
            device_id="pixiv-weight-test",
            absolute_path=str(current_path),
            last_known_path=str(current_path),
            match_status="matched",
            weight=1,
            created_at=2,
            updated_at=2,
        )
        legacy = DeviceFile(
            device_id="pixiv-weight-test",
            absolute_path=None,
            last_known_path=str(legacy_path),
            match_status="dangling",
            weight=2,
            created_at=1,
            updated_at=1,
        )
        session.add(current)
        session.add(legacy)
        session.commit()
        current_id = current.id
        legacy_id = legacy.id

    monkeypatch.setattr(sources, "get_device_id", lambda: "pixiv-weight-test")
    result = sources.reconcile_pixiv_device_file_aliases(root_dir=str(tmp_path))

    with Session(engine) as session:
        restored = session.get(DeviceFile, current_id)
        assert restored is not None
        assert restored.weight == 2
        session.delete(restored)
        old = session.get(DeviceFile, legacy_id)
        if old is not None:
            session.delete(old)
        session.commit()

    assert result["rebound_count"] == 1


def test_candidate_path_resolution_falls_back_to_existing_review_batch(tmp_path) -> None:
    reservoir_root = tmp_path / "3、pinterest"
    review_root = tmp_path / "2、pinterest"
    review_file = review_root / "author" / "image.jpg"
    review_file.parent.mkdir(parents=True)
    review_file.write_bytes(b"review")

    resolved = sources.resolve_existing_candidate_path(
        reservoir_root,
        "author/image.jpg",
        fallback_roots=(review_root,),
    )

    assert resolved == review_file


def test_pixiv_path_resolution_falls_back_to_existing_review_batch(tmp_path) -> None:
    reservoir_root = tmp_path / "3、pixiv" / "recommend"
    review_root = tmp_path / "2、pixiv" / "recommend"
    review_file = review_root / "artist" / "artwork.png"
    review_file.parent.mkdir(parents=True)
    review_file.write_bytes(b"review")

    resolved = sources.resolve_existing_pixiv_absolute_path(
        reservoir_root,
        "artist/artwork.png",
        fallback_roots=(review_root,),
    )

    assert resolved == review_file


def test_pinterest_related_sync_accepts_headless_mode() -> None:
    parameters = inspect.signature(sources.run_pinterest_related_sync).parameters

    assert "headless" in parameters
    assert "download_items" in parameters


def test_candidate_downloads_are_forced_to_one_worker_per_platform() -> None:
    assert sources.PIXIV_DOWNLOAD_WORKERS == 1
    assert sources.PINTEREST_DOWNLOAD_WORKERS == 1


def test_pixiv_soft_batch_limit_keeps_queued_author_group_whole() -> None:
    rows = [
        {"artwork_id": "a1", "user_id": "author-a"},
        {"artwork_id": "a2", "user_id": "author-a"},
        {"artwork_id": "a3", "user_id": "author-a"},
        {"artwork_id": "b1", "user_id": "author-b"},
        {"artwork_id": "b2", "user_id": "author-b"},
    ]

    selected = sources.select_pixiv_pending_author_groups(rows, soft_limit=2)

    assert [row["artwork_id"] for row in selected] == ["a1", "a2", "a3"]


def test_pixiv_soft_weights_do_not_override_author_group_integrity() -> None:
    rows = [
        {"artwork_id": "a1", "user_id": "author-a", "x_restrict": 0},
        {"artwork_id": "a2", "user_id": "author-a", "x_restrict": 0},
        {"artwork_id": "b1", "user_id": "author-b", "x_restrict": 0},
    ]

    selected = sources.select_pixiv_pending_author_groups(rows, soft_limit=1)

    assert sources.PIXIV_AUTHOR_SOFT_WEIGHT == 50
    assert [row["artwork_id"] for row in selected] == ["a1", "a2"]


def test_pixiv_soft_batch_limit_keeps_unowned_works_independent() -> None:
    rows = [
        {"artwork_id": "unknown-1", "user_id": ""},
        {"artwork_id": "unknown-2", "user_id": ""},
    ]

    selected = sources.select_pixiv_pending_author_groups(rows, soft_limit=1)

    assert [row["artwork_id"] for row in selected] == ["unknown-1"]


def test_pinterest_home_downloads_share_the_flat_reservoir_root(tmp_path) -> None:
    assert sources.pinterest_home_root(tmp_path) == sources.pinterest_related_root(tmp_path)

    item = SimpleNamespace(source_kind="homefeed", collection_url=sources.PINTEREST_HOME_COLLECTION_URL)
    board_info = sources._pinterest_candidate_board_info_for_source_item(item)
    relative_path, entries = sources.build_download_plan(
        board_info,
        {
            "pin_id": "123",
            "title": "sample",
            "entries": [{"url": "https://example.com/sample.jpg", "media_type": "image"}],
        },
    )

    assert board_info["path_prefix"] == ""
    assert Path(relative_path).parent == Path(".")
    assert Path(entries[0]["relative_path"]).parent == Path(".")
    assert (
        sources.normalize_pinterest_candidate_relative_path(
            r"home\123_sample.jpg",
            source_kind="homefeed",
        )
        == "123_sample.jpg"
    )


def test_pinterest_candidate_enqueue_belongs_to_pinterest_store() -> None:
    assert hasattr(sources.PinterestStateStore, "enqueue_pin_candidate")
    assert not hasattr(sources.PixivStateStore, "enqueue_pin_candidate")


@pytest.mark.parametrize(
    ("source_kind", "collection_url", "expected_store"),
    [
        ("home_recommend", sources.PIXIV_HOME_RECOMMEND_COLLECTION_URL, "home_recommend"),
        ("author", sources.PIXIV_AUTHOR_COLLECTION_URL.format(author_id="9"), "related"),
    ],
)
def test_pending_pixiv_source_cache_is_not_rehydrated_for_download(
    monkeypatch: pytest.MonkeyPatch,
    source_kind: str,
    collection_url: str,
    expected_store: str,
) -> None:
    source_item = SimpleNamespace(
        remote_id="110565528",
        remote_url="https://www.pixiv.net/artworks/110565528",
        media_url="https://i.pximg.net/example.jpg",
        source_kind=source_kind,
        collection_url=collection_url,
        extra_json={
            "candidate_status": "pending",
            "pixiv_author_id": "9",
            "pixiv_author_name": "tester",
        },
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def exec(self, _statement):
            return SimpleNamespace(all=lambda: [source_item])

    class FakeCursor:
        def fetchall(self):
            return []

    class FakeConnection:
        def execute(self, _statement):
            return FakeCursor()

        def close(self):
            return None

    class FakeStore:
        def __init__(self):
            self.rows = []

        def connect_db(self):
            return FakeConnection()

        def upsert_manifest_items(self, _conn, rows, *, sync_time):
            assert sync_time
            self.rows.extend(rows)

    stores = {key: FakeStore() for key in ("related", "home_following", "home_recommend")}
    monkeypatch.setattr(sources, "Session", lambda _engine: FakeSession())
    monkeypatch.setattr(sources, "create_pixiv_related_store", lambda _root: stores["related"])
    monkeypatch.setattr(sources, "create_pixiv_home_following_store", lambda _root: stores["home_following"])
    monkeypatch.setattr(sources, "create_pixiv_home_recommend_store", lambda _root: stores["home_recommend"])

    result = sources.enqueue_pending_pixiv_source_items_to_candidate_stores(user_id=2, root_dir="unused")

    assert result == {"queued": 0, "known": 0, "terminal": 0, "skipped": 0}
    assert stores[expected_store].rows == []


def test_pixiv_runtime_uses_source_activity_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    @contextmanager
    def fake_lease(**_kwargs):
        events.append("lease-enter")
        yield
        events.append("lease-exit")

    manager = runtime.SyncJobManager()
    monkeypatch.setattr(runtime, "pixiv_source_activity_lease", fake_lease)
    monkeypatch.setattr(manager, "_run_job_with_pixiv_lease", lambda _config: events.append("run"))

    manager._run_job({"requested_sources": ["pixiv_download"]})

    assert events == ["lease-enter", "run", "lease-exit"]


def test_pinterest_runtime_does_not_take_pixiv_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = runtime.SyncJobManager()
    monkeypatch.setattr(
        runtime,
        "pixiv_source_activity_lease",
        lambda **_kwargs: pytest.fail("Pinterest-only work must not take the Pixiv lease"),
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        manager,
        "_run_job_with_pixiv_lease",
        lambda config: calls.append(config["requested_sources"]),
    )

    manager._run_job({"requested_sources": ["pinterest_collect_ids"]})

    assert calls == [["pinterest_collect_ids"]]


def test_pixiv_curation_does_not_take_remote_activity_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = runtime.SyncJobManager()
    monkeypatch.setattr(
        runtime,
        "pixiv_source_activity_lease",
        lambda **_kwargs: pytest.fail("Local Pixiv curation must remain parallel with remote discovery"),
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        manager,
        "_run_job_with_pixiv_lease",
        lambda config: calls.append(config["requested_sources"]),
    )

    manager._run_job({"requested_sources": ["pixiv_curate"]})

    assert calls == [["pixiv_curate"]]


def test_scheduled_discovery_still_downloads_cached_pixiv_when_discovery_is_disabled() -> None:
    profile = SimpleNamespace(
        pixiv_enabled=True,
        pixiv_scheduled_discovery_enabled=False,
        pinterest_enabled=True,
    )

    sources = runtime.scheduled_home_discovery_sources(profile)

    assert sources == [
        ("pixiv", ["pixiv_download"], "candidate:pixiv", {"pixiv_rating_family": "pixiv"}),
        ("pinterest", ["pinterest_download"], "candidate:pinterest", {}),
    ]


def test_scheduled_discovery_keeps_pixiv_by_default_when_enabled() -> None:
    profile = SimpleNamespace(
        pixiv_enabled=True,
        pixiv_scheduled_discovery_enabled=True,
        pinterest_enabled=False,
    )

    sources = runtime.scheduled_home_discovery_sources(profile)

    assert sources == [
        ("pixiv", ["pixiv_download"], "candidate:pixiv", {"pixiv_rating_family": "pixiv"}),
    ]


def test_pixiv_download_does_not_consume_legacy_url_cache_when_discovery_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_counts = iter([32])
    monkeypatch.setattr(runtime, "count_local_media_files", lambda *_args, **_kwargs: next(file_counts))
    monkeypatch.setattr(
        runtime,
        "refill_candidate_review_batch",
        lambda **_kwargs: {
            "after_media_count": 32,
            "moved_media_count": 0,
            "moves": [],
        },
    )

    manager = runtime.SyncJobManager()
    state_updates: list[dict[str, object]] = []
    monkeypatch.setattr(manager, "_set_state", lambda _user_id, **kwargs: state_updates.append(kwargs))
    monkeypatch.setattr(
        manager,
        "_run_pixiv_discover_download",
        lambda _config: pytest.fail("Remote discovery must remain disabled"),
    )

    result = manager._run_pixiv_download(
        {
            "user_id": 2,
            "root_dir": r"E:\data\m2510mn",
            "platform_download_target_count": 200,
            "pixiv_allow_candidate_collection": False,
        }
    )

    assert result["existing_count"] == 32
    assert result["new_download_count"] == 0
    assert result["remaining_count"] == 168
    assert result["download_attempts"] == []


def test_pixiv_download_clamps_oversized_target_and_trims_review_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime, "count_local_media_files", lambda *_args, **_kwargs: 691)
    trim_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        runtime,
        "trim_candidate_review_batch",
        lambda **kwargs: trim_calls.append(kwargs) or {"after_media_count": 200},
    )
    monkeypatch.setattr(
        runtime,
        "refill_candidate_review_batch",
        lambda **_kwargs: {"after_media_count": 200, "moved_media_count": 0},
    )

    result = runtime.SyncJobManager()._run_pixiv_download(
        {
            "user_id": 2,
            "root_dir": r"E:\data\m2510mn",
            "platform_download_target_count": 1000,
            "pixiv_rating_family": "pixiv",
        }
    )

    assert result["desired_count"] == 200
    assert result["existing_count"] == 200
    assert trim_calls[0]["limit"] == 200
    assert trim_calls[0]["platform"] == "pixiv"


def test_scheduled_pixiv_shortfall_is_allowed_after_cached_downloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = SimpleNamespace(
        user_id=2,
        pixiv_enabled=True,
        pixiv_scheduled_discovery_enabled=False,
        pinterest_enabled=False,
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def exec(self, _statement):
            return SimpleNamespace(all=lambda: [profile])

    calls: list[dict[str, object]] = []

    def fake_run_external_and_wait(_profile, **kwargs):
        calls.append(kwargs)
        return {
            "stage": "completed",
            "message": "Pixiv 缓存已消费",
            "summary": {"pixiv_download": {"remaining_count": 37}},
        }

    monkeypatch.setattr(runtime, "ensure_private_media_sync_schema", lambda: None)
    monkeypatch.setattr(runtime, "Session", lambda _engine: FakeSession())
    monkeypatch.setattr(
        runtime.sync_job_manager,
        "run_external_and_wait",
        fake_run_external_and_wait,
    )

    result = runtime.run_scheduled_home_discovery(target_count=200)

    assert result["success_count"] == 1
    assert result["failures"] == {}
    assert [call["scope_key"] for call in calls] == ["candidate:pixiv"]
    assert all(call["sources"] == ["pixiv_download"] for call in calls)
    assert calls[0]["overrides"] == {
        "platform_download_target_count": 200,
        "scheduled_acquisition_target_count": 200,
        "pixiv_allow_candidate_collection": False,
        "pixiv_rating_family": "pixiv",
    }


def test_pixiv_daily_acquisition_downloads_to_cache_but_refills_only_200(monkeypatch):
    cached_counts = iter([300, 1300, 1300])
    monkeypatch.setattr(runtime, "count_platform_cached_media", lambda *_args: next(cached_counts))
    refill_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        runtime,
        "refill_candidate_review_batch",
        lambda **kwargs: refill_calls.append(kwargs) or {"after_media_count": 200},
    )
    manager = runtime.SyncJobManager()
    discover_calls: list[int] = []
    monkeypatch.setattr(
        manager,
        "_run_pixiv_discover_download",
        lambda config: discover_calls.append(int(config["platform_download_target_count"]))
        or {"new_download_count": 1000},
    )

    result = manager._run_pixiv_download(
        {
            "user_id": 2,
            "root_dir": r"E:\data\m2510mn",
            "platform_download_target_count": 200,
            "scheduled_acquisition_target_count": 1000,
            "pixiv_allow_candidate_collection": True,
        }
    )

    assert discover_calls == [1000]
    assert result["mode"] == "daily_acquisition"
    assert result["new_download_count"] == 1000
    assert result["remaining_count"] == 0
    assert refill_calls[0]["limit"] == 200


def test_pinterest_daily_acquisition_downloads_500_but_refills_only_200(monkeypatch):
    cached_counts = iter([400, 900, 900])
    monkeypatch.setattr(runtime, "count_platform_cached_media", lambda *_args: next(cached_counts))
    monkeypatch.setattr(runtime, "count_pending_source_candidates", lambda **_kwargs: 500)
    monkeypatch.setattr(
        runtime,
        "run_pinterest_candidate_download",
        lambda **_kwargs: {"new_download_count": 500},
    )
    refill_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        runtime,
        "refill_candidate_review_batch",
        lambda **kwargs: refill_calls.append(kwargs) or {"after_media_count": 200},
    )

    result = runtime.SyncJobManager()._run_pinterest_download(
        {
            "user_id": 2,
            "root_dir": r"E:\data\m2510mn",
            "platform_download_target_count": 200,
            "scheduled_acquisition_target_count": 500,
        }
    )

    assert result["mode"] == "daily_acquisition"
    assert result["new_download_count"] == 500
    assert result["remaining_count"] == 0
    assert refill_calls[0]["limit"] == 200


def test_standard_daily_media_acquisition_baseline():
    from backend.core.jobs import scheduler

    assert scheduler.MEDIA_SYNC_PIXIV_DAILY_ACQUISITION_COUNT == 1000
    assert scheduler.MEDIA_SYNC_PINTEREST_DAILY_ACQUISITION_COUNT == 500


def test_scheduled_media_acquisition_uses_platform_specific_daily_targets(monkeypatch):
    profile = SimpleNamespace(
        user_id=2,
        pixiv_enabled=True,
        pixiv_scheduled_discovery_enabled=True,
        pinterest_enabled=True,
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def exec(self, _statement):
            return SimpleNamespace(all=lambda: [profile])

    calls: list[dict[str, object]] = []

    def fake_run(_profile, **kwargs):
        calls.append(kwargs)
        key = "pixiv_download" if kwargs["scope_key"] == "candidate:pixiv" else "pinterest_download"
        return {"stage": "completed", "summary": {key: {"remaining_count": 0}}}

    monkeypatch.setattr(runtime, "ensure_private_media_sync_schema", lambda: None)
    monkeypatch.setattr(runtime, "Session", lambda _engine: FakeSession())
    monkeypatch.setattr(runtime.sync_job_manager, "run_external_and_wait", fake_run)

    result = runtime.run_scheduled_home_discovery(
        target_counts={"pixiv": 1000, "pinterest": 500}
    )

    assert result["target_counts"] == {"pixiv": 1000, "pinterest": 500}
    assert [call["overrides"]["scheduled_acquisition_target_count"] for call in calls] == [1000, 500]
    assert [call["overrides"]["platform_download_target_count"] for call in calls] == [200, 200]


@pytest.mark.parametrize(
    ("platform", "before_pending", "after_home_pending", "expected_remaining"),
    [("pinterest", 2684, 2854, 30)],
)
def test_collect_ids_adds_target_to_existing_pending_pool(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    before_pending: int,
    after_home_pending: int,
    expected_remaining: int,
) -> None:
    final_pending = before_pending + 200
    pending_counts = iter([before_pending, after_home_pending, final_pending])
    related_calls: list[dict[str, object]] = []

    monkeypatch.setattr(runtime, "count_pending_source_candidates", lambda **_kwargs: next(pending_counts))
    monkeypatch.setattr(runtime, f"run_{platform}_home_sync", lambda **_kwargs: {"ok": True})

    def fake_related_sync(**kwargs):
        related_calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(runtime, f"run_{platform}_related_sync", fake_related_sync)

    manager = runtime.SyncJobManager()
    result = getattr(manager, f"_run_{platform}_collect_ids")(
        {
            "user_id": 2,
            "root_dir": r"E:\data\m2510mn",
            "platform_download_target_count": 200,
            f"{platform}_related_seed_limit": 12,
        }
    )

    assert result["before_pending_count"] == before_pending
    assert result["target_new_count"] == 200
    assert result["target_count"] == final_pending
    assert result["new_pending_count"] == 200
    assert related_calls
    seed_limit = int(related_calls[0]["seed_limit"])
    per_seed_limit = int(related_calls[0]["download_limit"])
    assert per_seed_limit * seed_limit >= expected_remaining


def test_pixiv_collect_prefers_author_watermarks_before_home_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int]] = []

    def fake_author_sync(**kwargs):
        events.append(("author", int(kwargs["target_new_count"])))
        return {"downloaded_count": 15}

    def fake_home_sync(**kwargs):
        events.append(("home", int(kwargs["download_limit"])))
        return {"new_download_count": 5}

    monkeypatch.setattr(runtime, "run_pixiv_author_sync", fake_author_sync)
    monkeypatch.setattr(runtime, "run_pixiv_home_sync", fake_home_sync)
    monkeypatch.setattr(runtime, "run_pixiv_related_sync", lambda **_kwargs: {"queued_count": 0})

    result = runtime.SyncJobManager()._run_pixiv_discover_download(
        {
            "user_id": 2,
            "root_dir": r"E:\data\m2510mn",
            "platform_download_target_count": 20,
            "pixiv_related_seed_limit": 12,
        }
    )

    assert events == [("author", 20), ("home", 5)]
    assert result["new_download_count"] == 20
    assert result["author"] == {"downloaded_count": 15}
