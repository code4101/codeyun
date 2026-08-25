from pathlib import Path
from types import SimpleNamespace
import sqlite3

from sqlmodel import Session, select

from backend.core import pixiv_url_migration as migration
from backend.core.jobs import local_runtime
from backend.plugins.modules.media_sync.models import MediaSyncSourceItem


def _url_item(remote_id: str, *, media_index: int = 0, status: str = "pending") -> MediaSyncSourceItem:
    return MediaSyncSourceItem(
        user_id=9,
        platform="pixiv",
        remote_id=remote_id,
        media_index=media_index,
        remote_url=f"https://www.pixiv.net/artworks/{remote_id}",
        media_url=f"https://i.pximg.net/{remote_id}_{media_index}.jpg",
        extra_json={"candidate_status": status},
    )


def test_historical_1820_candidates_are_frozen_without_url_row_duplicates(engine, monkeypatch):
    monkeypatch.setattr(migration, "engine", engine)
    captured: dict = {}

    def fake_submit(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="migration-run"), True

    monkeypatch.setattr(local_runtime, "submit_local_job_once", fake_submit)
    with Session(engine) as session:
        session.add_all([_url_item(str(remote_id)) for remote_id in range(1, 1821)])
        session.add(_url_item("1", media_index=1))
        session.add(_url_item("9999", status="skipped"))
        session.commit()

    result = migration.enqueue_pixiv_url_migration(user_id=9, root_dir=r"E:\media")

    assert result == {
        "queued": True,
        "local_job_run_id": "migration-run",
        "frozen_remote_count": 1820,
        "frozen_row_count": 1821,
    }
    assert captured["payload"]["remote_ids"] == [str(remote_id) for remote_id in range(1, 1821)]
    assert captured["resource_key"] == "resource:media-sync:curation:pixiv"


def test_failed_historical_download_keeps_only_retryable_id_and_error(engine, monkeypatch):
    monkeypatch.setattr(migration, "engine", engine)
    with Session(engine) as session:
        session.add(_url_item("100"))
        session.commit()

    migration._scrub_source_items_for_remote_id(
        user_id=9,
        remote_id="100",
        succeeded=False,
        error="temporary failure",
    )

    with Session(engine) as session:
        item = session.exec(select(MediaSyncSourceItem)).one()
    assert item.remote_id == "100"
    assert item.remote_url == ""
    assert item.media_url == ""
    assert item.downloaded_at is None
    assert item.absolute_path is None
    assert item.extra_json["candidate_status"] == "error"
    assert item.extra_json["last_error"] == "temporary failure"


def test_failed_redownload_clears_stale_downloaded_path(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(migration, "engine", engine)
    missing_path = tmp_path / "missing.jpg"
    with Session(engine) as session:
        session.add(
            MediaSyncSourceItem(
                user_id=9,
                platform="pixiv",
                remote_id="101",
                media_index=0,
                absolute_path=str(missing_path),
                downloaded_at=1.0,
                device_id="old-device",
                extra_json={"candidate_status": "downloaded"},
            )
        )
        session.commit()

    migration._scrub_source_items_for_remote_id(
        user_id=9,
        remote_id="101",
        succeeded=False,
        error="404 Not Found",
    )

    with Session(engine) as session:
        item = session.exec(select(MediaSyncSourceItem)).one()
    assert item.absolute_path is None
    assert item.downloaded_at is None
    assert item.device_id is None
    assert item.extra_json["candidate_status"] == "error"
    assert item.extra_json["last_error"] == "404 Not Found"


def test_successful_redownload_retires_removed_remote_page(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(migration, "engine", engine)
    missing_path = tmp_path / "removed-page.jpg"
    with Session(engine) as session:
        session.add(
            MediaSyncSourceItem(
                user_id=9,
                platform="pixiv",
                remote_id="102",
                media_index=1,
                absolute_path=str(missing_path),
                downloaded_at=1.0,
                device_id="old-device",
                extra_json={"candidate_status": "downloaded"},
            )
        )
        session.commit()

    migration._scrub_source_items_for_remote_id(
        user_id=9,
        remote_id="102",
        succeeded=True,
    )

    with Session(engine) as session:
        item = session.exec(select(MediaSyncSourceItem)).one()
    assert item.absolute_path is None
    assert item.downloaded_at is None
    assert item.device_id is None
    assert item.extra_json["candidate_status"] == "skipped"
    assert item.extra_json["migration_reason"] == "remote_page_no_longer_exists"


def test_partial_multi_page_artwork_is_not_treated_as_fully_present(tmp_path):
    existing = tmp_path / "01.jpg"
    existing.write_bytes(b"page-1")
    missing = tmp_path / "02.jpg"

    assert migration._all_registered_paths_exist(
        [{"absolute_path": str(existing)}, {"absolute_path": str(missing)}]
    ) is False
    assert migration._all_registered_paths_exist(
        [{"absolute_path": str(existing)}]
    ) is True


def test_final_scrub_removes_source_and_state_urls(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(migration, "engine", engine)
    with Session(engine) as session:
        session.add(_url_item("100"))
        session.add(
            MediaSyncSourceItem(
                user_id=9,
                platform="pixiv",
                remote_id="200",
                remote_url="https://www.pixiv.net/artworks/200",
                media_url="https://i.pximg.net/200.jpg",
                absolute_path=str(tmp_path / "200.jpg"),
                downloaded_at=1.0,
                extra_json={"candidate_status": "downloaded"},
            )
        )
        session.commit()

    db_path = tmp_path / "state.sqlite3"
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE artworks (
            artwork_id TEXT PRIMARY KEY, artwork_url TEXT, thumbnail_url TEXT,
            download_status TEXT, last_error TEXT
        );
        CREATE TABLE artwork_pages (
            artwork_id TEXT, original_url TEXT, status TEXT, last_error TEXT
        );
        INSERT INTO artworks VALUES ('100', 'art', 'thumb', 'pending', NULL);
        INSERT INTO artwork_pages VALUES ('100', 'original', 'error', NULL);
        """
    )
    conn.commit()
    conn.close()

    class FakeStore:
        def __init__(self, path: Path, manifest: Path):
            self.db_path = path
            self.manifest_path = manifest

        def connect_db(self):
            return sqlite3.connect(self.db_path)

    result = migration._scrub_pixiv_url_storage(
        user_id=9,
        stores=[FakeStore(db_path, manifest_path)],
    )

    with Session(engine) as session:
        rows = session.exec(select(MediaSyncSourceItem)).all()
    assert all(not row.remote_url and not row.media_url for row in rows)
    conn = sqlite3.connect(db_path)
    artwork = conn.execute(
        "SELECT artwork_url, thumbnail_url, download_status FROM artworks"
    ).fetchone()
    page = conn.execute("SELECT original_url, status FROM artwork_pages").fetchone()
    conn.close()
    assert artwork == ("", "", "skipped")
    assert page == ("", "skipped")
    assert not manifest_path.exists()
    assert result["source_rows_scrubbed"] == 2
    assert result["state_artworks_scrubbed"] == 1
    assert result["state_pages_scrubbed"] == 1
