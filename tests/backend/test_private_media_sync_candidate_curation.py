import hashlib
import time
from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from backend.models import DeviceFile, User
from backend.private_modules.media_sync import sources as media_sync_sources
from backend.private_modules.media_sync.models import MediaSyncSourceItem


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("platform", "formal_dir_name", "candidate_dir_name"),
    [
        ("pixiv", "pixiv", "_pixiv"),
        ("pinterest", "pinterest", "_pinterest"),
    ],
)
def test_run_candidate_curation_promotes_positive_and_deletes_nonpositive(
    tmp_path,
    monkeypatch,
    platform,
    formal_dir_name,
    candidate_dir_name,
):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(media_sync_sources, "engine", engine)
    monkeypatch.setattr(media_sync_sources, "get_device_id", lambda: "device-1")

    root_dir = tmp_path / "media-root"
    candidate_root = root_dir / candidate_dir_name
    formal_root = root_dir / formal_dir_name
    promote_relative_path = Path("author") / "keep.jpg"
    delete_relative_path = Path("author") / "drop.jpg"
    promote_candidate_path = candidate_root / promote_relative_path
    delete_candidate_path = candidate_root / delete_relative_path
    promote_candidate_path.parent.mkdir(parents=True, exist_ok=True)
    delete_candidate_path.parent.mkdir(parents=True, exist_ok=True)
    promote_candidate_path.write_bytes(b"candidate-keep")
    delete_candidate_path.write_bytes(b"candidate-drop")

    promote_hash = _sha256(promote_candidate_path)
    delete_hash = _sha256(delete_candidate_path)
    now = time.time()

    with Session(engine) as session:
        user = User(
            username=f"{platform}_tester",
            email=f"{platform}_tester@example.com",
            hashed_password="pw",
            is_active=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)

        promote_record = DeviceFile(
            device_id="device-1",
            absolute_path=str(promote_candidate_path),
            last_known_path=str(promote_candidate_path),
            content_hash=promote_hash,
            hash_algorithm="sha256",
            file_size=int(promote_candidate_path.stat().st_size),
            match_status="matched",
            weight=1,
            created_at=now,
            updated_at=now,
            last_seen_at=now,
            hash_updated_at=now,
        )
        delete_record = DeviceFile(
            device_id="device-1",
            absolute_path=str(delete_candidate_path),
            last_known_path=str(delete_candidate_path),
            content_hash=delete_hash,
            hash_algorithm="sha256",
            file_size=int(delete_candidate_path.stat().st_size),
            match_status="matched",
            weight=0,
            created_at=now,
            updated_at=now,
            last_seen_at=now,
            hash_updated_at=now,
        )
        promote_item = MediaSyncSourceItem(
            user_id=user.id,
            platform=platform,
            source_kind="related",
            collection_url=f"related://{platform}",
            remote_id=f"{platform}-keep",
            media_index=0,
            remote_url=f"https://example.com/{platform}/keep",
            media_url=f"https://example.com/{platform}/keep.jpg",
            media_type="image",
            relative_path=promote_relative_path.as_posix(),
            absolute_path=str(promote_candidate_path),
            device_id="device-1",
            content_hash=promote_hash,
            hash_algorithm="sha256",
            file_size=int(promote_candidate_path.stat().st_size),
            downloaded_at=now,
            first_seen_at=now,
            updated_at=now,
        )
        delete_item = MediaSyncSourceItem(
            user_id=user.id,
            platform=platform,
            source_kind="related",
            collection_url=f"related://{platform}",
            remote_id=f"{platform}-drop",
            media_index=0,
            remote_url=f"https://example.com/{platform}/drop",
            media_url=f"https://example.com/{platform}/drop.jpg",
            media_type="image",
            relative_path=delete_relative_path.as_posix(),
            absolute_path=str(delete_candidate_path),
            device_id="device-1",
            content_hash=delete_hash,
            hash_algorithm="sha256",
            file_size=int(delete_candidate_path.stat().st_size),
            downloaded_at=now,
            first_seen_at=now,
            updated_at=now,
        )
        session.add(promote_record)
        session.add(delete_record)
        session.add(promote_item)
        session.add(delete_item)
        session.commit()
        session.refresh(promote_record)
        session.refresh(delete_record)
        session.refresh(promote_item)
        session.refresh(delete_item)
        user_id = user.id
        promote_record_id = promote_record.id
        delete_record_id = delete_record.id
        promote_item_id = promote_item.id
        delete_item_id = delete_item.id

    logs: list[str] = []
    summary = media_sync_sources.run_candidate_curation(
        user_id=user_id,
        root_dir=str(root_dir),
        platform=platform,
        log=logs.append,
    )

    promote_formal_path = formal_root / promote_relative_path

    assert summary["platform"] == platform
    assert summary["reviewed_count"] == 2
    assert summary["promote_ready_count"] == 1
    assert summary["delete_ready_count"] == 1
    assert summary["promoted_count"] == 1
    assert summary["deleted_count"] == 1
    assert summary["error_count"] == 0
    assert any("候选收编开始" in row for row in logs)
    assert any("候选收编完成" in row for row in logs)

    assert promote_formal_path.exists()
    assert promote_formal_path.read_bytes() == b"candidate-keep"
    assert not promote_candidate_path.exists()
    assert not delete_candidate_path.exists()

    with Session(engine) as session:
        promote_record = session.get(DeviceFile, promote_record_id)
        delete_record = session.get(DeviceFile, delete_record_id)
        promote_item = session.get(MediaSyncSourceItem, promote_item_id)
        delete_item = session.get(MediaSyncSourceItem, delete_item_id)

        assert promote_record is not None
        assert promote_record.absolute_path == str(promote_formal_path)
        assert promote_record.last_known_path == str(promote_formal_path)
        assert promote_record.match_status == "matched"
        assert promote_record.weight == 1

        assert delete_record is not None
        assert delete_record.absolute_path is None
        assert delete_record.last_known_path == str(delete_candidate_path)
        assert delete_record.match_status == "dangling"
        assert delete_record.weight == 0

        assert promote_item is not None
        assert promote_item.absolute_path == str(promote_formal_path)
        assert promote_item.relative_path == promote_relative_path.as_posix()
        assert promote_item.device_id == "device-1"
        assert promote_item.content_hash == promote_hash

        assert delete_item is not None
        assert delete_item.absolute_path is None
        assert delete_item.relative_path == delete_relative_path.as_posix()
        assert delete_item.device_id == "device-1"
        assert delete_item.content_hash == delete_hash

        active_records = session.exec(
            select(DeviceFile).where(
                DeviceFile.device_id == "device-1",
                DeviceFile.absolute_path.is_not(None),
            )
        ).all()
        assert [row.absolute_path for row in active_records] == [str(promote_formal_path)]
