from pathlib import Path

from PIL import Image
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from backend.api import filesystem as filesystem_api
from backend.core.devices import device as device_core
from backend.models import DeviceFile, ResourceIdentity


def _create_device_file_metadata_tables(engine) -> None:
    ResourceIdentity.__table__.create(engine, checkfirst=True)
    DeviceFile.__table__.create(engine, checkfirst=True)


def _build_media_entry(
    *,
    item_id: str,
    modified_at: int,
    content_hash: str | None = None,
    visual_hash: str | None = None,
) -> dict:
    return {
        "id": item_id,
        "name": f"{item_id}.png",
        "relative_path": f"{item_id}.png",
        "folder_path": "",
        "modified_at": modified_at,
        "size": 1,
        "kind": "image",
        "mime_type": "image/png",
        "weight": 0,
        "width": 100,
        "height": 100,
        "content_hash": content_hash,
        "visual_hash": visual_hash,
    }


def test_sort_supported_media_entries_clusters_exact_and_visual_duplicates():
    entries = [
        _build_media_entry(
            item_id="anchor",
            modified_at=400,
            content_hash="hash-shared",
            visual_hash="0000000000000000",
        ),
        _build_media_entry(
            item_id="unique",
            modified_at=350,
            content_hash="hash-unique",
            visual_hash="f000000000000000",
        ),
        _build_media_entry(
            item_id="exact-dup",
            modified_at=300,
            content_hash="hash-shared",
            visual_hash="ffffffffffffffff",
        ),
        _build_media_entry(
            item_id="near-dup",
            modified_at=200,
            visual_hash="0000000000000001",
        ),
    ]

    rules = filesystem_api.GallerySortProgram(
        rules=[
            filesystem_api.GallerySortRule(field="duplicate_cluster", direction="asc", nulls="last"),
            filesystem_api.GallerySortRule(field="modified_at", direction="desc", nulls="last"),
        ]
    )

    filesystem_api._sort_supported_media_entries(entries, "path", rules)

    assert [entry["id"] for entry in entries] == [
        "anchor",
        "exact-dup",
        "near-dup",
        "unique",
    ]
    assert [entry["duplicate_cluster_order"] for entry in entries] == [0, 0, 0, 1]
    assert [entry["duplicate_cluster_distance"] for entry in entries] == [0, 0, 1, 0]
    assert [entry["duplicate_cluster_size"] for entry in entries] == [3, 3, 3, 1]


def test_media_listing_duplicate_cluster_only_reorders_within_current_page():
    entries = [
        _build_media_entry(
            item_id="anchor",
            modified_at=400,
            content_hash="hash-shared",
            visual_hash="0000000000000000",
        ),
        _build_media_entry(
            item_id="unique-a",
            modified_at=350,
            content_hash="hash-unique-a",
            visual_hash="f000000000000000",
        ),
        _build_media_entry(
            item_id="exact-dup",
            modified_at=300,
            content_hash="hash-shared",
            visual_hash="ffffffffffffffff",
        ),
        _build_media_entry(
            item_id="unique-b",
            modified_at=250,
            content_hash="hash-unique-b",
            visual_hash="0f00000000000000",
        ),
    ]

    rules = filesystem_api.GallerySortProgram(
        rules=[
            filesystem_api.GallerySortRule(field="duplicate_cluster", direction="asc", nulls="last"),
            filesystem_api.GallerySortRule(field="modified_at", direction="desc", nulls="last"),
        ]
    )

    normalized_rules = filesystem_api._prepare_media_listing_snapshot_entries(entries, "path", rules)
    assert [entry["id"] for entry in entries] == [
        "anchor",
        "unique-a",
        "exact-dup",
        "unique-b",
    ]

    first_page, total_count, next_offset = filesystem_api._slice_media_listing_entries(entries, offset=0, limit=2)
    filesystem_api._apply_duplicate_cluster_sort_to_entries(first_page, normalized_rules)

    assert total_count == 4
    assert next_offset == 2
    assert [entry["id"] for entry in first_page] == ["anchor", "unique-a"]
    assert [entry["duplicate_cluster_size"] for entry in first_page] == [1, 1]

    second_page, _, second_next_offset = filesystem_api._slice_media_listing_entries(entries, offset=2, limit=2)
    filesystem_api._apply_duplicate_cluster_sort_to_entries(second_page, normalized_rules)

    assert second_next_offset is None
    assert [entry["id"] for entry in second_page] == ["exact-dup", "unique-b"]
    assert [entry["duplicate_cluster_size"] for entry in second_page] == [1, 1]


def test_attach_cached_media_metadata_persists_visual_hash(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _create_device_file_metadata_tables(engine)

    image_path = tmp_path / "cluster.png"
    Image.new("RGB", (16, 16), color=(240, 120, 40)).save(image_path)
    identity_path = image_path.resolve(strict=False)
    stat_result = image_path.stat()

    entries = [
        {
            "id": "cluster.png",
            "name": "cluster.png",
            "path": "cluster.png",
            "absolute_path": str(identity_path),
            "relative_path": "cluster.png",
            "folder_path": "",
            "size": stat_result.st_size,
            "created_at": int(stat_result.st_ctime * 1000),
            "modified_at": int(stat_result.st_mtime * 1000),
            "kind": "image",
            "mime_type": "image/png",
            "_absolute_identity_path": str(identity_path),
            "_file_path": Path(image_path),
        }
    ]

    monkeypatch.setattr(device_core, "get_device_id", lambda: "device-test")

    with Session(engine) as session:
        status = filesystem_api._attach_cached_media_metadata(entries, session, include_visual_hash=True)
        record = session.exec(
            select(DeviceFile).where(
                DeviceFile.device_id == "device-test",
                DeviceFile.absolute_path == str(identity_path),
            )
        ).first()

    assert entries[0]["visual_hash"]
    assert entries[0]["visual_hash_algorithm"] == "dhash-8"
    assert record is not None
    assert record.visual_hash == entries[0]["visual_hash"]
    assert record.visual_hash_algorithm == "dhash-8"
    assert status["requested"] is True
    assert status["indexed_count"] == 1
    assert status["missing_count"] == 0
    assert status["computed_count"] == 1
    assert status["complete"] is True


def test_attach_cached_media_metadata_reuses_visual_hash_from_matching_content_hash(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _create_device_file_metadata_tables(engine)

    image_path = tmp_path / "reused.png"
    Image.new("RGB", (24, 24), color=(20, 120, 220)).save(image_path)
    identity_path = image_path.resolve(strict=False)
    stat_result = image_path.stat()
    donor_visual_hash = "abcdef0123456789"

    with Session(engine) as session:
        session.add(
            DeviceFile(
                device_id="device-test",
                absolute_path=str(identity_path),
                last_known_path=str(identity_path),
                content_hash="shared-content-hash",
                hash_algorithm="sha256",
                file_size=stat_result.st_size,
                modified_at_ms=int(stat_result.st_mtime * 1000),
                media_kind="image",
                mime_type="image/png",
            )
        )
        session.add(
            DeviceFile(
                device_id="device-test",
                absolute_path=str(tmp_path / "donor.png"),
                last_known_path=str(tmp_path / "donor.png"),
                content_hash="shared-content-hash",
                hash_algorithm="sha256",
                visual_hash=donor_visual_hash,
                visual_hash_algorithm="dhash-8",
                file_size=1,
                modified_at_ms=1,
                media_kind="image",
                mime_type="image/png",
            )
        )
        session.commit()

    entries = [
        {
            "id": "reused.png",
            "name": "reused.png",
            "path": "reused.png",
            "absolute_path": str(identity_path),
            "relative_path": "reused.png",
            "folder_path": "",
            "size": stat_result.st_size,
            "created_at": int(stat_result.st_ctime * 1000),
            "modified_at": int(stat_result.st_mtime * 1000),
            "kind": "image",
            "mime_type": "image/png",
            "_absolute_identity_path": str(identity_path),
            "_file_path": Path(image_path),
        }
    ]

    monkeypatch.setattr(device_core, "get_device_id", lambda: "device-test")
    monkeypatch.setattr(
        filesystem_api,
        "_compute_image_dhash",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should reuse cached visual hash")),
    )

    with Session(engine) as session:
        status = filesystem_api._attach_cached_media_metadata(entries, session, include_visual_hash=True)

    assert entries[0]["content_hash"] == "shared-content-hash"
    assert entries[0]["visual_hash"] == donor_visual_hash
    assert entries[0]["visual_hash_algorithm"] == "dhash-8"
    assert status["requested"] is True
    assert status["indexed_count"] == 1
    assert status["reused_content_hash_count"] == 1
    assert status["computed_count"] == 0
    assert status["complete"] is True


def test_attach_cached_media_metadata_schedules_visual_hash_prewarm_for_browse_requests(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _create_device_file_metadata_tables(engine)

    image_path = tmp_path / "browse.png"
    Image.new("RGB", (20, 20), color=(180, 40, 120)).save(image_path)
    identity_path = image_path.resolve(strict=False)
    stat_result = image_path.stat()

    entries = [
        {
            "id": "browse.png",
            "name": "browse.png",
            "path": "browse.png",
            "absolute_path": str(identity_path),
            "relative_path": "browse.png",
            "folder_path": "",
            "size": stat_result.st_size,
            "created_at": int(stat_result.st_ctime * 1000),
            "modified_at": int(stat_result.st_mtime * 1000),
            "kind": "image",
            "mime_type": "image/png",
            "_absolute_identity_path": str(identity_path),
            "_file_path": Path(image_path),
        }
    ]

    scheduled: dict[str, object] = {}

    monkeypatch.setattr(device_core, "get_device_id", lambda: "device-test")
    monkeypatch.setattr(
        filesystem_api,
        "_schedule_visual_hash_prewarm",
        lambda prewarm_key, device_id, candidates: scheduled.update({
            "prewarm_key": prewarm_key,
            "device_id": device_id,
            "candidates": candidates,
        }),
    )

    with Session(engine) as session:
        status = filesystem_api._attach_cached_media_metadata(
            entries,
            session,
            include_visual_hash=False,
            prewarm_visual_hash_key="browse:device-test",
        )

    assert entries[0]["visual_hash"] is None
    assert scheduled["prewarm_key"] == "browse:device-test"
    assert scheduled["device_id"] == "device-test"
    candidates = scheduled["candidates"]
    assert isinstance(candidates, list)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.absolute_path == str(identity_path)
    assert candidate.modified_at_ms == int(stat_result.st_mtime * 1000)
    assert status["requested"] is False
    assert status["indexed_count"] == 0
    assert status["missing_count"] == 1
    assert status["prewarm_scheduled_count"] == 1


def test_list_supported_entries_does_not_prewarm_visual_hash_without_duplicate_cluster_rule(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _create_device_file_metadata_tables(engine)

    image_path = tmp_path / "plain-browse.png"
    Image.new("RGB", (20, 20), color=(40, 120, 180)).save(image_path)

    scheduled: list[tuple[str | None, str, list[object]]] = []

    monkeypatch.setattr(device_core, "get_device_id", lambda: "device-test")
    monkeypatch.setattr(
        filesystem_api,
        "_schedule_visual_hash_prewarm",
        lambda prewarm_key, device_id, candidates: scheduled.append((prewarm_key, device_id, list(candidates))),
    )

    with Session(engine) as session:
        result = filesystem_api._list_supported_entries(
            absolute_path=str(tmp_path),
            allowed_kinds={"image"},
            response_key="media",
            session=session,
            sort_program=filesystem_api.GallerySortProgram(
                rules=[filesystem_api.GallerySortRule(field="weight", direction="desc", nulls="last")]
            ),
        )

    assert result["visual_hash_status"]["requested"] is False
    assert result["visual_hash_status"]["prewarm_scheduled_count"] == 0
    assert scheduled == []


def test_list_supported_entries_reports_scan_index_response_progress(tmp_path, monkeypatch):
    image_path = tmp_path / "progress.png"
    Image.new("RGB", (12, 12), color=(40, 80, 120)).save(image_path)

    events: list[dict] = []

    monkeypatch.setattr(device_core, "get_device_id", lambda: "device-test")

    result = filesystem_api._list_supported_entries(
        absolute_path=str(tmp_path),
        allowed_kinds={"image"},
        response_key="media",
        progress_callback=events.append,
    )

    assert result["total_count"] == 1
    assert [event["stage"] for event in events] == ["scanning", "scanning", "indexing", "responding"]
    assert events[0] == {
        "stage": "scanning",
        "message": "正在扫描媒体文件",
        "progress_current": 0,
        "progress_total": filesystem_api.DEFAULT_MEDIA_SCAN_LIMIT,
    }
    assert events[1]["progress_current"] == 1
    assert events[2]["message"] == "正在整理 1 个媒体文件"
    assert events[3]["message"] == "正在生成媒体列表，共 1 项"


def test_list_media_entries_includes_pdf_documents(tmp_path):
    pdf_path = tmp_path / "manual.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n")
    (tmp_path / "notes.txt").write_text("plain text", encoding="utf-8")

    result = filesystem_api.list_media_entries(absolute_path=str(tmp_path))

    assert result["total_count"] == 1
    [entry] = result["media"]
    assert entry["name"] == "manual.pdf"
    assert entry["kind"] == "pdf"
    assert entry["mime_type"] == "application/pdf"
    assert entry["width"] is None
    assert entry["height"] is None


def test_list_supported_entries_prewarms_visual_hash_when_duplicate_cluster_rule_active(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _create_device_file_metadata_tables(engine)

    image_path = tmp_path / "cluster-browse.png"
    Image.new("RGB", (20, 20), color=(120, 40, 180)).save(image_path)

    scheduled: list[tuple[str | None, str, list[object]]] = []

    monkeypatch.setattr(device_core, "get_device_id", lambda: "device-test")
    monkeypatch.setattr(
        filesystem_api,
        "_schedule_visual_hash_prewarm",
        lambda prewarm_key, device_id, candidates: scheduled.append((prewarm_key, device_id, list(candidates))),
    )

    with Session(engine) as session:
        result = filesystem_api._list_supported_entries(
            absolute_path=str(tmp_path),
            allowed_kinds={"image"},
            response_key="media",
            session=session,
            sort_program=filesystem_api.GallerySortProgram(
                rules=[
                    filesystem_api.GallerySortRule(field="duplicate_cluster", direction="asc", nulls="last"),
                    filesystem_api.GallerySortRule(field="weight", direction="desc", nulls="last"),
                ]
            ),
        )

    assert result["visual_hash_status"]["requested"] is True
    assert result["visual_hash_status"]["indexed_count"] == 1
    assert len(scheduled) == 1
    prewarm_key, device_id, candidates = scheduled[0]
    assert isinstance(prewarm_key, str)
    assert prewarm_key
    assert device_id == "device-test"
    assert len(candidates) == 1
