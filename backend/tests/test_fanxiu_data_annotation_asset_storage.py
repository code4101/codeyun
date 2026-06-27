import base64
import json

import pytest

from backend.core.fanxiu.data_annotation.storage import (
    save_data_annotation_asset_tree_bundle,
    save_data_annotation_image_bytes,
)


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def _png_data_url() -> str:
    return "data:image/png;base64," + base64.b64encode(_PNG_1X1).decode("ascii")


def test_save_asset_tree_bundle_materializes_image_data_url(tmp_path, monkeypatch):
    import backend.core.fanxiu.data_annotation.storage as storage

    monkeypatch.setattr(storage, "fanxiu_data_annotation_dir", lambda: tmp_path)
    path = storage.data_annotation_asset_tree_path("entry-a")
    tree = [
        {
            "id": "image-279",
            "type": "image",
            "title": "0279.png",
            "filename": "0279.png",
            "imageDataUrl": _png_data_url(),
            "width": 1,
            "height": 1,
            "shapes": [],
        }
    ]

    normalized = save_data_annotation_asset_tree_bundle(path, tree, entry_id="entry-a")

    image_path = tmp_path / "entries" / "entry-a" / "images" / "0279.png"
    assert image_path.read_bytes() == _PNG_1X1
    assert normalized[0]["filename"] == "0279.png"
    assert "imageDataUrl" not in normalized[0]
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert "imageDataUrl" not in persisted[0]


def test_save_asset_tree_bundle_rejects_missing_image_reference(tmp_path, monkeypatch):
    import backend.core.fanxiu.data_annotation.storage as storage

    monkeypatch.setattr(storage, "fanxiu_data_annotation_dir", lambda: tmp_path)
    path = storage.data_annotation_asset_tree_path("entry-a")
    tree = [
        {
            "id": "image-279",
            "type": "image",
            "title": "洞天福地",
            "filename": "0279.png",
            "width": 900,
            "height": 1600,
            "shapes": [],
        }
    ]

    with pytest.raises(FileNotFoundError, match="0279.png"):
        save_data_annotation_asset_tree_bundle(path, tree, entry_id="entry-a")

    assert not path.exists()


def test_save_data_annotation_image_bytes_allocates_from_entry_state(tmp_path, monkeypatch):
    import backend.core.fanxiu.data_annotation.storage as storage

    monkeypatch.setattr(storage, "fanxiu_data_annotation_dir", lambda: tmp_path)
    entry_id = "entry-a"
    image_dir = storage.data_annotation_entry_image_dir(entry_id)
    image_dir.mkdir(parents=True)
    (image_dir / "0007.png").write_bytes(_PNG_1X1)
    path = storage.data_annotation_asset_tree_path(entry_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([
            {"type": "image", "filename": "0009.png", "children": []},
        ]),
        encoding="utf-8",
    )

    asset = save_data_annotation_image_bytes(_PNG_1X1, entry_id=entry_id)

    assert asset.filename == "0010.png"
    assert asset.path.read_bytes() == _PNG_1X1
    assert asset.path.parent == image_dir
