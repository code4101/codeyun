from types import SimpleNamespace

from backend.core import storage as storage_module


def test_migrate_legacy_attachments_moves_files_to_data_dir(monkeypatch, tmp_path):
    legacy_dir = tmp_path / "backend-static" / "uploads"
    attachments_dir = tmp_path / "data" / "attachments"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "a.png").write_bytes(b"a")
    (legacy_dir / "b.jpg").write_bytes(b"b")

    monkeypatch.setattr(storage_module, "LEGACY_UPLOADS_DIR", legacy_dir)
    monkeypatch.setattr(
        storage_module,
        "get_settings",
        lambda: SimpleNamespace(attachments_dir=attachments_dir, data_dir=tmp_path / "data"),
    )

    moved_count = storage_module.migrate_legacy_attachments()

    assert moved_count == 2
    assert (attachments_dir / "a.png").read_bytes() == b"a"
    assert (attachments_dir / "b.jpg").read_bytes() == b"b"
    assert not (legacy_dir / "a.png").exists()
    assert not (legacy_dir / "b.jpg").exists()


def test_migrate_legacy_attachments_keeps_existing_target_files(monkeypatch, tmp_path):
    legacy_dir = tmp_path / "backend-static" / "uploads"
    attachments_dir = tmp_path / "data" / "attachments"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    attachments_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "same.png").write_bytes(b"legacy")
    (legacy_dir / "new.png").write_bytes(b"new")
    (attachments_dir / "same.png").write_bytes(b"current")

    monkeypatch.setattr(storage_module, "LEGACY_UPLOADS_DIR", legacy_dir)
    monkeypatch.setattr(
        storage_module,
        "get_settings",
        lambda: SimpleNamespace(attachments_dir=attachments_dir, data_dir=tmp_path / "data"),
    )

    moved_count = storage_module.migrate_legacy_attachments()

    assert moved_count == 1
    assert (attachments_dir / "same.png").read_bytes() == b"current"
    assert (attachments_dir / "new.png").read_bytes() == b"new"
    assert (legacy_dir / "same.png").read_bytes() == b"legacy"
