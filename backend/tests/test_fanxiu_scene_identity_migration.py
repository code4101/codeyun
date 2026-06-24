import json

from backend.core.fanxiu.data_annotation.scene_identity_migration import (
    migrate_scene_identity_levels_file,
    migrate_scene_identity_levels_in_tree,
)


def test_scene_identity_level_migration_infers_legacy_scopes_without_touching_shapes():
    tree = [
        {
            "type": "folder",
            "title": "日常",
            "children": [
                {
                    "type": "image",
                    "filename": "0266.png",
                    "title": "局部",
                    "shapes": [
                        {"title": "法则之主", "isSceneIdentity": True, "sceneIdentityScope": "local"},
                    ],
                },
                {
                    "type": "image",
                    "filename": "0034.png",
                    "title": "全局",
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

    migrated, stats = migrate_scene_identity_levels_in_tree(tree)
    children = migrated[0]["children"]

    assert [item["sceneIdentityLevel"] for item in children] == [1, 2, 0]
    assert children[0]["shapes"][0]["sceneIdentityScope"] == "local"
    assert stats.to_dict() == {
        "image_count": 3,
        "added_level_count": 3,
        "preserved_level_count": 0,
        "level0_count": 1,
        "level1_count": 1,
        "level2_count": 1,
    }


def test_scene_identity_level_migration_preserves_explicit_level_by_default():
    tree = [
        {
            "type": "image",
            "filename": "0001.png",
            "sceneIdentityLevel": 0,
            "shapes": [{"title": "旧全局", "isSceneIdentity": True, "sceneIdentityScope": "global"}],
        }
    ]

    migrated, stats = migrate_scene_identity_levels_in_tree(tree)

    assert migrated[0]["sceneIdentityLevel"] == 0
    assert stats.preserved_level_count == 1
    assert stats.added_level_count == 0


def test_scene_identity_level_migration_file_dry_run_does_not_write(tmp_path):
    path = tmp_path / "asset-tree.json"
    path.write_text(json.dumps([{"type": "image", "filename": "0001.png", "shapes": []}], ensure_ascii=False), encoding="utf-8")

    result = migrate_scene_identity_levels_file(path, write=False)

    assert result["changed"] is True
    assert result["write"] is False
    assert result["backup_path"] is None
    assert json.loads(path.read_text(encoding="utf-8"))[0].get("sceneIdentityLevel") is None
