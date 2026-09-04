from __future__ import annotations

import io
import json
import os
import time
from pathlib import Path

from PIL import Image

from backend.core.fanxiu.behavior_tree.kernel_scheduler import create_behavior_tree_executor
from backend.core.fanxiu.data_annotation.behavior_tree_executor import BehaviorTreeContext
from backend.core.fanxiu.data_annotation.scene_diagnostics import save_scene_diagnostic_frame


def _write_scene_image(directory: Path, scene_id: int, color: tuple[int, int, int]) -> dict:
    filename = f"{scene_id:04d}.png"
    Image.new("RGB", (90, 160), color).save(directory / filename)
    return {
        "type": "image",
        "id": scene_id,
        "title": f"scene-{scene_id}",
        "filename": filename,
        "width": 90,
        "height": 160,
        "layer": 1 if scene_id == 101 else 2,
        "shapes": [],
        "children": [],
    }


def _diagnostic_context(tmp_path: Path):
    asset_root = tmp_path / "assets"
    image_root = asset_root / "images"
    image_root.mkdir(parents=True)
    scenes = {
        101: _write_scene_image(image_root, 101, (255, 255, 255)),
        201: _write_scene_image(image_root, 201, (180, 180, 180)),
        301: _write_scene_image(image_root, 301, (80, 80, 80)),
    }
    scenes[101]["shapes"] = [
        {
            "id": "identity",
            "title": "身份",
            "x": 0.1,
            "y": 0.1,
            "w": 0.25,
            "h": 0.12,
            "isSceneIdentity": True,
            "_inheritanceSourceSceneId": 101,
        },
        {
            "id": "action",
            "title": "返回",
            "x": 0.1,
            "y": 0.7,
            "w": 0.25,
            "h": 0.12,
            "_inheritanceSourceSceneId": 101,
        },
    ]
    tree_path = asset_root / "tree.json"
    tree_path.write_text("[]", encoding="utf-8")
    return {
        "asset_tree": list(scenes.values()),
        "asset_tree_path": tree_path,
        "images": scenes,
    }


def _live_frame(runner) -> str:
    output = io.BytesIO()
    Image.new("RGB", (90, 160), (230, 230, 230)).save(output, format="PNG")
    return runner._data_url(output.getvalue())


def test_scene_comparison_overlays_all_shapes_on_both_images(monkeypatch, tmp_path) -> None:
    runner = create_behavior_tree_executor()
    context = BehaviorTreeContext(runner, _diagnostic_context(tmp_path))
    output_root = tmp_path / "diagnostics"
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.scene_diagnostics._diagnostic_root",
        lambda: output_root,
    )

    result = Path(context.render_scene_comparison(_live_frame(runner), 101))

    assert result.is_file()
    metadata = json.loads(result.with_suffix(".json").read_text(encoding="utf-8"))
    assert metadata["kind"] == "scene_comparison"
    with Image.open(result) as image:
        colors = image.convert("RGB").getcolors(maxcolors=image.width * image.height)
    assert colors is not None
    palette = {color for _count, color in colors}
    assert (220, 0, 0) in palette
    assert (0, 0, 0) in palette


def test_unknown_overview_deduplicates_layer0_and_global_candidates_without_shapes(
    monkeypatch,
    tmp_path,
) -> None:
    runner = create_behavior_tree_executor()
    context = BehaviorTreeContext(runner, _diagnostic_context(tmp_path))
    output_root = tmp_path / "diagnostics"
    scores = {101: 98.0, 201: 96.0, 301: 70.0}
    monkeypatch.setattr(
        runner,
        "_scene_reference_similarity",
        lambda _ctx, image, _frame: scores[int(image["id"])],
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.scene_diagnostics._diagnostic_root",
        lambda: output_root,
    )

    result = Path(context.render_unknown_scene_overview(_live_frame(runner), [301, 101], top_k=2))

    assert result.is_file()
    metadata = json.loads(result.with_suffix(".json").read_text(encoding="utf-8"))
    assert metadata["layer0_scene_ids"] == [301, 101]
    assert metadata["global_candidates"] == [
        {"scene_id": 101, "similarity": 98.0},
        {"scene_id": 201, "similarity": 96.0},
    ]
    assert metadata["displayed_scene_ids"] == [301, 101, 201]
    assert metadata["shapes_rendered"] is False


def test_scene_diagnostic_evidence_is_bounded_by_age_and_file_count(monkeypatch, tmp_path) -> None:
    runner = create_behavior_tree_executor()
    output_root = tmp_path / "diagnostics"
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.scene_diagnostics._diagnostic_root",
        lambda: output_root,
    )
    monkeypatch.setenv("CODEYUN_FANXIU_SCENE_DIAGNOSTIC_MAX_FILES", "4")
    monkeypatch.setenv("CODEYUN_FANXIU_SCENE_DIAGNOSTIC_MAX_BYTES", str(1024 * 1024))
    monkeypatch.setenv("CODEYUN_FANXIU_SCENE_DIAGNOSTIC_MAX_AGE_SECONDS", "60")
    frame = _live_frame(runner)

    stale = output_root / "stale.png"
    output_root.mkdir(parents=True)
    stale.write_bytes(runner._decode_frame_data_url(frame))
    old = time.time() - 120
    os.utime(stale, (old, old))
    for index in range(3):
        save_scene_diagnostic_frame(
            runner,
            frame,
            kind="layer2_match",
            label=f"case-{index}",
            expected_scene_ids=[301],
            matched_scene_id=201,
            matched_layer=2,
        )

    retained = list(output_root.rglob("*.png")) + list(output_root.rglob("*.json"))
    assert not stale.exists()
    assert len(retained) <= 4
