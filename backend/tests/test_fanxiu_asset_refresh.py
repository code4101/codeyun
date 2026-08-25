from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


class _DebugContext:
    def __init__(self, runner, ctx, stop_event, readonly=False) -> None:
        self.runner = runner
        self.ctx = ctx
        self.stop_event = stop_event
        self.readonly = readonly

    def rebind(self, ctx, stop_event, readonly=False) -> None:
        self.ctx = ctx
        self.stop_event = stop_event
        self.readonly = readonly


class _Runner:
    def __init__(self) -> None:
        self._cell_execution_lock = threading.RLock()
        self._lock = threading.RLock()
        self._stop_event = None
        self.load_count = 0
        self.invalidations: list[Path] = []
        self._status: dict[str, Any] = {}

    def _load_asset_tree(self, path: Path) -> list[dict[str, Any]]:
        self.load_count += 1
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _index_images(tree: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        return {
            int(item["number"]): item
            for item in tree
            if item.get("type") == "image" and item.get("number") is not None
        }

    @staticmethod
    def _require_assets(_ctx: dict[str, Any]) -> None:
        return None

    def _invalidate_asset_derived_caches(self, path: Path) -> None:
        self.invalidations.append(path)

    @staticmethod
    def _fanxiu_runtime(ctx, _path, stop_event=None):
        return SimpleNamespace(
            ctx=ctx,
            stop_event=stop_event,
            scene_titles={scene_id: item["title"] for scene_id, item in ctx["images"].items()},
        )

    def _set_status_locked(self, status, message, *, phase):
        self._status.update(status=status, message=message, phase=phase)

    def _clear_current_task_locked(self) -> None:
        return None

    def _persist_status(self) -> None:
        return None


def _write_tree(path: Path, *, title: str, shape_title: str) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    "type": "image",
                    "number": 558,
                    "title": title,
                    "shapes": [{"id": "title", "title": shape_title}],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _binding(monkeypatch, tmp_path: Path):
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import FanxiuJupyterBinding
    from backend.core.fanxiu.data_annotation import debug_eval

    monkeypatch.setattr(debug_eval, "BehaviorTreeRuntimeDebugContext", _DebugContext)
    asset_tree_path = tmp_path / "asset-tree.json"
    _write_tree(asset_tree_path, title="云梦旧场景", shape_title="旧标题")
    runner = _Runner()
    binding = FanxiuJupyterBinding(runner, {"id": "entry"}, "entry", asset_tree_path)
    return binding, runner, asset_tree_path


def test_asset_refresh_uses_one_loader_for_incremental_and_force(monkeypatch, tmp_path) -> None:
    binding, runner, asset_tree_path = _binding(monkeypatch, tmp_path)

    assert runner.load_count == 1
    assert binding.runtime.scene_titles == {558: "云梦旧场景"}
    assert binding.runtime_ctx["asset_tree_generation"] == 1

    unchanged = binding.refresh_assets()
    assert unchanged["reloaded"] is False
    assert unchanged["pending"] is False
    assert runner.load_count == 1
    assert binding.refresh() is binding
    assert runner.load_count == 1

    _write_tree(asset_tree_path, title="云梦新场景（增量）", shape_title="试剑")
    incremental = binding.refresh_assets()
    assert incremental["reloaded"] is True
    assert runner.load_count == 2
    assert binding.runtime.scene_titles == {558: "云梦新场景（增量）"}
    assert binding.runtime_ctx["images"][558]["shapes"][0]["title"] == "试剑"

    forced = binding.refresh_assets(force=True)
    assert forced["reloaded"] is True
    assert forced["generation"] == 3
    assert runner.load_count == 3
    assert runner.invalidations == [asset_tree_path, asset_tree_path, asset_tree_path]


def test_asset_refresh_requested_inside_cell_is_applied_at_next_boundary(monkeypatch, tmp_path) -> None:
    binding, runner, asset_tree_path = _binding(monkeypatch, tmp_path)
    shell = SimpleNamespace(user_ns={})
    info = SimpleNamespace(raw_cell="print('business cell')")

    binding.begin_cell(info, shell)
    old_runtime = binding.runtime
    old_generation = binding.runtime_ctx["asset_tree_generation"]
    _write_tree(asset_tree_path, title="云梦下一代", shape_title="试剑")

    queued = binding.refresh_assets(force=True)
    assert queued["reloaded"] is False
    assert queued["pending"] is True
    assert binding.runtime is old_runtime
    assert binding.runtime.scene_titles == {558: "云梦旧场景"}
    assert binding.runtime_ctx["asset_tree_generation"] == old_generation

    binding.end_cell(SimpleNamespace(error_in_exec=None, error_before_exec=None))
    binding.begin_cell(SimpleNamespace(raw_cell="runtime.scene_titles"), shell)
    try:
        assert runner.load_count == 2
        assert binding.runtime.scene_titles == {558: "云梦下一代"}
        assert binding.runtime_ctx["asset_tree_generation"] == old_generation + 1
        assert shell.user_ns["runtime"] is binding.runtime
        assert shell.user_ns["ctx"] is binding.ctx
        assert shell.user_ns["refresh_assets"] == binding.refresh_assets
        assert shell.user_ns["refresh"] == binding.refresh
    finally:
        binding.end_cell(SimpleNamespace(error_in_exec=None, error_before_exec=None))


def test_scene_relation_cache_key_tracks_asset_generation() -> None:
    from backend.core.fanxiu.data_annotation.behavior_tree_runtime import BehaviorTreeRuntimeRunner

    runner = object.__new__(BehaviorTreeRuntimeRunner)
    runner.scene_threshold = 80
    runner.scene_thresholds = {}
    first = runner._scene_match_cache_key(
        {"asset_tree_revision": "same", "asset_tree_generation": 1, "images": {}},
        [558],
        threshold=None,
    )
    second = runner._scene_match_cache_key(
        {"asset_tree_revision": "same", "asset_tree_generation": 2, "images": {}},
        [558],
        threshold=None,
    )

    assert first != second


def test_failed_force_refresh_keeps_old_snapshot_and_stays_pending(monkeypatch, tmp_path) -> None:
    binding, runner, asset_tree_path = _binding(monkeypatch, tmp_path)
    old_runtime = binding.runtime
    asset_tree_path.write_text("{invalid", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        binding.refresh_assets(force=True)

    assert binding.runtime is old_runtime
    assert binding.runtime.scene_titles == {558: "云梦旧场景"}
    assert binding._asset_reload_requested is True
    assert runner.load_count == 2


def test_in_place_asset_mutation_advances_ctx_and_clears_derived_caches(tmp_path) -> None:
    from backend.core.fanxiu.data_annotation.behavior_tree_runtime import BehaviorTreeRuntimeRunner

    runner = BehaviorTreeRuntimeRunner()
    shape = {"id": "back", "title": "返回", "sceneJumpTarget": "34"}
    tree = [
        {
            "type": "image",
            "number": 558,
            "title": "云梦试剑",
            "filename": "0558.png",
            "shapes": [shape],
        }
    ]
    asset_tree_path = tmp_path / "asset-tree.json"
    asset_tree_path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    ctx = {
        "asset_tree": tree,
        "images": runner._index_images(tree),
        "asset_tree_revision": "old",
        "asset_tree_generation": 7,
        "_scene_graph_relation_cache": {"old": True},
        "_scene_discriminator_groups": [[{"old": True}]],
        "_scene_discriminator_score_cache": {"old": True},
        "_ocr_tokens_cache": {"frame": "same-frame"},
    }

    runner._record_scene_jump_landing(
        ctx,
        asset_tree_path,
        tree,
        shape,
        34,
        reason="test",
    )

    assert ctx["asset_tree_revision"] == hashlib.sha256(asset_tree_path.read_bytes()).hexdigest()
    assert ctx["asset_tree_generation"] == 8
    assert "_scene_graph_relation_cache" not in ctx
    assert "_scene_discriminator_groups" not in ctx
    assert "_scene_discriminator_score_cache" not in ctx
    # OCR is a frame cache, not an asset-derived cache, and remains reusable.
    assert ctx["_ocr_tokens_cache"] == {"frame": "same-frame"}
