import base64
from pathlib import Path
from types import SimpleNamespace

from backend.api import fanxiu


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def test_save_frame_accepts_numeric_same_level_position(monkeypatch, tmp_path):
    captured = {}
    tree = [{
        "id": "folder-login-popups",
        "type": "folder",
        "title": "登录弹窗",
        "children": [{
            "id": "image-recharge-gift",
            "type": "image",
            "title": "充值豪礼",
            "filename": "0022.png",
            "shapes": [],
        }],
    }]
    snapshot = SimpleNamespace(tree=tree, revision="before", updated_at=1.0)
    saved_snapshot = SimpleNamespace(tree=tree, revision="after", updated_at=2.0)
    asset = SimpleNamespace(
        entry_id="entry-a",
        filename="0693.png",
        path=Path(tmp_path) / "0693.png",
    )

    monkeypatch.setattr(fanxiu, "ensure_feature_access", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(fanxiu, "_get_user_device_or_404", lambda *_args, **_kwargs: SimpleNamespace(mode="local"))
    monkeypatch.setattr(fanxiu, "_data_annotation_asset_tree_path", lambda _entry_id: Path(tmp_path) / "asset-tree.json")
    monkeypatch.setattr(fanxiu, "read_data_annotation_asset_tree_snapshot", lambda _path: snapshot)

    def fake_save(path, data, node, **kwargs):
        captured.update(path=path, data=data, node=node, kwargs=kwargs)
        return SimpleNamespace(asset=asset, snapshot=saved_snapshot)

    monkeypatch.setattr(fanxiu, "save_data_annotation_frame_tree_node", fake_save)

    response = fanxiu.save_fanxiu_data_annotation_frame(
        fanxiu.FanxiuDataAnnotationSaveFrameRequest(
            entry_id="entry-a",
            current_frame_data_url="data:image/png;base64," + base64.b64encode(_PNG_1X1).decode("ascii"),
            title="充值豪礼-1折代金券",
            same_level_as_scene_id=22,
        ),
        current_user=SimpleNamespace(id=1),
        session=SimpleNamespace(),
    )

    assert response["filename"] == "0693.png"
    assert captured["kwargs"]["after_node_id"] == "image-recharge-gift"
    assert captured["kwargs"]["parent_id"] is None
    assert captured["node"]["title"] == "充值豪礼-1折代金券"
    assert captured["node"]["shapes"] == []
    assert captured["node"]["id"].startswith("image-")
