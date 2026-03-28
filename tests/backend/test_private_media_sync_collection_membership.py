import time
from pathlib import Path

from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from backend.models import DeviceFile, User
from backend.private_modules.media_sync import sources as media_sync_sources
from backend.private_modules.media_sync.models import MediaSyncCollectionMembership, MediaSyncSourceItem


def _seed_pinterest_mapping(
    *,
    engine,
    root_dir: Path,
    weight_by_pin_id: dict[str, int],
    content_hash_by_pin_id: dict[str, str] | None = None,
) -> tuple[int, str]:
    board_url = "https://www.pinterest.com/example/mn/"
    now = time.time()
    files_root = root_dir / "pinterest"
    files_root.mkdir(parents=True, exist_ok=True)

    with Session(engine) as session:
        user = User(
            username="membership_tester",
            email="membership_tester@example.com",
            hashed_password="pw",
            is_active=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)

        for index, (pin_id, weight) in enumerate(weight_by_pin_id.items(), start=1):
            file_path = files_root / f"{pin_id}.jpg"
            file_path.write_bytes(f"pin-{pin_id}".encode("utf-8"))
            content_hash = (content_hash_by_pin_id or {}).get(pin_id, f"hash-{pin_id}")
            record = DeviceFile(
                device_id="device-1",
                absolute_path=str(file_path),
                last_known_path=str(file_path),
                content_hash=content_hash,
                hash_algorithm="sha256",
                file_size=int(file_path.stat().st_size),
                match_status="matched",
                weight=weight,
                created_at=now + index,
                updated_at=now + index,
                last_seen_at=now + index,
                hash_updated_at=now + index,
            )
            source_item = MediaSyncSourceItem(
                user_id=user.id,
                platform="pinterest",
                source_kind="bookmark",
                collection_url=board_url,
                remote_id=pin_id,
                media_index=0,
                remote_url=f"https://www.pinterest.com/pin/{pin_id}/",
                media_url=f"https://example.com/{pin_id}.jpg",
                media_type="image",
                relative_path=file_path.name,
                absolute_path=str(file_path),
                device_id="device-1",
                content_hash=content_hash,
                hash_algorithm="sha256",
                file_size=int(file_path.stat().st_size),
                downloaded_at=now + index,
                first_seen_at=now + index,
                updated_at=now + index,
            )
            session.add(record)
            session.add(source_item)

        session.commit()
        return user.id, board_url


def test_build_pinterest_membership_snapshot_counts_present_and_missing(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(media_sync_sources, "engine", engine)
    monkeypatch.setattr(media_sync_sources, "get_device_id", lambda: "device-1")

    user_id, board_url = _seed_pinterest_mapping(
        engine=engine,
        root_dir=tmp_path / "media-root",
        weight_by_pin_id={"1001": 2, "1002": 1, "1003": 0},
    )

    now = time.time()
    with Session(engine) as session:
        session.add(
            MediaSyncCollectionMembership(
                user_id=user_id,
                platform="pinterest",
                collection_kind="board",
                collection_url=board_url,
                remote_id="1001",
                remote_url="https://www.pinterest.com/pin/1001/",
                desired_state="present",
                observed_state="present",
                example_absolute_path=str((tmp_path / "media-root" / "pinterest" / "1001.jpg")),
                local_weight=2,
                last_checked_at=now,
                last_present_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            MediaSyncCollectionMembership(
                user_id=user_id,
                platform="pinterest",
                collection_kind="board",
                collection_url=board_url,
                remote_id="1002",
                remote_url="https://www.pinterest.com/pin/1002/",
                desired_state="present",
                observed_state="missing",
                example_absolute_path=str((tmp_path / "media-root" / "pinterest" / "1002.jpg")),
                local_weight=1,
                last_checked_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    snapshot = media_sync_sources.build_pinterest_membership_snapshot(
        root_dir=str(tmp_path / "media-root"),
        user_id=user_id,
        board_url=board_url,
        enabled=True,
    )

    assert snapshot["counts"]["total"] == 2
    assert snapshot["counts"]["done"] == 1
    assert snapshot["counts"]["pending"] == 1
    assert snapshot["counts"]["skipped"] == 0
    assert "已确认 present 的 pin 默认跳过" in str(snapshot["message"] or "")


def test_run_pinterest_membership_sync_uses_cache_when_all_desired_pins_already_present(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(media_sync_sources, "engine", engine)
    monkeypatch.setattr(media_sync_sources, "get_device_id", lambda: "device-1")

    user_id, board_url = _seed_pinterest_mapping(
        engine=engine,
        root_dir=tmp_path / "media-root",
        weight_by_pin_id={"2001": 2},
    )

    now = time.time()
    with Session(engine) as session:
        session.add(
            MediaSyncCollectionMembership(
                user_id=user_id,
                platform="pinterest",
                collection_kind="board",
                collection_url=board_url,
                remote_id="2001",
                remote_url="https://www.pinterest.com/pin/2001/",
                desired_state="present",
                observed_state="present",
                example_absolute_path=str((tmp_path / "media-root" / "pinterest" / "2001.jpg")),
                local_weight=2,
                last_checked_at=now,
                last_present_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    monkeypatch.setattr(media_sync_sources, "backfill_pinterest_source_items", lambda *args, **kwargs: {"added": 0, "updated": 0})
    monkeypatch.setattr(
        media_sync_sources,
        "open_browser",
        lambda: (_ for _ in ()).throw(AssertionError("cache hit should not open browser")),
    )

    logs: list[str] = []
    summary = media_sync_sources.run_pinterest_membership_sync(
        user_id=user_id,
        root_dir=str(tmp_path / "media-root"),
        board_url=board_url,
        log=logs.append,
    )

    assert summary["used_cache"] is True
    assert summary["counts"]["done"] == 1
    assert any("命中缓存" in row for row in logs)


def test_run_pinterest_membership_sync_saves_missing_pin_and_marks_present(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(media_sync_sources, "engine", engine)
    monkeypatch.setattr(media_sync_sources, "get_device_id", lambda: "device-1")

    user_id, board_url = _seed_pinterest_mapping(
        engine=engine,
        root_dir=tmp_path / "media-root",
        weight_by_pin_id={"3001": 1},
    )

    monkeypatch.setattr(media_sync_sources, "backfill_pinterest_source_items", lambda *args, **kwargs: {"added": 0, "updated": 0})

    class FakeTab:
        def close(self):
            return None

    class FakeBrowser:
        def new_tab(self):
            return FakeTab()

        def quit(self):
            return None

    monkeypatch.setattr(media_sync_sources, "open_browser", lambda: FakeBrowser())
    monkeypatch.setattr(
        media_sync_sources,
        "ensure_pinterest_pin_saved_to_board",
        lambda detail_tab, *, pin_url, board_url: {"status": "saved", "detail": "mn\n已收藏至此图板"},
    )

    summary = media_sync_sources.run_pinterest_membership_sync(
        user_id=user_id,
        root_dir=str(tmp_path / "media-root"),
        board_url=board_url,
        log=lambda message: None,
    )

    assert summary["used_cache"] is False
    assert summary["saved_count"] == 1
    assert summary["counts"]["done"] == 1
    assert summary["manifest_path"] is None

    with Session(engine) as session:
        row = session.exec(
            select(MediaSyncCollectionMembership).where(
                MediaSyncCollectionMembership.user_id == user_id,
                MediaSyncCollectionMembership.platform == "pinterest",
                MediaSyncCollectionMembership.collection_url == board_url,
                MediaSyncCollectionMembership.remote_id == "3001",
            )
        ).first()

    assert row is not None
    assert row.observed_state == "present"
    assert row.desired_state == "present"
    assert row.last_applied_at is not None


def test_run_pinterest_membership_sync_marks_remaining_missing_after_apply_attempt(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(media_sync_sources, "engine", engine)
    monkeypatch.setattr(media_sync_sources, "get_device_id", lambda: "device-1")

    user_id, board_url = _seed_pinterest_mapping(
        engine=engine,
        root_dir=tmp_path / "media-root",
        weight_by_pin_id={"4001": 1},
    )

    monkeypatch.setattr(media_sync_sources, "backfill_pinterest_source_items", lambda *args, **kwargs: {"added": 0, "updated": 0})

    class FakeTab:
        def close(self):
            return None

    class FakeBrowser:
        def new_tab(self):
            return FakeTab()

        def quit(self):
            return None

    monkeypatch.setattr(media_sync_sources, "open_browser", lambda: FakeBrowser())
    monkeypatch.setattr(
        media_sync_sources,
        "ensure_pinterest_pin_saved_to_board",
        lambda detail_tab, *, pin_url, board_url: {"status": "clicked", "detail": "mn"},
    )

    summary = media_sync_sources.run_pinterest_membership_sync(
        user_id=user_id,
        root_dir=str(tmp_path / "media-root"),
        board_url=board_url,
        log=lambda message: None,
    )

    assert summary["used_cache"] is False
    assert summary["counts"]["pending"] == 1

    with Session(engine) as session:
        row = session.exec(
            select(MediaSyncCollectionMembership).where(
                MediaSyncCollectionMembership.user_id == user_id,
                MediaSyncCollectionMembership.platform == "pinterest",
                MediaSyncCollectionMembership.collection_url == board_url,
                MediaSyncCollectionMembership.remote_id == "4001",
            )
        ).first()

    assert row is not None
    assert row.observed_state == "missing"
    assert row.desired_state == "present"
    assert row.last_applied_at is not None


def test_run_pinterest_membership_sync_skips_already_present_items_in_incremental_mode(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(media_sync_sources, "engine", engine)
    monkeypatch.setattr(media_sync_sources, "get_device_id", lambda: "device-1")

    user_id, board_url = _seed_pinterest_mapping(
        engine=engine,
        root_dir=tmp_path / "media-root",
        weight_by_pin_id={"5001": 2, "5002": 1},
    )

    now = time.time()
    with Session(engine) as session:
        session.add(
            MediaSyncCollectionMembership(
                user_id=user_id,
                platform="pinterest",
                collection_kind="board",
                collection_url=board_url,
                remote_id="5001",
                remote_url="https://www.pinterest.com/pin/5001/",
                desired_state="present",
                observed_state="present",
                example_absolute_path=str((tmp_path / "media-root" / "pinterest" / "5001.jpg")),
                local_weight=2,
                last_checked_at=now,
                last_present_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    monkeypatch.setattr(media_sync_sources, "backfill_pinterest_source_items", lambda *args, **kwargs: {"added": 0, "updated": 0})

    class FakeTab:
        def close(self):
            return None

    class FakeBrowser:
        def new_tab(self):
            return FakeTab()

        def quit(self):
            return None

    monkeypatch.setattr(media_sync_sources, "open_browser", lambda: FakeBrowser())

    handled_pin_urls: list[str] = []

    def fake_ensure(detail_tab, *, pin_url, board_url):
        handled_pin_urls.append(pin_url)
        return {"status": "saved", "detail": "mn\n已收藏至此图板"}

    monkeypatch.setattr(media_sync_sources, "ensure_pinterest_pin_saved_to_board", fake_ensure)

    summary = media_sync_sources.run_pinterest_membership_sync(
        user_id=user_id,
        root_dir=str(tmp_path / "media-root"),
        board_url=board_url,
        log=lambda message: None,
    )

    assert summary["candidate_count"] == 2
    assert summary["counts"]["done"] == 2
    assert handled_pin_urls == ["https://www.pinterest.com/pin/5002/"]


def test_run_pinterest_membership_sync_skips_duplicate_content_hash_when_equivalent_pin_already_present(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(media_sync_sources, "engine", engine)
    monkeypatch.setattr(media_sync_sources, "get_device_id", lambda: "device-1")

    user_id, board_url = _seed_pinterest_mapping(
        engine=engine,
        root_dir=tmp_path / "media-root",
        weight_by_pin_id={"7001": 2, "7002": 1},
        content_hash_by_pin_id={"7001": "shared-hash", "7002": "shared-hash"},
    )

    now = time.time()
    with Session(engine) as session:
        session.add(
            MediaSyncCollectionMembership(
                user_id=user_id,
                platform="pinterest",
                collection_kind="board",
                collection_url=board_url,
                remote_id="7001",
                remote_url="https://www.pinterest.com/pin/7001/",
                desired_state="present",
                observed_state="present",
                example_absolute_path=str((tmp_path / "media-root" / "pinterest" / "7001.jpg")),
                local_weight=2,
                last_checked_at=now,
                last_present_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    monkeypatch.setattr(media_sync_sources, "backfill_pinterest_source_items", lambda *args, **kwargs: {"added": 0, "updated": 0})

    class FakeBrowser:
        def quit(self):
            return None

    monkeypatch.setattr(media_sync_sources, "open_browser", lambda: FakeBrowser())
    monkeypatch.setattr(
        media_sync_sources,
        "ensure_pinterest_pin_saved_to_board",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("duplicate hash should skip remote save")),
    )

    logs: list[str] = []
    summary = media_sync_sources.run_pinterest_membership_sync(
        user_id=user_id,
        root_dir=str(tmp_path / "media-root"),
        board_url=board_url,
        log=logs.append,
    )

    assert summary["used_cache"] is True
    assert summary["counts"]["done"] == 2
    assert any("内容重复" in row for row in logs)

    with Session(engine) as session:
        row = session.exec(
            select(MediaSyncCollectionMembership).where(
                MediaSyncCollectionMembership.user_id == user_id,
                MediaSyncCollectionMembership.platform == "pinterest",
                MediaSyncCollectionMembership.collection_url == board_url,
                MediaSyncCollectionMembership.remote_id == "7002",
            )
        ).first()

    assert row is not None
    assert row.observed_state == "present"
    assert row.desired_state == "present"


def test_run_pinterest_membership_sync_recreates_tab_after_connection_lost(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(media_sync_sources, "engine", engine)
    monkeypatch.setattr(media_sync_sources, "get_device_id", lambda: "device-1")

    user_id, board_url = _seed_pinterest_mapping(
        engine=engine,
        root_dir=tmp_path / "media-root",
        weight_by_pin_id={"6001": 1},
    )

    monkeypatch.setattr(media_sync_sources, "backfill_pinterest_source_items", lambda *args, **kwargs: {"added": 0, "updated": 0})

    class FakeTab:
        def close(self):
            return None

    class FakeBrowser:
        def __init__(self):
            self.new_tab_count = 0

        def new_tab(self):
            self.new_tab_count += 1
            return FakeTab()

        def quit(self):
            return None

    browsers: list[FakeBrowser] = []

    def fake_open_browser():
        browser = FakeBrowser()
        browsers.append(browser)
        return browser

    monkeypatch.setattr(media_sync_sources, "open_browser", fake_open_browser)

    attempts = {"count": 0}

    def fake_ensure(detail_tab, *, pin_url, board_url):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("与页面的连接已断开。\n版本: 4.1.1.2")
        return {"status": "saved", "detail": "mn\n已收藏至此图板"}

    monkeypatch.setattr(media_sync_sources, "ensure_pinterest_pin_saved_to_board", fake_ensure)

    summary = media_sync_sources.run_pinterest_membership_sync(
        user_id=user_id,
        root_dir=str(tmp_path / "media-root"),
        board_url=board_url,
        log=lambda message: None,
    )

    assert summary["saved_count"] == 1
    assert attempts["count"] == 2
    assert len(browsers) == 2
