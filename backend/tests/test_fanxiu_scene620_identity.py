from __future__ import annotations

from backend.core.fanxiu.behavior_tree.runtime import create_behavior_tree_runtime_runner
from scripts.fanxiu_scene620_identity import IDENTITY_SPECS, refine_scene620_identity, validate_scene620_identity


def _tree() -> list[dict]:
    return [
        {
            "id": "scene-620",
            "type": "image",
            "title": "仙侣升级结算",
            "filename": "0620.png",
            "layer": 2,
            "children": [],
            "shapes": [
                {
                    "id": "shape-620-continue",
                    "title": "继续",
                    "isSceneIdentity": True,
                    "sceneIdentityRole": "required",
                    "ocrMatchRole": "required",
                    "ocrText": "点击屏幕继续",
                    "sceneJumpTarget": "659(8),642(2),422(1)",
                }
            ],
        }
    ]


def test_refine_scene620_uses_two_specific_required_anchors_and_keeps_continue_as_action() -> None:
    refined, report = refine_scene620_identity(_tree())
    validate_scene620_identity(refined)
    shapes = {shape["id"]: shape for shape in refined[0]["shapes"]}

    assert shapes["shape-620-continue"]["isSceneIdentity"] is False
    assert shapes["shape-620-continue"]["sceneIdentityRole"] == "off"
    assert shapes["shape-620-continue"]["sceneJumpTarget"] == ""
    assert [shapes[spec["id"]]["ocrText"] for spec in IDENTITY_SPECS] == ["仙侣", "升级"]
    assert all(shapes[spec["id"]]["sceneIdentityRole"] == "required" for spec in IDENTITY_SPECS)
    assert report["removed_contaminated_jump_targets"] == "659(8),642(2),422(1)"


def test_refine_scene620_is_idempotent() -> None:
    once, _ = refine_scene620_identity(_tree())
    twice, report = refine_scene620_identity(once)

    assert twice == once
    assert report["removed_contaminated_jump_targets"] == ""


def test_scene659_generic_continue_text_cannot_satisfy_scene620_identity_contract() -> None:
    generic_result_texts = ["仙宴", "获得奖励", "点击屏幕继续"]
    required = [spec["ocrText"] for spec in IDENTITY_SPECS]

    assert not all(any(anchor in text for text in generic_result_texts) for anchor in required)


def test_default_layer2_recognition_does_not_misidentify_scene659_as_scene620(monkeypatch) -> None:
    tree, _ = refine_scene620_identity(_tree())
    scene659 = {
        "id": "scene-659",
        "type": "image",
        "title": "仙宴圆满结束",
        "filename": "0659.png",
        "layer": 2,
        "children": [],
        "shapes": [
            {
                "id": "shape-659-identity",
                "title": "仙宴",
                "isSceneIdentity": True,
                "sceneIdentityRole": "required",
                "imageMatchRole": "off",
                "ocrMatchRole": "required",
                "ocrEnabled": True,
                "ocrText": "仙宴",
                "ocrMatchMode": "contains",
                "x": 0.1,
                "y": 0.1,
                "w": 0.3,
                "h": 0.1,
            }
        ],
    }
    tree.append(scene659)
    runner = create_behavior_tree_runtime_runner()
    ctx = {"asset_tree": tree, "images": runner._index_images(tree)}
    frame_texts = {"仙宴", "点击屏幕继续"}

    def match_shape(_ctx, _image, shape, _frame, *, condition=None, **_kwargs):
        text = str(shape.get("ocrText") or "")
        score = 100.0 if condition == "ocr" and text in frame_texts else 0.0
        return {"matched": score > 0, "similarity": score}

    monkeypatch.setattr(runner, "_match_shape", match_shape)
    monkeypatch.setattr(
        runner,
        "_scene_discriminator_adjusted_score",
        lambda _ctx, _image, _frame, score: score,
    )

    assert runner._identify_scene_number_by_graph(ctx, "saved-0659-frame") == (
        659,
        100.0,
        "matched",
    )
