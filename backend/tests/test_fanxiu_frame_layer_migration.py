import json

from backend.core.fanxiu.data_annotation.frame_layer_migration import (
    migrate_frame_layers_file,
    migrate_frame_layers_in_tree,
)


def test_frame_layer_migration_infers_legacy_scopes_without_touching_shapes():
    tree = [
        {
            "type": "folder",
            "title": "日常",
            "children": [
                {
                    "type": "image",
                    "filename": "0266.png",
                    "title": "旧局部",
                    "shapes": [
                        {"title": "法则之主", "isSceneIdentity": True, "sceneIdentityScope": "local"},
                    ],
                },
                {
                    "type": "image",
                    "filename": "0034.png",
                    "title": "旧全局",
                    "shapes": [
                        {"title": "世界", "isSceneIdentity": True, "sceneIdentityScope": "global"},
                    ],
                },
                {
                    "type": "image",
                    "filename": "0999.png",
                    "title": "模板",
                    "shapes": [
                        {"title": "按钮"},
                    ],
                },
            ],
        }
    ]

    migrated, stats = migrate_frame_layers_in_tree(tree)
    children = migrated[0]["children"]

    assert [item["layer"] for item in children] == [2, 1, 3]
    assert all("sceneIdentityLevel" not in item for item in children)
    assert children[0]["shapes"][0]["sceneIdentityScope"] == "local"
    assert stats.to_dict() == {
        "image_count": 3,
        "added_layer_count": 3,
        "preserved_layer_count": 0,
        "removed_legacy_level_count": 0,
        "layer1_count": 1,
        "layer2_count": 1,
        "layer3_count": 1,
    }


def test_frame_layer_migration_maps_old_level_to_new_layer_and_removes_old_field():
    tree = [
        {"type": "image", "filename": "0001.png", "sceneIdentityLevel": 2, "shapes": []},
        {"type": "image", "filename": "0002.png", "sceneIdentityLevel": 1, "shapes": []},
        {"type": "image", "filename": "0003.png", "sceneIdentityLevel": 0, "shapes": []},
    ]

    migrated, stats = migrate_frame_layers_in_tree(tree)

    assert [item["layer"] for item in migrated] == [1, 2, 3]
    assert all("sceneIdentityLevel" not in item for item in migrated)
    assert stats.removed_legacy_level_count == 3


def test_frame_layer_migration_preserves_explicit_layer_by_default():
    tree = [
        {
            "type": "image",
            "filename": "0001.png",
            "layer": 3,
            "sceneIdentityLevel": 2,
            "shapes": [{"title": "旧全局", "isSceneIdentity": True, "sceneIdentityScope": "global"}],
        }
    ]

    migrated, stats = migrate_frame_layers_in_tree(tree)

    assert migrated[0]["layer"] == 3
    assert "sceneIdentityLevel" not in migrated[0]
    assert stats.preserved_layer_count == 1
    assert stats.added_layer_count == 0


def test_frame_layer_migration_file_dry_run_does_not_write(tmp_path):
    path = tmp_path / "asset-tree.json"
    path.write_text(json.dumps([{"type": "image", "filename": "0001.png", "shapes": []}], ensure_ascii=False), encoding="utf-8")

    result = migrate_frame_layers_file(path, write=False)

    assert result["changed"] is True
    assert result["write"] is False
    assert result["backup_path"] is None
    assert json.loads(path.read_text(encoding="utf-8"))[0].get("layer") is None
