from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.core.fanxiu.data_annotation import storage


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    root = tmp_path / "data" / "workspace" / "codepc_test"
    monkeypatch.setattr(storage, "get_settings", lambda: SimpleNamespace(data_dir=root))
    return root


def test_resolve_data_annotation_image_prefers_entry_image(data_root):
    entry_id = "entry-a"
    image_path = data_root / "fanxiu" / "data-annotation" / "entries" / entry_id / "images" / "0034.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")

    asset = storage.resolve_data_annotation_image_asset("0034.png", entry_id=entry_id)

    assert asset.exists is True
    assert asset.storage_kind == "entry_images"
    assert asset.path == image_path
    assert asset.sidecar_path == image_path.with_suffix(".json")


def test_resolve_data_annotation_image_returns_preferred_missing_path(data_root):
    asset = storage.resolve_data_annotation_image_asset("0034.png", entry_id="entry-a")

    assert asset.exists is False
    assert asset.storage_kind == "missing"
    assert asset.path == data_root / "fanxiu" / "data-annotation" / "entries" / "entry-a" / "images" / "0034.png"
    assert asset.sidecar_path == asset.path.with_suffix(".json")


@pytest.mark.parametrize("filename", ["../0034.png", "0034.gif", ""])
def test_resolve_data_annotation_image_rejects_invalid_filename(data_root, filename):
    with pytest.raises(ValueError):
        storage.resolve_data_annotation_image_asset(filename, entry_id="entry-a")


def test_data_annotation_asset_tree_lives_in_entry_dir(data_root):
    path = storage.data_annotation_asset_tree_path(" bad/id ")

    assert path == data_root / "fanxiu" / "data-annotation" / "entries" / "bad_id" / "asset-tree.json"
