import time

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select, text
from sqlalchemy.exc import IntegrityError

from backend.core.devices.files import DeviceFileSyncSnapshot, reconcile_device_file_batch
from backend.migrations.manager import run_migrations
from backend.models import DeviceFile, ResourceIdentity


def test_device_file_defaults_support_rematching(session):
    record = DeviceFile(
        device_id="device-1",
        absolute_path=r"C:\cluster\logs\worker.log",
        last_known_path=r"C:\cluster\logs\worker.log",
    )

    session.add(record)
    session.commit()
    session.refresh(record)

    assert record.weight == 0
    assert record.hash_algorithm == "sha256"
    assert record.match_status == "matched"
    assert record.file_size is None
    assert record.modified_at_ms is None
    assert record.duration_ms is None
    assert record.width_px is None
    assert record.height_px is None
    assert record.media_kind is None
    assert record.mime_type is None


def test_device_file_requires_unique_live_absolute_path(session):
    session.add(
        DeviceFile(
            device_id="device-1",
            absolute_path=r"C:\cluster\logs\worker.log",
            last_known_path=r"C:\cluster\logs\worker.log",
        )
    )
    session.commit()

    session.add(
        DeviceFile(
            device_id="device-1",
            absolute_path=r"C:\cluster\logs\worker.log",
            last_known_path=r"C:\cluster\logs\worker.log",
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()

    session.rollback()


def test_device_file_allows_multiple_dangling_records_for_same_old_path(session):
    old_path = r"C:\cluster\logs\worker.log"
    session.add(
        DeviceFile(
            device_id="device-1",
            absolute_path=None,
            last_known_path=old_path,
            content_hash="old-a",
            file_size=123,
            match_status="dangling",
        )
    )
    session.add(
        DeviceFile(
            device_id="device-1",
            absolute_path=None,
            last_known_path=old_path,
            content_hash="old-b",
            file_size=456,
            match_status="dangling",
        )
    )
    session.commit()

    rows = session.exec(select(DeviceFile).order_by(DeviceFile.id)).all()
    assert len(rows) == 2
    assert all(row.absolute_path is None for row in rows)
    assert [row.last_known_path for row in rows] == [old_path, old_path]


def test_reconcile_device_file_batch_rebinds_dangling_record_by_hash(session):
    session.add(
        DeviceFile(
            device_id="device-1",
            absolute_path=None,
            last_known_path=r"C:\cluster\a.txt",
            content_hash="hash-a",
            hash_algorithm="sha256",
            file_size=100,
            match_status="dangling",
        )
    )
    session.commit()

    result = reconcile_device_file_batch(
        session,
        "device-1",
        [
            DeviceFileSyncSnapshot(
                absolute_path=r"C:\cluster\renamed\c.txt",
                content_hash="hash-a",
                hash_algorithm="sha256",
                file_size=100,
            )
        ],
    )

    assert result.created_count == 0
    assert result.rebound_count == 1

    rows = session.exec(select(DeviceFile).where(DeviceFile.device_id == "device-1")).all()
    assert len(rows) == 1
    assert rows[0].absolute_path == r"C:\cluster\renamed\c.txt"
    assert rows[0].last_known_path == r"C:\cluster\renamed\c.txt"
    assert rows[0].match_status == "matched"


def test_reconcile_device_file_batch_marks_missing_records_dangling_within_scope(session):
    session.add(
        DeviceFile(
            device_id="device-1",
            absolute_path=r"C:\scan\a.txt",
            last_known_path=r"C:\scan\a.txt",
            content_hash="old-a",
            file_size=10,
            match_status="matched",
        )
    )
    session.add(
        DeviceFile(
            device_id="device-1",
            absolute_path=r"D:\outside\keep.txt",
            last_known_path=r"D:\outside\keep.txt",
            content_hash="keep",
            file_size=20,
            match_status="matched",
        )
    )
    session.commit()

    result = reconcile_device_file_batch(
        session,
        "device-1",
        [
            DeviceFileSyncSnapshot(
                absolute_path=r"C:\scan\b.txt",
                content_hash="new-b",
                file_size=30,
            )
        ],
        mark_missing_as_dangling=True,
        scope_prefixes=[r"C:\scan"],
    )

    assert result.created_count == 1
    assert result.dangling_count == 1

    rows = {
        (row.absolute_path or row.last_known_path): row
        for row in session.exec(select(DeviceFile).where(DeviceFile.device_id == "device-1")).all()
    }
    assert rows[r"C:\scan\a.txt"].absolute_path is None
    assert rows[r"C:\scan\a.txt"].match_status == "dangling"
    assert rows[r"D:\outside\keep.txt"].absolute_path == r"D:\outside\keep.txt"
    assert rows[r"D:\outside\keep.txt"].match_status == "matched"


def test_reconcile_device_file_batch_rebinds_unseen_active_record_within_same_scan(session):
    session.add(
        DeviceFile(
            device_id="device-1",
            absolute_path=r"C:\scan\a.txt",
            last_known_path=r"C:\scan\a.txt",
            content_hash="hash-a",
            hash_algorithm="sha256",
            file_size=100,
            match_status="matched",
        )
    )
    session.commit()

    result = reconcile_device_file_batch(
        session,
        "device-1",
        [
            DeviceFileSyncSnapshot(
                absolute_path=r"C:\scan\renamed\c.txt",
                content_hash="hash-a",
                hash_algorithm="sha256",
                file_size=100,
            )
        ],
        mark_missing_as_dangling=True,
        scope_prefixes=[r"C:\scan"],
    )

    assert result.created_count == 0
    assert result.rebound_count == 1
    assert result.dangling_count == 0

    rows = session.exec(select(DeviceFile).where(DeviceFile.device_id == "device-1")).all()
    assert len(rows) == 1
    assert rows[0].absolute_path == r"C:\scan\renamed\c.txt"
    assert rows[0].match_status == "matched"


def test_reconcile_device_file_batch_merges_weight_from_scope_tail_match_when_same_path_record_exists(session):
    new_path = r"C:\scan\artist\set\image-01.png"
    old_path = r"C:\scan\001_artist\set\image-01.png"

    session.add(
        DeviceFile(
            device_id="device-1",
            absolute_path=new_path,
            last_known_path=new_path,
            file_size=100,
            modified_at_ms=1000,
            media_kind="image",
            weight=0,
            match_status="matched",
        )
    )
    session.add(
        DeviceFile(
            device_id="device-1",
            absolute_path=old_path,
            last_known_path=old_path,
            file_size=100,
            modified_at_ms=1000,
            media_kind="image",
            weight=3,
            match_status="matched",
        )
    )
    session.commit()

    result = reconcile_device_file_batch(
        session,
        "device-1",
        [
            DeviceFileSyncSnapshot(
                absolute_path=new_path,
                last_known_path=new_path,
                file_size=100,
                modified_at_ms=1000,
                media_kind="image",
            )
        ],
        mark_missing_as_dangling=True,
        scope_prefixes=[r"C:\scan"],
    )

    assert result.created_count == 0
    assert result.updated_count == 1
    assert result.dangling_count == 1

    rows = {
        (row.absolute_path or row.last_known_path): row
        for row in session.exec(select(DeviceFile).where(DeviceFile.device_id == "device-1")).all()
    }
    assert rows[new_path].absolute_path == new_path
    assert rows[new_path].weight == 3
    assert rows[new_path].match_status == "matched"
    assert rows[old_path].absolute_path is None
    assert rows[old_path].weight == 3
    assert rows[old_path].match_status == "dangling"


def test_reconcile_device_file_batch_updates_same_path_in_place_and_keeps_manual_cover(session):
    session.add(
        DeviceFile(
            device_id="device-1",
            absolute_path=r"C:\scan\a.txt",
            last_known_path=r"C:\scan\a.txt",
            content_hash="hash-old",
            hash_algorithm="sha256",
            file_size=100,
            modified_at_ms=1000,
            cover_path="device_covers/manual.jpg",
            cover_mime_type="image/jpeg",
            cover_source="manual",
            weight=7,
            match_status="matched",
        )
    )
    session.commit()

    result = reconcile_device_file_batch(
        session,
        "device-1",
        [
            DeviceFileSyncSnapshot(
                absolute_path=r"C:\scan\a.txt",
                last_known_path=r"C:\scan\a.txt",
                content_hash="hash-new",
                hash_algorithm="sha256",
                file_size=200,
                modified_at_ms=2000,
            )
        ],
    )

    assert result.created_count == 0
    assert result.rebound_count == 0
    assert result.updated_count == 1

    rows = session.exec(select(DeviceFile).where(DeviceFile.device_id == "device-1")).all()
    assert len(rows) == 1
    assert rows[0].absolute_path == r"C:\scan\a.txt"
    assert rows[0].last_known_path == r"C:\scan\a.txt"
    assert rows[0].file_size == 200
    assert rows[0].modified_at_ms == 2000
    assert rows[0].content_hash == "hash-new"
    assert rows[0].cover_path == "device_covers/manual.jpg"
    assert rows[0].cover_source == "manual"
    assert rows[0].weight == 7


def test_reconcile_device_file_batch_updates_same_path_in_place_and_clears_auto_cover(session):
    session.add(
        DeviceFile(
            device_id="device-1",
            absolute_path=r"C:\scan\a.txt",
            last_known_path=r"C:\scan\a.txt",
            content_hash="hash-old",
            hash_algorithm="sha256",
            file_size=100,
            modified_at_ms=1000,
            cover_path="device_covers/auto.jpg",
            cover_mime_type="image/jpeg",
            cover_source="auto",
            match_status="matched",
        )
    )
    session.commit()

    result = reconcile_device_file_batch(
        session,
        "device-1",
        [
            DeviceFileSyncSnapshot(
                absolute_path=r"C:\scan\a.txt",
                last_known_path=r"C:\scan\a.txt",
                content_hash="hash-new",
                hash_algorithm="sha256",
                file_size=200,
                modified_at_ms=2000,
            )
        ],
    )

    assert result.created_count == 0
    assert result.rebound_count == 0
    assert result.updated_count == 1

    rows = session.exec(select(DeviceFile).where(DeviceFile.device_id == "device-1")).all()
    assert len(rows) == 1
    assert rows[0].absolute_path == r"C:\scan\a.txt"
    assert rows[0].content_hash == "hash-new"
    assert rows[0].cover_path is None
    assert rows[0].cover_mime_type is None
    assert rows[0].cover_source is None
    assert rows[0].cover_updated_at is None


def test_reconcile_device_file_batch_clears_auto_cover_when_same_path_changed_without_hash(session):
    session.add(
        DeviceFile(
            device_id="device-1",
            absolute_path=r"C:\scan\a.txt",
            last_known_path=r"C:\scan\a.txt",
            content_hash="hash-old",
            hash_algorithm="sha256",
            file_size=100,
            modified_at_ms=1000,
            cover_path="device_covers/auto.jpg",
            cover_mime_type="image/jpeg",
            cover_source="auto",
            match_status="matched",
        )
    )
    session.commit()

    result = reconcile_device_file_batch(
        session,
        "device-1",
        [
            DeviceFileSyncSnapshot(
                absolute_path=r"C:\scan\a.txt",
                last_known_path=r"C:\scan\a.txt",
                file_size=100,
                modified_at_ms=2000,
            )
        ],
    )

    assert result.created_count == 0
    assert result.rebound_count == 0
    assert result.updated_count == 1

    rows = session.exec(select(DeviceFile).where(DeviceFile.device_id == "device-1")).all()
    assert len(rows) == 1
    assert rows[0].absolute_path == r"C:\scan\a.txt"
    assert rows[0].content_hash is None
    assert rows[0].hash_updated_at is None
    assert rows[0].cover_path is None
    assert rows[0].cover_source is None


def test_run_migrations_upgrades_legacy_devicefile_table():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    legacy_ts = time.time()
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE notenode (id TEXT PRIMARY KEY, color VARCHAR)")
        conn.exec_driver_sql(
            """
            CREATE TABLE devicefile (
                id INTEGER PRIMARY KEY,
                device_id VARCHAR NOT NULL,
                absolute_path VARCHAR NOT NULL,
                weight INTEGER DEFAULT 0,
                created_at FLOAT,
                updated_at FLOAT,
                CONSTRAINT uq_devicefile_device_path UNIQUE (device_id, absolute_path)
            )
            """
        )
        conn.exec_driver_sql(
            """
            INSERT INTO devicefile (id, device_id, absolute_path, weight, created_at, updated_at)
            VALUES (1, 'device-1', 'C:\\legacy\\a.txt', 7, ?, ?)
            """,
            (legacy_ts, legacy_ts),
        )

    run_migrations(engine)

    with Session(engine) as session:
        columns = {
            row[1]
            for row in session.exec(text("PRAGMA table_info(devicefile)")).all()
        }
        record = session.exec(select(DeviceFile).where(DeviceFile.id == 1)).one()

    assert "last_known_path" in columns
    assert "content_hash" in columns
    assert "file_size" in columns
    assert "match_status" in columns
    assert "cover_path" in columns
    assert "cover_mime_type" in columns
    assert "cover_source" in columns
    assert "cover_updated_at" in columns
    assert "modified_at_ms" in columns
    assert "duration_ms" in columns
    assert "width_px" in columns
    assert "height_px" in columns
    assert "media_kind" in columns
    assert "mime_type" in columns
    assert "numeric_id" in columns
    assert record.absolute_path == r"C:\legacy\a.txt"
    assert record.last_known_path == r"C:\legacy\a.txt"
    assert record.match_status == "matched"
    assert record.weight == 7
    assert record.numeric_id == 1

    with Session(engine) as session:
        identity = session.get(ResourceIdentity, 1)
    assert identity is not None
    assert identity.resource_type == "device_file"
    assert identity.legacy_pk == "1"

    SQLModel.metadata.drop_all(engine)
