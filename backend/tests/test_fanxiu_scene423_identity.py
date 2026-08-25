from __future__ import annotations

from backend.core.fanxiu.behavior_tree.runtime import create_behavior_tree_runtime_runner
from scripts.fanxiu_scene423_identity import IDENTITY_SPEC, refine_scene423_identity, validate_scene423_identity


def _tree() -> list[dict]:
    return [
        {
            "id": "scene-423",
            "type": "image",
            "title": "",
            "filename": "0423.png",
            "children": [],
            "shapes": [
                {
                    "id": "old-generic-continue",
                    "title": "继续",
                    "isSceneIdentity": True,
                    "sceneIdentityRole": "required",
                    "imageMatchRole": "off",
                    "ocrMatchRole": "required",
                    "ocrEnabled": True,
                    "ocrText": "继续",
                    "sceneJumpTarget": "308(2),181(1)",
                }
            ],
        }
    ]


def test_scene423_keeps_layer2_but_separates_specific_identity_from_continue_action() -> None:
    refined, report = refine_scene423_identity(_tree())
    validate_scene423_identity(refined)
    scene = refined[0]
    identities = [shape for shape in scene["shapes"] if shape.get("isSceneIdentity")]
    action = next(shape for shape in scene["shapes"] if shape.get("title") == "继续")

    assert scene["layer"] == 2
    assert [shape["ocrText"] for shape in identities] == ["仙宴圆满结束"]
    assert action["isSceneIdentity"] is False
    assert action["ocrText"] == "点击屏幕继续"
    assert action["sceneJumpTarget"] == ""
    assert report["removed_contaminated_jump_targets"] == "308(2),181(1)"


def test_scene423_refinement_is_idempotent() -> None:
    once, _ = refine_scene423_identity(_tree())
    twice, report = refine_scene423_identity(once)

    assert twice == once
    assert report["removed_contaminated_jump_targets"] == ""


def test_default_layer2_generic_continue_frame_no_longer_matches_scene423(monkeypatch) -> None:
    tree, _ = refine_scene423_identity(_tree())
    runner = create_behavior_tree_runtime_runner()
    ctx = {"asset_tree": tree, "images": runner._index_images(tree)}

    def match_shape(_ctx, _image, shape, frame, *, condition=None, **_kwargs):
        score = 100.0 if condition == "ocr" and str(shape.get("ocrText") or "") in frame else 0.0
        return {"matched": score > 0, "similarity": score}

    monkeypatch.setattr(runner, "_match_shape", match_shape)
    monkeypatch.setattr(runner, "_scene_discriminator_adjusted_score", lambda _ctx, _image, _frame, score: score)

    assert runner._identify_scene_number_by_graph(ctx, "点击屏幕继续") == (None, 0.0, "no_match")
    assert runner._identify_scene_number_by_graph(ctx, "仙宴圆满结束 点击屏幕继续") == (423, 100.0, "matched")


def test_scene423_identity_is_specific_against_scene659_and_other_generic_results() -> None:
    negative_ocr_texts = (
        "仙宴 触发哮天犬效果 宴会氛围 点击屏幕继续",
        "仙侣 升级 点击屏幕继续",
        "恭喜获得 点击屏幕继续",
        "灵兽吞噬 点击屏幕继续",
    )

    assert all(IDENTITY_SPEC["ocrText"] not in text for text in negative_ocr_texts)
