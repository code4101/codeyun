from __future__ import annotations

from scripts.fanxiu_scene659_identity import IDENTITY_SPECS, refine_scene659_identity, validate_scene659_identity


def _tree() -> list[dict]:
    return [{
        "id": "scene-659", "type": "image", "filename": "0659.png", "layer": 2, "children": [],
        "shapes": [
            {"id": "old", "title": "仙宴圆满结束", "isSceneIdentity": True, "sceneIdentityRole": "required", "ocrMatchRole": "required", "ocrText": "仙宴圆满结束"},
            {"id": "action", "title": "点击屏幕继续", "isSceneIdentity": False, "sceneJumpTarget": "659(4),422(4),660(3),642"},
        ],
    }]


def test_scene659_uses_two_specific_required_anchors_and_preserves_action_history() -> None:
    refined, report = refine_scene659_identity(_tree())
    validate_scene659_identity(refined)
    identities = [shape for shape in refined[0]["shapes"] if shape.get("isSceneIdentity")]
    action = next(shape for shape in refined[0]["shapes"] if shape.get("id") == "action")

    assert [shape["ocrText"] for shape in identities] == ["触发", "宾客名单"]
    assert action["sceneJumpTarget"] == "659(4),422(4),660(3),642"
    assert report["replaced_identity_ids"] == ["old"]


def test_scene659_refinement_is_idempotent() -> None:
    once, _ = refine_scene659_identity(_tree())
    twice, _ = refine_scene659_identity(once)
    assert twice == once


def test_scene659_dual_anchor_rejects_scene423_and_generic_results() -> None:
    required = [spec["ocrText"] for spec in IDENTITY_SPECS]
    negatives = (
        "仙宴圆满结束 宾客名单 点击屏幕继续",
        "仙侣升级 点击屏幕继续",
        "恭喜获得 点击屏幕继续",
    )
    assert all(not all(anchor in text for anchor in required) for text in negatives)
