from __future__ import annotations

import ast
from pathlib import Path

import pytest

from backend.core.fanxiu.data_annotation.runner import create_behavior_tree_runtime_runner
from backend.core.fanxiu.data_annotation.behavior_tree_runtime import BehaviorTreeRuntime


RAW_OCR_METHODS = {
    "_ocr_frame",
    "_ocr_fragments",
    "_ocr_tokens",
    "_cached_ocr_fragments",
    "_cached_ocr_tokens",
}

RAW_OCR_ENGINE_OWNERS = {
    "_ocr_frame",
    "_ocr_fragments",
    "_ocr_tokens",
    "_cached_ocr_result",
    "_cached_ocr_fragments",
    "_cached_ocr_tokens",
    "_has_cached_ocr_tokens",
    "_shared_spatial_ocr_result",
    "_ocr_fragments_in_shapes",
    "_ocr_tokens_in_shapes",
}


def test_fanxiu_business_code_has_no_direct_raw_frame_ocr_calls() -> None:
    source_root = Path(__file__).parents[1] / "core" / "fanxiu" / "data_annotation"
    violations: list[str] = []
    for path in source_root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        owners: list[str] = []

        class Visitor(ast.NodeVisitor):
            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                owners.append(node.name)
                self.generic_visit(node)
                owners.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node: ast.Call) -> None:
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in RAW_OCR_METHODS
                    and (owners[-1] if owners else "") not in RAW_OCR_ENGINE_OWNERS
                ):
                    violations.append(
                        f"{path.relative_to(source_root)}:{node.lineno} "
                        f"{owners[-1] if owners else '<module>'} -> {node.func.attr}"
                    )
                self.generic_visit(node)

        Visitor().visit(tree)

    assert violations == []


def test_runtime_public_ocr_returns_only_recognized_scene_shape_intersections(monkeypatch) -> None:
    runner = create_behavior_tree_runtime_runner()
    image = {
        "type": "image",
        "filename": "0530.png",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"id": "identity", "title": "网络状态不佳", "x": 0.2, "y": 0.4, "w": 0.3, "h": 0.05},
        ],
    }
    ctx = {"asset_tree": [image], "images": {530: image}}
    runtime = BehaviorTreeRuntime(runner, ctx)
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: (530, 100.0))
    monkeypatch.setattr(
        runner,
        "_shared_spatial_ocr_result",
        lambda *_args, **_kwargs: {
            "lines": [
                {"text": "网络状态不佳", "x": 200, "y": 650, "w": 240, "h": 32},
                {"text": "全屏其它文字", "x": 20, "y": 1200, "w": 200, "h": 30},
            ],
            "tokens": [],
        },
    )

    assert [item["text"] for item in runtime.ocr_fragments("frame")] == ["网络状态不佳"]
    assert runtime.ocr_text("frame") == "网络状态不佳"


def test_scene_recognition_does_not_apply_full_frame_ocr_override(monkeypatch) -> None:
    runner = create_behavior_tree_runtime_runner()
    ctx = {"asset_tree": [], "images": {}}
    monkeypatch.setattr(
        runner,
        "_identify_scene_number_by_graph",
        lambda *_args, **_kwargs: (None, 0.0, "no_match"),
    )
    monkeypatch.setattr(
        runner,
        "_cached_ocr_fragments",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不得读取全帧 OCR")),
    )

    assert runner._identify_scene_number(ctx, "frame") == (None, 0.0)
    assert ctx["_last_scene_recognition_status"] == "no_match"


def test_shape_crop_ocr_missing_shape_fails_closed_without_full_frame_fallback(monkeypatch) -> None:
    runner = create_behavior_tree_runtime_runner()
    image = {"type": "image", "filename": "0001.png", "width": 900, "height": 1600, "shapes": []}
    monkeypatch.setattr(
        runner,
        "_ocr_frame",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不得回退全帧 OCR")),
    )

    assert runner._ocr_fragments_in_shapes("frame", image, ("不存在",), ctx=None) == []
    assert runner._ocr_tokens_in_shapes("frame", image, ("不存在",), ctx=None) == []


def test_popup_directory_indexes_specific_scene_and_its_bound_action(monkeypatch) -> None:
    runner = create_behavior_tree_runtime_runner()
    image = {
        "type": "image",
        "title": "网络状态不佳提示",
        "filename": "0530.png",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"id": "identity", "title": "网络状态不佳", "sceneIdentityRole": "required"},
            {"id": "confirm", "title": "确定", "sceneJumpTarget": "-1"},
        ],
    }
    tree = [{"type": "folder", "title": "弹窗", "children": [image]}]
    candidates = runner._auto_close_guard_images(tree)
    monkeypatch.setattr(runner, "_scene_score", lambda *_args, **_kwargs: 100.0)

    candidate, score = runner._auto_close_popup_graph_match(
        {"asset_tree": tree, "images": {530: image}},
        candidates,
        "frame",
    )

    assert len(candidates) == 1
    assert candidates[0]["folder_path"] == "弹窗"
    assert candidates[0]["action_shape"]["title"] == "确定"
    assert candidate is candidates[0]
    assert score == 100.0


def test_lingmai_summary_never_consumes_lundao_scene_302() -> None:
    runner = create_behavior_tree_runtime_runner()

    class Runtime:
        def cur_frame(self, update: bool = False) -> str:
            return "frame"

    result = runner._confirm_daily_lingmai_summary_popup(
        Runtime(),
        {},
        task_label="灵脉",
        scene_id=302,
        frame="frame",
    )

    with pytest.raises(RuntimeError, match="未识别到正式灵脉收益确认 scene"):
        next(result)
