import base64
import json

import pytest

from backend.core.fanxiu.data_annotation.storage import (
    FanxiuDataAnnotationAssetTreeConflict,
    read_data_annotation_asset_tree_snapshot,
    save_data_annotation_asset_tree_bundle,
    save_data_annotation_asset_tree_snapshot,
    save_data_annotation_frame_tree_node,
    save_data_annotation_image_bytes,
    update_data_annotation_asset_tree,
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


def test_save_asset_tree_bundle_clears_filename_only_image_title(tmp_path, monkeypatch):
    import backend.core.fanxiu.data_annotation.storage as storage

    monkeypatch.setattr(storage, "fanxiu_data_annotation_dir", lambda: tmp_path)
    path = storage.data_annotation_asset_tree_path("entry-a")
    tree = [{
        "id": "image-377",
        "type": "image",
        "title": " 0377.PNG ",
        "filename": "0377.png",
        "imageDataUrl": _png_data_url(),
        "shapes": [],
    }, {
        "id": "image-378",
        "type": "image",
        "title": "道法争锋",
        "filename": "0378.png",
        "imageDataUrl": _png_data_url(),
        "shapes": [],
    }]

    normalized = save_data_annotation_asset_tree_bundle(path, tree, entry_id="entry-a")

    assert normalized[0]["title"] == ""
    assert normalized[1]["title"] == "道法争锋"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert [node["title"] for node in persisted] == ["", "道法争锋"]


def test_save_asset_tree_bundle_corrects_mismatched_jpeg_suffix(tmp_path, monkeypatch):
    import backend.core.fanxiu.data_annotation.storage as storage

    monkeypatch.setattr(storage, "fanxiu_data_annotation_dir", lambda: tmp_path)
    path = storage.data_annotation_asset_tree_path("entry-a")
    tree = [{
        "id": "image-30",
        "type": "image",
        "title": "有红包的群",
        "filename": "0030.jpg",
        "imageDataUrl": _png_data_url(),
        "shapes": [],
    }]

    normalized = save_data_annotation_asset_tree_bundle(path, tree, entry_id="entry-a")

    assert normalized[0]["filename"] == "0030.png"
    assert (tmp_path / "entries" / "entry-a" / "images" / "0030.png").read_bytes() == _PNG_1X1


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

    before_write_calls = []
    with pytest.raises(FileNotFoundError, match="0279.png"):
        save_data_annotation_asset_tree_bundle(
            path,
            tree,
            entry_id="entry-a",
            before_write=lambda: before_write_calls.append(True),
        )

    assert not path.exists()
    assert before_write_calls == []


def test_save_asset_tree_bundle_calls_before_write_after_validation(tmp_path, monkeypatch):
    import backend.core.fanxiu.data_annotation.storage as storage

    monkeypatch.setattr(storage, "fanxiu_data_annotation_dir", lambda: tmp_path)
    path = storage.data_annotation_asset_tree_path("entry-a")
    calls = []

    save_data_annotation_asset_tree_bundle(
        path,
        [{
            "id": "image-279",
            "type": "image",
            "filename": "0279.png",
            "imageDataUrl": _png_data_url(),
            "shapes": [],
        }],
        entry_id="entry-a",
        before_write=lambda: calls.append(path.exists()),
    )

    assert calls == [False]
    assert path.is_file()


def test_save_asset_tree_bundle_migrates_shape_load_direction_fields(tmp_path, monkeypatch):
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
            "shapes": [
                {
                    "id": "list",
                    "contentDirection": "down",
                    "load_mode": "paged",
                    "load_boundary": "cyclic",
                    "load_initial_position": "unknown",
                    "children": [{
                        "id": "child",
                        "内容方向": "右",
                        "loadMode": "continuous",
                        "loadBoundary": "bounded",
                        "loadInitialPosition": "start",
                    }],
                }
            ],
        }
    ]

    normalized = save_data_annotation_asset_tree_bundle(path, tree, entry_id="entry-a")

    shape = normalized[0]["shapes"][0]
    assert shape["loadDirection"] == "down"
    assert shape["loadMode"] == "paged"
    assert shape["loadBoundary"] == "cyclic"
    assert shape["loadInitialPosition"] == "unknown"
    assert "contentDirection" not in shape
    assert shape["children"][0]["loadDirection"] == "right"
    assert "loadMode" not in shape["children"][0]
    assert "loadBoundary" not in shape["children"][0]
    assert "loadInitialPosition" not in shape["children"][0]
    assert "内容方向" not in shape["children"][0]


def test_save_asset_tree_bundle_removes_retired_recognition_parent_field(tmp_path, monkeypatch):
    import backend.core.fanxiu.data_annotation.storage as storage

    monkeypatch.setattr(storage, "fanxiu_data_annotation_dir", lambda: tmp_path)
    path = storage.data_annotation_asset_tree_path("entry-a")
    tree = [{
        "id": "image-279",
        "type": "image",
        "title": "0279.png",
        "filename": "0279.png",
        "imageDataUrl": _png_data_url(),
        "recognitionParentId": 34,
        "shapes": [],
    }]

    normalized = save_data_annotation_asset_tree_bundle(path, tree, entry_id="entry-a")

    assert "recognitionParentId" not in normalized[0]
    assert "recognitionParentId" not in json.loads(path.read_text(encoding="utf-8"))[0]


def test_save_asset_tree_bundle_preserves_normalized_scene_parent_ids(tmp_path, monkeypatch):
    import backend.core.fanxiu.data_annotation.storage as storage

    monkeypatch.setattr(storage, "fanxiu_data_annotation_dir", lambda: tmp_path)
    path = storage.data_annotation_asset_tree_path("entry-a")
    tree = [{
        "id": "image-74",
        "type": "image",
        "title": "0074.png",
        "filename": "0074.png",
        "imageDataUrl": _png_data_url(),
        "parentSceneIds": " #34，18,34,74,abc ",
        "shapes": [],
    }]

    normalized = save_data_annotation_asset_tree_bundle(path, tree, entry_id="entry-a")

    assert normalized[0]["parentSceneIds"] == "34,18"
    assert json.loads(path.read_text(encoding="utf-8"))[0]["parentSceneIds"] == "34,18"


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


def test_save_data_annotation_image_bytes_uses_actual_png_suffix_when_resetting_jpeg(tmp_path, monkeypatch):
    import backend.core.fanxiu.data_annotation.storage as storage

    monkeypatch.setattr(storage, "fanxiu_data_annotation_dir", lambda: tmp_path)

    asset = save_data_annotation_image_bytes(_PNG_1X1, entry_id="entry-a", filename="0030.jpg")

    assert asset.filename == "0030.png"
    assert asset.path.name == "0030.png"
    assert asset.path.read_bytes() == _PNG_1X1
    assert not (asset.path.parent / "0030.jpg").exists()


def test_asset_tree_snapshot_uses_content_revision_for_conflicts(tmp_path):
    path = tmp_path / "asset-tree.json"
    initial = save_data_annotation_asset_tree_snapshot(path, [{"id": "folder", "type": "folder", "title": "旧", "children": []}])
    saved = save_data_annotation_asset_tree_snapshot(
        path,
        [{"id": "folder", "type": "folder", "title": "新", "children": []}],
        expected_revision=initial.revision,
    )

    assert saved.revision != initial.revision
    with pytest.raises(FanxiuDataAnnotationAssetTreeConflict):
        save_data_annotation_asset_tree_snapshot(path, initial.tree, expected_revision=initial.revision)


def test_asset_tree_semantic_update_preserves_unrelated_latest_fields(tmp_path):
    path = tmp_path / "asset-tree.json"
    save_data_annotation_asset_tree_snapshot(
        path,
        [{
            "id": "image-1",
            "type": "image",
            "title": "用户刚改的标题",
            "shapes": [{"id": "shape-1", "title": "按钮", "sceneJumpTarget": "2"}],
        }],
    )

    def update_jump(tree):
        tree[0]["shapes"][0]["sceneJumpTarget"] = "2(3)"
        return True

    update_data_annotation_asset_tree(path, update_jump)
    snapshot = read_data_annotation_asset_tree_snapshot(path)

    assert snapshot.tree[0]["title"] == "用户刚改的标题"
    assert snapshot.tree[0]["shapes"][0]["sceneJumpTarget"] == "2(3)"


def test_frame_tree_save_replays_unique_node_on_latest_revision(tmp_path, monkeypatch):
    import backend.core.fanxiu.data_annotation.storage as storage

    monkeypatch.setattr(storage, "fanxiu_data_annotation_dir", lambda: tmp_path)
    path = storage.data_annotation_asset_tree_path("entry-a")
    initial = save_data_annotation_asset_tree_snapshot(
        path,
        [{"id": "folder-a", "type": "folder", "title": "目录", "children": []}],
    )
    save_data_annotation_asset_tree_snapshot(
        path,
        [
            {"id": "folder-a", "type": "folder", "title": "另一页面改名", "children": []},
            {"id": "folder-b", "type": "folder", "title": "并发新增", "children": []},
        ],
        expected_revision=initial.revision,
    )

    saved = save_data_annotation_frame_tree_node(
        path,
        _PNG_1X1,
        {"id": "image-new", "type": "image", "title": "", "shapes": []},
        entry_id="entry-a",
        parent_id="folder-a",
        expected_revision=initial.revision,
    )

    assert [node["id"] for node in saved.snapshot.tree] == ["folder-a", "folder-b"]
    assert saved.snapshot.tree[0]["title"] == "另一页面改名"
    assert saved.snapshot.tree[0]["children"][0]["id"] == "image-new"
    assert saved.asset.path.is_file()


@pytest.mark.parametrize("conflict_kind", ["duplicate", "missing-anchor"])
def test_frame_tree_save_conflict_writes_neither_node_nor_image(tmp_path, monkeypatch, conflict_kind):
    import backend.core.fanxiu.data_annotation.storage as storage

    monkeypatch.setattr(storage, "fanxiu_data_annotation_dir", lambda: tmp_path)
    path = storage.data_annotation_asset_tree_path("entry-a")
    initial = save_data_annotation_asset_tree_snapshot(
        path,
        [{"id": "image-existing", "type": "image", "title": "旧", "shapes": []}],
    )
    node_id = "image-existing" if conflict_kind == "duplicate" else "image-new"

    with pytest.raises(FanxiuDataAnnotationAssetTreeConflict):
        save_data_annotation_frame_tree_node(
            path,
            _PNG_1X1,
            {"id": node_id, "type": "image", "title": "新", "shapes": []},
            entry_id="entry-a",
            after_node_id="missing" if conflict_kind == "missing-anchor" else None,
            expected_revision=initial.revision,
        )

    assert read_data_annotation_asset_tree_snapshot(path) == initial
    image_dir = storage.data_annotation_entry_image_dir("entry-a")
    assert not image_dir.exists() or not list(image_dir.iterdir())


def test_frame_tree_save_rolls_back_image_when_tree_write_fails(tmp_path, monkeypatch):
    import backend.core.fanxiu.data_annotation.storage as storage

    monkeypatch.setattr(storage, "fanxiu_data_annotation_dir", lambda: tmp_path)
    path = storage.data_annotation_asset_tree_path("entry-a")
    initial = save_data_annotation_asset_tree_snapshot(path, [])
    monkeypatch.setattr(storage, "write_data_annotation_json", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(OSError, match="disk full"):
        save_data_annotation_frame_tree_node(
            path,
            _PNG_1X1,
            {"id": "image-new", "type": "image", "title": "新", "shapes": []},
            entry_id="entry-a",
            expected_revision=initial.revision,
        )

    assert read_data_annotation_asset_tree_snapshot(path) == initial
    image_dir = storage.data_annotation_entry_image_dir("entry-a")
    assert not image_dir.exists() or not list(image_dir.iterdir())


def test_frame_tree_save_rejects_non_sequential_explicit_scene_number(tmp_path, monkeypatch):
    import backend.core.fanxiu.data_annotation.storage as storage

    monkeypatch.setattr(storage, "fanxiu_data_annotation_dir", lambda: tmp_path)
    path = storage.data_annotation_asset_tree_path("entry-a")
    initial = save_data_annotation_asset_tree_snapshot(path, [])

    with pytest.raises(ValueError, match=r"当前应为 #1，不能创建 #600"):
        save_data_annotation_frame_tree_node(
            path,
            _PNG_1X1,
            {
                "id": "image-jumped",
                "type": "image",
                "title": "跳号场景",
                "filename": "0600.png",
                "shapes": [],
            },
            entry_id="entry-a",
            expected_revision=initial.revision,
        )

    assert read_data_annotation_asset_tree_snapshot(path) == initial
    assert not storage.resolve_data_annotation_image_asset(
        "0600.png", entry_id="entry-a"
    ).exists


def test_next_scene_number_counts_business_prefixed_runtime_scene_ids(
    tmp_path,
    monkeypatch,
):
    import backend.core.fanxiu.data_annotation.storage as storage

    monkeypatch.setattr(storage, "fanxiu_data_annotation_dir", lambda: tmp_path)
    path = storage.data_annotation_asset_tree_path("entry-a")
    save_data_annotation_image_bytes(
        _PNG_1X1,
        entry_id="entry-a",
        filename="lingxiao-preview-580.png",
    )
    save_data_annotation_asset_tree_snapshot(
        path,
        [
            {
                "id": "image-prefixed",
                "type": "image",
                "title": "业务前缀场景",
                "filename": "lingxiao-preview-580.png",
                "shapes": [],
            }
        ],
        entry_id="entry-a",
    )

    assert storage.next_data_annotation_image_filename("entry-a") == "0581.png"
